# -*- coding: utf-8 -*-
# Wersja: OSTATECZNA (AI + Airtable + Dwuetapowa Analiza + Spersonalizowane Przypomnienia)
from flask import Flask, request, Response
import threading
import os
import json
import requests
import time
import vertexai
from vertexai.generative_models import (
    GenerativeModel, Part, Content, GenerationConfig,
    SafetySetting, HarmCategory, HarmBlockThreshold
)
import errno
from config import FB_VERIFY_TOKEN, BREVO_API_KEY, FROM_EMAIL, ADMIN_EMAIL_NOTIFICATIONS
from database import DatabaseTable
import logging
from datetime import datetime, timedelta
import pytz
from apscheduler.schedulers.background import BackgroundScheduler
import atexit
import uuid

# --- Konfiguracja Ogólna ---
app = Flask(__name__)
VERIFY_TOKEN = os.environ.get("FB_VERIFY_TOKEN", FB_VERIFY_TOKEN)
FACEBOOK_GRAPH_API_URL = "https://graph.facebook.com/v19.0/me/messages"
HISTORY_DIR = os.path.join(os.path.dirname(__file__), "conversation_store")
MAX_HISTORY_TURNS = 10

# === ZABEZPIECZENIE PRZED SPAMEM (MESSAGE BUFFERING) ===
user_timers = {}
user_message_buffers = {}
DEBOUNCE_SECONDS = 5  # Zwiększamy do 10 sekund, żeby dać czas na pisanie

# --- Wczytywanie konfiguracji z pliku ---
config_path = '/home/korepetotor2/strona/config.json'
try:
    with open(config_path, 'r', encoding='utf-8') as f:
        config = json.load(f)
except (FileNotFoundError, json.JSONDecodeError) as e:
    print(f"!!! KRYTYCZNY BŁĄD: Nie można wczytać pliku '{config_path}': {e}")
    exit()

AI_CONFIG = config.get("AI_CONFIG", {})
AIRTABLE_CONFIG = config.get("AIRTABLE_CONFIG", {})
PAGE_CONFIG = config.get("PAGE_CONFIG", {})

PROJECT_ID = AI_CONFIG.get("PROJECT_ID")
LOCATION = AI_CONFIG.get("LOCATION")
MODEL_ID = AI_CONFIG.get("MODEL_ID")

# Inicjalizacja bazy danych SQLite (zastąpienie Airtable)
try:
    clients_table = DatabaseTable('Klienci')
    print("--- Połączenie z bazą danych SQLite OK.")
except Exception as e:
    print(f"!!! BŁĄD: Nie można połączyć się z bazą danych: {e}")
    clients_table = None

# === NOWE STAŁE DLA SYSTEMU PRZYPOMNIEŃ ===
NUDGE_TASKS_FILE = "nudge_tasks.json"
FOLLOW_UP_WINDOW_HOURS = 23
TIMEZONE = "Europe/Warsaw"
NUDGE_WINDOW_START, NUDGE_WINDOW_END = 6, 23

# --- Znaczniki i Ustawienia Modelu ---
AGREEMENT_MARKER = "[ZAPISZ_NA_LEKCJE]"
PRESENT_OFFER_MARKER = "[PREZENTUJ_OFERTE]" # <--- DODAJ TĘ LINIĘ
EXPECTING_REPLY = "EXPECTING_REPLY"
CONVERSATION_ENDED = "CONVERSATION_ENDED"
FOLLOW_UP_LATER = "FOLLOW_UP_LATER"

GENERATION_CONFIG = GenerationConfig(temperature=0.7, top_p=0.95, top_k=40, max_output_tokens=1024)
SAFETY_SETTINGS = [
    SafetySetting(category=HarmCategory.HARM_CATEGORY_HARASSMENT, threshold=HarmBlockThreshold.BLOCK_ONLY_HIGH),
    SafetySetting(category=HarmCategory.HARM_CATEGORY_HATE_SPEECH, threshold=HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE),
]

# =====================================================================
# === INICJALIZACJA AI ================================================
# =====================================================================
gemini_model = None
try:
    if not all([PROJECT_ID, LOCATION, MODEL_ID]):
        print("!!! KRYTYCZNY BŁĄD: Brak pełnej konfiguracji AI w pliku config.json")
    else:
        print(f"--- Inicjalizowanie Vertex AI: Projekt={PROJECT_ID}, Lokalizacja={LOCATION}")
        vertexai.init(project=PROJECT_ID, location=LOCATION)
        print("--- Inicjalizacja Vertex AI OK.")
        print(f"--- Ładowanie modelu: {MODEL_ID}")
        gemini_model = GenerativeModel(MODEL_ID)
        print(f"--- Model {MODEL_ID} załadowany OK.")
except Exception as e:
    print(f"!!! KRYTYCZNY BŁĄD inicjalizacji Vertex AI: {e}", flush=True)


# =====================================================================
# === INSTRUKCJE SYSTEMOWE DLA AI =====================================
# =====================================================================

SYSTEM_INSTRUCTION_CLASSIFIER = f"""
Twoim zadaniem jest analiza ostatniej wiadomości klienta w kontekście całej rozmowy i sklasyfikowanie jego intencji.
Odpowiedz TYLKO I WYŁĄCZNIE jednym z trzech statusów: `{EXPECTING_REPLY}`, `{CONVERSATION_ENDED}`, `{FOLLOW_UP_LATER}`.

- `{EXPECTING_REPLY}`: Użyj, gdy rozmowa jest w toku, a bot oczekuje odpowiedzi na pytanie.
- `{CONVERSATION_ENDED}`: Użyj, gdy klient jednoznacznie kończy rozmowę lub odrzuca ofertę.
- `{FOLLOW_UP_LATER}`: Użyj, gdy klient deklaruje, że odezwie się później (np. "dam znać wieczorem", "muszę porozmawiać z mężem").
"""

SYSTEM_INSTRUCTION_ESTIMATOR = """
Jesteś ekspertem w analizie języka naturalnego w celu estymacji czasu.
- **Aktualna data i godzina to: `__CURRENT_TIME__`.**
- **Kontekst:** Klient właśnie powiedział, że odezwie się później.

Na podstawie poniższej historii rozmowy, oszacuj, kiedy NAJPRAWDOPODOBNIEJ skontaktuje się ponownie.
Twoja odpowiedź MUSI być TYLKO I WYŁĄCZNIE datą i godziną w formacie ISO 8601: `YYYY-MM-DDTHH:MM:SS`.

**REGUŁY:**
- Bądź konserwatywny, dodaj 1-2 godziny buforu do swojego oszacowania.
- Zawsze używaj tego samego roku, co w `__CURRENT_TIME__`.
- Wynik musi być w przyszłości względem `__CURRENT_TIME__`.
- Jeśli klient mówi ogólnie "wieczorem", załóż godzinę 20:30.
- Jeśli klient mówi "po szkole", załóż godzinę 18:00.

Przykład (zakładając `__CURRENT_TIME__` = `2025-09-18T15:00:00`):
- Historia: "...klient: dam znać wieczorem." -> Twoja odpowiedź: `2025-09-18T20:30:00`
"""

SYSTEM_INSTRUCTION_GENERAL = f"""
### O Tobie (Twoja Rola)
Jesteś profesjonalnym i przyjaznym asystentem klienta w centrum korepetycji online. Twoim celem jest przekonanie użytkownika do umówienia pierwszej, testowej lekcji.
- **Styl Komunikacji:** Twoje wiadomości muszą być KRÓTKIE i angażujące. Zawsze kończ je pytaniem. Zawsze zwracaj się do użytkownika per "Państwo". Pamiętaj, że możesz rozmawiać zarówno z rodzicem, jak i bezpośrednio z uczniem. Unikaj używania wykrzykników. NIGDY nie powtarzaj tej samej wiadomości, jeśli podobna znajduje się już w historii.

### Informacje o Usłudze
1.  **Format lekcji:**
    - Korepetycje odbywają się online, 1-na-1 z doświadczonym korepetytorem. Platforma: Microsoft Teams (wystarczy kliknąć w link).
    - Nie oferuj korepetycji stacjonarnych.
2.  **Korepetytorzy:**
    - Korepetycji udzielają osoby z doświadczeniem w nauczaniu online (często studenci, ale unikaj mówienia o tym wprost, chyba że użytkownik zapyta – wtedy potwierdź, że mają kilkuletnie doświadczenie).
    - Użytkownik może wybrać konkretnego korepetytora (np. kobietę lub mężczyznę) podczas rezerwacji na stronie.
3.  **Logistyka:**
    - Terminy lekcji są ustalane poprzez stronę rezerwacji (link wyślemy później).
    - Lekcje można odwoływać i przekładać bezpłatnie w okresie podanym podczas rezerwacji.
    - **Płatność:** Lekcję testową wyjątkowo można opłacić dopiero po połączeniu się z korepetytorem (bez ryzyka).

### Wymagane Dane
Aby system mógł przygotować ofertę, musisz zebrać od klienta:
1.  **Typ szkoły** (podstawowa, liceum, technikum).
2.  **Klasę** ucznia.
3.  **Poziom** (podstawa lub rozszerzenie) – dotyczy TYLKO szkół średnich (liceum/technikum).

**ZASADA ANALIZY HISTORII:**
ZANIM zadasz pytanie, przeanalizuj CAŁĄ historię czatu. Jeśli użytkownik podał już daną informację (nawet dawno temu), NIE PYTAJ PONOWNIE. Potwierdź, że wiesz i dopytaj tylko o braki.

### Prezentacja Oferty (BARDZO WAŻNE!)
Kiedy zdobędziesz WSZYSTKIE wymagane dane (klasa, typ szkoły oraz poziom jeśli dotyczy), Twoja następna odpowiedź MUSI zawierać **TYLKO I WYŁĄCZNIE** ten tag:
`{PRESENT_OFFER_MARKER}`

**ZASADY KRYTYCZNE OFERTY:**
1. **NIGDY nie podawaj ceny samodzielnie.** Cenę wylicza i wysyła system zewnętrzny po wykryciu tagu.
2. **NIGDY nie pisz "Mamy ofertę..." ani "Koszt to...".**
3. Jeśli masz komplet danych, wyślij sam tag `{PRESENT_OFFER_MARKER}`. Nie dodawaj żadnego tekstu.

### Przepływ Rozmowy
1.  **Powitanie:** Jeśli to pierwsza wiadomość, przywitaj się i zapytaj, czy szukają korepetycji (chyba że od razu napisali, że szukają).
2.  **Zbieranie danych:** Zapytaj o klasę i typ szkoły.
3.  **Doprecyzowanie:** Jeśli to szkoła średnia, a nie podano poziomu – dopytaj o poziom (podstawa/rozszerzenie).
4.  **Oferta:** Gdy masz komplet danych -> wyślij tag `{PRESENT_OFFER_MARKER}`.
5.  **Zachęta (po ofercie):** System wyśle cenę. Wtedy Twoim zadaniem jest zachęcenie do lekcji testowej (przypomnij o braku ryzyka płatności).

### Obsługa Sprzeciwów (Perswazja)
Nigdy nie kończ rozmowy po pierwszej odmowie. Próbuj przekonać do 3 razy.
- **Obawa: "Uczeń będzie rozkojarzony":** Odpowiedz: "To częsta obawa, ale proszę się nie martwić. Nasi korepetytorzy prowadzą lekcje w bardzo angażujący sposób."
- **Obawa: "Online się nie sprawdza":** Zapytaj: "Czy uczeń miał już korepetycje online 1-na-1, czy doświadczenie opiera się na lekcjach szkolnych z pandemii? Korepetytor korepetytorowi nierówny, a nasze metody są sprawdzone."
- **Inne (cena, niechęć):** Podkreślaj zalety: elastyczne terminy, personalizowane podejście, wygoda. Zaproponuj lekcję testową bez zobowiązań.

### Inne Zasady
- **Zakres przedmiotów:** Obsługujesz WYŁĄCZNIE matematykę. Jeśli pytają o inny przedmiot (angielski, fizyka, chemia itd.), napisz uprzejmie: "Aktualnie udzielamy korepetycji tylko z matematyki, ale chętnie w niej pomożemy." i wróć do tematu matematyki.
- **AI / Bot:** Jeśli zapytają wprost, czy jesteś botem, przyznaj, że jesteś automatycznym asystentem. W razie problemów podaj email: edu.najechalski@gmail.com.

### Twój GŁÓWNY CEL
- Kiedy rozpoznasz, że użytkownik jednoznacznie zgadza się na umówienie lekcji, Twoja odpowiedź dla niego MUSI być krótka i MUSI kończyć się specjalnym znacznikiem: `{AGREEMENT_MARKER}`.
"""

# =====================================================================
# === FUNKCJE POMOCNICZE ==============================================
# =====================================================================

def calculate_price(school, class_info, level):
    """Oblicza cenę. Funkcja odporna na błędy odmiany i interpunkcji AI."""
    
    # LOGOWANIE DANYCH WEJŚCIOWYCH
    logging.info(f"[CENA_DEBUG] Start obliczeń. Surowe dane -> Szkoła: '{school}', Klasa: '{class_info}', Poziom: '{level}'")

    school_norm = str(school).lower().replace('.', '').strip()
    class_norm = str(class_info).lower().replace('.', '').replace('klasa', '').strip()
    level_norm = str(level).lower().replace('.', '').strip() if level else ""

    logging.info(f"[CENA_DEBUG] Znormalizowane -> Szkoła: '{school_norm}', Klasa: '{class_norm}', Poziom: '{level_norm}'")

    if any(x in school_norm for x in ["podstawowa", "sp"]):
        logging.info("[CENA_DEBUG] Wynik: 65 zł (Wykryto szkołę podstawową)")
        return 65
    elif any(x in school_norm for x in ["liceum", "technikum", "lo", "tech", "średnia", "zawodówka"]):
        if any(x in class_norm for x in ["4", "5", "matura", "maturalna"]):
            logging.info("[CENA_DEBUG] Wynik: 80 zł (Wykryto klasę maturalną)")
            return 80
        if "rozszerz" in level_norm:
            logging.info("[CENA_DEBUG] Wynik: 75 zł (Wykryto poziom rozszerzony)")
            return 75
        else:
            logging.info("[CENA_DEBUG] Wynik: 70 zł (Wykryto poziom podstawowy/domyślny)")
            return 70
    
    logging.warning(f"[CENA_DEBUG] BŁĄD: Nie dopasowano żadnej reguły cenowej dla szkoły: '{school_norm}'")
    return None

def send_email_via_brevo(to_email, subject, html_content):
    """Wysyła email przez Brevo API z rozszerzonym logowaniem."""
    headers = {
        "accept": "application/json",
        "api-key": BREVO_API_KEY,
        "content-type": "application/json"
    }
    
    # Dodajemy timestamp do tematu, żeby Gmail nie łączył wiadomości w wątki
    unique_subject = f"{subject} [{datetime.now().strftime('%H:%M:%S')}]"

    payload = {
        "sender": {
            "name": "Bot Korepetycje",
            "email": FROM_EMAIL
        },
        "to": [{"email": to_email}],
        "subject": unique_subject,
        "htmlContent": html_content
    }
    
    try:
        logging.info(f"EMAIL_DEBUG: Próba wysłania maila do {to_email}...")
        response = requests.post("https://api.brevo.com/v3/smtp/email", json=payload, headers=headers, timeout=15)
        
        # Logujemy pełną odpowiedź serwera
        logging.info(f"EMAIL_DEBUG: Status: {response.status_code}")
        logging.info(f"EMAIL_DEBUG: Odpowiedź serwera: {response.text}")

        if response.status_code == 201:
            logging.info(f"✅ Email zaakceptowany przez Brevo. ID: {response.json().get('messageId')}")
        else:
            logging.error(f"❌ Brevo odrzuciło maila: {response.status_code} - {response.text}")
            
    except Exception as e:
        logging.error(f"❌ Wyjątek krytyczny w send_email_via_brevo: {e}")

def load_config():
    try:
        with open('config.json', 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logging.critical(f"KRYTYCZNY BŁĄD wczytywania config.json: {e}")
        return {}

def get_user_profile(psid, page_access_token):
    """Pobiera imię, nazwisko i zdjęcie profilowe użytkownika z Facebook Graph API."""
    try:
        # Uproszczenie: Usuwamy pobieranie zdjęcia profilowego zgodnie z instrukcją
        url = f"https://graph.facebook.com/v19.0/{psid}?fields=first_name,last_name&access_token={page_access_token}"
        
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        first_name = data.get("first_name")
        last_name = data.get("last_name")
        
        return first_name, last_name, None # Zwracamy None zamiast profile_pic_url
        
    except requests.exceptions.RequestException as e:
        logging.error(f"Błąd pobierania profilu FB dla PSID {psid}: {e}")
        # Logujemy dokładną treść błędu od Facebooka, żeby widzieć co poszło nie tak
        if hasattr(e, 'response') and e.response is not None:
             logging.error(f"Treść błędu FB: {e.response.text}")
        return None, None, None

def create_or_find_client_in_airtable(psid, page_access_token, clients_table_obj):
    if not clients_table_obj:
        return None

    try:
        existing_client = clients_table_obj.first(formula=f"{{ClientID}} = '{psid}'")
        
        # Próba pobrania z FB
        first_name, last_name, _ = get_user_profile(psid, page_access_token)

        if existing_client:
            return psid
        
        # Tworzenie nowego rekordu
        new_client_data = {
            "ClientID": psid,
            # Jeśli FB zawiedzie (puste first_name), wpisz Twoje dane awaryjne
            "ImieKlienta": first_name if first_name else "Wpisz",
            "NazwiskoKlienta": last_name if last_name else "dane"
        }
            
        clients_table_obj.create(new_client_data)
        return psid
    except Exception as e:
        logging.error(f"Błąd bazy danych: {e}")
        return None

def ensure_dir(directory):
    try: os.makedirs(directory)
    except OSError as e:
        if e.errno != errno.EEXIST: raise

def load_history(user_psid):
    filepath = os.path.join(HISTORY_DIR, f"{user_psid}.json")
    if not os.path.exists(filepath): return []
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            history_data = json.load(f)
        history = []
        for msg_data in history_data:
            if msg_data.get('role') in ('user', 'model') and msg_data.get('parts'):
                parts = [Part.from_text(p['text']) for p in msg_data['parts']]
                msg = Content(role=msg_data['role'], parts=parts)
                msg.read = msg_data.get('read', False)
                msg.timestamp = msg_data.get('timestamp')
                history.append(msg)
        return history
    except Exception: return []

def save_history(user_psid, history):
    ensure_dir(HISTORY_DIR)
    filepath = os.path.join(HISTORY_DIR, f"{user_psid}.json")
    history_to_save = history  # Bez limitu długości historii
    history_data = []
    for msg in history_to_save:
        parts_data = [{'text': part.text} for part in msg.parts]
        msg_dict = {'role': msg.role, 'parts': parts_data}
        if hasattr(msg, 'read'):
            msg_dict['read'] = msg.read
        if hasattr(msg, 'timestamp'):
            msg_dict['timestamp'] = msg.timestamp
        history_data.append(msg_dict)
    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(history_data, f, indent=2)
    except Exception as e:
        logging.error(f"BŁĄD zapisu historii dla {user_psid}: {e}")

# =====================================================================
# === FUNKCJE ZARZĄDZANIA PRZYPOMNIENIAMI (NUDGE) =======================
# =====================================================================
def load_nudge_tasks(tasks_file):
    if not os.path.exists(tasks_file): return {}
    try:
        with open(tasks_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception: return {}

def save_nudge_tasks(tasks, tasks_file):
    try:
        logging.info(f"Saving {len(tasks)} tasks to {tasks_file}: {[(k, v.get('status'), v.get('level')) for k, v in tasks.items()]}")
        with open(tasks_file, 'w', encoding='utf-8') as f:
            json.dump(tasks, f, indent=2)
        logging.info(f"Saved successfully")
    except Exception as e:
        logging.error(f"Błąd zapisu zadań przypomnień: {e}")

def cancel_nudge(psid, tasks_file):
    tasks = load_nudge_tasks(tasks_file)
    tasks_to_remove = [task_id for task_id, task in tasks.items() if task.get("psid") == psid]
    for task_id in tasks_to_remove:
        del tasks[task_id]
    if tasks_to_remove:
        save_nudge_tasks(tasks, tasks_file)
        logging.info(f"Anulowano {len(tasks_to_remove)} przypomnień dla PSID {psid}.")

def adjust_time_for_window(nudge_time):
    """Dostosuj czas do okna 6:00-23:00."""
    if 23 <= nudge_time.hour < 24 or 0 <= nudge_time.hour < 1:
        # Jeśli między 23:00 a 1:00, wyślij o 22:30 poprzedniego dnia
        nudge_time = nudge_time.replace(hour=22, minute=30, second=0, microsecond=0) - timedelta(days=1)
    elif 1 <= nudge_time.hour < 6:
        # Jeśli między 1:00 a 6:00, wyślij o 6:00 tego samego dnia
        nudge_time = nudge_time.replace(hour=6, minute=0, second=0, microsecond=0)
    return nudge_time

def schedule_nudge(psid, page_id, status, tasks_file, nudge_time_iso=None, nudge_message=None, level=None):
    # For expect_reply, don't cancel existing, allow multiple levels
    if status.startswith("pending_expect_reply"):
        pass
    else:
        cancel_nudge(psid, tasks_file)
    tasks = load_nudge_tasks(tasks_file)
    logging.info(f"schedule_nudge loaded {len(tasks)} tasks: {[(k, v.get('status'), v.get('level')) for k, v in tasks.items()]}")
    if status == "pending_expect_reply_2":
        for tid, t in list(tasks.items()):
            if t.get("psid") == psid and t.get("status") == "pending_expect_reply_1":
                t["status"] = "done"
                logging.info(f"Set task {tid} to done")
                break
    task_id = str(uuid.uuid4())
    task_data = {"psid": psid, "page_id": page_id, "status": status}
    if nudge_time_iso:
        nudge_time = datetime.fromisoformat(nudge_time_iso)
        nudge_time = adjust_time_for_window(nudge_time)
        task_data["nudge_time_iso"] = nudge_time.isoformat()
    if nudge_message: task_data["nudge_message"] = nudge_message
    if level: task_data["level"] = level
    tasks[task_id] = task_data
    logging.info(f"Added new task {task_id} with status {status}, level {level}, now tasks: {len(tasks)}")
    save_nudge_tasks(tasks, tasks_file)
    logging.info(f"Zaplanowano przypomnienie (status: {status}, level: {level}) dla PSID {psid} o {task_data.get('nudge_time_iso')}.")

def check_and_send_nudges():
    page_config_from_file = load_config().get("PAGE_CONFIG", {})
    if not page_config_from_file:
        logging.error("[Scheduler] Błąd wczytywania konfiguracji.")
        return
    tasks = load_nudge_tasks(NUDGE_TASKS_FILE)
    #logging.info(f"[Scheduler] Załadowano {len(tasks)} zadań przypomnień.")
    #logging.info(f"Tasks: {[ (k, v.get('status'), v.get('level')) for k, v in tasks.items() ]}")
    now = datetime.now(pytz.timezone(TIMEZONE))
    tasks_to_modify = {}
    for task_id, task in list(tasks.items()):
        if not task.get("status", "").startswith("pending"): continue
        try:
            nudge_time = datetime.fromisoformat(task["nudge_time_iso"])
        except (ValueError, KeyError):
            logging.error(f"[Scheduler] Błąd formatu daty w zadaniu {task_id}. Usuwam zadanie.")
            task['status'] = 'failed_date_format'
            tasks_to_modify[task_id] = task
            continue
        if now >= nudge_time:
            is_in_window = NUDGE_WINDOW_START <= now.hour < NUDGE_WINDOW_END
            if is_in_window:
                logging.info(f"[Scheduler] Czas na przypomnienie (status: {task['status']}) dla PSID {task['psid']}")
                page_config = page_config_from_file.get(task["page_id"])
                if page_config and page_config.get("token"):
                    psid, token = task['psid'], page_config["token"]
                    message_to_send = task.get("nudge_message")
                    level = task.get("level", 1)
                    if message_to_send:
                        send_message_with_typing(psid, message_to_send, token)
                        logging.info(f"[Scheduler] Wysłano przypomnienie poziom {level} dla PSID {psid}")
                        # Dodaj wiadomość przypominającą do historii konwersacji
                        history = load_history(psid)
                        reminder_msg = Content(role="model", parts=[Part.from_text(message_to_send)])
                        history.append(reminder_msg)
                        save_history(psid, history)
                        logging.info(f"Dodano wiadomość przypominającą do historii dla PSID {psid}")
                    if level == 1 and task["status"] == "pending_expect_reply_1":
                        # Schedule level 2
                        now = datetime.now(pytz.timezone(TIMEZONE))
                        nudge_time = now + timedelta(hours=6)
                        nudge_time = adjust_time_for_window(nudge_time)
                        schedule_nudge(psid, task["page_id"], "pending_expect_reply_2", NUDGE_TASKS_FILE,
                                       nudge_time_iso=nudge_time.isoformat(),
                                       nudge_message="Czy są Państwo nadal zainteresowani korepetycjami?",
                                       level=2)
                        # Reload tasks to include the newly scheduled level 2
                        tasks = load_nudge_tasks(NUDGE_TASKS_FILE)
                    task['status'] = 'done'
                    tasks_to_modify[task_id] = task
                    # Save immediately after sending to prevent duplicates
                    tasks.update(tasks_to_modify)
                    save_nudge_tasks(tasks, NUDGE_TASKS_FILE)
                    tasks_to_modify = {}
                else:
                    task["status"] = "failed_no_token"
                    tasks_to_modify[task_id] = task
            else:
                logging.info(f"[Scheduler] Zła pora. Przeplanowuję {task['psid']}...")
                next_day_start = now.replace(hour=NUDGE_WINDOW_START, minute=5, second=0, microsecond=0)
                if now.hour >= NUDGE_WINDOW_END: next_day_start += timedelta(days=1)
                task["nudge_time_iso"] = next_day_start.isoformat()
                tasks_to_modify[task_id] = task
    if tasks_to_modify:
        tasks.update(tasks_to_modify)
        save_nudge_tasks(tasks, NUDGE_TASKS_FILE)
        logging.info("[Scheduler] Zaktualizowano zadania przypomnień.")

# =====================================================================
# === NOWE FUNKCJE DLA WYSPECJALIZOWANYCH AI ==========================
# =====================================================================

def run_data_extractor_ai(history):
    """AI nr 2: Wyciąga ustrukturyzowane dane z całej rozmowy."""
    logging.info("[AI_EXTRACTOR] Uruchamiam analizę historii rozmowy...")
    
    instruction = """
    Przeanalizuj całą rozmowę. Twoim zadaniem jest wyciągnąć 3 kluczowe informacje: szkołę, klasę i poziom.
    Odpowiedź MUSI być w formacie JSON.
    - `szkola`: Jedno ze słów: "Podstawowa", "Liceum", "Technikum". Jeśli ktoś napisał "zawodówka", "technik" lub "LO", potraktuj to odpowiednio.
    - `klasa`: Tylko cyfra, np. 1, 2, 3, 4, 8.
    - `poziom`: Jedno ze słów: "podstawa", "rozszerzenie" lub null, jeśli nie dotyczy lub jest to szkoła podstawowa.

    Jeśli brakuje którejś informacji, w `status` wpisz "missing_data" i w `missing` podaj listę brakujących pól.

    Przykład 1 (sukces):
    { "status": "success", "szkola": "Liceum", "klasa": "4", "poziom": "podstawa" }
    Przykład 2 (brak danych):
    { "status": "missing_data", "missing": ["klasa", "poziom"] }
    """
    
    chat_history_text = "\n".join([f"{msg.role}: {msg.parts[0].text}" for msg in history])
    full_prompt = f"{instruction}\n\nHistoria czatu:\n{chat_history_text}"
    
    try:
        response = gemini_model.generate_content(full_prompt)
        clean_text = response.text.strip().replace("```json", "").replace("```", "").strip()
        
        # LOGOWANIE SUROWEJ ODPOWIEDZI AI
        logging.info(f"[AI_EXTRACTOR] Surowa odpowiedź JSON od Gemini: {clean_text}")
        
        data = json.loads(clean_text)
        
        if data.get("status") == "success":
            logging.info(f"[AI_EXTRACTOR] SUKCES: Wyciągnięto dane: {data}")
        else:
            logging.info(f"[AI_EXTRACTOR] BRAK DANYCH: Brakuje pól: {data.get('missing')}")
            
        return data
    except (json.JSONDecodeError, AttributeError, Exception) as e:
        logging.error(f"[AI_EXTRACTOR] BŁĄD PARSOWANIA: {e}. Odpowiedź modelu: {response.text if 'response' in locals() else 'Brak'}")
        return { "status": "missing_data", "missing": ["szkola", "klasa", "poziom"] }

def run_question_creator_ai(history, missing_fields):
    """AI nr 3: Tworzy naturalne pytanie o brakujące dane."""
    instruction = f"""
    Jesteś asystentem AI. Twoim zadaniem jest stworzyć jedno, krótkie i naturalne pytanie, aby uzupełnić brakujące dane.
    Brakuje nam informacji o: {', '.join(missing_fields)}.
    Na podstawie historii rozmowy, sformułuj pytanie, które będzie logicznie pasować do konwersacji.
    """
    
    full_prompt = [Content(role="user", parts=[Part.from_text(instruction)])] + history
    
    try:
        response = gemini_model.generate_content(full_prompt)
        return response.text.strip()
    except Exception as e:
        logging.error(f"Błąd kreatora pytań AI: {e}")
        return "Proszę podać więcej szczegółów."

# =====================================================================
# === FUNKCJE KOMUNIKACJI Z AI ========================================
# =====================================================================
def send_message(recipient_id, message_text, page_access_token):
    if not all([recipient_id, message_text, page_access_token]): return
    params = {"access_token": page_access_token}
    payload = {"recipient": {"id": recipient_id}, "message": {"text": message_text}, "messaging_type": "RESPONSE"}
    try:
        r = requests.post(FACEBOOK_GRAPH_API_URL, params=params, json=payload, timeout=30)
        r.raise_for_status()
        logging.info(f"Wysłano wiadomość do {recipient_id}: '{message_text[:50]}...'")
    except requests.exceptions.RequestException as e:
        logging.error(f"Błąd wysyłania do {recipient_id}: {e}")

def send_message_with_typing(recipient_id, message_text, page_access_token):
    if not all([recipient_id, message_text, page_access_token]): return
    params = {"access_token": page_access_token}
    
    # 1. Wyślij "dymek pisania" (typing_on) - czysto wizualnie
    typing_payload = {"recipient": {"id": recipient_id}, "sender_action": "typing_on"}
    try:
        requests.post(FACEBOOK_GRAPH_API_URL, params=params, json=typing_payload, timeout=30)
    except requests.exceptions.RequestException:
        pass
    
    # 2. Wyślij wiadomość NATYCHMIAST (bez delay i sleep)
    payload = {"recipient": {"id": recipient_id}, "message": {"text": message_text}, "messaging_type": "RESPONSE"}
    try:
        r = requests.post(FACEBOOK_GRAPH_API_URL, params=params, json=payload, timeout=30)
        r.raise_for_status()
        logging.info(f"Wysłano wiadomość do {recipient_id}: '{message_text[:50]}...'")
    except requests.exceptions.RequestException as e:
        logging.error(f"Błąd wysyłania do {recipient_id}: {e}")

def classify_conversation(history):
    if not gemini_model: return EXPECTING_REPLY
    chat_history_text = "\n".join([f"Klient: {msg.parts[0].text}" if msg.role == 'user' else f"Bot: {msg.parts[0].text}" for msg in history[-4:]])
    prompt_for_analysis = f"OTO FRAGMENT HISTORII CZATU:\n---\n{chat_history_text}\n---"
    full_prompt = [
        Content(role="user", parts=[Part.from_text(SYSTEM_INSTRUCTION_CLASSIFIER)]),
        Content(role="model", parts=[Part.from_text("Rozumiem. Zwrócę jeden z trzech statusów.")]),
        Content(role="user", parts=[Part.from_text(prompt_for_analysis)])
    ]
    try:
        analysis_config = GenerationConfig(temperature=0.0)
        response = gemini_model.generate_content(full_prompt, generation_config=analysis_config)
        status = "".join(part.text for part in response.candidates[0].content.parts).strip()
        if status in [EXPECTING_REPLY, CONVERSATION_ENDED, FOLLOW_UP_LATER]: return status
        return EXPECTING_REPLY
    except Exception as e:
        logging.error(f"BŁĄD klasyfikatora AI: {e}", exc_info=True)
        return EXPECTING_REPLY

def estimate_follow_up_time(history):
    if not gemini_model: return None
    now_str = datetime.now(pytz.timezone(TIMEZONE)).isoformat()
    formatted_instruction = SYSTEM_INSTRUCTION_ESTIMATOR.replace("__CURRENT_TIME__", now_str)
    chat_history_text = "\n".join([f"Klient: {msg.parts[0].text}" if msg.role == 'user' else f"Bot: {msg.parts[0].text}" for msg in history])
    prompt_for_analysis = f"OTO PEŁNA HISTORIA CZATU:\n---\n{chat_history_text}\n---"
    full_prompt = [
        Content(role="user", parts=[Part.from_text(formatted_instruction)]),
        Content(role="model", parts=[Part.from_text("Rozumiem. Zwrócę datę w formacie ISO 8601.")]),
        Content(role="user", parts=[Part.from_text(prompt_for_analysis)])
    ]
    try:
        analysis_config = GenerationConfig(temperature=0.2)
        response = gemini_model.generate_content(full_prompt, generation_config=analysis_config)
        if not response.candidates: return None
        time_str = "".join(part.text for part in response.candidates[0].content.parts).strip()
        if "T" in time_str and ":" in time_str: return time_str
        return None
    except Exception as e:
        logging.error(f"BŁĄD estymatora czasu AI: {e}", exc_info=True)
        return None

def get_gemini_response(history, prompt_details, is_follow_up=False):
    if not gemini_model: return "Przepraszam, mam chwilowy problem z moim systemem."
    if is_follow_up:
        system_instruction = ("Jesteś uprzejmym asystentem. Twoim zadaniem jest napisanie krótkiej, spersonalizowanej wiadomości przypominającej. "
                              "Na podstawie historii rozmowy, nawiąż do ostatniego tematu i delikatnie zapytaj, czy użytkownik podjął już decyzję.")
        history_context = history[-4:] 
        full_prompt = [Content(role="user", parts=[Part.from_text(system_instruction)]),
                       Content(role="model", parts=[Part.from_text("Rozumiem. Stworzę wiadomość przypominającą.")])] + history_context
    else:
        system_instruction = SYSTEM_INSTRUCTION_GENERAL.format(
            prompt_details=prompt_details, agreement_marker=AGREEMENT_MARKER)
        full_prompt = [Content(role="user", parts=[Part.from_text(system_instruction)]),
                       Content(role="model", parts=[Part.from_text("Rozumiem. Jestem gotów do rozmowy z klientem.")])] + history
    try:
        response = gemini_model.generate_content(full_prompt, generation_config=GENERATION_CONFIG, safety_settings=SAFETY_SETTINGS)
        if not response.candidates: return "Twoja wiadomość nie mogła zostać przetworzona."
        generated_text = "".join(part.text for part in response.candidates[0].content.parts).strip()
        if is_follow_up and not generated_text:
            logging.warning("AI (przypomnienie) zwróciło pusty tekst. Używam domyślnej wiadomości.")
            return "Dzień dobry, chciałem tylko zapytać, czy udało się Państwu podjąć decyzję w sprawie lekcji?"
        return generated_text
    except Exception as e:
        logging.error(f"BŁĄD wywołania Gemini: {e}", exc_info=True)
        return "Przepraszam, wystąpił nieoczekiwany błąd."

# =====================================================================
# === LOGIKA OPÓŹNIONEGO URUCHOMIENIA (AI) ============================
# =====================================================================
def handle_conversation_logic(sender_id, recipient_id, combined_text):
    """Ta funkcja uruchamia się DOPIERO po X sekundach ciszy."""
    try:
        logging.info(f"AI START: Przetwarzam zbiorczą wiadomość od {sender_id}: '{combined_text}'")

        # --- TUTAJ ZACZYNA SIĘ STARA LOGIKA AI ---
        
        page_config = PAGE_CONFIG.get(recipient_id)
        if not page_config: return
        page_token = page_config.get("token")
        prompt_details = page_config.get("prompt_details")
        
        history = load_history(sender_id)
        
        # Dodajemy ZBIORCZĄ wiadomość do historii
        new_msg = Content(role="user", parts=[Part.from_text(combined_text)])
        new_msg.read = False
        history.append(new_msg)

        # Sprawdź tryby specjalne
        manual_mode_active = any(msg for msg in history if msg.role == 'model' and msg.parts[0].text == 'MANUAL_MODE')
        post_reservation_mode_active = any(msg for msg in history if msg.role == 'model' and msg.parts[0].text == 'POST_RESERVATION_MODE')

        if manual_mode_active:
            logging.info(f"Użytkownik {sender_id} jest w trybie ręcznym.")
            save_history(sender_id, history)
            return

        if post_reservation_mode_active:
            user_msg_lower = combined_text.lower()
            if "pomoc" in user_msg_lower:
                admin_email = ADMIN_EMAIL_NOTIFICATIONS
                last_msgs = "\n".join([f"Klient: {msg.parts[0].text}" if msg.role == 'user' else f"Bot: {msg.parts[0].text}" for msg in history[-5:]])
                html_content = f"<p>Użytkownik {sender_id} prosi o pomoc.</p><pre>{last_msgs}</pre>"
                send_email_via_brevo(admin_email, "Prośba o pomoc", html_content)
                history.append(Content(role="model", parts=[Part.from_text("MANUAL_MODE")]))
                save_history(sender_id, history)
                return
            send_message_with_typing(sender_id, 'Dziękujemy za kontakt. Wpisz "POMOC" jeśli masz pytania.', page_token)
            return

        # --- GŁÓWNE WYWOŁANIE AI ---
        ai_response_raw = get_gemini_response(history, prompt_details)

        if PRESENT_OFFER_MARKER in ai_response_raw:
            logging.info("Tag [PREZENTUJ_OFERTE] wykryty.")
            extracted_data = run_data_extractor_ai(history)
            if extracted_data.get("status") == "success":
                price = calculate_price(extracted_data["szkola"], extracted_data["klasa"], extracted_data.get("poziom"))
                if price:
                    final_offer = f"Oferujemy korepetycje matematyczne za {price} zł za lekcję 60 minut. Czy umówić lekcję?"
                    send_message_with_typing(sender_id, final_offer, page_token)
                    history.append(Content(role="model", parts=[Part.from_text(final_offer)]))
                else:
                    error_msg = "Nie udało się obliczyć ceny. Proszę podać klasę i typ szkoły."
                    send_message_with_typing(sender_id, error_msg, page_token)
                    history.append(Content(role="model", parts=[Part.from_text(error_msg)]))
            else:
                missing_info_message = run_question_creator_ai(history, extracted_data["missing"])
                send_message_with_typing(sender_id, missing_info_message, page_token)
                history.append(Content(role="model", parts=[Part.from_text(missing_info_message)]))

        elif AGREEMENT_MARKER in ai_response_raw:
             client_id = create_or_find_client_in_airtable(sender_id, page_token, clients_table)
             if client_id:
                # Powiadomienie admina
                send_email_via_brevo(ADMIN_EMAIL_NOTIFICATIONS, f"Zgoda na lekcję {sender_id}", "Nowy klient!")
                
                reservation_link = f"https://zakręcone-korepetycje.pl/rezerwacja-testowa.html?clientID={client_id}"
                msg = f"Oto Twój link do rezerwacji:\n\n{reservation_link}\n\nZapraszamy!"
                send_message_with_typing(sender_id, msg, page_token)
                history.append(Content(role="model", parts=[Part.from_text(msg)]))
             else:
                send_message_with_typing(sender_id, "Błąd systemu rezerwacji.", page_token)

        else:
            # Zwykła odpowiedź
            send_message_with_typing(sender_id, ai_response_raw, page_token)
            history.append(Content(role="model", parts=[Part.from_text(ai_response_raw)]))
        
        save_history(sender_id, history)

    except Exception as e:
        logging.error(f"KRYTYCZNY BŁĄD w logice AI: {e}", exc_info=True)


# =====================================================================
# =====================================================================
# === BUFOROWANIE I ODBIERANIE WIADOMOŚCI =============================
# =====================================================================
def process_event(event_payload):
    """Ta funkcja tylko zbiera wiadomości i zarządza timerem."""
    try:
        sender_id = event_payload.get("sender", {}).get("id")
        recipient_id = event_payload.get("recipient", {}).get("id")
        
        # 1. Obsługa Read Receipts
        if event_payload.get("read"):
            return

        user_message_text = event_payload.get("message", {}).get("text", "").strip()
        if not user_message_text or event_payload.get("message", {}).get("is_echo"):
            return

        # Anulujemy przypomnienie NATYCHMIAST, nie czekając 10 sekund
        cancel_nudge(sender_id, NUDGE_TASKS_FILE)

        logging.info(f"Odebrano wiadomość od {sender_id}: '{user_message_text}'")

        # 2. Dodaj wiadomość do bufora użytkownika
        if sender_id not in user_message_buffers:
            user_message_buffers[sender_id] = []
        user_message_buffers[sender_id].append(user_message_text)

        # 3. Anuluj poprzedni timer (jeśli użytkownik znowu napisał, przerywamy odliczanie)
        if sender_id in user_timers:
            user_timers[sender_id].cancel()

        # 4. Ustaw nowy timer (teraz na 10 sekund)
        timer = threading.Timer(DEBOUNCE_SECONDS, lambda: run_delayed_logic(sender_id, recipient_id))
        user_timers[sender_id] = timer
        timer.start()
        logging.info(f"Restart timera dla {sender_id}. Czekam {DEBOUNCE_SECONDS}s na ciszę...")

    except Exception as e:
        logging.error(f"Błąd w process_event: {e}", exc_info=True)

def run_delayed_logic(sender_id, recipient_id):
    """Funkcja pomocnicza wywoływana przez Timer."""
    # POPRAWKA: Używamy pop(), aby pobrać i wyczyścić bufor w jednym kroku
    # To zapobiega sytuacji, gdzie stara funkcja przetwarzała tekst, a nowa go nie widziała
    messages = user_message_buffers.pop(sender_id, [])
    
    if not messages:
        return
    
    combined_text = " ".join(messages)
    
    # Usuwamy timer ze słownika, bo już się wykonał
    if sender_id in user_timers:
        del user_timers[sender_id]
        
    # Uruchom właściwą logikę AI
    handle_conversation_logic(sender_id, recipient_id, combined_text)
        
# =====================================================================
# === WEBHOOK FLASK I URUCHOMIENIE ====================================
# =====================================================================
@app.route('/webhook', methods=['GET'])
def webhook_verification():
    if request.args.get('hub.mode') == 'subscribe' and request.args.get('hub.verify_token') == VERIFY_TOKEN:
        return Response(request.args.get('hub.challenge'), status=200)
    else:
        return Response("Verification failed", status=403)

@app.route('/webhook', methods=['POST'])
def webhook_handle():
    data = json.loads(request.data)
    if data.get("object") == "page":
        for entry in data.get("entry", []):
            for event in entry.get("messaging", []):
                thread = threading.Thread(target=process_event, args=(event,))
                thread.start()
        return Response("EVENT_RECEIVED", status=200)
    else:
        return Response("NOT_PAGE_EVENT", status=404)

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - [%(threadName)s] - %(message)s')
    logging.getLogger('apscheduler.executors.default').setLevel(logging.WARNING)
    logging.getLogger('werkzeug').setLevel(logging.WARNING)
    logging.getLogger('apscheduler.scheduler').setLevel(logging.WARNING)
    ensure_dir(HISTORY_DIR)
    
    scheduler = BackgroundScheduler(timezone=TIMEZONE)
    scheduler.add_job(func=check_and_send_nudges, trigger="interval", seconds=30)
    scheduler.start()
    atexit.register(lambda: scheduler.shutdown())
    
    port = int(os.environ.get("PORT", 5000))
    logging.info(f"Uruchamianie serwera na porcie {port}...")
    try:
        from waitress import serve
        serve(app, host='0.0.0.0', port=port)
    except ImportError:
        app.run(host='0.0.0.0', port=port, debug=True)
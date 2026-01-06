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
# Zamieniono Airtable na SQLite
from database import DatabaseTable
import logging
from datetime import datetime, timedelta
import pytz
from apscheduler.schedulers.background import BackgroundScheduler
import atexit
import uuid

# --- Konfiguracja Ogólna ---
app = Flask(__name__)
VERIFY_TOKEN = os.environ.get("FB_VERIFY_TOKEN", "KOLAGEN")
FACEBOOK_GRAPH_API_URL = "https://graph.facebook.com/v19.0/me/messages"
HISTORY_DIR = "/home/korepetotor2/strona/conversation_store"
MAX_HISTORY_TURNS = 10

# Klucz API dla Brevo (tymczasowo)
BREVO_API_KEY = "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"

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

SYSTEM_INSTRUCTION_GENERAL = """
### O Tobie (Twoja Rola)
Jesteś profesjonalnym i przyjaznym asystentem klienta w centrum korepetycji online. Twoim celem jest przekonanie użytkownika do umówienia pierwszej, testowej lekcji.
- **Styl Komunikacji:** Twoje wiadomości muszą być KRÓTKIE i angażujące. Zawsze kończ je pytaniem. Zawsze zwracaj się do użytkownika per "Państwo". Pamiętaj, że możesz rozmawiać zarówno z rodzicem, jak i bezpośrednio z uczniem. Unikaj używania wykrzykników. NIGDY nie powtarzaj tej samej wiadomości, jeśli jakaś wiadomość znajduje się w historii nie możesz wysłać jej ponownie.



### Informacje o Usłudze
1.  **Cennik (za lekcję 60 minut):**
    - Szkoła Podstawowa: 65 zł
    - Szkoła średnia (klasy niematuralne, podstawa): 70 zł
    - Szkoła średnia (klasy niematuralne, rozszerzenie): 75 zł
    - Szkoła średnia (klasa maturalna, podstawa i rozszerzenie): 80 zł
2.  **Format lekcji:**
    - Korepetycje odbywają się online, 1-na-1 z doświadczonym korepetytorem.
    - Platforma: Microsoft Teams. Wystarczy kliknąć w otrzymany link.
3. **Korepetytorzy:*
    - Korepetycji udzielają głownie studenci,ale staraj się unikać o tym nie mówić, jeśli użytkownik nie zapyta.
    - Wiekszość naszych korepetytorów ma kilkuletnie doświadczenie z udzielania korepetycji uczniom
    - Korepetycji udzielają osoby z doświadczeniem w prowadzeniu korepetycji online

### Kluczowe Zadania i Przepływ Rozmowy
Postępuj zgodnie z poniższą chronologią, **dzieląc rozmowę na krótkie wiadomości i NIE zadając pytań, jeśli znasz już odpowiedź**:
1.  **Powitanie:** JEŚLI pierwsza wiadomość użytkownika to ogólne powitanie, odpowiedz powitaniem i zapytaj, w czym możesz pomóc. JEŚLI użytkownik od razu pisze, że szuka korepetycji, przejdź bezpośrednio do kroku 2 pomijając krok 1..
2.  **Zbieranie informacji (Szkoła i klasa):** Zapytaj o klasę i typ szkoły ucznia.
3.  **Inteligentna analiza:** JEŚLI użytkownik w swojej odpowiedzi poda zarówno klasę, jak i typ szkoły, przejdź od razu do kroku 5.
4.  **Zbieranie informacji (Poziom):** JEŚLI podany przez klienta typ szkoły to NIE podstawówka, czyli jest to liceum lub technikum ORAZ użytkownik nie podał poziomu (podstawa czy rozszerzenie), w osobnej wiadomości zapytaj o poziom(podstawa czy rozszerzenie).
5.  **Prezentacja oferty:** Na podstawie zebranych danych, przedstaw cenę i format lekcji.
6.  **Zachęta do działania:** Po przedstawieniu oferty, zawsze aktywnie proponuj umówienie pierwszej, testowej lekcji.

### Jak Obsługiwać Sprzeciwy
- JEŚLI klient ma wątpliwości, zapytaj o ich powód.
- JEŚLI klient twierdzi, że uczeń będzie **rozkojarzony**, ODPOWIEDZ: "To częsta obawa, ale proszę się nie martwić. Nasi korepetytorzy prowadzą lekcje w bardzo angażujący sposób."
- JEŚLI klient twierdzi, że korepetycje online się nie sprawdziły, ZAPYTAJ: "Czy uczeń miał już do czynienia z korepetycjami online 1-na-1, czy doświadczenie opiera się głównie na lekcjach szkolnych z czasów pandemii?"

### Obsługa pytań o tożsamość AI
- Jeśli klient zapyta, czy rozmawia z botem lub AI, odpowiedz uprzejmie, że jesteś automatycznym asystentem obsługującym rozmowy przez Messenger.
- Jeśli klient chce skontaktować się z człowiekiem, poinformuj, że obsługa klienta przez Messenger jest realizowana przez automatycznego asystenta, a w przypadku potrzeby można skontaktować się pod adresem email: edu.najechalski@gmail.com.
- Ogólnie unikaj samodzielnego ujawniania, że jesteś botem – skupiaj się na pomocy w umówieniu lekcji.

### Zakres przedmiotów
- Obsługujesz WYŁĄCZNIE matematykę. Jeśli rozmówca pyta o inny przedmiot (np. angielski, fizyka, chemia, polski, biologia, historia, informatyka itp.), odpowiedz uprzejmie:
  "Aktualnie udzielamy korepetycji tylko z matematyki, ale chętnie pomożemy w matematyce."
- Następnie płynnie przejdź do standardowego przepływu dla matematyki (zebranie klasy/poziomu i przedstawienie oferty).
- Nigdy nie przedstawiaj oferty ani cen dla innych przedmiotów i nie podejmuj rozmowy merytorycznej z innego przedmiotu poza krótką informacją powyżej.

### Twój GŁÓWNY CEL i Format Odpowiedzi
Twoim nadrzędnym celem jest uzyskanie od użytkownika zgody na pierwszą lekcję.
- Kiedy rozpoznasz, że użytkownik jednoznacznie zgadza się na umówienie lekcji, Twoja odpowiedź dla niego MUSI być krótka i MUSI kończyć się specjalnym znacznikiem: `{agreement_marker}`.
"""

# =====================================================================
# === FUNKCJE POMOCNICZE ==============================================
# =====================================================================

def send_email_via_brevo(to_email, subject, html_content):
    """Wysyła email przez Brevo API."""
    headers = {
        "accept": "application/json",
        "api-key": BREVO_API_KEY,
        "content-type": "application/json"
    }
    payload = {
        "sender": {
            "name": "Bot Korepetycje",
            "email": config.get("FROM_EMAIL", "bot@korepetycje.pl")
        },
        "to": [{"email": to_email}],
        "subject": subject,
        "htmlContent": html_content
    }
    try:
        response = requests.post("https://api.brevo.com/v3/smtp/email", json=payload, headers=headers)
        if response.status_code == 201:
            logging.info(f"Email wysłany pomyślnie do {to_email}: {subject}")
        else:
            logging.error(f"Błąd wysyłania emaila do {to_email}: {response.status_code} - {response.text}")
    except Exception as e:
        logging.error(f"Wyjątek podczas wysyłania emaila: {e}")

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
        # Dodajemy 'profile_pic' do listy pól, o które prosimy
        url = f"https://graph.facebook.com/v19.0/{psid}?fields=first_name,last_name,profile_pic&access_token={page_access_token}"
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        # Zwracamy teraz trzy wartości
        return data.get("first_name"), data.get("last_name"), data.get("profile_pic")
        
    except requests.exceptions.RequestException as e:
        logging.error(f"Błąd pobierania profilu FB dla PSID {psid}: {e}")
        return None, None, None

def create_or_find_client_in_airtable(psid, page_access_token, clients_table_obj):
    """Sprawdza, czy klient istnieje w Airtable. Jeśli nie, tworzy go, zapisując dane do nowych kolumn."""
    if not clients_table_obj:
        logging.error("Airtable nie jest skonfigurowane, nie można utworzyć klienta.")
        return None

    try:
        existing_client = clients_table_obj.first(formula=f"{{ClientID}} = '{psid}'")
        if existing_client:
            logging.info(f"Klient o PSID {psid} już istnieje w Airtable.")
            return psid
        
        logging.info(f"Klient o PSID {psid} nie istnieje. Tworzenie nowego rekordu...")
        first_name, last_name, profile_pic_url = get_user_profile(psid, page_access_token)
        
        # === ZMIANA NAZW PÓL JEST TUTAJ ===
        new_client_data = {
            "ClientID": psid,
        }
        if first_name:
            new_client_data["ImięKlienta"] = first_name # Zmieniono z "Imię"
        if last_name:
            new_client_data["NazwiskoKlienta"] = last_name # Zmieniono z "Nazwisko"
        if profile_pic_url:
            new_client_data["Zdjęcie"] = profile_pic_url
        # === KONIEC ZMIANY ===
            
        clients_table_obj.create(new_client_data)
        logging.info(f"Pomyślnie utworzono nowego klienta w Airtable dla PSID {psid}.")
        return psid
        
    except Exception as e:
        logging.error(f"Wystąpił błąd podczas operacji na Airtable dla PSID {psid}: {e}", exc_info=True)
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
                history.append(Content(role=msg_data['role'], parts=parts))
        return history
    except Exception: return []

def save_history(user_psid, history):
    ensure_dir(HISTORY_DIR)
    filepath = os.path.join(HISTORY_DIR, f"{user_psid}.json")
    history_to_save = history[-(MAX_HISTORY_TURNS * 2):]
    history_data = []
    for msg in history_to_save:
        parts_data = [{'text': part.text} for part in msg.parts]
        history_data.append({'role': msg.role, 'parts': parts_data})
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
        with open(tasks_file, 'w', encoding='utf-8') as f:
            json.dump(tasks, f, indent=2)
    except Exception as e:
        logging.error(f"Błąd zapisu zadań przypomnień: {e}")

def cancel_nudge(psid, tasks_file):
    tasks = load_nudge_tasks(tasks_file)
    task_id_to_remove = next((task_id for task_id, task in tasks.items() if task.get("psid") == psid), None)
    if task_id_to_remove:
        del tasks[task_id_to_remove]
        save_nudge_tasks(tasks, tasks_file)
        logging.info(f"Anulowano przypomnienie dla PSID {psid}.")

def schedule_nudge(psid, page_id, status, tasks_file, nudge_time_iso=None, nudge_message=None):
    cancel_nudge(psid, tasks_file)
    tasks = load_nudge_tasks(tasks_file)
    task_id = str(uuid.uuid4())
    task_data = {"psid": psid, "page_id": page_id, "status": status}
    if nudge_time_iso: task_data["nudge_time_iso"] = nudge_time_iso
    if nudge_message: task_data["nudge_message"] = nudge_message
    tasks[task_id] = task_data
    save_nudge_tasks(tasks, tasks_file)
    logging.info(f"Zaplanowano przypomnienie (status: {status}) dla PSID {psid}.")

def check_and_send_nudges():
    # logging.info(f"[{datetime.now(pytz.timezone(TIMEZONE)).strftime('%H:%M:%S')}] [Scheduler] Uruchamiam sprawdzanie przypomnień...")
    page_config_from_file = load_config().get("PAGE_CONFIG", {})
    if not page_config_from_file:
        logging.error("[Scheduler] Błąd wczytywania konfiguracji.")
        return
    tasks = load_nudge_tasks(NUDGE_TASKS_FILE)
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
                    if message_to_send:
                        send_message(psid, message_to_send, token)
                    task['status'] = 'done'
                    tasks_to_modify[task_id] = task
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
# === GŁÓWNA LOGIKA PRZETWARZANIA ======================================
# =====================================================================
def process_event(event_payload):
    try:
        logging.info("Wątek 'process_event' wystartował.")
        if not PAGE_CONFIG: return
        sender_id = event_payload.get("sender", {}).get("id")
        recipient_id = event_payload.get("recipient", {}).get("id")
        if not sender_id or not recipient_id or event_payload.get("message", {}).get("is_echo"): return
        if event_payload.get("read"):
             logging.info(f"Użytkownik {sender_id} odczytał wiadomość. (Brak akcji anulującej)")
             return
        user_message_text = event_payload.get("message", {}).get("text", "").strip()
        if not user_message_text: return
        cancel_nudge(sender_id, NUDGE_TASKS_FILE)
        page_config = PAGE_CONFIG.get(recipient_id)
        if not page_config: return
        page_token = page_config.get("token")
        prompt_details = page_config.get("prompt_details")
        page_name = page_config.get("name", "Nieznana Strona")
        history = load_history(sender_id)
        history.append(Content(role="user", parts=[Part.from_text(user_message_text)]))

        # Sprawdź tryby specjalne
        manual_mode_active = any(msg for msg in history if msg.role == 'model' and msg.parts[0].text == 'MANUAL_MODE')
        post_reservation_mode_active = any(msg for msg in history if msg.role == 'model' and msg.parts[0].text == 'POST_RESERVATION_MODE')

        if manual_mode_active:
            logging.info(f"Użytkownik {sender_id} jest w trybie ręcznym - brak odpowiedzi automatycznej.")
            save_history(sender_id, history)  # Zapisz historię z nową wiadomością użytkownika
            return

        if post_reservation_mode_active:
            elif mode == "POST_RESERVATION_MODE":
                user_msg_lower = user_message_text.lower()
                if "pomoc" in user_msg_lower:
                    # Powiadomienie
                    admin_email = config.get("ADMIN_EMAIL", "edu.najechalski@gmail.com")
                    last_msgs = "\n".join([f"Klient: {msg.parts[0].text}" if msg.role == 'user' else f"Bot: {msg.parts[0].text}" for msg in history[-5:]])
                    html_content = f"<p>Użytkownik {sender_id} poprosił o pomoc po rezerwacji.</p><p>PSID: {sender_id}</p><p>Ostatnie wiadomości:</p><pre>{last_msgs}</pre>"
                    send_email_via_brevo(admin_email, "Prośba o pomoc od użytkownika", html_content)
                    # Przejdź w MANUAL_MODE
                    history.append(Content(role="model", parts=[Part.from_text("MANUAL_MODE")]))
                    save_history(sender_id, history)
                    logging.info(f"Użytkownik {sender_id} przeszedł w tryb ręczny.")
                    return
                else:
                    # Standardowa wiadomość
                    send_message(sender_id, 'Dziękujemy za kontakt. Moja rola asystenta zakończyła się wraz z wysłaniem linku do rezerwacji. W przypadku jakichkolwiek pytań lub problemów, proszę odpowiedzieć na tę wiadomość: "POMOC". Udzielimy odpowiedzi najszybciej, jak to możliwe.', page_token)
                    return

        ai_response_raw = get_gemini_response(history, prompt_details)

        logging.info("Uruchamiam analityka AI (Etap 1: Klasyfikacja)...")
        conversation_status = classify_conversation(history)
        logging.info(f"AI (Klasyfikacja) zwróciło status: {conversation_status}")
        follow_up_time_iso = None
        if conversation_status == FOLLOW_UP_LATER:
            logging.info("Uruchamiam analityka AI (Etap 2: Estymacja czasu)...")
            follow_up_time_iso = estimate_follow_up_time(history)
            logging.info(f"AI (Estymacja) zwróciło czas: {follow_up_time_iso}")

        final_message_to_user = ""
        if AGREEMENT_MARKER in ai_response_raw:
            client_id = create_or_find_client_in_airtable(sender_id, page_token, clients_table)
            if client_id:
                reservation_link = f"https://zakręcone-korepetycje.pl/rezerwacja-testowa.html?clientID={client_id}"
                final_message_to_user = f"Świetnie! Utworzyłem dla Państwa osobisty link do rezerwacji.\n\n{reservation_link}\n\nProszę wybrać wolny termin. Zarezerwowana lekcja będzie automatycznie potwierdzona. Lekcję testową należy opłacić do 5 minut od połączenia się z korepetytorem. Termin lekcji można odwołać lub przełożyć używajac panelu klienta, do którego dostęp dostaną Państwo po rezerwacji lub ewentualnie kontaktująć się z nami. Link jest personalny proszę nie udostępniać go nikomu. Udostępnienie linku jest równoważne z udostępnieniem dostępu do zarządzania lekcjami. BARDZO PROSIMY O ODWOŁYWANIE lekcji w przypadku rozmyślenia się. W troscę o naszych klientów nie wymagamy płatności przed połączeniem, prosimy o nienadużywanie tego (;"
            else:
                final_message_to_user = "Wystąpił błąd z naszym systemem rezerwacji."
        else:
            final_message_to_user = ai_response_raw

        history.append(Content(role="model", parts=[Part.from_text(ai_response_raw)]))

        send_message(sender_id, final_message_to_user, page_token)

        if AGREEMENT_MARKER in ai_response_raw:
            # Oznacz początek trybu po rezerwacji
            history.append(Content(role="model", parts=[Part.from_text("POST_RESERVATION_MODE")]))

        # Oznacz wiadomość użytkownika jako przeczytaną
        mark_seen_params = {"access_token": page_token}
        mark_seen_payload = {"recipient": {"id": sender_id}, "sender_action": "mark_seen"}
        try:
            requests.post(FACEBOOK_GRAPH_API_URL, params=mark_seen_params, json=mark_seen_payload, timeout=30)
            logging.info(f"Oznaczono wiadomość od {sender_id} jako przeczytaną.")
        except requests.exceptions.RequestException as e:
            logging.error(f"Błąd oznaczania wiadomości jako przeczytanej dla {sender_id}: {e}")

        if conversation_status == FOLLOW_UP_LATER and follow_up_time_iso:
            try:
                nudge_time_naive = datetime.fromisoformat(follow_up_time_iso)
                local_tz = pytz.timezone(TIMEZONE)
                nudge_time = local_tz.localize(nudge_time_naive)
                now = datetime.now(pytz.timezone(TIMEZONE))
                if now < nudge_time < (now + timedelta(hours=FOLLOW_UP_WINDOW_HOURS)):
                    logging.info("Status to FOLLOW_UP_LATER. Data jest poprawna. Generuję spersonalizowane przypomnienie...")
                    follow_up_message = get_gemini_response(history, prompt_details, is_follow_up=True)
                    logging.info(f"AI (przypomnienie) wygenerowało: '{follow_up_message}'")
                    schedule_nudge(sender_id, recipient_id, "pending_follow_up", 
                                   tasks_file=NUDGE_TASKS_FILE,
                                   nudge_time_iso=nudge_time.isoformat(), 
                                   nudge_message=follow_up_message)
                else:
                    logging.warning(f"AI zwróciło nielogiczną datę ({follow_up_time_iso}). Ignoruję przypomnienie.")
            except ValueError:
                logging.error(f"AI zwróciło nieprawidłowy format daty: {follow_up_time_iso}. Ignoruję przypomnienie.")
        elif conversation_status == EXPECTING_REPLY:
            logging.info("Status to EXPECTING_REPLY. (Brak akcji przypominającej)")
            pass
        else:
            logging.info(f"Status to {conversation_status}. NIE planuję przypomnienia.")
        
        save_history(sender_id, history)
    except Exception as e:
        logging.error(f"KRYTYCZNY BŁĄD w wątku process_event: {e}", exc_info=True)

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
    logging.getLogger('apscheduler.scheduler').setLevel(logging.WARNING)
    ensure_dir(HISTORY_DIR)
    
    scheduler = BackgroundScheduler(timezone=TIMEZONE)
    scheduler.add_job(func=check_and_send_nudges, trigger="interval", seconds=20)
    scheduler.start()
    atexit.register(lambda: scheduler.shutdown())
    
    port = int(os.environ.get("PORT", 5000))
    logging.info(f"Uruchamianie serwera na porcie {port}...")
    try:
        from waitress import serve
        serve(app, host='0.0.0.0', port=port)
    except ImportError:
        app.run(host='0.0.0.0', port=port, debug=True)

# -*- coding: utf-8 -*-
# Wersja: FINALNA (AI + Integracja z Airtable + Automatyczne Linki)

from flask import Flask, request, Response
import threading
import os
import json
import requests
import time
import vertexai
import pytz
import atexit
import uuid
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from vertexai.generative_models import (
    GenerativeModel, Part, Content, GenerationConfig,
    SafetySetting, HarmCategory, HarmBlockThreshold
)
from pyairtable import Api # DODANO: Import biblioteki Airtable
import errno
import logging

# --- Konfiguracja Ogólna ---
app = Flask(__name__)
VERIFY_TOKEN = os.environ.get("FB_VERIFY_TOKEN", "KOLAGEN")
FACEBOOK_GRAPH_API_URL = "https://graph.facebook.com/v19.0/me/messages"
HISTORY_DIR = "conversation_store"
MAX_HISTORY_TURNS = 10

# --- Wczytywanie konfiguracji z pliku ---
config = {}
try:
    with open('config.json', 'r', encoding='utf-8') as f:
        config = json.load(f)
except (FileNotFoundError, json.JSONDecodeError) as e:
    print(f"!!! KRYTYCZNY BŁĄD: Nie można wczytać pliku 'config.json': {e}")
    exit()

NUDGE_TASKS_FILE = "nudge_tasks.json"
READ_DELAY_MINUTES = 1
UNREAD_DELAY_MINUTES = 1.5
TIMEZONE = "Europe/Warsaw"
NUDGE_WINDOW_START = 6  # Godzina 6:00
NUDGE_WINDOW_END = 23   # Godzina 23:59 (w praktyce do północy)
NUDGE_EMOJI = "👍"

AI_CONFIG = config.get("AI_CONFIG", {})
AIRTABLE_CONFIG = config.get("AIRTABLE_CONFIG", {})
PAGE_CONFIG = config.get("PAGE_CONFIG", {})

PROJECT_ID = AI_CONFIG.get("PROJECT_ID")
LOCATION = AI_CONFIG.get("LOCATION")
MODEL_ID = AI_CONFIG.get("MODEL_ID")

AIRTABLE_API_KEY = AIRTABLE_CONFIG.get("API_KEY")
AIRTABLE_BASE_ID = AIRTABLE_CONFIG.get("BASE_ID")
CLIENTS_TABLE_NAME = AIRTABLE_CONFIG.get("CLIENTS_TABLE_NAME")

# --- Inicjalizacja Airtable API ---
airtable_api = None
if all([AIRTABLE_API_KEY, AIRTABLE_BASE_ID, CLIENTS_TABLE_NAME]):
    try:
        airtable_api = Api(AIRTABLE_API_KEY)
        clients_table = airtable_api.table(AIRTABLE_BASE_ID, CLIENTS_TABLE_NAME)
        print("--- Połączenie z Airtable OK.")
    except Exception as e:
        print(f"!!! BŁĄD: Nie można połączyć się z Airtable: {e}")
else:
    print("!!! OSTRZEŻENIE: Brak pełnej konfiguracji Airtable w config.json.")


# --- Znaczniki i Ustawienia Modelu ---
AGREEMENT_MARKER = "[ZAPISZ_NA_LEKCJE]"
GENERATION_CONFIG = GenerationConfig(temperature=0.7, top_p=0.95, top_k=40, max_output_tokens=1024)
SAFETY_SETTINGS = [
    SafetySetting(category=HarmCategory.HARM_CATEGORY_HARASSMENT, threshold=HarmBlockThreshold.BLOCK_ONLY_HIGH),
    SafetySetting(category=HarmCategory.HARM_CATEGORY_HATE_SPEECH, threshold=HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE),
]

# =====================================================================
# === FUNKCJE ZARZĄDZANIA PRZYPOMNIENIAMI (NUDGE) =======================
# =====================================================================

def load_nudge_tasks():
    """Wczytuje zadania przypomnień z pliku JSON."""
    if not os.path.exists(NUDGE_TASKS_FILE):
        return {}
    try:
        with open(NUDGE_TASKS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return {}

def save_nudge_tasks(tasks):
    """Zapisuje zadania przypomnień do pliku JSON."""
    try:
        with open(NUDGE_TASKS_FILE, 'w', encoding='utf-8') as f:
            json.dump(tasks, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.error(f"Błąd zapisu zadań przypomnień: {e}")

def cancel_nudge(psid):
    """Anuluje wszystkie aktywne przypomnienia dla danego użytkownika."""
    tasks = load_nudge_tasks()
    task_to_remove = None
    for task_id, task_data in tasks.items():
        if task_data.get("psid") == psid and task_data.get("status") == "pending":
            task_to_remove = task_id
            break
    if task_to_remove:
        del tasks[task_to_remove]
        save_nudge_tasks(tasks)
        logging.info(f"Anulowano przypomnienie dla PSID {psid}.")

def schedule_nudge(psid, page_id, delay_minutes):
    """Planuje nowe zadanie przypomnienia, anulując poprzednie."""
    cancel_nudge(psid) # Zawsze anuluj stare zadanie przed dodaniem nowego
    
    tasks = load_nudge_tasks()
    task_id = str(uuid.uuid4())
    now = datetime.now(pytz.timezone(TIMEZONE))
    # ZMIANA: Używamy minut zamiast godzin
    nudge_time = now + timedelta(minutes=delay_minutes)
    
    tasks[task_id] = {
        "psid": psid,
        "page_id": page_id,
        "nudge_time_iso": nudge_time.isoformat(),
        "delay_minutes": delay_minutes,
        "status": "pending"
    }
    save_nudge_tasks(tasks)
    logging.info(f"Zaplanowano przypomnienie dla PSID {psid} za {delay_minutes} min.")

def handle_read_receipt(psid, page_id):
    """Obsługuje zdarzenie odczytania wiadomości przez użytkownika."""
    logging.info(f"Użytkownik {psid} odczytał wiadomość. Zmieniam harmonogram przypomnienia.")
    # Anuluj stare przypomnienie "nieprzeczytane" (18h) i ustaw nowe "przeczytane" (6h)
    schedule_nudge(psid, page_id, READ_DELAY_MINUTES)


# =====================================================================
# === INICJALIZACJA AI (Wersja dla Vertex AI) ==========================
# =====================================================================
gemini_model = None
try:
    if not all([PROJECT_ID, LOCATION, MODEL_ID]):
        print("!!! KRYTYCZNY BŁĄD: Brak pełnej konfiguracji AI (PROJECT_ID, LOCATION, MODEL_ID) w pliku config.json")
    else:
        print(f"--- Inicjalizowanie Vertex AI: Projekt={PROJECT_ID}, Lokalizacja={LOCATION}")
        vertexai.init(project=PROJECT_ID, location=LOCATION)
        print("--- Inicjalizacja Vertex AI OK.")
        print(f"--- Ładowanie modelu: {MODEL_ID}")
        gemini_model = GenerativeModel(MODEL_ID)
        print(f"--- Model {MODEL_ID} załadowany OK.")
except Exception as e:
    print(f"!!! KRYTYCZNY BŁĄD inicjalizacji Vertex AI: {e}", flush=True)
    logging.critical(f"KRYTYCZNY BŁĄD inicjalizacji Vertex AI: {e}", exc_info=True)


# =====================================================================
# === GŁÓWNA INSTRUKCJA SYSTEMOWA DLA AI (bez zmian) ===================
# =====================================================================
SYSTEM_INSTRUCTION_GENERAL = """
### O Tobie (Twoja Rola)
Jesteś profesjonalnym i przyjaznym asystentem klienta w centrum korepetycji online. Twoim celem jest przekonanie użytkownika do umówienia pierwszej, testowej lekcji.
- **Styl Komunikacji:** Twoje wiadomości muszą być KRÓTKIE i angażujące. Zawsze kończ je pytaniem. Zawsze zwracaj się do użytkownika per "Państwo". Pamiętaj, że możesz rozmawiać zarówno z rodzicem, jak i bezpośrednio z uczniem.

### Informacje o Usłudze
1.  **Cennik (za lekcję 60 minut):**
    - Szkoła Podstawowa: 65 zł
    - Szkoła średnia (klasy niematuralne, podstawa): 70 zł
    - Szkoła średnia (klasy niematuralne, rozszerzenie): 75 zł
    - Szkoła średnia (klasa maturalna, podstawa i rozszerzenie): 80 zł
2.  **Format lekcji:**
    - Korepetycje odbywają się online, 1-na-1 z doświadczonym korepetytorem.
    - Platforma: Microsoft Teams. Wystarczy kliknąć w otrzymany link, nie trzeba nic pobierać.

### Kluczowe Zadania i Przepływ Rozmowy
Postępuj zgodnie z poniższą chronologią, **dzieląc rozmowę na krótkie wiadomości i NIE zadając pytań, jeśli znasz już odpowiedź**:
1.  **Powitanie:** Przywitaj się i zapytaj, w czym możesz pomóc (np. "Dzień dobry! W czym mogę Państwu pomóc?").
2.  **Zbieranie informacji (Krok 1 - Szkoła i klasa):** Zapytaj o klasę i typ szkoły ucznia. Przykład: "Świetnie! Do której klasy i jakiego typu szkoły uczęszcza uczeń?"
3.  **Inteligentna analiza:** JEŚLI użytkownik w swojej odpowiedzi poda zarówno klasę, jak i typ szkoły (np. "8 klasa podstawówki"), przejdź od razu do prezentacji oferty. NIE dopytuj ponownie o typ szkoły.
4.  **Zbieranie informacji (Krok 2 - Poziom):** JEŚLI typ szkoły to liceum lub technikum i nie podano poziomu, w osobnej wiadomości zapytaj o poziom. Przykład: "Dziękuję. A czy chodzi o materiał na poziomie podstawowym czy rozszerzonym?"
5.  **Prezentacja oferty:** Na podstawie zebranych danych, przedstaw cenę i format lekcji.
6.  **Zachęta do działania:** Po przedstawieniu oferty, zawsze aktywnie proponuj umówienie pierwszej, testowej lekcji.

### Jak Obsługiwać Sprzeciwy
- JEŚLI klient ma wątpliwości, zapytaj o ich powód.
- JEŚLI klient twierdzi, że uczeń będzie **rozkojarzony**, ODPOWIEDZ: "To częsta obawa, ale proszę się nie martwić. Nasi korepetytorzy prowadzą lekcje w bardzo angażujący sposób."
- JEŚLI klient twierdzi, że korepetycje online się nie sprawdziły, ZAPYTAJ: "Czy uczeń miał już do czynienia z korepetycjami online 1-na-1, czy doświadczenie opiera się głównie na lekcjach szkolnych z czasów pandemii?"

### Twój GŁÓWNY CEL i Format Odpowiedzi
Twoim nadrzędnym celem jest uzyskanie od użytkownika zgody na pierwszą lekcję.
- Kiedy rozpoznasz, że użytkownik jednoznacznie zgadza się na umówienie lekcji (używa zwrotów jak "Tak, chcę", "Zgadzam się", "Zapiszmy się", "Poproszę"), Twoja odpowiedź dla niego MUSI być krótka i MUSI kończyć się specjalnym znacznikiem: `{agreement_marker}`.
- Przykład poprawnej odpowiedzi: "Doskonale, to świetna decyzja! {agreement_marker}"
"""

# =====================================================================
# === NOWE FUNKCJE POMOCNICZE (Airtable i Profil FB) ===================
# =====================================================================

def load_config():
    """Wczytuje pełną konfigurację z pliku JSON."""
    try:
        with open('config.json', 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logging.critical(f"KRYTYCZNY BŁĄD: Nie można wczytać pliku 'config.json': {e}")
        return {} # Zwróć pusty słownik w razie błędu

def check_and_send_nudges():
    """Główna funkcja harmonogramu. Sprawdza i wysyła zaległe przypomnienia."""
    logging.info("[Scheduler] Uruchamiam sprawdzanie przypomnień...")
    
    # === KLUCZOWA POPRAWKA JEST TUTAJ ===
    # Zawsze wczytuj konfigurację na początku, aby mieć pewność, że mamy dostęp do tokenów.
    # Używamy zmiennej `page_config_from_file`, aby uniknąć konfliktu nazw.
    page_config_from_file = load_config().get("PAGE_CONFIG", {})
    if not page_config_from_file:
        logging.error("[Scheduler] Nie udało się wczytać konfiguracji stron. Pomijam sprawdzanie.")
        return
    # === KONIEC POPRAWKI ===

    tasks = load_nudge_tasks()
    now = datetime.now(pytz.timezone(TIMEZONE))
    tasks_modified = False
    
    for task_id, task in list(tasks.items()):
        if task.get("status") != "pending":
            continue

        nudge_time = datetime.fromisoformat(task["nudge_time_iso"])
        
        if now >= nudge_time:
            is_in_window = NUDGE_WINDOW_START <= now.hour <= NUDGE_WINDOW_END
            
            if is_in_window:
                logging.info(f"[Scheduler] Wysyłam przypomnienie do PSID {task['psid']}...")
                page_config = page_config_from_file.get(task["page_id"]) # Używamy wczytanej konfiguracji
                
                if page_config and page_config.get("token"):
                    send_message(task["psid"], NUDGE_EMOJI, page_config["token"])
                    task["status"] = "sent"
                    tasks_modified = True
                else:
                    logging.error(f"[Scheduler] Brak tokena dla page_id {task['page_id']}. Nie można wysłać.")
                    task["status"] = "failed"
                    tasks_modified = True
            else:
                logging.info(f"[Scheduler] Zła pora na wysyłkę do {task['psid']}. Przeplanowuję.")
                next_day_start = now.replace(hour=NUDGE_WINDOW_START, minute=0, second=0)
                if now.hour >= NUDGE_WINDOW_END:
                    next_day_start += timedelta(days=1)
                
                task["nudge_time_iso"] = next_day_start.isoformat()
                tasks_modified = True

    if tasks_modified:
        save_nudge_tasks(tasks)

def get_user_profile(psid, page_access_token):
    """Pobiera imię i nazwisko użytkownika z Facebook Graph API."""
    try:
        url = f"https://graph.facebook.com/v19.0/{psid}?fields=first_name,last_name&access_token={page_access_token}"
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        return data.get("first_name"), data.get("last_name")
    except requests.exceptions.RequestException as e:
        logging.error(f"Błąd pobierania profilu FB dla PSID {psid}: {e}")
        return None, None

def create_or_find_client_in_airtable(psid, page_access_token, clients_table_obj):
    """Sprawdza, czy klient istnieje w Airtable. Jeśli nie, tworzy go. Zwraca ClientID (PSID)."""
    if not clients_table_obj:
        logging.error("Airtable nie jest skonfigurowane, nie można utworzyć klienta.")
        return None

    try:
        # Sprawdź, czy klient już istnieje
        existing_client = clients_table_obj.first(formula=f"{{ClientID}} = '{psid}'")
        if existing_client:
            logging.info(f"Klient o PSID {psid} już istnieje w Airtable.")
            return psid
        
        # Jeśli nie istnieje, utwórz go
        logging.info(f"Klient o PSID {psid} nie istnieje. Tworzenie nowego rekordu...")
        first_name, last_name = get_user_profile(psid, page_access_token)
        
        new_client_data = {
            "ClientID": psid,
            "Źródło": "Messenger Bot"
        }
        if first_name:
            new_client_data["Imię"] = first_name
        if last_name:
            new_client_data["Nazwisko"] = last_name
            
        clients_table_obj.create(new_client_data)
        logging.info(f"Pomyślnie utworzono nowego klienta w Airtable dla PSID {psid}.")
        return psid
        
    except Exception as e:
        logging.error(f"Wystąpił błąd podczas operacji na Airtable dla PSID {psid}: {e}", exc_info=True)
        return None

# =====================================================================
# === FUNKCJE POMOCNICZE (bez zmian) ==================================
# =====================================================================
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
    except Exception as e:
        logging.error(f"BŁĄD parsowania historii dla {user_psid}: {e}.")
        return []

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
            json.dump(history_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.error(f"BŁĄD zapisu historii dla {user_psid}: {e}")

# =====================================================================
# === FUNKCJE KOMUNIKACJI (bez zmian) =================================
# =====================================================================
def send_message(recipient_id, message_text, page_access_token):
    if not all([recipient_id, message_text, page_access_token]):
        logging.error("Błąd wysyłania: Brak ID, treści lub tokenu.")
        return
    params = {"access_token": page_access_token}
    payload = {"recipient": {"id": recipient_id}, "message": {"text": message_text}, "messaging_type": "RESPONSE"}
    try:
        r = requests.post(FACEBOOK_GRAPH_API_URL, params=params, json=payload, timeout=30)
        r.raise_for_status()
        logging.info(f"Wysłano wiadomość do {recipient_id}: '{message_text[:50]}...'")
    except requests.exceptions.RequestException as e:
        logging.error(f"Błąd wysyłania do {recipient_id}: {e}")
        logging.error(f"    Odpowiedź serwera: {e.response.text if e.response else 'Brak'}")

def get_gemini_response(history, prompt_details):
    if not gemini_model:
        return "Przepraszam, mam chwilowy problem z moim systemem."
    system_instruction = SYSTEM_INSTRUCTION_GENERAL.format(
        prompt_details=prompt_details, agreement_marker=AGREEMENT_MARKER)
    full_prompt = [
        Content(role="user", parts=[Part.from_text(system_instruction)]),
        Content(role="model", parts=[Part.from_text("Rozumiem. Jestem gotów do rozmowy z klientem.")])
    ] + history
    try:
        response = gemini_model.generate_content(
            full_prompt, generation_config=GENERATION_CONFIG, safety_settings=SAFETY_SETTINGS)
        if not response.candidates:
            return "Twoja wiadomość nie mogła zostać przetworzona (zasady bezpieczeństwa)."
        return "".join(part.text for part in response.candidates[0].content.parts).strip()
    except Exception as e:
        logging.error(f"BŁĄD wywołania Gemini: {e}", exc_info=True)
        return "Przepraszam, wystąpił nieoczekiwany błąd."

# =====================================================================
# === GŁÓWNA LOGIKA PRZETWARZANIA (ZMODYFIKOWANA) ======================
# =====================================================================
def process_event(event_payload):
    try:
        logging.info("Wątek 'process_event' wystartował.")
        if not PAGE_CONFIG: return
            
        sender_id = event_payload.get("sender", {}).get("id")
        recipient_id = event_payload.get("recipient", {}).get("id")

        if not sender_id or not recipient_id or event_payload.get("message", {}).get("is_echo"):
            return
        
        # --- ZMIANA 1: Obsługa zdarzenia odczytania ---
        if event_payload.get("read"):
            handle_read_receipt(sender_id, recipient_id)
            return
        # --- KONIEC ZMIANY ---

        page_config = PAGE_CONFIG.get(recipient_id)
        if not page_config: return

        page_token = page_config.get("token")
        prompt_details = page_config.get("prompt_details")
        page_name = page_config.get("name", "Nieznana Strona")

        if not page_token or not prompt_details: return

        user_message_text = event_payload.get("message", {}).get("text", "").strip()
        if not user_message_text: return
        
        # --- ZMIANA 2: Anuluj przypomnienie, bo użytkownik odpowiedział ---
        cancel_nudge(sender_id)
        # --- KONIEC ZMIANY ---

        logging.info(f"--- Przetwarzanie dla strony '{page_name}' | Użytkownik {sender_id} ---")
        logging.info(f"Odebrano wiadomość: '{user_message_text}'")

        history = load_history(sender_id)
        history.append(Content(role="user", parts=[Part.from_text(user_message_text)]))

        logging.info("Wysyłam zapytanie do AI Gemini...")
        ai_response_raw = get_gemini_response(history, prompt_details)
        logging.info(f"AI odpowiedziało: '{ai_response_raw[:100]}...'")
        
        final_message_to_user = ""
        
        if AGREEMENT_MARKER in ai_response_raw:
            logging.info(">>> ZNALEZIONO ZNACZNIK ZGODY! <<<")
            client_id = create_or_find_client_in_airtable(sender_id, page_token, clients_table)
            
            if client_id:
                reservation_link = f"https://zakręcone-korepetycje.pl/?clientID={client_id}"
                final_message_to_user = (
                    f"Świetnie! Utworzyłem dla Państwa osobisty link do rezerwacji.\n\n"
                    f"{reservation_link}\n\n"
                    f"Proszę go nie udostępniać nikomu. Zapraszam do wybrania terminu!"
                )
            else:
                final_message_to_user = "Wygląda na to, że wystąpił błąd z naszym systemem rezerwacji. Proszę spróbować ponownie za chwilę."
        else:
            final_message_to_user = ai_response_raw

        send_message(sender_id, final_message_to_user, page_token)
        history.append(Content(role="model", parts=[Part.from_text(final_message_to_user)]))
        
        # --- ZMIANA 3: Zaplanuj przypomnienie po wysłaniu wiadomości ---
        if AGREEMENT_MARKER not in final_message_to_user: # Nie planuj przypomnienia, jeśli wysłaliśmy link
            schedule_nudge(sender_id, recipient_id, UNREAD_DELAY_MINUTES)
        # --- KONIEC ZMIANY ---

        save_history(sender_id, history)
        logging.info(f"--- Zakończono przetwarzanie dla {sender_id} ---")
    except Exception as e:
        logging.error(f"KRYTYCZNY BŁĄD w wątku process_event: {e}", exc_info=True)

# =====================================================================
# === WEBHOOK FLASK I URUCHOMIENIE (bez zmian) ========================
# =====================================================================

@app.route('/webhook', methods=['GET'])
def webhook_verification():
    if request.args.get('hub.mode') == 'subscribe' and request.args.get('hub.verify_token') == VERIFY_TOKEN:
        return Response(request.args.get('hub.challenge'), status=200)
    else:
        return Response("Verification failed", status=403)

@app.route('/webhook', methods=['POST'])
def webhook_handle():
    data = request.json
    if data.get("object") == "page":
        for entry in data.get("entry", []):
            for event in entry.get("messaging", []):
                thread = threading.Thread(target=process_event, args=(event,))
                thread.start()
        return Response("EVENT_RECEIVED", status=200)
    else:
        return Response("NOT_PAGE_EVENT", status=404)

if __name__ == '__main__':
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - [%(threadName)s] - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    ensure_dir(HISTORY_DIR)
    
    # --- DODANO URUCHOMIENIE HARMONOGRAMU ---
    scheduler = BackgroundScheduler(timezone=TIMEZONE)
    # Uruchom sprawdzanie co 5 minut
    scheduler.add_job(func=check_and_send_nudges, trigger="interval", seconds=5)
    scheduler.start()
    # Zarejestruj zamknięcie harmonogramu przy wyjściu
    atexit.register(lambda: scheduler.shutdown())
    # --- KONIEC DODAWANIA ---
    
    port = int(os.environ.get("PORT", 8080))
    logging.info(f"Uruchamianie serwera na porcie {port}...")
    try:
        from waitress import serve
        serve(app, host='0.0.0.0', port=port)
    except ImportError:
        app.run(host='0.0.0.0', port=port, debug=True)

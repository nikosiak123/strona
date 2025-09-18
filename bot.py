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
from pyairtable import Api
import errno
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

AI_CONFIG = config.get("AI_CONFIG", {})
AIRTABLE_CONFIG = config.get("AIRTABLE_CONFIG", {})
PAGE_CONFIG = config.get("PAGE_CONFIG", {})

PROJECT_ID = AI_CONFIG.get("PROJECT_ID")
LOCATION = AI_CONFIG.get("LOCATION")
MODEL_ID = AI_CONFIG.get("MODEL_ID")

AIRTABLE_API_KEY = AIRTABLE_CONFIG.get("API_KEY")
AIRTABLE_BASE_ID = AIRTABLE_CONFIG.get("BASE_ID")
CLIENTS_TABLE_NAME = AIRTABLE_CONFIG.get("CLIENTS_TABLE_NAME")

airtable_api = None
clients_table = None
if all([AIRTABLE_API_KEY, AIRTABLE_BASE_ID, CLIENTS_TABLE_NAME]):
    try:
        airtable_api = Api(AIRTABLE_API_KEY)
        clients_table = airtable_api.table(AIRTABLE_BASE_ID, CLIENTS_TABLE_NAME)
        print("--- Połączenie z Airtable OK.")
    except Exception as e:
        print(f"!!! BŁĄD: Nie można połączyć się z Airtable: {e}")
else:
    print("!!! OSTRZEŻENIE: Brak pełnej konfiguracji Airtable w config.json.")

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

SYSTEM_INSTRUCTION_ESTIMATOR = f"""
Jesteś ekspertem w analizie języka naturalnego w celu estymacji czasu.
- **Aktualna data i godzina to: `{{current_time}}`.**
- **Kontekst:** Klient właśnie powiedział, że odezwie się później.

Na podstawie poniższej historii rozmowy, oszacuj, kiedy NAJPRAWDOPODOBNIEJ skontaktuje się ponownie.
Twoja odpowiedź MUSI być TYLKO I WYŁĄCZNIE datą i godziną w formacie ISO 8601: `YYYY-MM-DDTHH:MM:SS`.

**REGUŁY:**
- Bądź konserwatywny, dodaj 1-2 godziny buforu do swojego oszacowania.
- Zawsze używaj tego samego roku, co w `{{current_time}}`.
- Wynik musi być w przyszłości względem `{{current_time}}`.
- Jeśli klient mówi ogólnie "wieczorem", załóż godzinę 20:30.
- Jeśli klient mówi "po szkole", załóż godzinę 18:00.

Przykład (zakładając `{{current_time}}` = `2025-09-18T15:00:00`):
- Historia: "...klient: dam znać wieczorem." -> Twoja odpowiedź: `2025-09-18T20:30:00`
"""

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
    - Platforma: Microsoft Teams. Wystarczy kliknąć w otrzymany link.

### Kluczowe Zadania i Przepływ Rozmowy
Postępuj zgodnie z poniższą chronologią, **dzieląc rozmowę na krótkie wiadomości i NIE zadając pytań, jeśli znasz już odpowiedź**:
1.  **Powitanie:** JEŚLI pierwsza wiadomość użytkownika to ogólne powitanie, odpowiedz powitaniem i zapytaj, w czym możesz pomóc. JEŚLI użytkownik od razu pisze, że szuka korepetycji, przejdź bezpośrednio do kroku 2.
2.  **Zbieranie informacji (Szkoła i klasa):** Zapytaj o klasę i typ szkoły ucznia.
3.  **Inteligentna analiza:** JEŚLI użytkownik w swojej odpowiedzi poda zarówno klasę, jak i typ szkoły, przejdź od razu do kroku 5.
4.  **Zbieranie informacji (Poziom):** JEŚLI typ szkoły to liceum lub technikum i nie podano poziomu, w osobnej wiadomości zapytaj o poziom.
5.  **Prezentacja oferty:** Na podstawie zebranych danych, przedstaw cenę i format lekcji.
6.  **Zachęta do działania:** Po przedstawieniu oferty, zawsze aktywnie proponuj umówienie pierwszej, testowej lekcji.

### Jak Obsługiwać Sprzeciwy
- JEŚLI klient ma wątpliwości, zapytaj o ich powód.
- JEŚLI klient twierdzi, że uczeń będzie **rozkojarzony**, ODPOWIEDZ: "To częsta obawa, ale proszę się nie martwić. Nasi korepetytorzy prowadzą lekcje w bardzo angażujący sposób."
- JEŚLI klient twierdzi, że korepetycje online się nie sprawdziły, ZAPYTAJ: "Czy uczeń miał już do czynienia z korepetycjami online 1-na-1, czy doświadczenie opiera się głównie na lekcjach szkolnych z czasów pandemii?"

### Twój GŁÓWNY CEL i Format Odpowiedzi
Twoim nadrzędnym celem jest uzyskanie od użytkownika zgody na pierwszą lekcję.
- Kiedy rozpoznasz, że użytkownik jednoznacznie zgadza się na umówienie lekcji, Twoja odpowiedź dla niego MUSI być krótka i MUSI kończyć się specjalnym znacznikiem: `{agreement_marker}`.
"""

# =====================================================================
# === FUNKCJE POMOCNICZE ==============================================
# =====================================================================
def load_config():
    try:
        with open('config.json', 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logging.critical(f"KRYTYCZNY BŁĄD wczytywania config.json: {e}")
        return {}

def get_user_profile(psid, page_access_token):
    try:
        url = f"https://graph.facebook.com/v19.0/{psid}?fields=first_name,last_name&access_token={page_access_token}"
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        return data.get("first_name"), data.get("last_name")
    except Exception as e:
        logging.error(f"Błąd pobierania profilu FB dla PSID {psid}: {e}")
        return None, None

def create_or_find_client_in_airtable(psid, page_access_token, clients_table_obj):
    if not clients_table_obj:
        logging.error("Airtable nie jest skonfigurowane.")
        return None
    try:
        existing_client = clients_table_obj.first(formula=f"{{ClientID}} = '{psid}'")
        if existing_client: return psid
        first_name, last_name = get_user_profile(psid, page_access_token)
        new_client_data = {"ClientID": psid, "Źródło": "Messenger Bot"}
        if first_name: new_client_data["Imię"] = first_name
        if last_name: new_client_data["Nazwisko"] = last_name
        clients_table_obj.create(new_client_data)
        return psid
    except Exception as e:
        logging.error(f"Błąd operacji na Airtable dla PSID {psid}: {e}", exc_info=True)
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
    logging.info(f"[{datetime.now(pytz.timezone(TIMEZONE)).strftime('%H:%M:%S')}] [Scheduler] Uruchamiam sprawdzanie przypomnień...")
    page_config_from_file = load_config().get("PAGE_CONFIG", {})
    if not page_config_from_file:
        logging.error("[Scheduler] Błąd wczytywania konfiguracji.")
        return
    tasks = load_nudge_tasks(NUDGE_TASKS_FILE)
    now = datetime.now(pytz.timezone(TIMEZONE))
    tasks_to_modify = {}
    for task_id, task in list(tasks.items()):
        if not task.get("status", "").startswith("pending"): continue
        nudge_time = datetime.fromisoformat(task["nudge_time_iso"])
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
    print("\n" + "="*20 + " PROMPT DLA AI (Klasyfikator) " + "="*20)
    for msg in full_prompt: print(f"--- ROLE: {msg.role} ---\n{msg.parts[0].text}\n" + "-"*60)
    print("="*56 + "\n")
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
    formatted_instruction = SYSTEM_INSTRUCTION_ESTIMATOR.replace("{{current_time}}", now_str)
    chat_history_text = "\n".join([f"Klient: {msg.parts[0].text}" if msg.role == 'user' else f"Bot: {msg.parts[0].text}" for msg in history])
    prompt_for_analysis = f"OTO PEŁNA HISTORIA CZATU:\n---\n{chat_history_text}\n---"
    full_prompt = [
        Content(role="user", parts=[Part.from_text(formatted_instruction)]),
        Content(role="model", parts=[Part.from_text("Rozumiem. Zwrócę datę w formacie ISO 8601.")]),
        Content(role="user", parts=[Part.from_text(prompt_for_analysis)])
    ]
    print("\n" + "="*20 + " PROMPT DLA AI (Estymator Czasu) " + "="*20)
    for msg in full_prompt: print(f"--- ROLE: {msg.role} ---\n{msg.parts[0].text}\n" + "-"*60)
    print("="*62 + "\n")
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
    print("\n" + "="*20 + " PROMPT DLA AI (Rozmowa/Przypomnienie) " + "="*20)
    for msg in full_prompt: print(f"--- ROLE: {msg.role} ---\n{msg.parts[0].text}\n" + "-"*60)
    print("="*66 + "\n")
    try:
        response = gemini_model.generate_content(full_prompt, generation_config=GENERATION_CONFIG, safety_settings=SAFETY_SETTINGS)
        if not response.candidates: return "Twoja wiadomość nie mogła zostać przetworzona."
        return "".join(part.text for part in response.candidates[0].content.parts).strip()
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
             cancel_nudge(sender_id, NUDGE_TASKS_FILE)
             logging.info(f"Użytkownik {sender_id} odczytał wiadomość. Anulowano przypomnienia.")
             return
        page_config = PAGE_CONFIG.get(recipient_id)
        if not page_config: return
        page_token = page_config.get("token")
        user_message_text = event_payload.get("message", {}).get("text", "").strip()
        if not user_message_text: return
        cancel_nudge(sender_id, NUDGE_TASKS_FILE)
        prompt_details = page_config.get("prompt_details")
        page_name = page_config.get("name", "Nieznana Strona")
        history = load_history(sender_id)
        history.append(Content(role="user", parts=[Part.from_text(user_message_text)]))
        ai_response_raw = get_gemini_response(history, prompt_details)
        history.append(Content(role="model", parts=[Part.from_text(ai_response_raw)]))
        
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
                reservation_link = f"https://zakręcone-korepetycje.pl/?clientID={client_id}"
                final_message_to_user = f"Świetnie! Utworzyłem dla Państwa osobisty link do rezerwacji.\n\n{reservation_link}\n\nProszę go nie udostępniać nikomu."
            else:
                final_message_to_user = "Wystąpił błąd z naszym systemem rezerwacji."
        else:
            final_message_to_user = ai_response_raw
            
        send_message(sender_id, final_message_to_user, page_token)
        
        if conversation_status == FOLLOW_UP_LATER and follow_up_time_iso:
            try:
                nudge_time = datetime.fromisoformat(follow_up_time_iso).astimezone(pytz.timezone(TIMEZONE))
                now = datetime.now(pytz.timezone(TIMEZONE))
                if now < nudge_time < (now + timedelta(hours=FOLLOW_UP_WINDOW_HOURS)):
                    logging.info("Status to FOLLOW_UP_LATER. Data jest poprawna. Generuję spersonalizowane przypomnienie...")
                    follow_up_message = get_gemini_response(history, prompt_details, is_follow_up=True)
                    logging.info(f"AI (przypomnienie) wygenerowało: '{follow_up_message}'")
                    schedule_nudge(sender_id, recipient_id, "pending_follow_up", 
                                   tasks_file=NUDGE_TASKS_FILE,
                                   nudge_time_iso=follow_up_time_iso, 
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
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - [%(threadName)s] - %(message)s')
    logging.getLogger('apscheduler.executors.default').setLevel(logging.WARNING)
    logging.getLogger('apscheduler.scheduler').setLevel(logging.WARNING)
    ensure_dir(HISTORY_DIR)
    
    scheduler = BackgroundScheduler(timezone=TIMEZONE)
    scheduler.add_job(func=check_and_send_nudges, trigger="interval", seconds=20)
    scheduler.start()
    atexit.register(lambda: scheduler.shutdown())
    
    port = int(os.environ.get("PORT", 8080))
    logging.info(f"Uruchamianie serwera na porcie {port}...")
    try:
        from waitress import serve
        serve(app, host='0.0.0.0', port=port)
    except ImportError:
        app.run(host='0.0.0.0', port=port, debug=True)

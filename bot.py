# -*- coding: utf-8 -*-
# Wersja: OSTATECZNA (Google AI Studio z Kluczem API)

from flask import Flask, request, Response
import threading
import os
import json
import requests
import google.generativeai as genai
import errno
import logging

# --- Konfiguracja Ogólna ---
app = Flask(__name__)
VERIFY_TOKEN = os.environ.get("FB_VERIFY_TOKEN", "KOLAGEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") # Wczytujemy klucz API
MODEL_ID = "gemini-1.5-flash-latest"
FACEBOOK_GRAPH_API_URL = "https://graph.facebook.com/v19.0/me/messages"
HISTORY_DIR = "conversation_store"
MAX_HISTORY_TURNS = 10

# --- Znaczniki i Ustawienia Modelu (POPRAWIONA SKŁADNIA) ---
AGREEMENT_MARKER = "[ZAPISZ_NA_LEKCJE]"
# POPRAWKA: Definiujemy konfigurację jako zwykły słownik (dictionary)
GENERATION_CONFIG = {
    "temperature": 0.7,
    "top_p": 0.95,
    "top_k": 40,
    "max_output_tokens": 1024,
}
# POPRAWKA: Definiujemy ustawienia bezpieczeństwa jako listę słowników
SAFETY_SETTINGS = [
    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_ONLY_HIGH"},
    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
]

# =====================================================================
# === INICJALIZACJA AI (Nowa wersja z Kluczem API) =====================
# =====================================================================
gemini_model = None
try:
    if not GEMINI_API_KEY:
        print("!!! KRYTYCZNY BŁĄD: Brak klucza GEMINI_API_KEY. Ustaw zmienną środowiskową.")
    else:
        print("--- Konfigurowanie Google AI z kluczem API...")
        genai.configure(api_key=GEMINI_API_KEY)
        print("--- Konfiguracja Google AI OK.")
        print(f"--- Ładowanie modelu: {MODEL_ID}")
        gemini_model = genai.GenerativeModel(MODEL_ID)
        print(f"--- Model {MODEL_ID} załadowany OK.")
except Exception as e:
    print(f"!!! KRYTYCZNY BŁĄD inicjalizacji Google AI: {e}", flush=True)
    logging.critical(f"KRYTYCZNY BŁĄD inicjalizacji Google AI: {e}", exc_info=True)


# =====================================================================
# === GŁÓWNA INSTRUKCJA SYSTEMOWA DLA AI ===============================
# =====================================================================
SYSTEM_INSTRUCTION_GENERAL = """
Jesteś profesjonalnym i przyjaznym asystentem klienta w centrum korepetycji online.
Twoje zadanie jest oparte o następujące szczegóły dotyczące usługi, którą reprezentujesz:
---
{prompt_details}
---
Twoje zadania:
1.  Odpowiadaj na pytania, bazując wyłącznie na informacjach podanych powyżej.
2.  Zachęcaj do umówienia się na pierwszą lekcję.
3.  Rozpoznaj, kiedy użytkownik jednoznacznie zgadza się na umówienie lekcji (np. "Tak, chcę", "Zapisz mnie").
4.  Kiedy wykryjesz zgodę, Twoja odpowiedź dla użytkownika MUSI być krótka i MUSI kończyć się specjalnym znacznikiem: `{agreement_marker}`. Przykład: "Doskonale! {agreement_marker}"

Styl komunikacji: Zawsze zwracaj się do użytkownika per "Państwo". Bądź uprzejmy i profesjonalny.
"""

# =====================================================================
# === FUNKCJE POMOCNICZE ==============================================
# =====================================================================
def load_config(config_file='config.json'):
    try:
        with open(config_file, 'r', encoding='utf-8') as f:
            return json.load(f).get("PAGE_CONFIG", {})
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logging.critical(f"KRYTYCZNY BŁĄD: Nie można wczytać pliku '{config_file}': {e}")
        return {}

def ensure_dir(directory):
    try:
        os.makedirs(directory)
    except OSError as e:
        if e.errno != errno.EEXIST:
            raise

def load_history(user_psid):
    filepath = os.path.join(HISTORY_DIR, f"{user_psid}.json")
    if not os.path.exists(filepath): return []
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception: return []

def save_history(user_psid, history):
    ensure_dir(HISTORY_DIR)
    filepath = os.path.join(HISTORY_DIR, f"{user_psid}.json")
    history_to_save = history[-(MAX_HISTORY_TURNS * 2):]
    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(history_to_save, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.error(f"BŁĄD zapisu historii dla {user_psid}: {e}")

# =====================================================================
# === FUNKCJE KOMUNIKACJI Z FB I AI ===================================
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
        logging.error("KRYTYCZNY BŁĄD: Model Gemini niedostępny!")
        return "Przepraszam, mam chwilowy problem z moim systemem."

    system_instruction = SYSTEM_INSTRUCTION_GENERAL.format(
        prompt_details=prompt_details, agreement_marker=AGREEMENT_MARKER)
    
    full_prompt_for_api = [
        {'role': 'user', 'parts': [system_instruction]},
        {'role': 'model', 'parts': ["Rozumiem. Jestem gotów do rozmowy z klientem."]}
    ] + history

    try:
        # OSTATECZNA POPRAWKA: Przekazujemy słownik bezpośrednio
        response = gemini_model.generate_content(
            full_prompt_for_api,
            generation_config=GENERATION_CONFIG, 
            safety_settings=SAFETY_SETTINGS)
            
        if not response.parts:
            block_reason = response.prompt_feedback.block_reason.name if response.prompt_feedback else "Nieznany"
            logging.error(f"BŁĄD Gemini - ODPOWIEDŹ ZABLOKOWANA! Powód: {block_reason}")
            return "Twoja wiadomość nie mogła zostać przetworzona (zasady bezpieczeństwa)."
            
        return response.text.strip()
    except Exception as e:
        logging.error(f"BŁĄD wywołania Gemini: {e}")
        return "Przepraszam, wystąpił nieoczekiwany błąd."

# =====================================================================
# === GŁÓWNA LOGIKA PRZETWARZANIA ======================================
# =====================================================================
def process_event(event_payload):
    try:
        logging.info("Wątek 'process_event' wystartował.")
        PAGE_CONFIG = load_config()
        if not PAGE_CONFIG:
            logging.error("Brak konfiguracji PAGE_CONFIG w pliku config.json.")
            return
            
        sender_id = event_payload.get("sender", {}).get("id")
        recipient_id = event_payload.get("recipient", {}).get("id")

        if not sender_id or not recipient_id or event_payload.get("message", {}).get("is_echo"):
            return

        page_config = PAGE_CONFIG.get(recipient_id)
        if not page_config: return

        page_token = page_config.get("token")
        prompt_details = page_config.get("prompt_details")
        page_name = page_config.get("name", "Nieznana Strona")

        if not page_token or not prompt_details: return

        user_message_text = event_payload.get("message", {}).get("text", "").strip()
        if not user_message_text: return

        logging.info(f"--- Przetwarzanie dla strony '{page_name}' | Użytkownik {sender_id} ---")
        logging.info(f"Odebrano wiadomość: '{user_message_text}'")

        history = load_history(sender_id)
        history.append({'role': 'user', 'parts': [user_message_text]})

        ai_response_raw = get_gemini_response(history, prompt_details)
        logging.info(f"AI odpowiedziało: '{ai_response_raw[:100]}...'")
        
        if AGREEMENT_MARKER in ai_response_raw:
            logging.info(">>> ZNALEZIONO ZNACZNIK ZGODY! <<<")
            print(f"\n!!! UŻYTKOWNIK (PSID: {sender_id}) ZGODZIŁ SIĘ NA LEKCJĘ !!!")
            print(f"!!! DOTYCZY STRONY: '{page_name}' !!!\n")
            final_message_to_user = "Okej, zapisuję"
            send_message(sender_id, final_message_to_user, page_token)
            history.append({'role': 'model', 'parts': [final_message_to_user]})
        else:
            send_message(sender_id, ai_response_raw, page_token)
            history.append({'role': 'model', 'parts': [ai_response_raw]})

        save_history(sender_id, history)
        logging.info(f"--- Zakończono przetwarzanie dla {sender_id} ---")
    except Exception as e:
        logging.error(f"KRYTYCZNY BŁĄD w wątku process_event: {e}", exc_info=True)

# =====================================================================
# === WEBHOOK FLASK ===================================================
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

# =====================================================================
# === URUCHOMIENIE SERWERA ============================================
# =====================================================================
if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    ensure_dir(HISTORY_DIR)
    port = int(os.environ.get("PORT", 8080))
    logging.info(f"Uruchamianie serwera na porcie {port}...")
    try:
        from waitress import serve
        serve(app, host='0.0.0.0', port=port)
    except ImportError:
        app.run(host='0.0.0.0', port=port, debug=True)

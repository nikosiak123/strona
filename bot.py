# -*- coding: utf-8 -*-
# Wersja: OSTATECZNA (Vertex AI z konfiguracją w pliku JSON)

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
    exit() # Zakończ działanie, jeśli nie ma konfiguracji

AI_CONFIG = config.get("AI_CONFIG", {})
PAGE_CONFIG = config.get("PAGE_CONFIG", {})

PROJECT_ID = AI_CONFIG.get("PROJECT_ID")
LOCATION = AI_CONFIG.get("LOCATION")
MODEL_ID = AI_CONFIG.get("MODEL_ID")

# --- Znaczniki i Ustawienia Modelu ---
AGREEMENT_MARKER = "[ZAPISZ_NA_LEKCJE]"
GENERATION_CONFIG = GenerationConfig(temperature=0.7, top_p=0.95, top_k=40, max_output_tokens=1024)
SAFETY_SETTINGS = [
    SafetySetting(category=HarmCategory.HARM_CATEGORY_HARASSMENT, threshold=HarmBlockThreshold.BLOCK_ONLY_HIGH),
    SafetySetting(category=HarmCategory.HARM_CATEGORY_HATE_SPEECH, threshold=HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE),
]

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
# === GŁÓWNA INSTRUKCJA SYSTEMOWA DLA AI ===============================
# =====================================================================
SYSTEM_INSTRUCTION_GENERAL = """
### O Tobie (Twoja Rola)
Jesteś profesjonalnym i przyjaznym asystentem klienta w centrum korepetycji online. Twoim celem jest przekonanie użytkownika do umówienia pierwszej, testowej lekcji.
- **Styl Komunikacji:** Twoje wiadomości muszą być KRÓTKIE i angażujące. Zawsze kończ je pytaniem, aby zachęcić do odpowiedzi. Zawsze zwracaj się do użytkownika per "Państwo". Bądź uprzejmy i profesjonalny.

### Informacje o Usłudze, które przekazujesz klientom
1.  **Cennik (za lekcję 60 minut):**
    - Szkoła Podstawowa: 65 zł
    - Szkoła średnia (klasy niematuralne, podstawa): 70 zł
    - Szkoła średnia (klasy niematuralne, rozszerzenie): 75 zł
    - Szkoła średnia (klasa maturalna, podstawa i rozszerzenie): 80 zł
2.  **Format lekcji:**
    - Korepetycje odbywają się online, jeden na jeden z doświadczonym korepetytorem.
    - Platforma: Microsoft Teams. Wystarczy kliknąć w otrzymany link, nie trzeba nic pobierać ani się logować.

### Twoje Wewnętrzne Wytyczne (Tego NIE mówisz klientowi)
- Nie wspominaj, że korepetytorzy to studenci, chyba że zostaniesz o to wprost zapytany. Zamiast tego podkreślaj ich doświadczenie.
- Rezerwacja terminu następuje na stronie internetowej, ale Twoim zadaniem NIE jest wysyłanie linku. Twoim zadaniem jest uzyskanie zgody na pierwszą lekcję.

### Kluczowe Zadania i Przepływ Rozmowy
Postępuj zgodnie z poniższą chronologią, **dzieląc rozmowę na jak najwięcej krótkich wiadomości**:
1.  **Powitanie:** Przywitaj się krótko i zapytaj, czy użytkownik poszukuje korepetycji (chyba że już to napisał).
2.  **Zbieranie informacji (Krok 1 - Szkoła):** W pierwszej kolejności zapytaj tylko o typ szkoły. Przykład: "Świetnie! A czy chodzi o szkołę podstawową czy średnią?"
3.  **Zbieranie informacji (Krok 2 - Klasa):** W osobnej wiadomości zapytaj o klasę. Przykład: "Rozumiem, szkoła średnia. Do której klasy uczęszcza uczeń?"
4.  **Zbieranie informacji (Krok 3 - Poziom):** JEŚLI szkoła jest średnia, w kolejnej, osobnej wiadomości zapytaj o poziom. Przykład: "Dziękuję. A czy chodzi o materiał na poziomie podstawowym czy rozszerzonym?"
5.  **Prezentacja oferty:** Na podstawie zebranych danych, przedstaw cenę oraz informacje o formacie lekcji online.
6.  **Zachęta do działania:** Po przedstawieniu oferty, zawsze aktywnie proponuj umówienie pierwszej, testowej lekcji. Podkreśl, że to świetna okazja, by bez zobowiązań sprawdzić, jak wyglądają zajęcia.

### Jak Obsługiwać Sprzeciwy (szczególnie dotyczące lekcji online)
- JEŚLI klient ma wątpliwości, zawsze zapytaj o ich powód, np. "Jeśli mogę zapytać, co budzi Państwa największe wątpliwości?".
- JEŚLI klient twierdzi, że uczeń będzie **rozkojarzony**, ODPOWIEDZ: "To częsta obawa, ale proszę się nie martwić. Nasi korepetytorzy prowadzą lekcje w bardzo angażujący sposób, skupiając całą uwagę na uczniu, więc na pewno nie grozi mu rozkojarzenie."
- JEŚLI klient twierdzi, że korepetycje online się nie sprawdziły, ZAPYTAJ: "Czy uczeń miał już do czynienia z korepetycjami online 1-na-1, czy doświadczenie opiera się głównie na lekcjach szkolnych z czasów pandemii?"
- JEŚLI odpowiedź to "lekcje szkolne", ODPOWIEDZ: "Rozumiem Państwa obawy. Proszę mi wierzyć, że lekcja 1-na-1 z korepetytorem doświadczonym w nauczaniu online to zupełnie inna jakość niż zdalna lekcja w 30-osobowej klasie."
- JEŚLI odpowiedź to "inne korepetycje", ODPOWIEDZ: "Dziękuję za informację. Warto pamiętać, że korepetytor korepetytorowi nierówny. Wielu naszych klientów miało podobne wątpliwości, a po pierwszej lekcji próbnej byli bardzo zadowoleni. Może warto dać szansę również nam?"

### Twój GŁÓWNY CEL i Format Odpowiedzi
Twoim nadrzędnym celem jest uzyskanie od użytkownika zgody na pierwszą lekcję.
- Kiedy rozpoznasz, że użytkownik jednoznacznie zgadza się na umówienie lekcji (używa zwrotów jak "Tak, chcę", "Zgadzam się", "Zapiszmy się", "Poproszę"), Twoja odpowiedź dla niego MUSI być krótka i MUSI kończyć się specjalnym znacznikiem: `{agreement_marker}`.
- Przykład poprawnej odpowiedzi: "Doskonale, to świetna decyzja! {agreement_marker}"
"""

# =====================================================================
# === FUNKCJE POMOCNICZE ==============================================
# =====================================================================

def ensure_dir(directory):
    try:
        os.makedirs(directory)
    except OSError as e:
        if e.errno != errno.EEXIST:
            raise

def load_history(user_psid):
    filepath = os.path.join(HISTORY_DIR, f"{user_psid}.json")
    if not os.path.exists(filepath):
        return []
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            history_data = json.load(f)
        history = []
        for msg_data in history_data:
            if msg_data.get('role') in ('user', 'model') and msg_data.get('parts'):
                parts = [Part.from_text(p['text']) for p in msg_data['parts']]
                history.append(Content(role=msg_data['role'], parts=parts))
        return history
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        logging.error(f"BŁĄD parsowania historii dla {user_psid}: {e}. Zaczynam od nowa.")
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
        return "Przepraszam, mam chwilowy problem z moim systemem. Proszę spróbować ponownie za chwilę."
    system_instruction = SYSTEM_INSTRUCTION_GENERAL.format(
        prompt_details=prompt_details, agreement_marker=AGREEMENT_MARKER)
    full_prompt = [
        Content(role="user", parts=[Part.from_text(system_instruction)]),
        Content(role="model", parts=[Part.from_text("Rozumiem. Jestem gotów do rozmowy z klientem.")])
    ] + history
    try:
        response = gemini_model.generate_content(
            full_prompt, generation_config=GENERATION_CONFIG, safety_settings=SAFETY_SETTINGS)
        if not response.parts:
            block_reason = response.prompt_feedback.block_reason.name if response.prompt_feedback else "Nieznany"
            logging.error(f"BŁĄD Gemini - ODPOWIEDŹ ZABLOKOWANA! Powód: {block_reason}")
            return "Twoja wiadomość nie mogła zostać przetworzona (zasady bezpieczeństwa)."
        return "".join(part.text for part in response.candidates[0].content.parts).strip()
    except Exception as e:
        logging.error(f"BŁĄD wywołania Gemini: {e}")
        return "Przepraszam, wystąpił nieoczekiwany błąd. Proszę spróbować ponownie."

# =====================================================================
# === GŁÓWNA LOGIKA PRZETWARZANIA ======================================
# =====================================================================

def process_event(event_payload):
    try:
        logging.info("Wątek 'process_event' wystartował.")
        if not PAGE_CONFIG:
            logging.error("Brak konfiguracji PAGE_CONFIG. Wątek kończy pracę.")
            return
            
        sender_id = event_payload.get("sender", {}).get("id")
        recipient_id = event_payload.get("recipient", {}).get("id")

        if not sender_id or not recipient_id or event_payload.get("message", {}).get("is_echo"):
            return

        page_config = PAGE_CONFIG.get(recipient_id)
        if not page_config:
            logging.warning(f"Otrzymano wiadomość dla NIESKONFIGurowanej strony: {recipient_id}")
            return

        page_token = page_config.get("token")
        prompt_details = page_config.get("prompt_details")
        page_name = page_config.get("name", "Nieznana Strona")

        if not page_token or not prompt_details:
            logging.error(f"Brak tokena lub prompt_details dla strony '{page_name}' (ID: {recipient_id})")
            return

        user_message_text = event_payload.get("message", {}).get("text", "").strip()
        if not user_message_text:
            return

        logging.info(f"--- Przetwarzanie dla strony '{page_name}' | Użytkownik {sender_id} ---")
        logging.info(f"Odebrano wiadomość: '{user_message_text}'")

        history = load_history(sender_id)
        history.append(Content(role="user", parts=[Part.from_text(user_message_text)]))

        logging.info("Wysyłam zapytanie do AI Gemini...")
        ai_response_raw = get_gemini_response(history, prompt_details)
        logging.info(f"AI odpowiedziało (przed sprawdzeniem znacznika): '{ai_response_raw[:100]}...'")
        
        if AGREEMENT_MARKER in ai_response_raw:
            logging.info(">>> ZNALEZIONO ZNACZNIK ZGODY! Użytkownik chce się zapisać. <<<")
            
            print("\n" + "="*50)
            print(f"!!! UŻYTKOWNIK (PSID: {sender_id}) ZGODZIŁ SIĘ NA LEKCJĘ !!!")
            print(f"!!! DOTYCZY STRONY: '{page_name}' !!!")
            print("="*50 + "\n")

            final_message_to_user = "Okej, zapisuję"
            send_message(sender_id, final_message_to_user, page_token)
            
            history.append(Content(role="model", parts=[Part.from_text(final_message_to_user)]))
        else:
            logging.info("Brak znacznika zgody. Kontynuuję normalną rozmowę.")
            send_message(sender_id, ai_response_raw, page_token)
            history.append(Content(role="model", parts=[Part.from_text(ai_response_raw)]))

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
        logging.info("Weryfikacja GET pomyślna!")
        return Response(request.args.get('hub.challenge'), status=200)
    else:
        logging.warning("Weryfikacja GET nieudana.")
        return Response("Verification failed", status=403)

@app.route('/webhook', methods=['POST'])
def webhook_handle():
    logging.info("========== Otrzymano żądanie POST na /webhook ==========")
    data = request.json
    logging.info(f"Pełna treść żądania (payload): {data}")
    
    if data.get("object") == "page":
        for entry in data.get("entry", []):
            for event in entry.get("messaging", []):
                thread = threading.Thread(target=process_event, args=(event,))
                thread.start()
        return Response("EVENT_RECEIVED", status=200)
    else:
        logging.warning("Otrzymano żądanie, ale obiekt nie jest 'page'. Pomijam.")
        return Response("NOT_PAGE_EVENT", status=404)

# =====================================================================
# === URUCHOMIENIE SERWERA ============================================
# =====================================================================
if __name__ == '__main__':
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - [%(threadName)s] - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    ensure_dir(HISTORY_DIR)
    port = int(os.environ.get("PORT", 8080))
    logging.info(f"Uruchamianie serwera na porcie {port}...")
    try:
        from waitress import serve
        serve(app, host='0.0.0.0', port=port)
    except ImportError:
        logging.warning("Waitress nie jest zainstalowany. Uruchamiam w trybie deweloperskim Flask.")
        app.run(host='0.0.0.0', port=port, debug=True)

# -*- coding: utf-8 -*-
# Wersja: Pełna (AI, Wiele Stron, Wykrywanie Zgody, Logowanie)

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
PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "singular-carver-459118-g5")
LOCATION = os.environ.get("GCP_LOCATION", "us-central1")
MODEL_ID = os.environ.get("VERTEX_MODEL_ID", "gemini-1.5-flash-001")
FACEBOOK_GRAPH_API_URL = "https://graph.facebook.com/v19.0/me/messages"
HISTORY_DIR = "conversation_store"
MAX_HISTORY_TURNS = 10

# --- Znaczniki i Ustawienia Modelu ---
AGREEMENT_MARKER = "[ZAPISZ_NA_LEKCJE]"
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
Jesteś profesjonalnym i przyjaznym asystentem klienta w centrum korepetycji online.
Twoje zadanie jest oparte o następujące szczegóły dotyczące usługi, którą reprezentujesz:
---
{prompt_details}
---
Twoje zadania:
1.  **Odpowiadaj na pytania:** Udzielaj wyczerpujących odpowiedzi na pytania użytkownika, bazując wyłącznie na informacjach podanych powyżej (cena, format, przedmiot).
2.  **Zachęcaj do działania:** Po każdej odpowiedzi, aktywnie zachęcaj użytkownika do umówienia się na pierwszą lekcję.
3.  **Wykryj zgodę:** Twoim najważniejszym zadaniem jest rozpoznanie, kiedy użytkownik jednoznacznie zgadza się na umówienie pierwszej lekcji. Szukaj zwrotów takich jak "Tak, chcę umówić lekcję", "Zgadzam się", "Zapisz mnie", "Poproszę".
4.  **Użyj znacznika:** Kiedy wykryjesz zgodę, Twoja odpowiedź dla użytkownika MUSI być krótka i MUSI kończyć się specjalnym znacznikiem: `{agreement_marker}`.
    *   Przykład poprawnej odpowiedzi po wykryciu zgody: "Doskonale! Już przekazuję informację dalej. {agreement_marker}"
    *   Przykład poprawnej odpowiedzi po wykryciu zgody: "Świetna decyzja! {agreement_marker}"

Styl komunikacji: Zawsze zwracaj się do użytkownika per "Państwo". Bądź uprzejmy i profesjonalny. Nie używaj emotikon.
"""

# =====================================================================
# === FUNKCJE POMOCNICZE ==============================================
# =====================================================================

def load_config(config_file='config.json'):
    try:
        with open(config_file, 'r', encoding='utf-8') as f:
            return json.load(f).get("PAGE_CONFIG", {})
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logging.critical(f"KRYTYCZNY BŁĄD: Nie można wczytać pliku konfiguracyjnego '{config_file}': {e}")
        return {}

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
        if response.candidates:
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
        PAGE_CONFIG = load_config()
        if not PAGE_CONFIG:
            logging.error("Brak konfiguracji PAGE_CONFIG w pliku config.json. Wątek kończy pracę.")
            return
            
        sender_id = event_payload.get("sender", {}).get("id")
        recipient_id = event_payload.get("recipient", {}).get("id")

        if not sender_id or not

# -*- coding: utf-8 -*-
# Wersja: "Echo Bot" do celów diagnostycznych

from flask import Flask, request, Response
import os
import json
import requests

# --- Konfiguracja Ogólna ---
app = Flask(__name__)
VERIFY_TOKEN = os.environ.get("FB_VERIFY_TOKEN", "KOLAGEN")
FACEBOOK_GRAPH_API_URL = "https://graph.facebook.com/v19.0/me/messages"

# =====================================================================
# === FUNKCJE POMOCNICZE ==============================================
# =====================================================================

def load_config(config_file='config.json'):
    """Wczytuje tylko tokeny dostępu do stron z pliku konfiguracyjnego."""
    try:
        with open(config_file, 'r', encoding='utf-8') as f:
            return json.load(f).get("PAGE_CONFIG", {})
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"!!! KRYTYCZNY BŁĄD: Nie można wczytać pliku '{config_file}': {e}")
        return {}

def send_message(recipient_id, message_text, page_access_token):
    """Wysyła wiadomość tekstową do użytkownika na Messengerze."""
    if not all([recipient_id, message_text, page_access_token]):
        print("!!! Błąd wysyłania: Brak ID, treści lub tokenu.")
        return
    
    params = {"access_token": page_access_token}
    payload = {
        "recipient": {"id": recipient_id},
        "message": {"text": message_text},
        "messaging_type": "RESPONSE"
    }
    
    try:
        r = requests.post(FACEBOOK_GRAPH_API_URL, params=params, json=payload, timeout=30)
        r.raise_for_status() # Sprawdzi, czy nie ma błędu HTTP (np. 400, 500)
        print(f"--- Pomyślnie wysłano odpowiedź do {recipient_id}: '{message_text}'")
    except requests.exceptions.RequestException as e:
        print(f"!!! Błąd podczas wysyłania wiadomości do {recipient_id}: {e}")
        print(f"    Odpowiedź serwera: {e.response.text if e.response else 'Brak'}")

# =====================================================================
# === GŁÓWNY WEBHOOK ==================================================
# =====================================================================

@app.route('/webhook', methods=['GET', 'POST'])
def webhook_handle():
    # --- Obsługa weryfikacji przez Facebooka (metoda GET) ---
    if request.method == 'GET':
        if request.args.get('hub.mode') == 'subscribe' and request.args.get('hub.verify_token') == VERIFY_TOKEN:
            print("--- Weryfikacja GET pomyślna!")
            return Response(request.args.get('hub.challenge'), status=200)
        else:
            print("!!! Weryfikacja GET nieudana.")
            return Response("Verification failed", status=403)

    # --- Obsługa przychodzących wiadomości (metoda POST) ---
    if request.method == 'POST':
        data = request.json
        print("\n========== OTRZYMANO NOWE ZDARZENIE (POST) ==========")
        print(f"Pełna treść od Facebooka: {json.dumps(data, indent=2)}")

        if data.get("object") == "page":
            for entry in data.get("entry", []):
                for messaging_event in entry.get("messaging", []):
                    
                    # Sprawdzamy, czy to jest wiadomość i czy nie jest to "echo" (nasza własna odpowiedź)
                    if messaging_event.get("message") and not messaging_event["message"].get("is_echo"):
                        
                        sender_id = messaging_event["sender"]["id"]      # ID użytkownika, który pisze
                        recipient_id = messaging_event["recipient"]["id"] # ID Twojej strony, do której pisze
                        
                        # Sprawdzamy, czy w wiadomości jest tekst
                        if message_text := messaging_event["message"].get("text"):
                            print(f"--- Użytkownik {sender_id} napisał: '{message_text}'")
                            
                            # Wczytujemy konfigurację, aby znaleźć token dla tej strony
                            PAGE_CONFIG = load_config()
                            page_config = PAGE_CONFIG.get(recipient_id)
                            
                            if not page_config or not page_config.get("token"):
                                print(f"!!! BŁĄD: Brak tokena dla strony ID {recipient_id} w config.json!")
                                continue
                            
                            page_access_token = page_config["token"]
                            
                            # --- GŁÓWNA LOGIKA "ECHO" ---
                            # Odsyłamy dokładnie tę samą wiadomość
                            send_message(sender_id, message_text, page_access_token)
                            
        return Response("EVENT_RECEIVED", status=200)
    
    return Response("Unsupported method", status=405)

# =====================================================================
# === URUCHOMIENIE SERWERA ============================================
# =====================================================================
if __name__ == '__main__':
    # Upewnij się, że plik config.json istnieje
    if not os.path.exists('config.json'):
        print("\n!!! KRYTYCZNY BŁĄD: Brak pliku 'config.json'. Utwórz go przed uruchomieniem skryptu. !!!\n")
    else:
        port = int(os.environ.get("PORT", 8080))
        print(f"--- Uruchamianie Echo Bota na porcie {port} ---")
        # Do testów używamy wbudowanego serwera Flask
        app.run(host='0.0.0.0', port=port, debug=True)

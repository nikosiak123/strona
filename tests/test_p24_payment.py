import requests
import json
import hashlib
import uuid

# Konfiguracja Przelewy24 (z backend.py)
P24_MERCHANT_ID = 361049
P24_POS_ID = 361049
P24_CRC_KEY = "3d8d413164a23d5f"  # Klucz CRC
P24_API_KEY = "c1efdce3669a2a15b40d4630c3032b01"  # Klucz API
P24_API_URL = "https://secure.przelewy24.pl"
P24_SANDBOX = False

def generate_p24_sign(session_id, merchant_id, amount, currency, crc):
    """
    Generuje podpis SHA-384 dla Przelewy24 (REST API v1).
    """
    sign_payload = {
        "sessionId": session_id,
        "merchantId": int(merchant_id),
        "amount": int(amount),
        "currency": currency,
        "crc": crc
    }
    sign_json = json.dumps(sign_payload, separators=(',', ':'))
    return hashlib.sha384(sign_json.encode('utf-8')).hexdigest()

# Symulacja danych podobnych do logów
amount = 6500  # Kwota w groszach
currency = "PLN"
description = "Lekcja matematyka"
email = "klient@example.com"
country = "PL"
language = "pl"
urlStatus = "https://zakręcone-korepetycje.pl/api/payment-notification"

# Testuj różne urlReturn
urlReturn_options = [
    "https://google.com",
    "http://zakręcone-korepetycje.pl/",
    "https://zakręcone-korepetycje.pl/",
    "http://zakrecone-korepetycje.pl/",
    "https://zakrecone-korepetycje.pl/",
    "http://xn--zakrcone-korepetycje-8ac.pl/",
    "https://xn--zakrcone-korepetycje-8ac.pl/"
]

for urlReturn in urlReturn_options:
    print(f"\n=== Testing urlReturn: {urlReturn} ===")
    session_id = str(uuid.uuid4())  # Nowy session_id dla każdego testu

    # Generowanie podpisu
    sign = generate_p24_sign(session_id, P24_MERCHANT_ID, amount, currency, P24_CRC_KEY)

    payload = {
        "merchantId": P24_MERCHANT_ID,
        "posId": P24_POS_ID,
        "sessionId": session_id,
        "amount": amount,
        "currency": currency,
        "description": description,
        "email": email,
        "country": country,
        "language": language,
        "urlReturn": urlReturn,
        "urlStatus": urlStatus,
        "sign": sign
    }

    print("P24 payload:", json.dumps(payload, indent=2))

    try:
        response = requests.post(
            f"{P24_API_URL}/api/v1/transaction/register",
            json=payload,
            auth=(str(P24_POS_ID), P24_API_KEY),
            timeout=10
        )

        print(f"P24 request sent, status: {response.status_code}")
        print(f"Response: {response.text}")

        if response.status_code == 200:
            result = response.json()
            print("P24 Response:", json.dumps(result, indent=2))

            if 'data' in result and 'token' in result['data']:
                p24_token = result['data']['token']
                payment_url = f"{P24_API_URL}/trnRequest/{p24_token}"
                print(f"Generated payment URL: {payment_url}")
            else:
                print("ERROR: Brak tokena w odpowiedzi")
        else:
            print(f"P24 Error: {response.status_code} - {response.text}")

    except Exception as e:
        print(f"Exception: {e}")
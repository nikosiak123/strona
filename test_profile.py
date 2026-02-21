# -*- coding: utf-8 -*-
import os
import sys

# Dodaj główny katalog projektu do ścieżki, aby umożliwić import z 'config' i 'bot'
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

try:
    from strona.bot import get_user_profile
    from config import PAGE_CONFIG, MESSENGER_PAGE_ID
except ImportError as e:
    print(f"Błąd importu: {e}")
    print("Upewnij się, że plik config.py istnieje w głównym folderze i zawiera potrzebne zmienne.")
    sys.exit(1)

# --- KONFIGURACJA TESTU ---
# ⚠️ WAŻNE: Wklej tutaj prawdziwy PSID (Page-Scoped ID) użytkownika, którego profil chcesz przetestować.
# Ten PSID musi pochodzić od użytkownika, który wchodził w interakcję z Twoją stroną na Messengerze.
TEST_PSID = "ZASTAP_MNIE_PRAWDZIWYM_PSID" 
# -------------------------

def test_user_profile_fetch():
    """
    Testuje funkcję get_user_profile do pobierania danych z Facebook Graph API.
    """
    if TEST_PSID == "ZASTAP_MNIE_PRAWDZIWYM_PSID":
        print("!!! BŁĄD KONFIGURACJI TESTU !!!")
        print("Proszę, otwórz ten plik (test_profile.py) i w linii 20 zmień 'ZASTAP_MNIE_PRAWDZIWYM_PSID' na prawdziwy identyfikator PSID użytkownika.")
        return

    print(f"--- Rozpoczynam test pobierania profilu dla PSID: {TEST_PSID} ---")

    page_access_token = PAGE_CONFIG.get(MESSENGER_PAGE_ID, {}).get("token")

    if not page_access_token:
        print("!!! KRYTYCZNY BŁĄD: Nie znaleziono tokena dostępu do strony (PAGE_ACCESS_TOKEN) w pliku config.py.")
        return

    print("Pobieram dane z Facebook Graph API...")
    first_name, last_name, _ = get_user_profile(TEST_PSID, page_access_token)

    print("\n--- WYNIK TESTU ---")
    if first_name and last_name:
        print(f"✅ SUKCES!")
        print(f"Imię: {first_name}")
        print(f"Nazwisko: {last_name}")
    elif first_name:
        print(f"✅ SUKCES (częściowy)!")
        print(f"Imię: {first_name}")
        print("Nie udało się pobrać nazwiska (może być nieustawione przez użytkownika).")
    else:
        print(f"❌ PORAŻKA!")
        print("Nie udało się pobrać imienia i nazwiska. Możliwe przyczyny:")
        print("1. Podany PSID jest nieprawidłowy lub nie należy do tej strony.")
        print("2. Token dostępu do strony (PAGE_ACCESS_TOKEN) wygasł lub jest nieprawidłowy.")
        print("3. Użytkownik ma bardzo restrykcyjne ustawienia prywatności.")
        print("4. Aplikacja Facebooka nie ma odpowiednich uprawnień (pages_messaging).")

    print("------------------")

if __name__ == '__main__':
    test_user_profile_fetch()

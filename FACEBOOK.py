# -*- coding: utf-8 -*-
import os
import pickle
import time
import traceback
import sys
import json
import re
import unicodedata
import logging 
import random
from datetime import datetime

# --- IMPORTY DLA AIRTABLE, VERTEX AI I STEALTH ---
try:
    from pyairtable import Api
    AIRTABLE_AVAILABLE = True
except ImportError:
    AIRTABLE_AVAILABLE = False

import vertexai
from vertexai.generative_models import (
    GenerativeModel, Part, Content, GenerationConfig,
    SafetySetting, HarmCategory, HarmBlockThreshold
)
from selenium_stealth import stealth

from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.common.by import By
from selenium.common.exceptions import TimeoutException, NoSuchElementException, StaleElementReferenceException
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.keys import Keys


# --- KONFIGURACJA LOGOWANIA ---
logging.basicConfig(level=logging.WARNING, format='%(asctime)s - %(levelname)s - %(message)s')

# --- KONFIGURACJA ŚCIEŻEK I AIRTABLE ---
PATH_DO_GOOGLE_CHROME = os.environ.get('CHROME_BIN_PATH', '/opt/google/chrome/chrome')
PATH_DO_RECZNEGO_CHROMEDRIVER = os.environ.get('CHROMEDRIVER_PATH', '/home/nikodnaj/PROJEKT_AUTOMATYZACJA/chromedriver-linux64/chromedriver') 

AIRTABLE_API_KEY = "patcSdupvwJebjFDo.7e15a93930d15261989844687bcb15ac5c08c84a29920c7646760bc6f416146d"
AIRTABLE_BASE_ID = "appTjrMTVhYBZDPw9"
AIRTABLE_TABLE_NAME = "Statystyki"

USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
]
WINDOW_SIZES = ["1920,1080", "1366,768", "1536,864"]


# --- STAŁE ---
ERROR_SCREENSHOTS_DIR = "error_screenshots"
AI_LOG_FILE = "ai_analysis_log.txt"
COOKIES_FILE = "cookies.pkl"
PROCESSED_POSTS_FILE = "processed_posts_db.pkl"
AUTHOR_FILTER_KEYWORDS = ['spotted', 'ogloszenia', 'ogłoszenia', 'korepetycje', 'nauka', 'szkoła', 'centrum', 'instytut', 'grupa', 'group']

COMMENT_TEXTS_STANDARD = ["Polecam @Zakręcone Korepetycje. Cenowo wychodzi super. Wreszcie widać realną poprawę w ocenach.", "Bardzo polecam @Zakręcone Korepetycje. Córka mówi, że teraz rozumie 3 razy więcej niż na lekcji w szkole i widać mocną poprawę w ocenach. Mają bardzo dobrą cenę.","Polecam @Zakręcone Korepetycje. Cena super, a do tego bardzo dobra jakość zajęć. Oceny wyższe, niż przed lekcjami, o 2-3 stopnie  (:"]


COMMENT_TEXTS_HIGH_SCHOOL = ["Napiszcie do @Zakręcone Korepetycje, mój syn napisał podstawę z matmy na 94%. Zajęcia prowadzone w bardzo miłej atmosferze. Serdecznie polecam."]
# --- KONFIGURACJA AI ---
GENERATION_CONFIG = GenerationConfig(temperature=0.7, top_p=0.95, top_k=40, max_output_tokens=1024)
SAFETY_SETTINGS = [
    SafetySetting(category=HarmCategory.HARM_CATEGORY_HARASSMENT, threshold=HarmBlockThreshold.BLOCK_ONLY_HIGH),
    SafetySetting(category=HarmCategory.HARM_CATEGORY_HATE_SPEECH, threshold=HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE),
]

# --- Funkcje Pomocnicze ---
def take_error_screenshot(driver, location_name="unknown_error"):
    """Zapisuje zrzut ekranu w przypadku błędu."""
    try:
        if not os.path.exists(ERROR_SCREENSHOTS_DIR):
            os.makedirs(ERROR_SCREENSHOTS_DIR)
            
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = os.path.join(ERROR_SCREENSHOTS_DIR, f"ERROR_{location_name}_{timestamp}.png")
        
        # Opcjonalnie: Zmiana nazwy binarnej, jeśli korzystasz z nieoficjalnej
        # driver.save_screenshot(filename) # Używamy standardowej metody
        
        if driver and hasattr(driver, 'save_screenshot'):
             driver.save_screenshot(filename)
             print(f"BŁĄD ZAPISANO: Zrzut ekranu błędu zapisany w: {filename}")
        else:
             print("BŁĄD: Sterownik nie jest dostępny, aby zrobić zrzut ekranu.")

    except Exception as e:
        logging.error(f"Krytyczny błąd podczas próby zapisu zrzutu ekranu: {e}")

def random_sleep(min_seconds, max_seconds):
    time.sleep(random.uniform(min_seconds, max_seconds))

def human_typing(element, text):
    for char in text:
        element.send_keys(char)
        random_sleep(0.05, 0.2)

def human_scroll(driver):
    driver.execute_script(f"window.scrollBy(0, {random.randint(400, 800)});")
    random_sleep(1, 3)


def log_ai_interaction(post_text, ai_response):
    """Zapisuje treść posta i odpowiedź AI do pliku tekstowego."""
    try:
        with open(AI_LOG_FILE, 'a', encoding='utf-8') as f:
            f.write("="*80 + "\n")
            f.write(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write("-" * 20 + " TEKST POSTA " + "-" * 20 + "\n")
            f.write(post_text + "\n")
            f.write("-" * 20 + " ODPOWIEDŹ AI " + "-" * 20 + "\n")
            # Używamy json.dumps dla ładnego formatowania
            f.write(json.dumps(ai_response, indent=2, ensure_ascii=False) + "\n")
            f.write("="*80 + "\n\n")
    except Exception as e:
        logging.error(f"Nie udało się zapisać logu AI do pliku: {e}")


def save_cookies(driver, file_path):
    try:
        with open(file_path, 'wb') as file: pickle.dump(driver.get_cookies(), file)
    except Exception as e: logging.error(f"Nie udało się zapisać ciasteczek: {e}")

def load_cookies(driver, file_path):
    if not os.path.exists(file_path): return False
    try:
        with open(file_path, 'rb') as file:
            cookies = pickle.load(file)
            if not cookies: return False
            driver.get("https://www.facebook.com"); time.sleep(1)
            for cookie in cookies:
                if 'expiry' in cookie: cookie['expiry'] = int(cookie['expiry'])
                driver.add_cookie(cookie)
            driver.refresh()
            return True
    except Exception as e:
        logging.error(f"Nie udało się załadować ciasteczek: {e}")
        return False

def load_processed_post_keys():
    if os.path.exists(PROCESSED_POSTS_FILE):
        with open(PROCESSED_POSTS_FILE, 'rb') as f: return pickle.load(f)
    return set()

def save_processed_post_keys(keys_set):
    with open(PROCESSED_POSTS_FILE, 'wb') as f: pickle.dump(keys_set, f)

def classify_post_with_gemini(model, post_text):
    """
    Analizuje tekst posta, rozpoznaje kategorię, wiele przedmiotów oraz poziom nauczania.
    """
    default_response = {'category': "INNE", 'subject': None, 'level': None}
    if not post_text or len(post_text.strip()) < 10:
        return default_response

    system_instruction = """
Przeanalizuj poniższy tekst posta z Facebooka.
1. Skategoryzuj intencję posta jako SZUKAM, OFERUJE lub INNE.
2. Jeśli intencja to SZUKAM, zidentyfikuj przedmiot(y).
   - Jeśli jest to MATEMATYKA, użyj "MATEMATYKA".
   - Jeśli jest to FIZYKA, użyj "FIZYKA".
   - Jeśli jest to JĘZYK ANGIELSKI, użyj "ANGIELSKI".
   - Jeśli jest to JĘZYK POLSKI, użyj "POLSKI".
   - Jeśli jest to inny, konkretny przedmiot (np. chemia, biologia), użyj "INNY_PRZEDMIOT".
   - Jeśli w poście NIE MA informacji o przedmiocie, użyj "NIEZIDENTYFIKOWANY".
   - Jeśli jest WIELE przedmiotów, zwróć je jako listę, np. ["MATEMATYKA", "FIZYKA"].
3. Jeśli intencja to SZUKAM, określ poziom nauczania.
   - Jeśli mowa o 4 klasie szkoły podstawowej lub niżej, użyj "PODSTAWOWA_1_4".
   - Jeśli mowa o szkole średniej (liceum, technikum, matura), użyj "STANDARD_LICEUM".
   - Jeśli mowa o studiach, użyj "STUDIA".
   - We wszystkich innych przypadkach (np. klasy 5-8 szkoły podstawowej) lub gdy poziom nie jest wspomniany, użyj "STANDARD".

Odpowiedz TYLKO w formacie JSON:
{{
  "category": "SZUKAM" | "OFERUJE" | "INNE",
  "subject": "MATEMATYKA" | "FIZYKA" | "ANGIELSKI" | "POLSKI" | "INNY_PRZEDMIOT" | "NIEZIDENTYFIKOWANY" | ["MATEMATYKA", ...],
  "level": "PODSTAWOWA_1_4" | "STUDIA" | "STANDARD_LICEUM" | "STANDARD" | null
}}
Jeśli kategoria to OFERUJE lub INNE, subject i level zawsze są null.
"""
    full_prompt = [
        Content(role="user", parts=[Part.from_text(system_instruction)]),
        Content(role="model", parts=[Part.from_text("Rozumiem. Będę analizować tekst, zwracając kategorię, przedmiot(y) i poziom nauczania w formacie JSON.")]),
        Content(role="user", parts=[Part.from_text(f"Tekst posta:\n---\n{post_text}\n---")])
    ]
    try:
        response = model.generate_content(full_prompt, generation_config=GENERATION_CONFIG, safety_settings=SAFETY_SETTINGS)
        if not response.candidates:
            logging.error(f"Odpowiedź AI zablokowana. Powód: {response.prompt_feedback}")
            return {'category': "ERROR", 'subject': None, 'level': None}
        raw_text = response.text.strip().replace("```json", "").replace("```", "").strip()
        result = json.loads(raw_text)
        return result
    except Exception as e:
        logging.error(f"Nie udało się sklasyfikować posta: {e}")
        if 'response' in locals() and hasattr(response, 'text'):
             logging.error(f"SUROWA ODPOWIEDŹ PRZY BŁĘDZIE: {response.text}")
        return {'category': "ERROR", 'subject': None, 'level': None}

def try_hide_all_from_user(driver, post_container_element, author_name):
    wait = WebDriverWait(driver, 10)
    print(f"  INFO: Rozpoczynanie sekwencji UKRYWANIA WSZYSTKIEGO od '{author_name}'...")
    try:
        menu_button_xpath = ".//div[@aria-label='Działania dla tego posta'][@role='button']"
        menu_button = post_container_element.find_element(By.XPATH, menu_button_xpath)
        driver.execute_script("arguments[0].click();", menu_button)
        print("    Krok 1/6: Kliknięto menu 'Działania dla tego posta'."); time.sleep(1.5)
        report_button_xpath = "//div[@role='menuitem']//span[text()='Zgłoś post']"
        report_button = wait.until(EC.element_to_be_clickable((By.XPATH, report_button_xpath)))
        driver.execute_script("arguments[0].click();", report_button)
        print("    Krok 2/6: Kliknięto 'Zgłoś post'."); time.sleep(1.5)
        dont_want_to_see_xpath = "//div[@role='dialog']//span[text()='Nie chcę tego widzieć']"
        dont_want_to_see_button = wait.until(EC.element_to_be_clickable((By.XPATH, dont_want_to_see_xpath)))
        driver.execute_script("arguments[0].click();", dont_want_to_see_button)
        print("    Krok 3/6: Kliknięto 'Nie chcę tego widzieć'."); time.sleep(1.5)
        hide_all_xpath = f"//div[@role='dialog']//span[starts-with(text(), 'Ukryj wszystko od')]"
        hide_all_button = wait.until(EC.element_to_be_clickable((By.XPATH, hide_all_xpath)))
        driver.execute_script("arguments[0].click();", hide_all_button)
        print(f"    Krok 4/6: Kliknięto 'Ukryj wszystko od: {author_name}'."); time.sleep(1.5)
        confirm_hide_button_xpath = "//div[@aria-label='Ukryj'][@role='button']"
        confirm_hide_button = wait.until(EC.element_to_be_clickable((By.XPATH, confirm_hide_button_xpath)))
        driver.execute_script("arguments[0].click();", confirm_hide_button)
        print("    Krok 5/6: Potwierdzono 'Ukryj'. Czekam 8 sekund..."); time.sleep(8)
        done_button_xpath = "//div[@role='dialog']//span[text()='Gotowe']"
        done_button = wait.until(EC.element_to_be_clickable((By.XPATH, done_button_xpath)))
        driver.execute_script("arguments[0].click();", done_button)
        print("    Krok 6/6: Kliknięto 'Gotowe'.")
        print(f"  SUKCES: Pomyślnie ukryto wszystkie posty od '{author_name}'.")
        return True
    except (NoSuchElementException, TimeoutException) as e:
        print(f"  BŁĄD: Nie udało się wykonać sekwencji ukrywania. Błąd: {str(e).splitlines()[0]}")
        try:
            driver.find_element(By.TAG_NAME, 'body').send_keys(Keys.ESCAPE)
            time.sleep(0.5)
            driver.find_element(By.TAG_NAME, 'body').send_keys(Keys.ESCAPE) 
        except: pass
        return False
    except Exception as e:
        take_error_screenshot(driver, "search_filter") # <-- WDROŻENIE

        print(f"  KRYTYCZNY BŁĄD w funkcji `try_hide_all_from_user`: {e}"); traceback.print_exc()
        return False

def update_airtable(status_to_update):
    """
    Aktualizuje (inkrementuje) wartość w kolumnie statusu w wierszu z dzisiejszą datą.
    Używa prostego porównania tekstu, zakładając, że kolumna 'Data' w Airtable jest typu 'Single line text'.
    """
    if not AIRTABLE_AVAILABLE:
        print("INFO: Pomijanie aktualizacji Airtable (biblioteka niedostępna).")
        return

    print(f"INFO: [Airtable] Próba aktualizacji statystyk dla statusu: '{status_to_update}'")
    
    try:
        api = Api(AIRTABLE_API_KEY)
        table = api.table(AIRTABLE_BASE_ID, AIRTABLE_TABLE_NAME)
        
        # Krok 1: Definicja formatu (DD.MM.YYYY z kropkami)
        today_str = datetime.now().strftime('%d.%m.%Y') 
        
        # ----------------------------------------------------------------------
        # Krok 2: ZMIANA FORMUŁY NA PROSTE PORÓWNANIE TEKSTU
        # Ta formuła zadziała, jeśli kolumna 'Data' jest typu 'Single line text'.
        formula_filter = f"{{Data}} = '{today_str}'"
        # ----------------------------------------------------------------------
        
        print(f"INFO: [Airtable] Szukanie wiersza za pomocą formuły: {formula_filter}")
        record = table.first(formula=formula_filter)
        
        if record:
            # --- ZNALEZIONO ISTNIEJĄCY WIERSZ ---
            record_id = record['id']
            current_value = record['fields'].get(status_to_update, 0) or 0
            new_value = int(current_value) + 1
            
            table.update(record_id, {status_to_update: new_value})
            print(f"SUKCES: [Airtable] Zaktualizowano '{status_to_update}' na {new_value} dla daty {today_str}.")
        else:
            # --- TWORZENIE NOWEGO WIERSZA ---
            print(f"INFO: [Airtable] Brak wiersza dla daty {today_str}. Tworzenie nowego...")
            
            new_record_data = {'Data': today_str, 'Odrzucone': 0, 'Oczekuję': 0, 'Przesłane': 0}
            new_record_data[status_to_update] = 1 
            
            table.create(new_record_data)
            print(f"SUKCES: [Airtable] Utworzono nowy wiersz dla {today_str} i ustawiono '{status_to_update}' na 1.")

    except Exception as e:
        print(f"BŁĄD: [Airtable] Nie udało się zaktualizować tabeli: {e}")
        traceback.print_exc()

def comment_and_check_status(driver, main_post_container, comment_list):
    """
    Dodaje komentarz, obsługując dwa scenariusze i używając precyzyjnego selektora dla kontenera.
    Następnie sprawdza status komentarza w odpowiednim kontekście.
    """
    wait = WebDriverWait(driver, 10)
    comment_textbox = None
    action_context = None 
    
    try:
        # --- ŚCIEŻKA A: Próba znalezienia i kliknięcia przycisku "Skomentuj" ---
        comment_button_xpath = ".//div[@aria-label='Dodaj komentarz' or @aria-label='Comment'][@role='button']"
        comment_button = main_post_container.find_element(By.XPATH, comment_button_xpath)
        driver.execute_script("arguments[0].click();", comment_button)
        print("    AKCJA: Ścieżka A - Kliknięto przycisk 'Skomentuj'.")
        time.sleep(2)

        # Czekamy na pojawienie się nowego, precyzyjnego kontenera
        
        # --- ZŁOŻONY XPATH ŁĄCZĄCY WSZYSTKIE KLASY I ROLĘ DIALOGU ---
        new_container_xpath = (
            "//div[@role='dialog' and contains(@class, 'x1n2onr6') and contains(@class, 'x1ja2u2z') and "
            "contains(@class, 'x1afcbsf') and contains(@class, 'xdt5ytf') and contains(@class, 'x1a2a7pz') and "
            "contains(@class, 'x71s49j') and contains(@class, 'x1qjc9v5') and contains(@class, 'xazwl86') and "
            "contains(@class, 'x1hl0hii') and contains(@class, 'x1aq6byr') and contains(@class, 'x2k6n7x') and "
            "contains(@class, 'x78zum5') and contains(@class, 'x1plvlek') and contains(@class, 'xryxfnj') and "
            "contains(@class, 'xcatxm7') and contains(@class, 'xrgej4m') and contains(@class, 'xh8yej3')]"
        )
        # --- KONIEC ZŁOŻONEGO XPATHA ---
        
        print("    INFO: Czekam na pojawienie się precyzyjnego kontenera komentarza...")
        action_context = wait.until(EC.visibility_of_element_located((By.XPATH, new_container_xpath)))
        print("    INFO: Ścieżka A - Znaleziono nowy, precyzyjny kontener komentarza.")
        
        # W nowym kontenerze szukamy pola tekstowego
        comment_textbox = action_context.find_element(By.XPATH, ".//div[@role='textbox']")
        
    except (NoSuchElementException, TimeoutException):
        # --- ŚCIEŻKA B: Przycisk nie zadziałał, szukamy pola tekstowego bezpośrednio w głównym kontenerze ---
        print("    INFO: Ścieżka B - Nie udało się znaleźć przycisku 'Skomentuj' lub nowego kontenera. Próba znalezienia pola tekstowego bezpośrednio.")
        action_context = main_post_container  # Kontekstem staje się główny kontener posta
        try:
            direct_textbox_xpath = ".//div[@role='textbox']"
            comment_textbox = action_context.find_element(By.XPATH, direct_textbox_xpath)
            print("    AKCJA: Ścieżka B - Znaleziono pole tekstowe bezpośrednio w kontenerze posta.")
        except NoSuchElementException:
            print("  BŁĄD: Nie znaleziono ani przycisku 'Skomentuj', ani bezpośredniego pola tekstowego. Komentowanie niemożliwe.")
            return None

    # --- WSPÓLNA LOGIKA PO ZNALEZIENIU `comment_textbox` i `action_context` ---
    if comment_textbox and action_context:
        try:
            # Wpisz losowy komentarz i wyślij
            comment_to_write = random.choice(comment_list)
            comment_textbox.send_keys(comment_to_write)
            print(f"    AKCJA: Wpisano komentarz: '{comment_to_write}'.")
            time.sleep(1)

            comment_textbox.send_keys(Keys.RETURN)
            print("    AKCJA: Wysłano komentarz. Czekam 8 sekund na przetworzenie...")
            time.sleep(8)
        except Exception as e:
            take_error_screenshot(driver, "search_filter") # <-- WDROŻENIE
            print(f"  BŁĄD: Wystąpił problem podczas wpisywania lub wysyłania komentarza: {e}")
            return None

    # Obsługa okna "Zasady grupy" (szukamy globalnie)
    try:
        group_rules_span = driver.find_element(By.XPATH, "//span[text()='Zasady grupy']")
        if group_rules_span.is_displayed():
            print("    INFO: Wykryto okno 'Zasady grupy'. Próba kliknięcia 'Rozumiem'.")
            understand_button = driver.find_element(By.XPATH, "//div[@aria-label='Rozumiem'][@role='button']")
            driver.execute_script("arguments[0].click();", understand_button)
            print("    INFO: Kliknięto 'Rozumiem'.")
            time.sleep(1)
    except NoSuchElementException:
        pass 

    # Sprawdź status komentarza W ODPOWIEDNIM KONTEKŚCIE (`action_context`)
    status = "Przesłane"
    try:
        rejected_xpath = ".//span[contains(text(), 'Odrzucono')] | .//div[contains(text(), 'Odrzucono')]"
        action_context.find_element(By.XPATH, rejected_xpath)
        status = "Odrzucone"
        print("    STATUS KOMENTARZA: ODRZUCONO")
    except NoSuchElementException:
        try:
            pending_xpath = ".//span[contains(text(), 'Oczekujący')] | .//div[contains(text(), 'Oczekujący')]"
            action_context.find_element(By.XPATH, pending_xpath)
            status = "Oczekuję"
            print("    STATUS KOMENTARZA: OCZEKUJĄCY")
        except NoSuchElementException:
            print("    STATUS KOMENTARZA: PRZESŁANE (domyślnie)")
    
    return status



# --- Główne Funkcje Logiki ---
def initialize_driver_and_login():
    print("\n--- START SKRYPTU: INICJALIZACJA PRZEGLĄDARKI (TRYB STEALTH) ---")
    driver = None
    try:
        service = ChromeService(executable_path=PATH_DO_RECZNEGO_CHROMEDRIVER)
        options = webdriver.ChromeOptions()
        options.binary_location = PATH_DO_GOOGLE_CHROME
        
        options.add_argument(f"user-agent={random.choice(USER_AGENTS)}")
        options.add_argument(f"window-size={random.choice(WINDOW_SIZES)}")
        options.add_argument("--disable-notifications")
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--disable-blink-features=AutomationControlled')
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option('useAutomationExtension', False)

        driver = webdriver.Chrome(service=service, options=options)
        
        stealth(driver, languages=["pl-PL", "pl"], vendor="Google Inc.", platform="Win32", webgl_vendor="Intel Inc.", renderer="Intel Iris OpenGL Engine", fix_hairline=True)
        print("SUKCES: Przeglądarka uruchomiona w trybie stealth.")
        
        driver.get("https://www.facebook.com")
        
        if not load_cookies(driver, COOKIES_FILE):
            input("!!! PROSZĘ, ZALOGUJ SIĘ RĘCZNIE, a następnie naciśnij ENTER tutaj...")
            save_cookies(driver, COOKIES_FILE)

        wait = WebDriverWait(driver, 15)
        wait.until(EC.presence_of_element_located((By.XPATH, "//input[@aria-label='Szukaj na Facebooku']")))
        print("SUKCES: Sesja przeglądarki jest aktywna i jesteś zalogowany!")
        return driver
    except Exception as e:
        take_error_screenshot(driver, "search_filter") # <-- WDROŻENIE
        logging.critical(f"Błąd krytyczny podczas inicjalizacji: {e}", exc_info=True)
        if driver: driver.quit()
        return None


def search_and_filter(driver):
    print("--- ROZPOCZYNANIE WYSZUKIWANIA I FILTROWANIA ---")
    wait = WebDriverWait(driver, 20)
    try:
        search_xpath = "//input[@aria-label='Szukaj na Facebooku' or @placeholder='Szukaj na Facebooku']"
        search_input = wait.until(EC.element_to_be_clickable((By.XPATH, search_xpath)))
        search_input.click()
        
        human_typing(search_input, "korepetycji")
        random_sleep(1, 2.5)
        search_input.send_keys(Keys.RETURN)
        
        random_sleep(3, 5)
        
        posts_filter_xpath = "//a[@role='link'][.//span[normalize-space(.)='Posty']][not(contains(@href,'/groups/'))]"
        posts_filter_alt_xpath = "//div[@role='list']//div[@role='listitem']//a[@role='link'][.//span[normalize-space(.)='Posty']]"
        try:
            posts_button = wait.until(EC.element_to_be_clickable((By.XPATH, posts_filter_xpath)))
        except TimeoutException:
            posts_button = wait.until(EC.element_to_be_clickable((By.XPATH, posts_filter_alt_xpath)))
        
        posts_button.click()
        random_sleep(2.5, 4)

        checkbox_xpath = "//input[@aria-label='Najnowsze posty'][@type='checkbox']"
        checkbox_element = wait.until(EC.element_to_be_clickable((By.XPATH, checkbox_xpath)))
        driver.execute_script("arguments[0].click();", checkbox_element)
        random_sleep(3, 6)
        print("SUKCES: Wyszukiwanie i filtrowanie zakończone pomyślnie.")
        return True
    except Exception as e:
        take_error_screenshot(driver, "search_filter") # <-- WDROŻENIE
        logging.error(f"Błąd podczas wyszukiwania lub filtrowania: {e}", exc_info=True)
        return False


def process_posts(driver, model):
    print("\n--- ROZPOCZYNANIE PRZETWARZANIA POSTÓW ---")
    processed_keys = load_processed_post_keys()
    
    no_new_posts_in_a_row = 0
    max_stale_scrolls = 5
    LICZBA_RODZICOW_DO_GORY = 5 
    print(f"Używana stała liczba rodziców do znalezienia kontenera: {LICZBA_RODZICOW_DO_GORY}")
    
    # --- System limitowania akcji ---
    action_timestamps = []
    LIMIT_30_MIN = 3
    LIMIT_60_MIN = 5
    
    loop_count = 0
    while True:
        loop_count += 1
        print(f"\n--- Pętla przetwarzania nr {loop_count} ---")
        try:
            # --- Weryfikacja limitów akcji ---
            current_time = time.time()
            action_timestamps = [t for t in action_timestamps if current_time - t < 3600]
            actions_last_30_min = sum(1 for t in action_timestamps if current_time - t < 1800)
            if actions_last_30_min >= LIMIT_30_MIN:
                oldest_in_window = min(t for t in action_timestamps if current_time - t < 1800)
                wait_time = 1800 - (current_time - oldest_in_window) + random.uniform(5, 15)
                print(f"INFO: Osiągnięto limit {LIMIT_30_MIN}/30min. Czekam {int(wait_time)} sekund...")
                time.sleep(wait_time)
                continue
            actions_last_60_min = len(action_timestamps)
            if actions_last_60_min >= LIMIT_60_MIN:
                oldest_in_window = min(action_timestamps)
                wait_time = 3600 - (current_time - oldest_in_window) + random.uniform(5, 15)
                print(f"INFO: Osiągnięto limit {LIMIT_60_MIN}/60min. Czekam {int(wait_time)} sekund...")
                time.sleep(wait_time)
                continue
            print(f"INFO: Stan limitów: {actions_last_30_min}/{LIMIT_30_MIN} (30 min), {actions_last_60_min}/{LIMIT_60_MIN} (60 min).")
            # --- Koniec weryfikacji ---

            story_message_xpath = "//div[@data-ad-rendering-role='story_message']"
            story_elements_on_page = driver.find_elements(By.XPATH, story_message_xpath)
            if not story_elements_on_page:
                print("OSTRZEŻENIE: Nie znaleziono żadnych treści postów. Czekam...")
                random_sleep(8, 12)
                continue

            new_posts_found_this_scroll = 0
            page_refreshed_in_loop = False
            for i, story_element in enumerate(story_elements_on_page):
                try:
                    main_post_container = story_element.find_element(By.XPATH, f"./ancestor::*[{LICZBA_RODZICOW_DO_GORY}]")
                    author_name = "Nieznany"
                    try:
                        author_element = main_post_container.find_element(By.XPATH, ".//strong | .//h3//a | .//h2//a")
                        author_name = author_element.text
                    except NoSuchElementException: pass
                    post_text = story_element.text
                    post_key = f"{author_name}_{post_text[:100]}"
                    if post_key in processed_keys:
                        print(f"--- DUPLIKAT POMINIĘTY ---\n  KLUCZ: {post_key}\n  AUTOR: {author_name}\n  TREŚĆ: {post_text[:80]}...\n--------------------------")
                        continue 
                    new_posts_found_this_scroll += 1
                    print(f"\n[NOWY POST] Analizowanie posta od: {author_name}")
                    classification = classify_post_with_gemini(model, post_text)
                    log_ai_interaction(post_text, classification)
                    category, subject, level = classification.get('category'), classification.get('subject'), classification.get('level')
                    
                    if category == 'SZUKAM':
                        should_comment = False
                        comment_reason = ""
                        comment_list_to_use = COMMENT_TEXTS_STANDARD # Domyślna lista

                        if level in ['PODSTAWOWA_1_4', 'STUDIA']:
                            print(f"INFO: Pomijanie posta. Poziom nauczania ('{level}') jest poza zakresem.")
                        else:
                            if level == 'STANDARD_LICEUM':
                                comment_list_to_use = COMMENT_TEXTS_HIGH_SCHOOL
                                print("INFO: Wybrano listę komentarzy dla szkoły średniej.")
                            
                            if subject == 'MATEMATYKA': should_comment, comment_reason = True, "Znaleziono: MATEMATYKA"
                            elif isinstance(subject, list) and 'MATEMATYKA' in subject: should_comment, comment_reason = True, f"Znaleziono MATEMATYKĘ na liście: {subject}"
                            elif subject == 'NIEZIDENTYFIKOWANY': should_comment, comment_reason = True, "Post 'SZUKAM' bez określonego przedmiotu."
                        
                        if should_comment:
                            print(f"✅✅✅ ZNALEZIONO DOPASOWANIE! Powód: {comment_reason}")
                            comment_status = comment_and_check_status(driver, main_post_container, comment_list_to_use)
                            if comment_status:
                                action_timestamps.append(time.time())
                                update_airtable(comment_status)
                                print("INFO: Odświeżanie strony po dodaniu komentarza...")
                                driver.refresh()
                                random_sleep(4, 7)
                                page_refreshed_in_loop = True
                        elif level not in ['PODSTAWOWA_1_4', 'STUDIA']:
                            print(f"INFO: Pomijanie 'SZUKAM'. Przedmiot(y): {subject} nie pasują.")
                    elif category == 'OFERUJE':
                        if any(keyword in author_name.lower() for keyword in AUTHOR_FILTER_KEYWORDS):
                             print(f"INFO: Pomijam ofertę od źródła ({author_name}).")
                        else:
                            print(f"❌ ZNALEZIONO OFERTĘ. Uruchamianie procedury ukrywania od '{author_name}'...")
                            try_hide_all_from_user(driver, main_post_container, author_name)
                    else:
                        print(f"INFO: Pomijanie posta. Kategoria: {category}, Przedmiot: {subject}, Poziom: {level}")
                    
                    processed_keys.add(post_key)
                    if page_refreshed_in_loop: break
                except (StaleElementReferenceException, NoSuchElementException) as e:
                    logging.warning(f"Element posta stał się nieaktualny. Błąd: {type(e).__name__}")
                    take_error_screenshot(driver, "search_filter") # <-- WDROŻENIE
                    if page_refreshed_in_loop: break
                    continue
                except Exception as e:
                    logging.error(f"Błąd wewnątrz pętli posta: {e}", exc_info=True)
                    take_error_screenshot(driver, "search_filter") # <-- WDROŻENIE
                    if page_refreshed_in_loop: break
                    continue
            
            if page_refreshed_in_loop:
                no_new_posts_in_a_row = 0
                save_processed_post_keys(processed_keys)
                continue
            
            if new_posts_found_this_scroll > 0:
                print(f"INFO: Przeanalizowano {new_posts_found_this_scroll} nowych postów. Zapisuję stan...")
                save_processed_post_keys(processed_keys)
                no_new_posts_in_a_row = 0
            else:
                print("INFO: Brak nowych postów na widocznym ekranie.")
                no_new_posts_in_a_row += 1
            
            if no_new_posts_in_a_row >= max_stale_scrolls:
                print(f"INFO: Brak nowych postów od {max_stale_scrolls} scrollowań. Odświeżam stronę...")
                driver.refresh(); random_sleep(10, 20)
                no_new_posts_in_a_row = 0
            else:
                print("INFO: Scrolluję w dół jak człowiek...")
                human_scroll(driver)
        
        except KeyboardInterrupt:
            break
        except Exception as e:
            take_error_screenshot(driver, "search_filter") # <-- WDROŻENIE
            logging.error(f"Błąd w głównej pętli: {e}", exc_info=True)
            random_sleep(25, 35)


# --- Główny Blok Wykonawczy ---
if __name__ == "__main__":
    import warnings
    warnings.filterwarnings("ignore", category=UserWarning)
    ai_model = None
    try:
        with open('config.json', 'r', encoding='utf-8') as f: config = json.load(f)
        AI_CONFIG = config.get("AI_CONFIG", {})
        PROJECT_ID, LOCATION, MODEL_ID = AI_CONFIG.get("PROJECT_ID"), AI_CONFIG.get("LOCATION"), AI_CONFIG.get("MODEL_ID")
        if not all([PROJECT_ID, LOCATION, MODEL_ID]):
            logging.critical("Brak pełnej konfiguracji AI w pliku config.json"); sys.exit(1)
        vertexai.init(project=PROJECT_ID, location=LOCATION)
        ai_model = GenerativeModel(MODEL_ID)
    except Exception as e:
        logging.critical(f"Nie udało się zainicjalizować modelu AI: {e}", exc_info=True); sys.exit(1)
    driver = None
    try:
        driver = initialize_driver_and_login()
        if driver and ai_model:
            if search_and_filter(driver):
                process_posts(driver, ai_model)
            else:
                logging.critical("Nie udało się wyszukać i przefiltrować. Zamykanie...")
        else:
            logging.critical("Sterownik przeglądarki lub model AI nie został poprawnie zainicjowany.")
    except KeyboardInterrupt:
        print("\nINFO: Przerwano działanie skryptu przez użytkownika (Ctrl-C).")
    except Exception as e:
        logging.critical(f"Krytyczny błąd ogólny: {e}", exc_info=True)
    finally:
        if driver: print("INFO: Zamykanie przeglądarki..."); driver.quit()
        print("INFO: Program zakończył działanie.")

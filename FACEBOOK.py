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

# --- IMPORTY DLA BAZY DANYCH, VERTEX AI I STEALTH ---
# Zamieniono Airtable na SQLite
try:
    from database_stats import update_stats
    DATABASE_AVAILABLE = True
except ImportError:
    DATABASE_AVAILABLE = False
    print("OSTRZEŻENIE: Nie można załadować database_stats.py")

from database import DatabaseTable
from config import FB_PASSWORD

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
from selenium.webdriver.common.action_chains import ActionChains # NOWY IMPORT

# --- KONFIGURACJA LOGOWANIA ---
logging.basicConfig(level=logging.WARNING, format='%(asctime)s - %(levelname)s - %(message)s')

# --- KONFIGURACJA ŚCIEŻEK I AIRTABLE ---
# Ścieżka do przeglądarki (u Ciebie to Chromium)
PATH_DO_GOOGLE_CHROME = '/usr/bin/google-chrome' 

# Ścieżka do sterownika
PATH_DO_RECZNEGO_CHROMEDRIVER = '/usr/local/bin/chromedriver'

# Usunięto konfigurację Airtable - teraz używamy lokalnej bazy SQLite

# --- STAŁE ---
COOKIES_FILE = "anastazja_cookies.json"
PROCESSED_POSTS_FILE = "processed_posts_db.pkl"
AI_LOG_FILE = "ai_analysis_log.txt"
ERROR_SCREENSHOTS_DIR = "debug_logs"
AUTHOR_FILTER_KEYWORDS = ['spotted', 'ogloszenia', 'ogłoszenia', 'korepetycje', 'nauka', 'szkoła', 'centrum', 'instytut', 'grupa', 'group']

# --- ZAKTUALIZOWANE LISTY KOMENTARZY ---
COMMENT_TEXTS_STANDARD = [
    "Polecam @Zakręcone Korepetycje. Cenowo wychodzi super. Wreszcie widać realną poprawę w ocenach.",
    "Bardzo polecam @Zakręcone Korepetycje. Córka mówi, że teraz rozumie 3 razy więcej niż na lekcji w szkole i widać mocną poprawę w ocenach. Mają bardzo dobrą cenę.",
    "Polecam @Zakręcone Korepetycje. Cena super, a do tego bardzo dobra jakość zajęć. Oceny wyższe, niż przed lekcjami, o 2-3 stopnie  (:",
]
COMMENT_TEXTS_HIGH_SCHOOL = [
    "Bardzo polecam @Zakręcone Korepetycje, mój syn napisał podstawę z matmy na 94%. Zajęcia prowadzone w bardzo miłej atmosferze.",
]
# --- Koniec stałych ---

# --- ZMIENNE DO IMITOWANIA CZŁOWIEKA ---
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
]
WINDOW_SIZES = ["1920,1080", "1366,768", "1536,864"]

# --- KONFIGURACJA AI ---
GENERATION_CONFIG = GenerationConfig(temperature=0.7, top_p=0.95, top_k=40, max_output_tokens=1024)
SAFETY_SETTINGS = [
    SafetySetting(category=HarmCategory.HARM_CATEGORY_HARASSMENT, threshold=HarmBlockThreshold.BLOCK_ONLY_HIGH),
    SafetySetting(category=HarmCategory.HARM_CATEGORY_HATE_SPEECH, threshold=HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE),
]

# --- NOWE FUNKCJE POMOCNICZE ---
def handle_final_verification(driver):
    """
    Obsługuje końcowy etap po awaryjnym logowaniu: powrót na FB, akceptacja cookies,
    weryfikacja sukcesu/ekranu 2FA.
    """
    wait = WebDriverWait(driver, 15)
    search_input_xpath = "//input[@aria-label='Szukaj na Facebooku']"
    
    print("\n--- ROZPOCZYNANIE KOŃCOWEJ WERYFIKACJI ---")

    # 1. Wejdź ponownie na stronę główną Facebooka
    driver.get("https://www.facebook.com")
    random_sleep(3, 5)

    # 2. Akceptacja ciasteczek (jeśli są)
    try:
        # XPATH dla przycisku akceptacji ciasteczek na FB (często role=button z konkretnym aria-label)
        cookies_xpath = "//div[@role='button'][@aria-label='Zaakceptuj ciasteczka'] | //button[contains(text(), 'Zaakceptuj')]"
        cookies_button = WebDriverWait(driver, 5).until(EC.element_to_be_clickable((By.XPATH, cookies_xpath)))
        
        human_safe_click(driver, cookies_button, "Zaakceptuj ciasteczka")
        print("INFO: Akceptacja ciasteczek wykonana.")
        random_sleep(2, 3)
        
    except (TimeoutException, NoSuchElementException):
        print("INFO: Nie znaleziono paska akceptacji ciasteczek.")
        pass

    # 3. Sprawdzenie, czy udało się zalogować (Pole Wyszukiwania)
    try:
        wait.until(EC.presence_of_element_located((By.XPATH, search_input_xpath)))
        print("SUKCES: PEŁNE ZALOGOWANIE PO AKCJI AWARYJNEJ.")
        return True # Zalogowanie udane, kontynuujemy skrypt

    except TimeoutException:
        print("OSTRZEŻENIE: Pole wyszukiwania wciąż niewidoczne. Sprawdzam 2FA.")

        # 4. Sprawdzenie ekranu weryfikacji dwuetapowej (2FA)
        try:
            # Szukanie tekstu z obrazka "Sprawdź powiadomienia na innym urządzeniu"
            twofa_text_xpath = "//span[contains(text(), 'Sprawdź powiadomienia na innym urządzeniu')]"
            twofa_screen = WebDriverWait(driver, 5).until(EC.presence_of_element_located((By.XPATH, twofa_text_xpath)))

            if twofa_screen.is_displayed():
                print("--- KRYTYCZNY EKRAN 2FA WYKRYTY ---")
                
                # Zrzut ekranu
                log_error_state(driver, "2FA_SCREENSHOT")
                
                # Kliknięcie "Spróbuj użyć innej metody"
                other_method_xpath = "//span[contains(text(), 'Spróbuj użyć innej metody')]/ancestor::button | //span[contains(text(), 'Spróbuj użyć innej metody')]/ancestor::div[@role='button']"
                other_method_button = driver.find_element(By.XPATH, other_method_xpath)
                
                human_safe_click(driver, other_method_button, "Spróbuj użyć innej metody (2FA)")
                
                print("INFO: Kliknięto 'Spróbuj użyć innej metody'.")
                
                # Zakończenie skryptu
                print("INFO: Wykryto barierę 2FA. Kończę działanie skryptu.")
                return False # Zalogowanie nieudane, zatrzymujemy skrypt

        except (TimeoutException, NoSuchElementException):
            print("INFO: Ekran 2FA nie został wykryty. Brak logowania i brak 2FA.")
            pass

    print("INFO: Koniec końcowej weryfikacji. Wymagane ręczne logowanie.")
    return False # Wymuszenie ręcznego logowania

def log_error_state(driver, location_name="unknown_error"):
    """Zapisuje zrzut ekranu (PNG) i pełny kod źródłowy (HTML) w przypadku błędu."""
    try:
        if not os.path.exists(ERROR_SCREENSHOTS_DIR):
            os.makedirs(ERROR_SCREENSHOTS_DIR)
            
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base_filename = os.path.join(ERROR_SCREENSHOTS_DIR, f"ERROR_{location_name}_{timestamp}")
        
        # 1. Zapis zrzutu ekranu (PNG)
        if driver and hasattr(driver, 'save_screenshot'):
             driver.save_screenshot(f"{base_filename}.png")
             print(f"BŁĄD ZAPISANO: Zrzut ekranu zapisany w: {base_filename}.png")
        
        # 2. Zapis pełnego kodu źródłowego (HTML)
        if driver and hasattr(driver, 'page_source'):
            page_html = driver.page_source
            with open(f"{base_filename}.html", "w", encoding="utf-8") as f:
                f.write(page_html)
            print(f"BŁĄD ZAPISANO: Kod źródłowy HTML zapisany w: {base_filename}.html")
        else:
             print("BŁĄD: Sterownik niedostępny, aby zapisać pełny stan strony.")

    except Exception as e:
        logging.error(f"Krytyczny błąd podczas próby zapisu stanu błędu: {e}")

def random_sleep(min_seconds, max_seconds):
    time.sleep(random.uniform(min_seconds, max_seconds))


# --- NOWA FUNKCJA DLA RUCHU MYSZY ---
def human_move_to_element(driver, target_element):
    """
    Symuluje nieregularny ruch myszy do docelowego elementu.
    Używa ActionChains.
    """
    try:
        target_location = target_element.location
        target_size = target_element.size
        
        # Oblicz docelowy punkt (środek elementu)
        target_x = target_location['x'] + target_size['width'] // 2
        target_y = target_location['y'] + target_size['height'] // 2
        
        actions = ActionChains(driver)
        
        # Tworzenie serii losowych, małych kroków
        # Pobieramy bieżące (przybliżone) współrzędne elementu, aby skrypt wiedział, skąd startuje
        current_x = driver.execute_script("return window.scrollX + arguments[0].getBoundingClientRect().left", target_element)
        current_y = driver.execute_script("return window.scrollY + arguments[0].getBoundingClientRect().top", target_element)

        num_steps = random.randint(5, 10)
        
        # Wykonaj początkowy ruch (np. 50, 50), jeśli kursor jest w nieznanym miejscu
        actions.move_by_offset(random.randint(50, 100), random.randint(50, 100)).perform()
        
        for _ in range(num_steps):
            dx = target_x - current_x
            dy = target_y - current_y

            # Losowe przesunięcie w bieżącym kroku, aby ruch nie był prostą linią
            step_x = dx / num_steps + random.uniform(-10, 10)
            step_y = dy / num_steps + random.uniform(-10, 10)
            
            actions.move_by_offset(int(step_x), int(step_y)).perform()
            current_x += step_x
            current_y += step_y
            random_sleep(0.05, 0.2)
        
        # Ostatni, dokładny ruch do centrum elementu
        actions.move_to_element(target_element).perform()
        print(f"    AKCJA MYSZY: Płynnie przesunięto kursor do elementu.")
        random_sleep(0.5, 1)

    except Exception as e:
        print(f"OSTRZEŻENIE MYSZY: Nie udało się wykonać płynnego ruchu myszy: {e}")
        # Jeśli ruch się nie uda, kontynuujemy bez niego.


# --- NOWA FUNKCJA DLA BEZPIECZNEGO KLIKANIA ---
def human_safe_click(driver, element, action_description="element"):
    """
    Wykonuje płynny ruch myszy, próbuje standardowego kliknięcia Selenium, 
    a w przypadku błędu (np. ElementClickIntercepted) używa JavaScript jako fallback.
    """
    try:
        # 1. Płynny ruch myszy do elementu
        human_move_to_element(driver, element)
        
        # 2. Próba standardowego kliknięcia Selenium (bardziej naturalne)
        element.click()
        print(f"    KLIK: Użyto standardowego kliknięcia dla: {action_description}")

    except (StaleElementReferenceException, Exception) as e:
        # Przechwytywanie wszystkich błędów kliknięcia (np. Intercepted, NotInteractable)
        print(f"    KLIK OSTRZEŻENIE: Standardowe kliknięcie zawiodło dla {action_description}. Powód: {type(e).__name__}. Użycie JavaScript.")
        
        # 3. Kliknięcie przez JavaScript jako awaryjna metoda
        driver.execute_script("arguments[0].click();", element)
        print(f"    KLIK: Użyto kliknięcia JS jako fallback dla: {action_description}")

    random_sleep(0.5, 1.5)


def human_typing_with_tagging(driver, element, text, tag_name="Zakręcone Korepetycje"):
    """
    Symuluje pisanie tekstu, z inteligentnym tagowaniem.
    Poprawnie identyfikuje pełną nazwę do tagowania i kontynuuje od właściwego miejsca.
    """
    wait = WebDriverWait(driver, 5)

    if '@' in text:
        # 1. Dzielimy tekst na część przed i po znaku '@'
        parts = text.split('@', 1)
        before_tag = parts[0]
        after_tag_full = parts[1]

        page_name_to_type = "Zakręcone Korepetycje"
        
        try:
            match = re.search(re.escape(page_name_to_type), after_tag_full, re.IGNORECASE)
            if match:
                text_after_tag = after_tag_full[match.end():]
            else:
                text_after_tag = " ".join(after_tag_full.split(' ')[1:])

        except IndexError:
             text_after_tag = ""


        # --- Sekwencja Pisania ---
        
        # Wpisz tekst przed tagiem
        for char in before_tag:
            element.send_keys(char)
            random_sleep(0.05, 0.15)
        
        # Wpisz znak '@' i zacznij pisać nazwę
        element.send_keys('@')
        random_sleep(0.5, 1)
        
        for char in page_name_to_type:
            element.send_keys(char)
            random_sleep(0.05, 0.15)
        
        random_sleep(1.5, 2.5)

        # Znajdź i kliknij sugestię
        try:
            suggestion_xpath = f"//li[@role='option']//span[contains(text(), '{tag_name}')]"
            suggestion = wait.until(EC.element_to_be_clickable((By.XPATH, suggestion_xpath)))
            
            # Używamy human_safe_click do kliknięcia sugestii
            human_safe_click(driver, suggestion, "Sugestia Tagowania")
            
            print(f"    AKCJA: Wybrano tag dla strony '{tag_name}'.")
            random_sleep(0.5, 1)
        except (NoSuchElementException, TimeoutException):
            print(f"  OSTRZEŻENIE: Nie znaleziono sugestii tagowania. Kontynuuję jako zwykły tekst.")
            element.send_keys(" ")
        
        # Dokończ pisanie reszty komentarza
        for char in text_after_tag:
            element.send_keys(char)
            random_sleep(0.05, 0.15)

    else:
        # Standardowe pisanie
        for char in text:
            element.send_keys(char)
            random_sleep(0.05, 0.15)

def human_typing(element, text):
    for char in text:
        element.send_keys(char)
        random_sleep(0.05, 0.2)

def human_scroll(driver):
    driver.execute_script(f"window.scrollBy(0, {random.randint(400, 800)});")
    random_sleep(1, 3)

def log_ai_interaction(post_text, ai_response):
    try:
        with open(AI_LOG_FILE, 'a', encoding='utf-8') as f:
            f.write("="*80 + "\n")
            f.write(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write("-" * 20 + " TEKST POSTA " + "-" * 20 + "\n")
            f.write(post_text + "\n")
            f.write("-" * 20 + " ODPOWIEDŹ AI " + "-" * 20 + "\n")
            f.write(json.dumps(ai_response, indent=2, ensure_ascii=False) + "\n")
            f.write("="*80 + "\n\n")
    except Exception as e:
        logging.error(f"Nie udało się zapisać logu AI do pliku: {e}")

def save_cookies(driver, file_path):
    try:
        with open(file_path, 'w') as file: json.dump(driver.get_cookies(), file)
    except Exception as e: logging.error(f"Nie udało się zapisać ciasteczek: {e}")

def load_cookies(driver, file_path):
    if not os.path.exists(file_path): return False
    try:
        with open(file_path, 'r') as file:
            cookies = json.load(file)
            if not cookies: return False
            driver.get("https://www.facebook.com"); random_sleep(1, 2)
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
   - Jeśli mowa o 4 klasie szkoły podstawowej lub niżej (np. "klasa 1-3", "czwarta klasa podstawówki"), użyj "PODSTAWOWA_1_4".
   - Jeśli mowa o szkole średniej (liceum, technikum, matura), użyj "STANDARD_LICEUM".
   - Jeśli mowa o studiach (np. "student", "politechnika", "uczelnia"), użyj "STUDIA".
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


def handle_fb_unavailable_error(driver):
    """Sprawdza czy wystąpił błąd 'Strona nie jest dostępna' i odświeża jeśli trzeba."""
    error_keywords = [
        "Ta strona nie jest teraz dostępna",
        "Może to być spowodowane błędem technicznym",
        "Odśwież stronę"
    ]
    
    # Sprawdzamy czy którykolwiek z tekstów jest na stronie
    page_source = driver.page_source
    if any(keyword in page_source for keyword in error_keywords):
        print("⚠️ WYKRYTO: Błąd Facebooka 'Strona niedostępna'. Próbuję naprawić...")
        
        try:
            # Próbujemy kliknąć niebieski przycisk "Odśwież stronę"
            refresh_button_xpath = "//div[@role='button']//span[text()='Odśwież stronę']"
            refresh_button = driver.find_element(By.XPATH, refresh_button_xpath)
            human_safe_click(driver, refresh_button, "Przycisk Odśwież na stronie błędu")
        except:
            # Jeśli przycisk nie zadziała, robimy twarde odświeżenie przeglądarki
            driver.refresh()
            
        random_sleep(5, 8)
        return True
    return False

# --- ZMODYFIKOWANE FUNKCJE GŁÓWNE ---

def _execute_emergency_action(driver):
    """
    Zawiera logikę awaryjną z minimalnym czekaniem (agresywna próba logowania).
    Próby 1, 2 i 3 są wykonywane niemal natychmiast po sobie.
    """
    # Używamy minimalnego czekania na buttony, ale ogólny timeout zostawiamy na 10s
    wait = WebDriverWait(driver, 10) 
    print("\n--- ROZPOCZYNANIE AGRESYWNEJ SEKWENCJI AWARYJNEJ ---")
    
    try:
        # 1. Znajdź i kliknij element "Anastazja Wiśniewska"
        anastazja_xpath = "//span[contains(text(), 'Anastazja Wiśniewska')] | //a[@title='Anastazja Wiśniewska'] | //a[contains(., 'Anastazja Wiśniewska')]"
        anastazja_element = wait.until(EC.element_to_be_clickable((By.XPATH, anastazja_xpath)))
        
        human_safe_click(driver, anastazja_element, "Anastazja Wiśniewska (awaryjnie)")
        
        # Redukujemy opóźnienie po kliknięciu do minimum
        random_sleep(0.5, 1) 
        
        # --- 2. ZLOKALIZUJ POLE Z HASŁEM (TRZY SZYBKIE PRÓBY) ---
        target_field = None
        
        # Skrócony timeout dla wewnętrznych szybkich prób
        wait_short = WebDriverWait(driver, 2) 

        # PRÓBA 1: Input z placeholder='Hasło' i tabindex='0' (Strict)
        password_xpath_strict = "//input[@placeholder='Hasło' and @tabindex='0']"
        try:
            target_field = wait_short.until(EC.element_to_be_clickable((By.XPATH, password_xpath_strict)))
            print("AKCJA AWARYJNA: Znaleziono pole Hasło (Strict).")
        except TimeoutException:
            pass
        
        # PRÓBA 2: Input z placeholder='Hasło' bez tabindex (Loose)
        if target_field is None:
            password_xpath_loose = "//input[@placeholder='Hasło']"
            try:
                target_field = wait_short.until(EC.element_to_be_clickable((By.XPATH, password_xpath_loose)))
                print("AKCJA AWARYJNA: Znaleziono pole Hasło (Loose).")
            except TimeoutException:
                pass
        
        # PRÓBA 3: FALLBACK NA OSTATNI INPUT Z TYPE='PASSWORD'
        if target_field is None:
            password_xpath_final_input = "//input[@type='password']"
            try:
                # Używamy find_elements, aby pobrać wszystkie pasujące bez czekania
                password_inputs = driver.find_elements(By.XPATH, password_xpath_final_input)
                
                if password_inputs:
                    target_field = password_inputs[-1] 
                    # Sprawdzenie, czy element jest widoczny, bo find_elements nie sprawdza widoczności
                    if target_field.is_displayed() and target_field.is_enabled():
                        print("AKCJA AWARYJNA: Wybrano ostatni Input type='password' (Fallback).")
                    else:
                        # Jeśli ostatni jest ukryty, to jest to problem
                        target_field = None 
                        raise NoSuchElementException 
                else:
                    raise NoSuchElementException 
            except NoSuchElementException:
                pass
            except Exception as e:
                 # Inny błąd podczas sprawdzania widoczności
                 print(f"OSTRZEŻENIE: Błąd podczas sprawdzania widoczności Fallback Inputa: {e}")
                 pass
        
        # --- WERYFIKACJA KOŃCOWA ---
        
        if not target_field:
             raise NoSuchElementException("Nie udało się znaleźć pola docelowego po wszystkich szybkich próbach.")

        # 3. Ruch myszy przed wpisaniem
        human_move_to_element(driver, target_field)

        # 4. Wyczyść pole i wpisz tekst: nikotyna
        target_field.clear()
        human_typing(target_field, FB_PASSWORD)
        print("AKCJA AWARYJNA: Wpisano hasło.")

        # 5. Naciśnij Enter
        target_field.send_keys(Keys.ENTER)
        print("AKCJA AWARYJNA: Naciśnięto Enter.")
        
        random_sleep(0.5, 1) # Minimalne czekanie po Enter
        
    except (TimeoutException, NoSuchElementException):
        print("OSTRZEŻENIE AWARYJNE: Nie znaleziono kluczowych elementów po agresywnych próbach. Koniec akcji awaryjnej.")
    except Exception as e:
        print(f"BŁĄD W BLOKU SEKWENCJI AWARYJNEJ: Message: {str(e).splitlines()[0]}")
        log_error_state(driver, "emergency_action_failed")
    
    print("--- KONIEC AGRESYWNEJ SEKWENCJI AWARYJNEJ ---")
    


def initialize_driver_and_login():
    print("\n--- START SKRYPTU: INICJALIZACJA PRZEGLĄDARKI (TRYB STEALTH) ---")
    driver = None
    try:
        # --- Krok 1: Inicjalizacja sterownika ---
        service = ChromeService(executable_path=PATH_DO_RECZNEGO_CHROMEDRIVER)
        options = webdriver.ChromeOptions()
        options.binary_location = PATH_DO_GOOGLE_CHROME
        options.add_argument("--headless=new") 
        options.add_argument(f"user-agent={random.choice(USER_AGENTS)}")
        options.add_argument(f"window-size={random.choice(WINDOW_SIZES)}")
        options.add_argument("--disable-notifications")
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--disable-blink-features=AutomationControlled')
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option('useAutomationExtension', False)

        try:
            driver = webdriver.Chrome(service=service, options=options)
            
            stealth(driver, languages=["pl-PL", "pl"], vendor="Google Inc.", platform="Win32", webgl_vendor="Intel Inc.", renderer="Intel Iris OpenGL Engine", fix_hairline=True)
            print("SUKCES: Przeglądarka uruchomiona w trybie stealth.")
        except Exception as e:
            print(f"BŁĄD: Nie udało się uruchomić Chrome lub ChromeDriver: {e}")
            print("Upewnij się, że Chrome i ChromeDriver są zainstalowane i ścieżki są poprawne.")
            exit(1)
        
        driver.get("https://www.facebook.com")
        
        # --- Krok 2: Próba ładowania ciasteczek ---
        cookies_loaded_successfully = load_cookies(driver, COOKIES_FILE)
        
        if not cookies_loaded_successfully:
            print("INFO: Nie udało się załadować ciasteczek.")
            
            _execute_emergency_action(driver)
            
            # Po nieudanej akcji awaryjnej, przechodzimy do weryfikacji
            if handle_final_verification(driver):
                return driver # Udało się zalogować po awaryjnej akcji
                
            # Jeśli weryfikacja zawiodła (2FA lub wciąż brak logowania)
            raise KeyboardInterrupt("Wymagane ręczne logowanie lub wykryto barierę 2FA.")

        # --- Krok 3: Weryfikacja zalogowania po udanym załadowaniu cookies ---
        wait = WebDriverWait(driver, 15)
        search_input_xpath = "//input[@aria-label='Szukaj na Facebooku']"
        
        try:
            wait.until(EC.presence_of_element_located((By.XPATH, search_input_xpath)))
            print("SUKCES: Sesja przeglądarki jest aktywna i jesteś zalogowany!")
            return driver
            
        except TimeoutException:
            print("OSTRZEŻENIE: Ciasteczka załadowane, ale nie znaleziono pola wyszukiwania (brak pełnego zalogowania).")
            
            # --- Obsługa BŁĘDU SESJI (np. "Invalid Request") ---
            wait_quick = WebDriverWait(driver, 3) 
            
            try:
                ok_button_xpath = "//div[@role='dialog']//span[text()='OK']/ancestor::div[@role='button']"
                ok_button = wait_quick.until(EC.element_to_be_clickable((By.XPATH, ok_button_xpath)))
                
                human_safe_click(driver, ok_button, "Przycisk 'OK' (błąd sesji)")
                
                print("INFO: Kliknięto 'OK' w oknie błędu sesji. Czekam chwilę i przechodzę do akcji awaryjnej.")
                random_sleep(1, 2)
                
            except (TimeoutException, NoSuchElementException):
                print("INFO: Błąd modalny 'Invalid Request' nie został wykryty.")
            
            # --- Uruchomienie AGRESYWNEJ AKCJI AWARYJNEJ ---
            _execute_emergency_action(driver)
            
            # --- Przejście do OSTATECZNEJ WERYFIKACJI ---
            if handle_final_verification(driver):
                return driver 
            
            # Jeśli weryfikacja zawiodła (2FA lub wciąż brak logowania)
            raise KeyboardInterrupt("Wykryto barierę 2FA lub wymagane ręczne logowanie.")


    except KeyboardInterrupt as e:
        # Obsługa przerwania rzuconego z powodu 2FA lub konieczności ręcznego logowania
        print(f"\nINFO: Przerwano działanie: {e}")
        # W tym miejscu chcemy, aby program zamknął driver w bloku finally
        return None 
        
    except Exception as e:
        logging.critical(f"Błąd krytyczny podczas inicjalizacji: {e}", exc_info=True)
        if driver:
            log_error_state(driver, "initialization_failed")
            driver.quit()
        return None


def search_and_filter(driver):
    print("--- ROZPOCZYNANIE WYSZUKIWANIA I FILTROWANIA ---")
    wait = WebDriverWait(driver, 20)
    
    max_retries = 3
    for attempt in range(max_retries):
        try:
            # NAJPIERW: Sprawdź czy FB nie wywalił błędu ze zdjęcia
            if handle_fb_unavailable_error(driver):
                print(f"INFO: Wykryto błąd niedostępności, próba {attempt + 1}/{max_retries}")
                # Po odświeżeniu sprawdzamy jeszcze raz czy jesteśmy na głównej
                if attempt == max_retries - 1:
                    driver.get("https://www.facebook.com")
                    random_sleep(5, 7)

            search_xpath = "//input[@aria-label='Szukaj na Facebooku' or @placeholder='Szukaj na Facebooku']"
            
            # Czekamy krótko na pole wyszukiwania
            try:
                search_input = WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.XPATH, search_xpath)))
            except TimeoutException:
                # Jeśli nie ma pola wyszukiwania, być może nadal jest błąd "Strona niedostępna"
                if handle_fb_unavailable_error(driver):
                    continue # Spróbuj pętlę od nowa
                raise # Jeśli to inny błąd, rzuć wyjątek wyżej

            # --- RUCH MYSZY I WPISYWANIE ---
            human_move_to_element(driver, search_input)
            search_input.click()
            
            # Czyścimy pole (na wypadek gdyby coś tam było)
            search_input.send_keys(Keys.CONTROL + "a")
            search_input.send_keys(Keys.BACKSPACE)
            
            human_typing(search_input, "korepetycji")
            random_sleep(1, 2.5)
            search_input.send_keys(Keys.RETURN)
            
            random_sleep(4, 6)
            
            # FILTROWANIE: Posty
            posts_filter_xpath = "//a[@role='link'][.//span[normalize-space(.)='Posty']][not(contains(@href,'/groups/'))]"
            posts_button = wait.until(EC.element_to_be_clickable((By.XPATH, posts_filter_xpath)))
            human_safe_click(driver, posts_button, "'Posty' (filtr)")
            
            random_sleep(3, 5)

            # FILTROWANIE: Najnowsze posty
            checkbox_xpath = "//input[@aria-label='Najnowsze posty'][@type='checkbox']"
            checkbox_element = wait.until(EC.element_to_be_clickable((By.XPATH, checkbox_xpath)))
            human_safe_click(driver, checkbox_element, "'Najnowsze posty' (checkbox)")
            
            random_sleep(3, 6)
            print("SUKCES: Wyszukiwanie i filtrowanie zakończone pomyślnie.")
            return True

        except Exception as e:
            print(f"OSTRZEŻENIE: Próba {attempt + 1} nieudana: {str(e).splitlines()[0]}")
            if attempt < max_retries - 1:
                driver.refresh()
                random_sleep(5, 10)
            else:
                logging.error(f"Błąd podczas wyszukiwania po {max_retries} próbach.")
                return False

def try_hide_all_from_user(driver, post_container_element, author_name):
    wait = WebDriverWait(driver, 10)
    print(f"  INFO: Rozpoczynanie sekwencji UKRYWANIA WSZYSTKIEGO od '{author_name}'...")
    try:
        menu_button_xpath = ".//div[@aria-label='Działania dla tego posta'][@role='button']"
        menu_button = post_container_element.find_element(By.XPATH, menu_button_xpath)
        
        # --- ZASTĄPIENIE RUCHU + KLIKNIĘCIA JS ---
        human_safe_click(driver, menu_button, "Menu posta (...)")
        print("    Krok 1/6: Kliknięto menu 'Działania dla tego posta'."); random_sleep(1.2, 1.8)
        
        report_button_xpath = "//div[@role='menuitem']//span[text()='Zgłoś post']"
        report_button = wait.until(EC.element_to_be_clickable((By.XPATH, report_button_xpath)))
        
        # --- ZASTĄPIENIE RUCHU + KLIKNIĘCIA JS ---
        human_safe_click(driver, report_button, "'Zgłoś post'")
        print("    Krok 2/6: Kliknięto 'Zgłoś post'."); random_sleep(1.2, 1.8)
        
        dont_want_to_see_xpath = "//div[@role='dialog']//span[text()='Nie chcę tego widzieć']"
        dont_want_to_see_button = wait.until(EC.element_to_be_clickable((By.XPATH, dont_want_to_see_xpath)))
        
        # --- ZASTĄPIENIE RUCHU + KLIKNIĘCIA JS ---
        human_safe_click(driver, dont_want_to_see_button, "'Nie chcę tego widzieć'")
        print("    Krok 3/6: Kliknięto 'Nie chcę tego widzieć'."); random_sleep(1.2, 1.8)
        
        hide_all_xpath = f"//div[@role='dialog']//span[starts-with(text(), 'Ukryj wszystko od')]"
        hide_all_button = wait.until(EC.element_to_be_clickable((By.XPATH, hide_all_xpath)))
        
        # --- ZASTĄPIENIE RUCHU + KLIKNIĘCIA JS ---
        human_safe_click(driver, hide_all_button, "'Ukryj wszystko'")
        print(f"    Krok 4/6: Kliknięto 'Ukryj wszystko od: {author_name}'."); random_sleep(1.2, 1.8)
        
        confirm_hide_button_xpath = "//div[@aria-label='Ukryj'][@role='button']"
        confirm_hide_button = wait.until(EC.element_to_be_clickable((By.XPATH, confirm_hide_button_xpath)))
        
        # --- ZASTĄPIENIE RUCHU + KLIKNIĘCIA JS ---
        human_safe_click(driver, confirm_hide_button, "'Potwierdź Ukryj'")
        print("    Krok 5/6: Potwierdzono 'Ukryj'. Czekam..."); random_sleep(7, 9)
        
        done_button_xpath = "//div[@role='dialog']//span[text()='Gotowe']"
        done_button = wait.until(EC.element_to_be_clickable((By.XPATH, done_button_xpath)))
        
        # --- ZASTĄPIENIE RUCHU + KLIKNIĘCIA JS ---
        human_safe_click(driver, done_button, "'Gotowe'")
        print("    Krok 6/6: Kliknięto 'Gotowe'.")
        print(f"  SUKCES: Pomyślnie ukryto wszystkie posty od '{author_name}'.")
        return True
    except (NoSuchElementException, TimeoutException) as e:
        print(f"  BŁĄD: Menu ukrywania zacięło się. Próbuję uciec klawiszem ESC...")
        
        # Próba 1: Naciśnij ESC 3 razy, żeby zamknąć wszelkie modale
        try:
            body = driver.find_element(By.TAG_NAME, 'body')
            for _ in range(3):
                body.send_keys(Keys.ESCAPE)
                random_sleep(0.5, 0.8)
            
            # Krótki test: Czy po ESC nadal widać jakiś dialog/nakładkę?
            # Szukamy czy na ekranie jest jakiś widoczny element o roli 'dialog'
            dialogs = driver.find_elements(By.XPATH, "//div[@role='dialog']")
            if any(d.is_displayed() for d in dialogs):
                print("  INFO: ESC nie pomogło, modale nadal wiszą. Odświeżam stronę...")
                driver.refresh()
                random_sleep(5, 8)
            else:
                print("  SUKCES: ESC zamknęło menu. Próbuję kontynuować...")
        except:
            # Jeśli nawet znalezienie 'body' padło, to znaczy że strona całkiem wisi
            driver.refresh()
            random_sleep(5, 8)
            
        return False
    except Exception as e:
        print(f"  KRYTYCZNY BŁĄD w funkcji `try_hide_all_from_user`: {e}"); traceback.print_exc()
        log_error_state(driver, "hide_sequence_fatal")
        return False

def update_database_stats(status_to_update):
    """Aktualizuje statystyki w lokalnej bazie danych SQLite."""
    if not DATABASE_AVAILABLE: 
        print("OSTRZEŻENIE: Baza danych niedostępna, pomijam aktualizację statystyk.")
        return
    print(f"INFO: [DB] Próba aktualizacji statystyk dla statusu: '{status_to_update}'")
    try:
        update_stats(status_to_update)
    except Exception as e:
        print(f"BŁĄD: [DB] Nie udało się zaktualizować statystyk: {e}")
        traceback.print_exc()


def comment_and_check_status(driver, main_post_container, comment_list):
    wait = WebDriverWait(driver, 10)
    comment_textbox, action_context = None, None
    
    try:
        comment_button_xpath = ".//div[@aria-label='Dodaj komentarz' or @aria-label='Comment'][@role='button']"
        comment_button = main_post_container.find_element(By.XPATH, comment_button_xpath)
        
        # --- ZASTĄPIENIE RUCHU + KLIKNIĘCIA JS ---
        human_safe_click(driver, comment_button, "'Dodaj komentarz'")
        
        print("    AKCJA: Ścieżka A - Kliknięto 'Skomentuj'."); random_sleep(1.5, 2.5)
        
        new_container_xpath = (
            "//div[@role='dialog' and contains(@class, 'x1n2onr6') and contains(@class, 'x1ja2u2z') and "
            "contains(@class, 'x1afcbsf') and contains(@class, 'xdt5ytf') and contains(@class, 'x1a2a7pz') and "
            "contains(@class, 'x71s49j') and contains(@class, 'x1qjc9v5') and contains(@class, 'xazwl86') and "
            "contains(@class, 'x1hl0hii') and contains(@class, 'x1aq6byr') and contains(@class, 'x2k6n7x') and "
            "contains(@class, 'x78zum5') and contains(@class, 'x1plvlek') and contains(@class, 'xryxfnj') and "
            "contains(@class, 'xcatxm7') and contains(@class, 'xrgej4m') and contains(@class, 'xh8yej3')]"
        )
        action_context = wait.until(EC.visibility_of_element_located((By.XPATH, new_container_xpath)))
        comment_textbox = action_context.find_element(By.XPATH, ".//div[@role='textbox']")
        
    except (NoSuchElementException, TimeoutException):
        print("    INFO: Ścieżka B - Próba znalezienia pola tekstowego bezpośrednio.")
        action_context = main_post_container
        try:
            direct_textbox_xpath = ".//div[@role='textbox']"
            comment_textbox = action_context.find_element(By.XPATH, direct_textbox_xpath)
        except NoSuchElementException:
            print("  BŁĄD: Nie znaleziono ani przycisku 'Skomentuj', ani bezpośredniego pola tekstowego.")
            log_error_state(driver, "comment_field_not_found")
            return None
    
    if comment_textbox and action_context:
        try:
            # --- RUCH MYSZY: Przed wpisaniem tekstu do pola komentarza ---
            human_move_to_element(driver, comment_textbox)
            
            comment_to_write = random.choice(comment_list)
            human_typing_with_tagging(driver, comment_textbox, comment_to_write, tag_name="Zakręcone Korepetycje - Matematyka")
            random_sleep(1, 2)
            comment_textbox.send_keys(Keys.RETURN)
            print("    AKCJA: Wysłano komentarz. Czekam..."); random_sleep(7, 9)
        except Exception as e:
            print(f"  BŁĄD: Problem podczas wpisywania/wysyłania komentarza: {e}")
            log_error_state(driver, "comment_send_failed")
            return None
    
    try:
        group_rules_span = driver.find_element(By.XPATH, "//span[text()='Zasady grupy']")
        if group_rules_span.is_displayed():
            understand_button = driver.find_element(By.XPATH, "//div[@aria-label='Rozumiem'][@role='button']")
            
            # --- ZASTĄPIENIE RUCHU + KLIKNIĘCIA JS ---
            human_safe_click(driver, understand_button, "'Rozumiem' (zasady)")
            
            random_sleep(1, 1.5)
    except NoSuchElementException: 
        pass
    
    # ... (logika sprawdzania statusu) ...

    status = "Przeslane"
    wait_short = WebDriverWait(driver, 3)
    
    try:
        rejected_xpath = "//span[contains(text(), 'Odrzucono')] | //div[contains(text(), 'Odrzucono')]"
        wait_short.until(EC.presence_of_element_located((By.XPATH, rejected_xpath)))
        status = "Odrzucone"
        
        if status in ["Odrzucone", "Oczekuję"]:
            log_error_state(driver, f"moderacja_status_{status.lower()}")
            
    except TimeoutException:
        try:
            pending_xpath = "//span[contains(text(), 'Oczekujący')] | //div[contains(text(), 'Oczekujący')]"
            wait_short.until(EC.presence_of_element_located((By.XPATH, pending_xpath)))
            status = "Oczekuję"
            
            if status in ["Odrzucone", "Oczekuję"]:
                log_error_state(driver, f"moderacja_status_{status.lower()}")
                
        except TimeoutException: 
            pass
    
    print(f"    STATUS KOMENTARZA: {status.upper()}")
    
    # Aktualizuj statystyki jeśli komentarz przesłany
    if status == "Przeslane" and DATABASE_AVAILABLE:
        # Przekazujemy tylko nazwę kolumny, którą chcemy zwiększyć o 1
        update_stats("Przeslane")
    
    return status

# ... (Funkcja process_posts i blok __main__ pozostają bez zmian) ...

def process_posts(driver, model):
    print("\n--- ROZPOCZYNANIE PRZETWARZANIA POSTÓW ---")
    processed_keys = load_processed_post_keys()
    
    no_new_posts_in_a_row = 0
    consecutive_empty_scans = 0  # NOWY LICZNIK: Puste skany pod rząd
    max_stale_scrolls = 50
    LICZBA_RODZICOW_DO_GORY = 5
    print(f"Używana stała liczba rodziców do znalezienia kontenera: {LICZBA_RODZICOW_DO_GORY}")
    
    # --- System limitowania akcji ---
    action_timestamps = []
    LIMIT_30_MIN = 10
    LIMIT_60_MIN = 20
    # --------------------------------
    
    # --- System liczenia błędów dla twardego resetu ---
    consecutive_errors = 0
    MAX_CONSECUTIVE_ERRORS = 3
    # ---------------------------------
    
    loop_count = 0
    while True:
        loop_count += 1
        print(f"\n--- Pętla przetwarzania nr {loop_count} ---")
        try:
            # --- 1. SPRAWDZENIE POPRAWNOŚCI LINKU (NOWE) ---
            current_url = driver.current_url.lower()
            # Sprawdzamy czy jesteśmy w wyszukiwarce (facebook.com/search...) i czy fraza to korepetycje
            # Jeśli link nie zawiera 'search' ani 'korepetycji', zakładamy, że bot się zgubił (np. wszedł na profil)
            if "search" not in current_url and "korepetycji" not in current_url:
                print(f"⚠️ OSTRZEŻENIE: Wykryto nieprawidłowy URL: {driver.current_url}")
                print("INFO: Bot zgubił ścieżkę. Powrót do strony głównej i ponowne wyszukiwanie...")
                
                driver.get("https://www.facebook.com")
                random_sleep(3, 5)
                
                if search_and_filter(driver):
                    print("SUKCES: Przywrócono widok wyszukiwania.")
                    consecutive_empty_scans = 0 # Reset liczników po powrocie
                    no_new_posts_in_a_row = 0
                else:
                    print("BŁĄD: Nie udało się przywrócić wyszukiwania. Czekam 30s...")
                    random_sleep(30, 31)
                continue # Przejdź do nowej iteracji pętli
            # -----------------------------------------------

            # --- WERYFIKACJA LIMITÓW AKCJI ---
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
            # --- Koniec weryfikacji limitów ---

            story_message_xpath = "//div[@data-ad-rendering-role='story_message']"
            story_elements_on_page = driver.find_elements(By.XPATH, story_message_xpath)
            
            # --- 2. OBSŁUGA BRAKU POSTÓW (NOWE) ---
            if not story_elements_on_page:
                consecutive_empty_scans += 1
                print(f"OSTRZEŻENIE: Nie znaleziono żadnych treści postów. Próba {consecutive_empty_scans}/3.")
                
                if consecutive_empty_scans >= 3:
                    print("⚠️ ALARM: 3 razy pod rząd brak postów. Odświeżam stronę...")
                    driver.refresh()
                    random_sleep(10, 15)
                    consecutive_empty_scans = 0 # Reset licznika po odświeżeniu
                else:
                    random_sleep(8, 12)
                
                continue # Wracamy na początek pętli
            else:
                consecutive_empty_scans = 0 # Zresetuj licznik, jeśli znaleziono posty
            # --------------------------------------

            new_posts_found_this_scroll = 0
            page_refreshed_in_loop = False
            for i, story_element in enumerate(story_elements_on_page):
                try:
                    # Krok 1: Znajdź główny kontener nadrzędny
                    main_post_container = story_element.find_element(By.XPATH, f"./ancestor::*[{LICZBA_RODZICOW_DO_GORY}]")
                    
                    # Krok 2: Ekstrakcja autora i treści
                    author_name = "Nieznany"
                    try:
                        author_element = main_post_container.find_element(By.XPATH, ".//strong | .//h3//a | .//h2//a")
                        author_name = author_element.text
                    except NoSuchElementException: pass
                    post_text = story_element.text
                    post_key = f"{author_name}_{post_text[:100]}"

                    # Sprawdzanie duplikatów
                    if post_key in processed_keys:
                        # Opcjonalnie: print(f"--- DUPLIKAT ---")
                        continue
                        
                    # Sprawdzanie liczby komentarzy (>= 10)
                    try:
                        comment_count_span_xpath = ".//span[contains(text(), 'komentarz') and not(contains(text(), 'Wyświetl więcej'))]"
                        comment_span = main_post_container.find_element(By.XPATH, comment_count_span_xpath)
                        match = re.search(r'(\d+)', comment_span.text)
                        if match and int(match.group(1)) >= 10:
                            print(f"INFO: Pomijanie posta. Liczba komentarzy ({int(match.group(1))}) jest >= 10.")
                            processed_keys.add(post_key)
                            continue
                    except NoSuchElementException: pass

                    new_posts_found_this_scroll += 1
                    
                    # Krok 4: Klasyfikacja AI i Logowanie
                    print(f"\n[NOWY POST] Analizowanie posta od: {author_name}")
                    classification = classify_post_with_gemini(model, post_text)
                    log_ai_interaction(post_text, classification)
                    category, subject, level = classification.get('category'), classification.get('subject'), classification.get('level')
                    
                    if category == 'SZUKAM':
                        should_comment, comment_reason, comment_list_to_use = False, "", COMMENT_TEXTS_STANDARD
                        
                        if level in ['PODSTAWOWA_1_4', 'STUDIA']:
                            print(f"INFO: Pomijanie posta. Poziom nauczania ('{level}') jest poza zakresem.")
                        else:
                            if level == 'STANDARD_LICEUM': comment_list_to_use = COMMENT_TEXTS_HIGH_SCHOOL
                            if subject == 'MATEMATYKA': should_comment, comment_reason = True, "Znaleziono: MATEMATYKA"
                            elif isinstance(subject, list) and 'MATEMATYKA' in subject: should_comment, comment_reason = True, f"Znaleziono MATEMATYKĘ na liście: {subject}"
                            elif subject == 'NIEZIDENTYFIKOWANY': should_comment, comment_reason = True, "Post 'SZUKAM' bez określonego przedmiotu."
                        
                        if should_comment:
                            print(f"✅✅✅ ZNALEZIONO DOPASOWANIE! Powód: {comment_reason}")
                            comment_status = comment_and_check_status(driver, main_post_container, comment_list_to_use)
                            if comment_status:
                                action_timestamps.append(time.time())
                                update_database_stats(comment_status)
                                print("INFO: Odświeżanie strony po dodaniu komentarza...")
                                driver.refresh(); random_sleep(4, 7)
                                page_refreshed_in_loop = True
                        elif level not in ['PODSTAWOWA_1_4', 'STUDIA']:
                            print(f"INFO: Pomijanie 'SZUKAM'. Przedmiot(y): {subject} nie pasują.")
                    
                    elif category == 'OFERUJE':
                        print(f"❌ ZNALEZIONO OFERTĘ. Próba ukrycia od '{author_name}'...")
                        success = try_hide_all_from_user(driver, main_post_container, author_name)
                        
                        if not success:
                            print("  INFO: Problemy z menu. Przywracam stronę główną i filtry...")
                            if search_and_filter(driver):
                                page_refreshed_in_loop = True
                                break
                            else:
                                raise Exception("Nie udało się przywrócić filtrów po błędzie ukrywania")
                    
                    else:
                        print(f"INFO: Pomijanie posta. Kategoria: {category}, Przedmiot: {subject}, Poziom: {level}")
                    
                    processed_keys.add(post_key)
                    if page_refreshed_in_loop: break
                
                except (StaleElementReferenceException, NoSuchElementException):
                    if page_refreshed_in_loop: break
                    continue
                except Exception as e:
                    logging.error(f"Błąd wewnątrz pętli posta: {e}", exc_info=True)
                    log_error_state(driver, "post_critical_inner")
                    if page_refreshed_in_loop: break
                    continue
            
            if page_refreshed_in_loop:
                print("INFO: Strona została odświeżona, rozpoczynam nową pętlę przetwarzania.")
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
            consecutive_errors += 1
            logging.critical(f"KRYTYCZNY BŁĄD W GŁÓWNEJ PĘTLI. PRÓBA ODZYSKANIA: {e}", exc_info=True)
            log_error_state(driver, "process_loop_fatal")
            print(f"INFO: Wykryto błąd ({consecutive_errors}/{MAX_CONSECUTIVE_ERRORS}). Czekam 30 sekund na stabilizację/zapis logów przed resetem...")
            time.sleep(30)
            
            if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                print(f"\n⚠️ UWAGA: {consecutive_errors} błędów pod rząd! Wykonuję PEŁNY TWARDY RESET...\n")
                try:
                    print("INFO: TWARDY RESET - Zamykam i reinicjalizuję przeglądarkę...")
                    if driver:
                        try: driver.quit()
                        except: pass
                    
                    random_sleep(5, 10)
                    
                    driver = initialize_driver_and_login()
                    if driver:
                        if search_and_filter(driver):
                            print("SUKCES: Twardy reset zakończony. Wznawiam skanowanie...")
                            consecutive_errors = 0
                            no_new_posts_in_a_row = 0
                        else:
                            print("BŁĄD: Ponowne wyszukiwanie po twardym resecie zawiodło.")
                            random_sleep(15, 25)
                    else:
                        print("BŁĄD: Nie udało się reinicjalizować przeglądarki.")
                        break
                except Exception as hard_reset_e:
                    logging.critical(f"BŁĄD: Twardy reset przeglądarki zawiódł! {hard_reset_e}.")
                    print("KRYTYCZNY BŁĄD: Twardy reset nie powiódł się. Kończę program.")
                    break
            else:
                try:
                    print("INFO: MIĘKKI RESET - Wracam na stronę główną i ponawiam wyszukiwanie...")
                    driver.get("https://www.facebook.com")
                    random_sleep(3, 5)
                    
                    if search_and_filter(driver):
                        print("SUKCES: Ponowne wyszukiwanie zakończone. Kontynuuję skanowanie.")
                        no_new_posts_in_a_row = 0
                    else:
                        print("BŁĄD: Ponowne wyszukiwanie zawiodło. Próbuję jeszcze raz za chwilę...")
                        random_sleep(15, 25)
                except Exception as reset_e:
                    logging.critical(f"BŁĄD: Miękki reset zawiódł! {reset_e}. Kontynuuję oczekiwanie.")
                    random_sleep(25, 35)
            # --- KONIEC ODZYSKIWANIA ---

# --- Główny Blok Wykonawczy ---
if __name__ == "__main__":
    print("DEBUG: Start skryptu - sekcja main")
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
        print("DEBUG: Vertex AI gotowe.")
        
    except Exception as e:
        logging.critical(f"Nie udało się zainicjalizować modelu AI: {e}", exc_info=True); sys.exit(1)
    
    driver = None
    retry_search_count = 0  # Licznik prób wyszukiwania

    while True: # Główna pętla utrzymująca skrypt przy życiu
        try:
            if not driver:
                print("DEBUG: Inicjalizacja nowej sesji przeglądarki...")
                driver = initialize_driver_and_login()

            if driver and ai_model:
                print("DEBUG: Próba uruchomienia wyszukiwania i filtrów...")
                
                if search_and_filter(driver):
                    print("SUKCES: Filtry ustawione. Rozpoczynam proces procesowania postów.")
                    retry_search_count = 0 # Reset licznika po sukcesie
                    process_posts(driver, ai_model)
                else:
                    # --- OBSŁUGA BŁĘDU search_and_filter ---
                    retry_search_count += 1
                    print(f"OSTRZEŻENIE: search_and_filter nie powiodło się (próba {retry_search_count}/3).")
                    
                    if retry_search_count >= 3:
                        print("⚠️ ALARM: Wielokrotny błąd wyszukiwania. Wykonuję TWARDY RESET...")
                        if driver: driver.quit()
                        driver = None # To wymusi nową inicjalizację w następnym obiegu while
                        random_sleep(10, 20)
                    else:
                        print("INFO: Próbuję odświeżyć stronę i ponowić wyszukiwanie...")
                        driver.refresh()
                        random_sleep(5, 10)
            else:
                print("BŁĄD: Sterownik nie zainicjowany. Ponawiam za 30s...")
                random_sleep(30, 31)

        except KeyboardInterrupt:
            print("\nINFO: Przerwano działanie skryptu (Ctrl-C).")
            break
        except Exception as e:
            # --- OBSŁUGA BŁĘDÓW KRYTYCZNYCH ---
            logging.critical(f"KRYTYCZNY BŁĄD OGÓLNY: {e}", exc_info=True)
            log_error_state(driver, "main_loop_fatal")
            
            # W razie fatalnego błędu, zamknij przeglądarkę i zacznij od nowa
            if driver:
                try: driver.quit()
                except: pass
            driver = None
            print("INFO: Restartuję sesję za 20 sekund...")
            random_sleep(20, 21)

    # Sprzątanie końcowe
    if driver:
        print("INFO: Zamykanie przeglądarki...")
        driver.quit()
    print("INFO: Program zakończył działanie.")
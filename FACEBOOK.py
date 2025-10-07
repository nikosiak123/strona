# -*- coding: utf-8 -*-
import os
import pickle
import time
import traceback
from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.common.by import By
from selenium.common.exceptions import TimeoutException, NoSuchElementException, StaleElementReferenceException
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.keys import Keys

# --- KONFIGURACJA ---
# Zmień te ścieżki, aby pasowały do Twojego systemu
PATH_DO_GOOGLE_CHROME = "/usr/bin/google-chrome"
PATH_DO_RECZNEGO_CHROMEDRIVER = "/usr/local/bin/chromedriver"
COOKIES_FILE = "cookies.pkl"
PROCESSED_POSTS_FILE = "processed_posts_db.pkl"

# --- Funkcje Pomocnicze (Ciasteczka i Przetworzone Posty) ---

def save_cookies(driver, file_path):
    """Zapisuje ciasteczka sesji do pliku."""
    try:
        with open(file_path, 'wb') as file:
            pickle.dump(driver.get_cookies(), file)
        print(f"INFO: Ciasteczka zapisane do: {file_path}")
    except Exception as e:
        print(f"BŁĄD: Nie udało się zapisać ciasteczek: {e}")

def load_cookies(driver, file_path):
    """Ładuje ciasteczka sesji z pliku."""
    if not os.path.exists(file_path):
        return False
    try:
        with open(file_path, 'rb') as file:
            cookies = pickle.load(file)
            if not cookies: return False
            driver.get("https://www.facebook.com")
            time.sleep(1)
            for cookie in cookies:
                if 'expiry' in cookie:
                    cookie['expiry'] = int(cookie['expiry'])
                driver.add_cookie(cookie)
            driver.refresh()
            return True
    except Exception as e:
        print(f"BŁĄD: Nie udało się załadować ciasteczek: {e}")
        return False

def load_processed_post_keys():
    """Wczytuje zbiór unikalnych kluczy już przetworzonych postów."""
    if os.path.exists(PROCESSED_POSTS_FILE):
        with open(PROCESSED_POSTS_FILE, 'rb') as f:
            return pickle.load(f)
    return set()

def save_processed_post_keys(keys_set):
    """Zapisuje zbiór kluczy przetworzonych postów do pliku."""
    with open(PROCESSED_POSTS_FILE, 'wb') as f:
        pickle.dump(keys_set, f)

# --- Główne Funkcje Logiki ---

def initialize_driver_and_login():
    """Inicjalizuje przeglądarkę Chrome i loguje się za pomocą ciasteczek."""
    print("\n--- INICJALIZACJA PRZEGLĄDARKI ---")
    driver = None
    try:
        service = ChromeService(executable_path=PATH_DO_RECZNEGO_CHROMEDRIVER)
        options = webdriver.ChromeOptions()
        options.binary_location = PATH_DO_GOOGLE_CHROME
        options.add_argument("--disable-notifications")
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument("--headless=new")  # Uruchom w trybie bez widocznego okna
        options.add_argument("--window-size=1920,1080")

        driver = webdriver.Chrome(service=service, options=options)
        print("INFO: Przeglądarka uruchomiona w trybie headless.")
        
        driver.get("https://www.facebook.com")
        
        if not load_cookies(driver, COOKIES_FILE):
            print("!!! Nie znaleziono ciasteczek. Niestety, w trybie headless nie można się zalogować ręcznie.")
            print("!!! Uruchom skrypt raz BEZ opcji '--headless', zaloguj się, a następnie uruchom go ponownie.")
            driver.quit()
            return None

        # Sprawdzenie, czy logowanie się powiodło
        wait = WebDriverWait(driver, 15)
        wait.until(EC.presence_of_element_located((By.XPATH, "//input[@aria-label='Szukaj na Facebooku']")))
        print("SUKCES: Sesja przeglądarki jest aktywna i jesteś zalogowany!")
        return driver
    except Exception as e:
        print(f"BŁĄD KRYTYCZNY PODCZAS INICJALIZACJI: {e}")
        if driver:
            driver.quit()
        return None

def search_and_filter(driver):
    """Wykonuje sekwencję wyszukiwania i filtrowania postów."""
    print("\n--- ROZPOCZYNANIE WYSZUKIWANIA I FILTROWANIA ---")
    wait = WebDriverWait(driver, 20)
    try:
        # Krok 1: Kliknięcie w pole wyszukiwania
        search_xpath = "//input[@aria-label='Szukaj na Facebooku' or @placeholder='Szukaj na Facebooku']"
        search_input = wait.until(EC.element_to_be_clickable((By.XPATH, search_xpath)))
        search_input.click()
        print("INFO: Kliknięto pole wyszukiwania.")

        # Krok 2: Wpisanie tekstu i naciśnięcie Enter
        search_input.send_keys("korepetycji")
        time.sleep(1)
        search_input.send_keys(Keys.RETURN)
        print("INFO: Wyszukano frazę 'korepetycji'.")
        
        # Krok 3: Kliknięcie w filtr "Posty"
        posts_filter_xpath = "//a[@role='link'][.//span[normalize-space(.)='Posty']][not(contains(@href,'/groups/'))]"
        posts_filter_alt_xpath = "//div[@role='list']//div[@role='listitem']//a[@role='link'][.//span[normalize-space(.)='Posty']]"
        try:
            posts_button = wait.until(EC.element_to_be_clickable((By.XPATH, posts_filter_xpath)))
        except TimeoutException:
            print("INFO: Główny XPath dla 'Posty' nie zadziałał, próba alternatywnego.")
            posts_button = wait.until(EC.element_to_be_clickable((By.XPATH, posts_filter_alt_xpath)))
        
        posts_button.click()
        print("INFO: Kliknięto filtr 'Posty'.")
        time.sleep(3) # Czekaj na załadowanie nowej zawartości

        # Krok 4: Kliknięcie w checkbox "Najnowsze posty"
        checkbox_xpath = "//input[@aria-label='Najnowsze posty'][@type='checkbox']"
        checkbox_element = wait.until(EC.element_to_be_clickable((By.XPATH, checkbox_xpath)))
        
        # Użycie JS do kliknięcia, co jest bardziej niezawodne
        driver.execute_script("arguments[0].click();", checkbox_element)
        print("INFO: Kliknięto checkbox 'Najnowsze posty'.")
        time.sleep(3) # Czekaj na posortowanie wyników
        
        return True

    except Exception as e:
        print(f"BŁĄD podczas wyszukiwania lub filtrowania: {e}")
        traceback.print_exc()
        return False

def process_posts(driver):
    """Przeszukuje stronę w poszukiwaniu postów i przetwarza te, których jeszcze nie widziano."""
    print("\n--- ROZPOCZYNANIE PRZETWARZANIA POSTÓW ---")
    processed_keys = load_processed_post_keys()
    wait = WebDriverWait(driver, 7)
    
    no_new_posts_in_a_row = 0
    max_stale_scrolls = 5 # Ile razy scrollować bez znalezienia nowych postów, zanim skrypt odświeży stronę
    
    while True:
        try:
            # Kontener, który identyfikuje każdy post
            post_container_xpath = "//div[@role='article']"
            
            # Wyszukanie wszystkich postów widocznych na stronie
            posts_on_page = driver.find_elements(By.XPATH, post_container_xpath)
            
            if not posts_on_page:
                print("OSTRZEŻENIE: Nie znaleziono żadnych kontenerów postów. Czekam...")
                time.sleep(10)
                continue

            new_posts_found_this_scroll = 0
            
            for post in posts_on_page:
                try:
                    # Stworzenie unikalnego klucza dla posta, aby go nie przetwarzać ponownie
                    # Używamy tekstu autora i pierwszych 100 znaków treści
                    author_name = "Nieznany"
                    try:
                        # Próba znalezienia autora w silnym tagu
                        author_element = post.find_element(By.XPATH, ".//strong")
                        author_name = author_element.text
                    except NoSuchElementException:
                        # Jeśli nie ma <strong>, szukaj w linku
                        try:
                            author_element = post.find_element(By.XPATH, ".//a[@role='link' and not(contains(@href, 'comment'))]")
                            author_name = author_element.text
                        except NoSuchElementException:
                             pass # Pozostaje "Nieznany"

                    post_text = post.text
                    post_key = f"{author_name}_{post_text[:100]}" # Klucz: autor + fragment treści
                    
                    if post_key in processed_keys:
                        continue # Pomiń post, jeśli już go przetworzyliśmy

                    # Jeśli to nowy post, przetwarzamy go
                    new_posts_found_this_scroll += 1
                    print(f"\n[NOWY POST] Autor: {author_name}")
                    print(f"  Treść: {post_text[:200].replace(os.linesep, ' ')}...")
                    
                    # === TUTAJ MOŻESZ DODAĆ LOGIKĘ PRZETWARZANIA POSTA ===
                    # np. analiza AI, polubienie, skomentowanie, zapis do bazy danych
                    # Na razie tylko dodajemy go do przetworzonych
                    # =======================================================

                    processed_keys.add(post_key)
                    
                except StaleElementReferenceException:
                    print("OSTRZEŻENIE: Element posta stał się nieaktualny, pomijam.")
                    continue

            if new_posts_found_this_scroll > 0:
                print(f"\nINFO: Znaleziono {new_posts_found_this_scroll} nowych postów. Zapisuję stan...")
                save_processed_post_keys(processed_keys)
                no_new_posts_in_a_row = 0 # Resetuj licznik, bo znaleziono coś nowego
            else:
                print("INFO: Brak nowych postów na widocznym ekranie.")
                no_new_posts_in_a_row += 1

            # Logika scrollowania i odświeżania
            if no_new_posts_in_a_row >= max_stale_scrolls:
                print(f"\nINFO: Przeskrolowano {max_stale_scrolls} razy bez nowych postów. Odświeżam stronę...")
                driver.refresh()
                time.sleep(15) # Dłuższa przerwa po odświeżeniu
                no_new_posts_in_a_row = 0 # Reset licznika
            else:
                # Scrollowanie na dół strony
                print(f"INFO: Scrolluję w dół... (próba bez nowych postów: {no_new_posts_in_a_row})")
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(5) # Czekaj na załadowanie nowych postów

        except KeyboardInterrupt:
            print("\nINFO: Przerwano działanie pętli przez użytkownika.")
            break
        except Exception as e:
            print(f"BŁĄD w głównej pętli przetwarzania: {e}")
            traceback.print_exc()
            print("INFO: Odczekuję 30 sekund przed ponowną próbą...")
            time.sleep(30)


# --- Główny Blok Wykonawczy ---
if __name__ == "__main__":
    driver = None
    try:
        driver = initialize_driver_and_login()
        if driver:
            if search_and_filter(driver):
                process_posts(driver)
            else:
                print("BŁĄD KRYTYCZNY: Nie udało się wyszukać i przefiltrować. Zamykanie...")

    except KeyboardInterrupt:
        print("\nINFO: Przerwano działanie skryptu przez użytkownika (Ctrl+C).")
    except Exception as e:
        print(f"\nKRYTYCZNY BŁĄD OGÓLNY: {e}")
        traceback.print_exc()
    finally:
        if driver:
            print("\nINFO: Zamykanie przeglądarki...")
            driver.quit()
        print("INFO: Program zakończył działanie.")

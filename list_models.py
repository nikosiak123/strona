# -*- coding: utf-8 -*-
import json
import os
try:
    import google.generativeai as genai
except ImportError:
    print("!!! BŁĄD: Wygląda na to, że biblioteka google-generativeai nie jest zainstalowana.")
    print("Uruchom: pip install google-generativeai")
    exit()

# --- Wczytywanie konfiguracji ---
config_path = 'config.json'
try:
    with open(config_path, 'r', encoding='utf-8') as f:
        config = json.load(f)
except (FileNotFoundError, json.JSONDecodeError) as e:
    print(f"!!! KRYTYCZNY BŁĄD: Nie można wczytać pliku '{config_path}': {e}")
    exit()

AI_CONFIG = config.get("AI_CONFIG", {})
GEMINI_API_KEY = AI_CONFIG.get("GEMINI_API_KEY") or os.environ.get("GEMINI_API_KEY")

if not GEMINI_API_KEY or "WSTAW" in GEMINI_API_KEY:
    print("!!! KRYTYCZNY BŁĄD: Brak klucza API Gemini w config.json. Wstaw prawidłowy klucz.")
    exit()

# --- Listowanie modeli ---
try:
    print("--- Łączenie z Google Gen AI...")
    genai.configure(api_key=GEMINI_API_KEY)
    print("--- Połączono. Pobieranie listy modeli...")
    print("-" * 20)
    print("Dostępne modele (obsługujące generowanie treści):")
    
    found_models = False
    for m in genai.list_models():
      if 'generateContent' in m.supported_generation_methods:
        print(f'- {m.name}')
        found_models = True
        
    print("-" * 20)
    if found_models:
        print("\nSkopiuj jedną z powyższych nazw modelu (np. 'models/gemini-1.5-flash-latest') i wklej ją jako wartość dla MODEL_ID w pliku config.json.")
    else:
        print("Nie znaleziono żadnych modeli, które obsługują generowanie treści.")

except Exception as e:
    print(f"!!! WYSTĄPIŁ BŁĄD: {repr(e)}")

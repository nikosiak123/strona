#!/usr/bin/env python3
"""
test_ai.py - Skrypt do testowania działania Google Vertex AI
Sprawdza czy AI działa poprawnie i drukuje logi w przypadku błędów.
"""

import os
import sys
import json
import traceback

# Dodaj ścieżkę do katalogu nadrzędnego (gdzie jest config.py)
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from config import AI_CONFIG

try:
    # Import Vertex AI
    import vertexai
    from vertexai.generative_models import GenerativeModel

    print("=== TEST DZIAŁANIA GOOGLE VERTEX AI ===\n")

    # Konfiguracja jest teraz importowana z config.py
    PROJECT_ID = AI_CONFIG.get("PROJECT_ID")
    LOCATION = AI_CONFIG.get("LOCATION")
    MODEL_ID = AI_CONFIG.get("MODEL_ID")

    if not all([PROJECT_ID, LOCATION, MODEL_ID]):
        print("❌ BŁĄD: Brak pełnej konfiguracji AI w config.py")
        print(f"   PROJECT_ID: {PROJECT_ID}")
        print(f"   LOCATION: {LOCATION}")
        print(f"   MODEL_ID: {MODEL_ID}")
        sys.exit(1)

    print("✅ Konfiguracja załadowana:")
    print(f"   Projekt: {PROJECT_ID}")
    print(f"   Region: {LOCATION}")
    print(f"   Model: {MODEL_ID}\n")

    # Zainicjalizuj Vertex AI
    print("🔄 Inicjalizacja Vertex AI...")
    vertexai.init(project=PROJECT_ID, location=LOCATION)

    # Utwórz model
    model = GenerativeModel(MODEL_ID)
    print("✅ Model zainicjalizowany\n")

    # Testowe zapytanie
    test_prompt = "Powiedz po polsku: 'Sztuczna inteligencja działa poprawnie.'"
    print("🔄 Wysyłanie testowego zapytania...")
    print(f"   Prompt: {test_prompt}")

    response = model.generate_content(test_prompt)

    # Sprawdź odpowiedź
    if response.candidates and len(response.candidates) > 0:
        answer = response.text.strip()
        print("✅ AI odpowiada poprawnie!")
        print(f"   Odpowiedź: {answer}")

        # Sprawdź czy odpowiedź zawiera oczekiwany tekst
        if "inteligencja" in answer.lower() and "działa" in answer.lower():
            print("✅ Test PASSED: Odpowiedź zawiera oczekiwane słowa")
        else:
            print("⚠️ UWAGA: Odpowiedź nie zawiera oczekiwanych słów, ale AI działa")

    else:
        print("❌ BŁĄD: Brak kandydatów w odpowiedzi")
        if hasattr(response, 'prompt_feedback'):
            print(f"   Prompt feedback: {response.prompt_feedback}")

    print("\n=== KONIEC TESTU ===")

except ImportError as e:
    print("❌ BŁĄD IMPORTU: Nie można zaimportować wymaganych modułów")
    print(f"   Szczegóły: {e}")
    print("   Upewnij się, że zainstalowano: pip install google-cloud-aiplatform")

except json.JSONDecodeError as e:
    print("❌ BŁĄD: Nieprawidłowy format pliku konfiguracyjnego")
    print(f"   Szczegóły: {e}")

except Exception as e:
    print("❌ NIEOCZEKIWANY BŁĄD:")
    print(f"   Typ błędu: {type(e).__name__}")
    print(f"   Wiadomość: {e}")
    print("\n=== ŚLEDZENIE STOSU ===")
    traceback.print_exc()

print("\nAby naprawić błędy AI, sprawdź:")
print("- Czy Vertex AI API jest włączone w Google Cloud Console")
print("- Czy konto serwisowe ma rolę 'Vertex AI User'")
print("- Czy GOOGLE_APPLICATION_CREDENTIALS wskazuje na prawidłowy plik JSON")
print("- Czy PROJECT_ID, LOCATION i MODEL_ID są poprawne w config.py")
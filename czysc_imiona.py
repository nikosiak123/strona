import os
import json

# Skrypt powinien być uruchomiony z katalogu 'strona'
CONVERSATION_STORE_DIR = 'conversation_store'

def clear_names_from_store():
    """
    Iteruje przez wszystkie pliki .json w folderze conversation_store
    i usuwa z nich wpisy zawierające imię i nazwisko użytkownika.
    """
    if not os.path.isdir(CONVERSATION_STORE_DIR):
        print(f"BŁĄD: Katalog '{CONVERSATION_STORE_DIR}' nie został znaleziony.")
        print("Upewnij się, że skrypt jest uruchamiany z katalogu 'strona', obok folderu 'conversation_store'.")
        return

    print(f"Rozpoczynam czyszczenie imion z plików w '{CONVERSATION_STORE_DIR}'...")
    
    cleared_files_count = 0
    
    for filename in os.listdir(CONVERSATION_STORE_DIR):
        if filename.endswith('.json'):
            filepath = os.path.join(CONVERSATION_STORE_DIR, filename)
            
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    history = json.load(f)
                
                # Filtrujemy historię, usuwając wpis z imieniem
                # Wpis z imieniem to wiadomość od 'model' zaczynająca się od 'name:'
                original_count = len(history)
                new_history = [
                    msg for msg in history 
                    if not (
                        isinstance(msg, dict) and
                        msg.get('role') == 'model' and
                        msg.get('parts') and
                        isinstance(msg.get('parts'), list) and
                        len(msg.get('parts')) > 0 and
                        isinstance(msg['parts'][0], dict) and
                        msg['parts'][0].get('text', '').startswith('name:')
                    )
                ]
                
                # Jeśli historia się zmieniła, zapisz plik
                if len(new_history) < original_count:
                    with open(filepath, 'w', encoding='utf-8') as f:
                        json.dump(new_history, f, indent=2)
                    print(f"Usunięto imię z pliku: {filename}")
                    cleared_files_count += 1

            except (json.JSONDecodeError, IOError) as e:
                print(f"Błąd podczas przetwarzania pliku {filename}: {e}")

    print(f"\nZakończono. Usunięto imiona z {cleared_files_count} plików.")

if __name__ == '__main__':
    clear_names_from_store()

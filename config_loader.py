import os

# Definiujemy ścieżki
BASE_DIR = os.path.dirname(os.path.abspath(__file__)) # /home/nikodnaj/strona
PARENT_DIR = os.path.dirname(BASE_DIR)              # /home/nikodnaj

# Próbujemy znaleźć plik bazy danych w nadrzędnym katalogu (wspólnym dla obu projektów)
DB_PATH = os.path.join(PARENT_DIR, "korki.db")

# Importujemy konfigurację z drugiego projektu jeśli istnieje
try:
    import sys
    sys.path.append(os.path.join(PARENT_DIR, 'strona-korki'))
    from config import *
except ImportError:
    pass

# Nadpisujemy DB_PATH, żeby na pewno wskazywała na wspólny plik
DB_PATH = os.path.join(PARENT_DIR, "korki.db")

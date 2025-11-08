"""
Osobna baza danych dla statystyk skryptu Facebook.
NIE WSPÓŁDZIELI bazy danych z backend.py - każdy ma swoją!
"""
import sqlite3
import os
from datetime import datetime

# Osobna baza danych dla statystyk Facebook
DB_PATH = os.path.join(os.path.dirname(__file__), 'facebook_stats.db')

def get_connection():
    """Zwraca połączenie z bazą danych statystyk."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_stats_database():
    """Inicjalizuje bazę danych statystyk."""
    conn = get_connection()
    cursor = conn.cursor()
    
    # Tabela Statystyki (odpowiednik Airtable)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS Statystyki (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            Data TEXT UNIQUE NOT NULL,
            Odrzucone INTEGER DEFAULT 0,
            Oczekuje INTEGER DEFAULT 0,
            Przeslane INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_statystyki_data ON Statystyki(Data)')
    
    conn.commit()
    conn.close()
    print(f"✓ Baza danych statystyk zainicjalizowana: {DB_PATH}")

def update_stats(status_field: str):
    """
    Aktualizuje statystyki dla dzisiejszej daty.
    status_field: 'Odrzucone', 'Oczekuje', lub 'Przeslane'
    """
    try:
        conn = get_connection()
        cursor = conn.cursor()
        
        today_str = datetime.now().strftime('%d.%m.%Y')
        
        # Sprawdź czy rekord istnieje
        cursor.execute("SELECT * FROM Statystyki WHERE Data = ?", [today_str])
        record = cursor.fetchone()
        
        if record:
            # Aktualizuj istniejący rekord
            current_value = record[status_field] or 0
            new_value = int(current_value) + 1
            cursor.execute(f"UPDATE Statystyki SET {status_field} = ? WHERE Data = ?", 
                         [new_value, today_str])
            print(f"SUKCES: [DB] Zaktualizowano '{status_field}' na {new_value} dla daty {today_str}.")
        else:
            # Utwórz nowy rekord
            cursor.execute("""
                INSERT INTO Statystyki (Data, Odrzucone, Oczekuje, Przeslane) 
                VALUES (?, 0, 0, 0)
            """, [today_str])
            cursor.execute(f"UPDATE Statystyki SET {status_field} = 1 WHERE Data = ?", [today_str])
            print(f"SUKCES: [DB] Utworzono nowy wiersz dla {today_str} i ustawiono '{status_field}' na 1.")
        
        conn.commit()
        conn.close()
        return True
        
    except Exception as e:
        print(f"BŁĄD: [DB] Nie udało się zaktualizować statystyk: {e}")
        import traceback
        traceback.print_exc()
        return False

# Inicjalizacja przy imporcie
if not os.path.exists(DB_PATH):
    init_stats_database()

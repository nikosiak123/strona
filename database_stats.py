"""
Osobna baza danych dla statystyk skryptu Facebook.
NIE WSPÓŁDZIELI bazy danych z backend.py - każdy ma swoją!
"""
import sqlite3
import os
from datetime import datetime
import pytz

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
            Scrolls INTEGER DEFAULT 0,
            LastCommentTime TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Dodaj kolumnę Scrolls jeśli nie istnieje (dla migracji)
    try:
        cursor.execute("ALTER TABLE Statystyki ADD COLUMN Scrolls INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass # Kolumna już istnieje
    
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_statystyki_data ON Statystyki(Data)')
    
    # Tabela Logów Komentarzy (szczegółowe zdarzenia)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS CommentLogs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            author TEXT,
            post_snippet TEXT,
            scrolls_since_refresh INTEGER,
            status TEXT
        )
    ''')
    
    conn.commit()
    conn.close()
    print(f"✓ Baza danych statystyk zainicjalizowana: {DB_PATH}")

def log_comment(author, post_snippet, scrolls, status):
    """Loguje szczegóły wysłanego komentarza."""
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO CommentLogs (author, post_snippet, scrolls_since_refresh, status) 
            VALUES (?, ?, ?, ?)
        """, [author, post_snippet, scrolls, status])
        conn.commit()
        conn.close()
        print(f"SUKCES: [DB] Zalogowano komentarz (scrolle: {scrolls}).")
        return True
    except Exception as e:
        print(f"BŁĄD: [DB] Nie udało się zalogować komentarza: {e}")
        return False

def get_comment_logs(limit=50):
    """Pobiera ostatnie logi komentarzy."""
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM CommentLogs ORDER BY id DESC LIMIT ?", [limit])
        records = cursor.fetchall()
        conn.close()
        return [dict(record) for record in records]
    except Exception as e:
        print(f"BŁĄD: [DB] Nie udało się pobrać logów komentarzy: {e}")
        return []

def update_stats(status_field: str):
    """
    Aktualizuje statystyki dla dzisiejszej daty.
    status_field: 'Odrzucone', 'Oczekuje', lub 'Przeslane'
    """
    try:
        conn = get_connection()
        cursor = conn.cursor()
        
        today_str = datetime.now(pytz.timezone('Europe/Warsaw')).strftime('%d.%m.%Y')
        now_str = datetime.now(pytz.timezone('Europe/Warsaw')).strftime('%Y-%m-%d %H:%M:%S')
        
        # Sprawdź czy rekord istnieje
        cursor.execute("SELECT * FROM Statystyki WHERE Data = ?", [today_str])
        record = cursor.fetchone()
        
        if record:
            # Aktualizuj istniejący rekord
            current_value = record[status_field] or 0
            new_value = int(current_value) + 1
            update_fields = f"{status_field} = ?"
            if status_field == "Przeslane":
                update_fields += ", LastCommentTime = ?"
                cursor.execute(f"UPDATE Statystyki SET {update_fields} WHERE Data = ?", 
                             [new_value, now_str, today_str])
            else:
                cursor.execute(f"UPDATE Statystyki SET {update_fields} WHERE Data = ?", 
                             [new_value, today_str])
            print(f"SUKCES: [DB] Zaktualizowano '{status_field}' na {new_value} dla daty {today_str}.")
        else:
            # Utwórz nowy rekord
            cursor.execute("""
                INSERT INTO Statystyki (Data, Odrzucone, Oczekuje, Przeslane, Scrolls, LastCommentTime) 
                VALUES (?, 0, 0, 0, 0, NULL)
            """, [today_str])
            if status_field == "Przeslane":
                cursor.execute(f"UPDATE Statystyki SET {status_field} = 1, LastCommentTime = ? WHERE Data = ?", [now_str, today_str])
            elif status_field == "Scrolls":
                cursor.execute(f"UPDATE Statystyki SET {status_field} = 1 WHERE Data = ?", [today_str])
            else:
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

def get_stats():
    """Pobiera wszystkie statystyki."""
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM Statystyki ORDER BY Data DESC")
        records = cursor.fetchall()
        conn.close()
        return [dict(record) for record in records]
    except Exception as e:
        print(f"BŁĄD: [DB] Nie udało się pobrać statystyk: {e}")
        return []

# Inicjalizacja przy imporcie
init_stats_database()

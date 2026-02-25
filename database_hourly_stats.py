import sqlite3
import os
from datetime import datetime

# Osobna baza danych dla statystyk godzinowych
DB_PATH = os.path.join(os.path.dirname(__file__), 'hourly_stats.db')

def get_connection():
    """Zwraca połączenie z bazą danych statystyk godzinowych."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_hourly_stats_database():
    """Inicjalizuje bazę danych statystyk godzinowych."""
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS HourlyStats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT UNIQUE NOT NULL,
            commented_posts INTEGER DEFAULT 0,
            loaded_posts_total INTEGER DEFAULT 0,
            sent_comments_count INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Migracja: sprawdź czy kolumna sent_comments_count istnieje, jeśli nie to dodaj
    cursor.execute("PRAGMA table_info(HourlyStats)")
    columns = [info[1] for info in cursor.fetchall()]
    if 'sent_comments_count' not in columns:
        try:
            cursor.execute("ALTER TABLE HourlyStats ADD COLUMN sent_comments_count INTEGER DEFAULT 0")
            print("✓ Dodano kolumnę sent_comments_count do HourlyStats")
        except Exception as e:
            print(f"Błąd migracji (dodawanie kolumny sent_comments_count): {e}")
    
    conn.commit()
    conn.close()
    print(f"✓ Baza danych statystyk godzinowych zainicjalizowana: {DB_PATH}")

def save_hourly_stats(timestamp_str, commented_count, loaded_count, sent_comments_count=0):
    """Zapisuje statystyki dla danej godziny. Używa REPLACE, aby uniknąć duplikatów."""
    try:
        conn = get_connection()
        cursor = conn.cursor()
        
        # Używamy INSERT OR REPLACE, aby zaktualizować wiersz, jeśli już istnieje dla danej godziny
        cursor.execute("""
            INSERT OR REPLACE INTO HourlyStats (timestamp, commented_posts, loaded_posts_total, sent_comments_count)
            VALUES (?, ?, ?, ?)
        """, [timestamp_str, commented_count, loaded_count, sent_comments_count])
        
        conn.commit()
        conn.close()
        print(f"STATS: Zapisano statystyki dla godziny {timestamp_str}: {commented_count} skomentowanych, {sent_comments_count} wysłanych, {loaded_count} załadowanych.")
        return True
    except Exception as e:
        print(f"BŁĄD ZAPISU STATYSTYK: {e}")
        return False

# Inicjalizacja przy pierwszym imporcie
if not os.path.exists(DB_PATH):
    init_hourly_stats_database()

def get_hourly_stats(limit=48):
    """Pobiera statystyki godzinowe, domyślnie z ostatnich 48 godzin."""
    try:
        conn = get_connection()
        cursor = conn.cursor()
        # Pobieramy najnowsze rekordy, aby mieć pewność, że mamy najświeższe dane
        cursor.execute("SELECT * FROM HourlyStats ORDER BY timestamp DESC LIMIT ?", [limit])
        records = cursor.fetchall()
        conn.close()
        # Zwracamy jako listę słowników
        return [dict(record) for record in records]
    except Exception as e:
        print(f"BŁĄD: [DB] Nie udało się pobrać statystyk godzinowych: {e}")
        return []

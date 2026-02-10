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
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    conn.commit()
    conn.close()
    print(f"✓ Baza danych statystyk godzinowych zainicjalizowana: {DB_PATH}")

def save_hourly_stats(timestamp_str, commented_count, loaded_count):
    """Zapisuje statystyki dla danej godziny. Używa REPLACE, aby uniknąć duplikatów."""
    try:
        conn = get_connection()
        cursor = conn.cursor()
        
        # Używamy INSERT OR REPLACE, aby zaktualizować wiersz, jeśli już istnieje dla danej godziny
        cursor.execute("""
            INSERT OR REPLACE INTO HourlyStats (timestamp, commented_posts, loaded_posts_total)
            VALUES (?, ?, ?)
        """, [timestamp_str, commented_count, loaded_count])
        
        conn.commit()
        conn.close()
        print(f"STATS: Zapisano statystyki dla godziny {timestamp_str}: {commented_count} komentarzy, {loaded_count} załadowanych postów.")
        return True
    except Exception as e:
        print(f"BŁĄD ZAPISU STATYSTYK: {e}")
        return False

# Inicjalizacja przy pierwszym imporcie
if not os.path.exists(DB_PATH):
    init_hourly_stats_database()

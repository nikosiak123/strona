import sqlite3
import os
from datetime import datetime
import pytz

DB_PATH = os.path.join(os.path.dirname(__file__), 'hourly_stats.db')

def get_connection():
    """Zwraca połączenie z bazą danych statystyk godzinowych."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def _migrate_database():
    """Wykonuje niezbędne migracje (dodaje brakujące kolumny)."""
    conn = get_connection()
    cursor = conn.cursor()

    # Sprawdź, czy kolumna sent_comments_count istnieje
    cursor.execute("PRAGMA table_info(HourlyStats)")
    columns = [info[1] for info in cursor.fetchall()]
    if 'sent_comments_count' not in columns:
        try:
            cursor.execute("ALTER TABLE HourlyStats ADD COLUMN sent_comments_count INTEGER DEFAULT 0")
            print("✓ Migracja: dodano kolumnę 'sent_comments_count' do HourlyStats")
        except Exception as e:
            print(f"Błąd migracji: {e}")

    conn.commit()
    conn.close()

def init_hourly_stats_database():
    """Tworzy tabelę, jeśli nie istnieje (przy pierwszym uruchomieniu)."""
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
    conn.commit()
    conn.close()
    print(f"✓ Baza danych statystyk godzinowych zainicjalizowana: {DB_PATH}")

def ensure_database():
    """Zapewnia, że baza istnieje i jest zmigrowana."""
    if not os.path.exists(DB_PATH):
        init_hourly_stats_database()
    else:
        _migrate_database()   # zawsze uruchamiamy migracje

# --- Wykonaj przy imporcie modułu ---
ensure_database()

# ------------------------------------------------------------
# Funkcje do odczytu/zapisu statystyk (bez zmian względem oryginału)
# ------------------------------------------------------------

def save_hourly_stats(timestamp_str, commented_count, loaded_count, sent_comments_count=0):
    """Zapisuje statystyki dla danej godziny."""
    try:
        conn = get_connection()
        cursor = conn.cursor()
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

def get_hourly_stats(limit=48):
    """Pobiera statystyki godzinowe, domyślnie z ostatnich 48 godzin."""
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM HourlyStats ORDER BY timestamp DESC LIMIT ?", [limit])
        records = cursor.fetchall()
        conn.close()
        return [dict(record) for record in records]
    except Exception as e:
        print(f"BŁĄD: [DB] Nie udało się pobrać statystyk godzinowych: {e}")
        return []

def increment_hourly_stat(stat_field: str, count: int = 1):
    """
    Inkrementuje podaną statystykę dla bieżącej godziny.
    Jeśli wpis dla godziny nie istnieje, tworzy go.
    """
    valid_fields = {'commented_posts', 'loaded_posts_total', 'sent_comments_count'}
    if stat_field not in valid_fields:
        print(f"BŁĄD: [HOURLY_STATS] Nieprawidłowe pole statystyki: {stat_field}")
        return False

    try:
        conn = get_connection()
        cursor = conn.cursor()

        now = datetime.now(pytz.timezone('Europe/Warsaw'))
        timestamp_str = now.replace(minute=0, second=0, microsecond=0).strftime('%Y-%m-%d %H:00:00')

        # Krok 1: Upewnij się, że wiersz dla danej godziny istnieje.
        cursor.execute("""
            INSERT INTO HourlyStats (timestamp, commented_posts, loaded_posts_total, sent_comments_count)
            VALUES (?, 0, 0, 0)
            ON CONFLICT(timestamp) DO NOTHING;
        """, [timestamp_str])

        # Krok 2: Zaktualizuj (zinkrementuj) odpowiednią kolumnę.
        cursor.execute(f"""
            UPDATE HourlyStats 
            SET {stat_field} = {stat_field} + ?
            WHERE timestamp = ?;
        """, [count, timestamp_str])

        conn.commit()
        conn.close()
        print(f"STATS: [HOURLY] Zinkrementowano '{stat_field}' o {count} dla godziny {timestamp_str}.")
        return True

    except Exception as e:
        print(f"BŁĄD INKREMENTACJI STATYSTYK GODZINOWYCH: {e}")
        import traceback
        traceback.print_exc()
        return False
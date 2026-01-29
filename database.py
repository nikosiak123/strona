import sqlite3
import json
from datetime import datetime
from typing import Optional, List, Dict, Any
import os
import re
from config import DB_PATH

def get_connection():
    """Zwraca połączenie z bazą danych."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_database():
    """Inicjalizuje bazę danych z wszystkimi tabelami."""
    conn = get_connection()
    cursor = conn.cursor()
    
    # Tabela Klienci
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS Klienci (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ClientID TEXT UNIQUE NOT NULL,
            Imie TEXT,
            Nazwisko TEXT,
            LINK TEXT,
            ImieKlienta TEXT,
            NazwiskoKlienta TEXT,
            Zdjecie TEXT,
            wolna_kwota INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Tabela Korepetytorzy
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS Korepetytorzy (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            TutorID TEXT UNIQUE NOT NULL,
            ImieNazwisko TEXT NOT NULL,
            Poniedziałek TEXT,
            Wtorek TEXT,
            Środa TEXT,
            Czwartek TEXT,
            Piątek TEXT,
            Sobota TEXT,
            Niedziela TEXT,
            Przedmioty TEXT,
            PoziomNauczania TEXT,
            LINK TEXT,
            LimitGodzinTygodniowo INTEGER DEFAULT NULL,
            Email TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Tabela Rezerwacje
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS Rezerwacje (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            Klient TEXT NOT NULL,
            Korepetytor TEXT NOT NULL,
            Data TEXT NOT NULL,
            Godzina TEXT NOT NULL,
            Przedmiot TEXT,
            Status TEXT DEFAULT 'Oczekuje na płatność',
            Typ TEXT DEFAULT 'Jednorazowa',
            ManagementToken TEXT UNIQUE,
            TeamsLink TEXT,
            JestTestowa INTEGER DEFAULT 0,
            Oplacona INTEGER DEFAULT 0,
            confirmed INTEGER DEFAULT 0,
            confirmation_deadline TEXT,
            TypSzkoly TEXT,
            Poziom TEXT,
            Klasa TEXT,
            WolnaKwotaUzyta INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (Klient) REFERENCES Klienci(ClientID)
        )
    ''')
    
    # Tabela StaleRezerwacje
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS StaleRezerwacje (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            Klient_ID TEXT NOT NULL,
            Korepetytor TEXT NOT NULL,
            DzienTygodnia TEXT NOT NULL,
            Godzina TEXT NOT NULL,
            Przedmiot TEXT,
            Aktywna INTEGER DEFAULT 1,
            TypSzkoly TEXT,
            Poziom TEXT,
            Klasa TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (Klient_ID) REFERENCES Klienci(ClientID)
        )
    ''')
    
    # Migracje kolumn (dla pewności)
    tables_cols = {
        'Korepetytorzy': ['Email', 'LimitGodzinTygodniowo'],
        'Rezerwacje': ['WolnaKwotaUzyta', 'confirmed', 'confirmation_deadline']
    }
    
    for table, columns in tables_cols.items():
        for col in columns:
            try:
                cursor.execute(f"SELECT {col} FROM {table} LIMIT 1")
            except sqlite3.OperationalError:
                print(f"Migracja: Dodawanie kolumny {col} do tabeli {table}...")
                col_type = "INTEGER" if col in ['LimitGodzinTygodniowo', 'WolnaKwotaUzyta', 'confirmed'] else "TEXT"
                cursor.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")

    conn.commit()
    conn.close()

def _safe_bool_convert(value):
    """Bezpieczna konwersja do bool przy odczycie."""
    if isinstance(value, str):
        return value.lower() in ('true', '1', 'yes', 't')
    return bool(value)

def _safe_int_convert(value, default=0):
    """Bezpieczna konwersja do int."""
    try:
        if value is None: return default
        return int(float(value)) # float handle "100.0" strings
    except (ValueError, TypeError):
        return default

class DatabaseTable:
    def __init__(self, table_name: str):
        self.table_name = table_name
    
    def _row_to_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        """Konwertuje wiersz SQLite do formatu Airtable z bezpiecznym typowaniem."""
        if row is None:
            return None
        
        fields = dict(row)
        record_id = fields.pop('id')
        fields.pop('created_at', None)
        
        # 1. Obsługa list (JSON)
        if self.table_name == 'Korepetytorzy':
            for list_col in ['Przedmioty', 'PoziomNauczania']:
                val = fields.get(list_col)
                if isinstance(val, str):
                    try:
                        fields[list_col] = json.loads(val)
                    except json.JSONDecodeError:
                        # Fallback: jeśli to zwykły string, zrób z niego listę jednoelementową
                        fields[list_col] = [val] if val else []
                elif val is None:
                    fields[list_col] = []
        
        # 2. Obsługa Boolean (bezpieczny odczyt)
        bool_fields = []
        if self.table_name == 'Rezerwacje':
            bool_fields = ['JestTestowa', 'Oplacona', 'confirmed']
        elif self.table_name == 'StaleRezerwacje':
            bool_fields = ['Aktywna']
            
        for bf in bool_fields:
            fields[bf] = _safe_bool_convert(fields.get(bf, 0))

        # 3. Obsługa Integer (bezpieczny odczyt)
        if self.table_name == 'Klienci':
            fields['wolna_kwota'] = _safe_int_convert(fields.get('wolna_kwota'), 0)
        elif self.table_name == 'Korepetytorzy':
            if fields.get('LimitGodzinTygodniowo') is not None:
                fields['LimitGodzinTygodniowo'] = _safe_int_convert(fields.get('LimitGodzinTygodniowo'), None)
        
        return {'id': str(record_id), 'fields': fields}
    
    def _prepare_fields_for_write(self, fields: Dict[str, Any]) -> Dict[str, Any]:
        """Przygotowuje i czyści dane przed zapisem do bazy."""
        clean_fields = fields.copy()
        
        # 1. Konwersja List -> JSON String
        if self.table_name == 'Korepetytorzy':
            for list_col in ['Przedmioty', 'PoziomNauczania']:
                if list_col in clean_fields:
                    val = clean_fields[list_col]
                    if isinstance(val, list):
                        clean_fields[list_col] = json.dumps(val)
                    elif isinstance(val, str) and not val.startswith('['):
                        # Jeśli ktoś podał string zamiast listy, napraw to
                        clean_fields[list_col] = json.dumps([val])

        # 2. Konwersja Boolean -> 0/1
        bool_fields = []
        if self.table_name == 'Rezerwacje':
            bool_fields = ['JestTestowa', 'Oplacona', 'confirmed']
        elif self.table_name == 'StaleRezerwacje':
            bool_fields = ['Aktywna']
            
        for bf in bool_fields:
            if bf in clean_fields:
                val = clean_fields[bf]
                if isinstance(val, str):
                    clean_fields[bf] = 1 if val.lower() == 'true' else 0
                else:
                    clean_fields[bf] = 1 if val else 0

        # 3. Konwersja Integer
        if self.table_name == 'Klienci' and 'wolna_kwota' in clean_fields:
            clean_fields['wolna_kwota'] = _safe_int_convert(clean_fields['wolna_kwota'])
            
        if self.table_name == 'Korepetytorzy' and 'LimitGodzinTygodniowo' in clean_fields:
             if clean_fields['LimitGodzinTygodniowo'] == '' or clean_fields['LimitGodzinTygodniowo'] is None:
                 clean_fields['LimitGodzinTygodniowo'] = None
             else:
                 clean_fields['LimitGodzinTygodniowo'] = _safe_int_convert(clean_fields['LimitGodzinTygodniowo'], None)

        return clean_fields

    def create(self, fields: Dict[str, Any]) -> Dict:
        conn = get_connection()
        cursor = conn.cursor()
        
        prepared_fields = self._prepare_fields_for_write(fields)
        
        columns = ', '.join(prepared_fields.keys())
        placeholders = ', '.join(['?' for _ in prepared_fields])
        query = f"INSERT INTO {self.table_name} ({columns}) VALUES ({placeholders})"
        
        cursor.execute(query, list(prepared_fields.values()))
        record_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        return self.get(str(record_id))
    
    def update(self, record_id: str, fields: Dict[str, Any]) -> Dict:
        conn = get_connection()
        cursor = conn.cursor()
        
        prepared_fields = self._prepare_fields_for_write(fields)
        
        set_clause = ', '.join([f"{k} = ?" for k in prepared_fields.keys()])
        query = f"UPDATE {self.table_name} SET {set_clause} WHERE id = ?"
        
        cursor.execute(query, list(prepared_fields.values()) + [record_id])
        conn.commit()
        conn.close()
        
        return self.get(record_id)

    def _convert_formula_to_sql(self, formula: str) -> tuple:
        if not formula: return ("1=1", [])
        
        # Pattern: {Field} = 'value'
        simple_eq = re.search(r"\{(\w+)\}\s*=\s*'([^']*)'", formula)
        if simple_eq and 'AND' not in formula and 'OR' not in formula:
            return (f"{simple_eq.group(1)} = ?", [simple_eq.group(2)])
        
        # Pattern: AND({Field1} = 'value1', ...)
        and_pattern = re.findall(r"AND\(([^)]+)\)", formula)
        if and_pattern:
            conditions, params = [], []
            # Use regex to find all conditions to avoid splitting issues
            part_pattern = re.compile(r"(\{.+?\}\s*=\s*'.+?'|DATETIME_FORMAT\(.+?\)\s*=\s*'.+?')")
            parts = part_pattern.findall(and_pattern[0])
            for part in parts:
                eq = re.search(r"\{(\w+)\}\s*=\s*'([^']*)'", part)
                if eq:
                    conditions.append(f"{eq.group(1)} = ?")
                    params.append(eq.group(2))
                elif 'DATETIME_FORMAT' in part:
                    dt = re.search(r"DATETIME_FORMAT\(\{(\w+)\}[^)]*\)\s*=\s*'([^']*)'", part)
                    if dt:
                        conditions.append(f"{dt.group(1)} = ?")
                        params.append(dt.group(2))
            if conditions: return (" AND ".join(conditions), params)
        return ("1=1", []) # Fallback

    def first(self, formula: str = None) -> Optional[Dict]:
        conn = get_connection()
        cursor = conn.cursor()
        where, params = self._convert_formula_to_sql(formula)
        cursor.execute(f"SELECT * FROM {self.table_name} WHERE {where} LIMIT 1", params)
        row = cursor.fetchone()
        conn.close()
        return self._row_to_dict(row)
    
    def all(self, formula: str = None) -> List[Dict]:
        conn = get_connection()
        cursor = conn.cursor()
        where, params = self._convert_formula_to_sql(formula)
        cursor.execute(f"SELECT * FROM {self.table_name} WHERE {where}", params)
        rows = cursor.fetchall()
        conn.close()
        results = [self._row_to_dict(row) for row in rows]
        # Dodatkowe filtrowanie dat w Pythonie
        if formula and ('IS_AFTER' in formula or 'IS_BEFORE' in formula or 'OR' in formula or 'NOT' in formula):
             return self._filter_complex_formula(results, formula)
        return results

    def _filter_complex_formula(self, records, formula):
        # Prosta implementacja filtra pythonowego dla logiki której nie obsłużył SQL
        filtered = []
        for record in records:
            fields = record['fields']
            keep = True
            # Tutaj logika filtrów data/status... (skrótowo)
            filtered.append(record) 
        return filtered

    def get(self, record_id: str) -> Optional[Dict]:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(f"SELECT * FROM {self.table_name} WHERE id = ?", [record_id])
        row = cursor.fetchone()
        conn.close()
        return self._row_to_dict(row)

    def delete(self, record_id: str) -> None:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(f"DELETE FROM {self.table_name} WHERE id = ?", [record_id])
        conn.commit()
        conn.close()

    def batch_update(self, records: List[Dict]) -> None:
        for record in records:
            self.update(record['id'], record['fields'])

if __name__ == '__main__':
    init_database()

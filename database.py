import sqlite3
import json
from datetime import datetime
from typing import Optional, List, Dict, Any
import os

# Wspólna baza danych dla bota i backendu
# Możesz ustawić zmienną środowiskową KORKI_DB_PATH lub użyje domyślnej
DB_PATH = os.environ.get('KORKI_DB_PATH', '/home/nikodnaj3/korki.db')

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
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Tabela Korepetytorzy
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS Korepetytorzy (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            TutorID TEXT UNIQUE NOT NULL,
            ImieNazwisko TEXT NOT NULL,
            Poniedzialek TEXT,
            Wtorek TEXT,
            Sroda TEXT,
            Czwartek TEXT,
            Piatek TEXT,
            Sobota TEXT,
            Niedziela TEXT,
            Przedmioty TEXT,
            PoziomNauczania TEXT,
            LINK TEXT,
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
            TypSzkoly TEXT,
            Poziom TEXT,
            Klasa TEXT,
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
    
    # Indeksy dla optymalizacji
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_klienci_clientid ON Klienci(ClientID)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_rezerwacje_klient ON Rezerwacje(Klient)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_rezerwacje_data ON Rezerwacje(Data)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_rezerwacje_token ON Rezerwacje(ManagementToken)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_stale_klient ON StaleRezerwacje(Klient_ID)')
    
    conn.commit()
    conn.close()
    print(f"✓ Baza danych zainicjalizowana: {DB_PATH}")


class DatabaseTable:
    """Klasa abstrakcyjna emulująca interfejs Airtable."""
    
    def __init__(self, table_name: str):
        self.table_name = table_name
    
    def _row_to_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        """Konwertuje wiersz SQLite do formatu podobnego do Airtable."""
        if row is None:
            return None
        
        fields = dict(row)
        record_id = fields.pop('id')
        fields.pop('created_at', None)
        
        # Konwersja JSON strings z powrotem na listy (dla Przedmioty i PoziomNauczania)
        if self.table_name == 'Korepetytorzy':
            if fields.get('Przedmioty'):
                try:
                    fields['Przedmioty'] = json.loads(fields['Przedmioty'])
                except:
                    fields['Przedmioty'] = []
            if fields.get('PoziomNauczania'):
                try:
                    fields['PoziomNauczania'] = json.loads(fields['PoziomNauczania'])
                except:
                    fields['PoziomNauczania'] = []
        
        # Konwersja 0/1 na False/True dla pól boolean
        if self.table_name == 'Rezerwacje':
            fields['JestTestowa'] = bool(fields.get('JestTestowa', 0))
            fields['Oplacona'] = bool(fields.get('Oplacona', 0))
        elif self.table_name == 'StaleRezerwacje':
            fields['Aktywna'] = bool(fields.get('Aktywna', 1))
        
        return {
            'id': str(record_id),
            'fields': fields
        }
    
    def _convert_formula_to_sql(self, formula: str) -> tuple:
        """Konwertuje prostą formułę Airtable na SQL WHERE clause.
        
        Obsługuje podstawowe wzorce używane w aplikacji.
        Zwraca (where_clause, params)
        """
        if not formula:
            return ("1=1", [])
        
        # Proste równości: {Field} = 'value'
        import re
        
        # Pattern: {Field} = 'value'
        simple_eq = re.search(r"\{(\w+)\}\s*=\s*'([^']*)'", formula)
        if simple_eq and 'AND' not in formula and 'OR' not in formula:
            field = simple_eq.group(1)
            value = simple_eq.group(2)
            return (f"{field} = ?", [value])
        
        # Pattern: AND({Field1} = 'value1', {Field2} = 'value2')
        and_pattern = re.findall(r"AND\(([^)]+)\)", formula)
        if and_pattern:
            conditions = []
            params = []
            parts = and_pattern[0].split(',')
            for part in parts:
                eq = re.search(r"\{(\w+)\}\s*=\s*'([^']*)'", part)
                if eq:
                    conditions.append(f"{eq.group(1)} = ?")
                    params.append(eq.group(2))
                # DATETIME_FORMAT({Data}, 'YYYY-MM-DD') = 'date'
                elif 'DATETIME_FORMAT' in part:
                    dt = re.search(r"DATETIME_FORMAT\(\{(\w+)\}[^)]*\)\s*=\s*'([^']*)'", part)
                    if dt:
                        conditions.append(f"{dt.group(1)} = ?")
                        params.append(dt.group(2))
            
            if conditions:
                return (" AND ".join(conditions), params)
        
        # Dla skomplikowanych formuł z datami - zwróć wszystko i filtruj w Pythonie
        return ("1=1", [])
    
    def first(self, formula: str = None) -> Optional[Dict]:
        """Zwraca pierwszy rekord pasujący do formuły."""
        conn = get_connection()
        cursor = conn.cursor()
        
        where_clause, params = self._convert_formula_to_sql(formula)
        query = f"SELECT * FROM {self.table_name} WHERE {where_clause} LIMIT 1"
        
        cursor.execute(query, params)
        row = cursor.fetchone()
        conn.close()
        
        return self._row_to_dict(row)
    
    def all(self, formula: str = None) -> List[Dict]:
        """Zwraca wszystkie rekordy pasujące do formuły."""
        conn = get_connection()
        cursor = conn.cursor()
        
        where_clause, params = self._convert_formula_to_sql(formula)
        query = f"SELECT * FROM {self.table_name} WHERE {where_clause}"
        
        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()
        
        results = [self._row_to_dict(row) for row in rows]
        
        # Dodatkowa filtracja dla złożonych formuł z datami
        if formula and ('IS_AFTER' in formula or 'IS_BEFORE' in formula):
            results = self._filter_by_date_formula(results, formula)
        
        return results
    
    def _filter_by_date_formula(self, records: List[Dict], formula: str) -> List[Dict]:
        """Filtruje rekordy po datach dla złożonych formuł."""
        import re
        from datetime import datetime, timedelta
        
        filtered = []
        for record in records:
            fields = record['fields']
            keep = True
            
            # IS_AFTER({Data}, NOW())
            if 'IS_AFTER' in formula and 'NOW()' in formula:
                date_str = fields.get('Data')
                if date_str:
                    try:
                        record_date = datetime.strptime(date_str, '%Y-%m-%d').date()
                        if record_date <= datetime.now().date():
                            keep = False
                    except:
                        pass
            
            # IS_AFTER({Data}, DATETIME_PARSE('date', 'YYYY-MM-DD'))
            after_match = re.search(r"IS_AFTER\(\{Data\}, DATETIME_PARSE\('([^']+)'", formula)
            if after_match:
                threshold_str = after_match.group(1)
                date_str = fields.get('Data')
                if date_str:
                    try:
                        record_date = datetime.strptime(date_str, '%Y-%m-%d').date()
                        threshold_date = datetime.strptime(threshold_str, '%Y-%m-%d').date()
                        if record_date <= threshold_date:
                            keep = False
                    except:
                        pass
            
            # IS_BEFORE({Data}, DATETIME_PARSE('date', 'YYYY-MM-DD'))
            before_match = re.search(r"IS_BEFORE\(\{Data\}, DATETIME_PARSE\('([^']+)'", formula)
            if before_match:
                threshold_str = before_match.group(1)
                date_str = fields.get('Data')
                if date_str:
                    try:
                        record_date = datetime.strptime(date_str, '%Y-%m-%d').date()
                        threshold_date = datetime.strptime(threshold_str, '%Y-%m-%d').date()
                        if record_date >= threshold_date:
                            keep = False
                    except:
                        pass
            
            if keep:
                filtered.append(record)
        
        return filtered
    
    def get(self, record_id: str) -> Optional[Dict]:
        """Pobiera rekord po ID."""
        conn = get_connection()
        cursor = conn.cursor()
        
        cursor.execute(f"SELECT * FROM {self.table_name} WHERE id = ?", [record_id])
        row = cursor.fetchone()
        conn.close()
        
        return self._row_to_dict(row)
    
    def create(self, fields: Dict[str, Any]) -> Dict:
        """Tworzy nowy rekord."""
        conn = get_connection()
        cursor = conn.cursor()
        
        # Konwersja list na JSON dla Korepetytorzy
        if self.table_name == 'Korepetytorzy':
            if 'Przedmioty' in fields and isinstance(fields['Przedmioty'], list):
                fields['Przedmioty'] = json.dumps(fields['Przedmioty'])
            if 'PoziomNauczania' in fields and isinstance(fields['PoziomNauczania'], list):
                fields['PoziomNauczania'] = json.dumps(fields['PoziomNauczania'])
        
        # Konwersja boolean na 0/1
        if self.table_name == 'Rezerwacje':
            if 'JestTestowa' in fields:
                fields['JestTestowa'] = 1 if fields['JestTestowa'] else 0
            if 'Oplacona' in fields:
                fields['Oplacona'] = 1 if fields['Oplacona'] else 0
        elif self.table_name == 'StaleRezerwacje':
            if 'Aktywna' in fields:
                fields['Aktywna'] = 1 if fields['Aktywna'] else 0
        
        columns = ', '.join(fields.keys())
        placeholders = ', '.join(['?' for _ in fields])
        query = f"INSERT INTO {self.table_name} ({columns}) VALUES ({placeholders})"
        
        cursor.execute(query, list(fields.values()))
        record_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        return self.get(str(record_id))
    
    def update(self, record_id: str, fields: Dict[str, Any]) -> Dict:
        """Aktualizuje rekord."""
        conn = get_connection()
        cursor = conn.cursor()
        
        # Konwersja list na JSON dla Korepetytorzy
        if self.table_name == 'Korepetytorzy':
            if 'Przedmioty' in fields and isinstance(fields['Przedmioty'], list):
                fields['Przedmioty'] = json.dumps(fields['Przedmioty'])
            if 'PoziomNauczania' in fields and isinstance(fields['PoziomNauczania'], list):
                fields['PoziomNauczania'] = json.dumps(fields['PoziomNauczania'])
        
        # Konwersja boolean na 0/1
        if self.table_name == 'Rezerwacje':
            if 'JestTestowa' in fields:
                fields['JestTestowa'] = 1 if fields['JestTestowa'] else 0
            if 'Oplacona' in fields:
                fields['Oplacona'] = 1 if fields['Oplacona'] else 0
        elif self.table_name == 'StaleRezerwacje':
            if 'Aktywna' in fields:
                fields['Aktywna'] = 1 if fields['Aktywna'] else 0
        
        set_clause = ', '.join([f"{k} = ?" for k in fields.keys()])
        query = f"UPDATE {self.table_name} SET {set_clause} WHERE id = ?"
        
        cursor.execute(query, list(fields.values()) + [record_id])
        conn.commit()
        conn.close()
        
        return self.get(record_id)
    
    def delete(self, record_id: str) -> None:
        """Usuwa rekord."""
        conn = get_connection()
        cursor = conn.cursor()
        
        cursor.execute(f"DELETE FROM {self.table_name} WHERE id = ?", [record_id])
        conn.commit()
        conn.close()
    
    def batch_update(self, records: List[Dict]) -> None:
        """Aktualizuje wiele rekordów naraz."""
        for record in records:
            self.update(record['id'], record['fields'])


# Inicjalizacja przy imporcie
if not os.path.exists(DB_PATH):
    init_database()

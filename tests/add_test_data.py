#!/usr/bin/env python3
"""
Skrypt dodający przykładowe dane testowe do bazy danych.
Uruchom: python add_test_data.py
"""

from database import DatabaseTable

def add_test_data():
    print("Dodawanie przykładowych danych testowych...")
    
    # Dodaj testowych klientów
    clients_table = DatabaseTable('Klienci')
    print("\n1. Dodawanie klientów...")
    
    test_clients = [
        {
            'ClientID': '123456789',
            'Imie': 'Jan',
            'Nazwisko': 'Kowalski',
            'LINK': 'https://facebook.com/jan.kowalski'
        },
        {
            'ClientID': '987654321',
            'Imie': 'Anna',
            'Nazwisko': 'Nowak',
            'LINK': 'https://facebook.com/anna.nowak'
        }
    ]
    
    for client in test_clients:
        try:
            clients_table.create(client)
            print(f"   ✓ Dodano klienta: {client['Imie']} {client['Nazwisko']}")
        except Exception as e:
            print(f"   ✗ Błąd: {e}")
    
    # Dodaj testowych korepetytorów
    tutors_table = DatabaseTable('Korepetytorzy')
    print("\n2. Dodawanie korepetytorów...")
    
    test_tutors = [
        {
            'TutorID': 'tutor001',
            'ImieNazwisko': 'Piotr Wiśniewski',
            'Email': 'piotr.wisniewski@zakreconekorepetycje.pl',
            'Poniedziałek': '14:00-20:00',
            'Wtorek': '14:00-20:00',
            'Środa': '14:00-20:00',
            'Czwartek': '14:00-20:00',
            'Piątek': '14:00-20:00',
            'Przedmioty': ['Matematyka', 'Fizyka'],
            'PoziomNauczania': ['podstawowka', 'liceum_podstawa', 'liceum_rozszerzenie'],
            'LINK': 'https://facebook.com/piotr.wisniewski'
        },
        {
            'TutorID': 'tutor002',
            'ImieNazwisko': 'Maria Kowalczyk',
            'Email': 'maria.kowalczyk@zakreconekorepetycje.pl',
            'Poniedziałek': '16:00-21:00',
            'Środa': '16:00-21:00',
            'Piątek': '16:00-21:00',
            'Przedmioty': ['Polski', 'Angielski'],
            'PoziomNauczania': ['podstawowka', 'liceum_podstawa'],
            'LINK': 'https://facebook.com/maria.kowalczyk'
        }
    ]
    
    for tutor in test_tutors:
        try:
            tutors_table.create(tutor)
            print(f"   ✓ Dodano korepetytora: {tutor['ImieNazwisko']}")
        except Exception as e:
            print(f"   ✗ Błąd: {e}")
    
    # Dodaj testowe rezerwacje
    reservations_table = DatabaseTable('Rezerwacje')
    print("\n3. Dodawanie rezerwacji...")
    
    from datetime import datetime, timedelta
    import pytz
    tomorrow = (datetime.now(pytz.timezone('Europe/Warsaw')) + timedelta(days=1)).strftime('%Y-%m-%d')
    
    test_reservations = [
        {
            'Klient': '123456789',
            'Korepetytor': 'Piotr Wiśniewski',
            'Data': tomorrow,
            'Godzina': '15:00',
            'Przedmiot': 'Matematyka',
            'Status': 'Oczekuje na płatność',
            'Typ': 'Jednorazowa',
            'ManagementToken': 'test-token-123',
            'TeamsLink': 'https://teams.microsoft.com/test',
            'JestTestowa': True,
            'Oplacona': False,
            'TypSzkoly': 'liceum',
            'Poziom': 'podstawowy',
            'Klasa': '2'
        }
    ]
    
    for reservation in test_reservations:
        try:
            reservations_table.create(reservation)
            print(f"   ✓ Dodano rezerwację: {reservation['Przedmiot']} - {reservation['Data']} {reservation['Godzina']}")
        except Exception as e:
            print(f"   ✗ Błąd: {e}")
    
    # Dodaj stałą rezerwację
    cyclic_table = DatabaseTable('StaleRezerwacje')
    print("\n4. Dodawanie stałych rezerwacji...")
    
    test_cyclic = [
        {
            'Klient_ID': '123456789',
            'Korepetytor': 'Piotr Wiśniewski',
            'DzienTygodnia': 'Poniedziałek',
            'Godzina': '16:00',
            'Przedmiot': 'Matematyka',
            'Aktywna': True,
            'TypSzkoly': 'liceum',
            'Poziom': 'rozszerzony',
            'Klasa': '2'
        }
    ]
    
    for cyclic in test_cyclic:
        try:
            cyclic_table.create(cyclic)
            print(f"   ✓ Dodano stałą rezerwację: {cyclic['Przedmiot']} - {cyclic['DzienTygodnia']} {cyclic['Godzina']}")
        except Exception as e:
            print(f"   ✗ Błąd: {e}")
    
    print("\n✓ Zakończono dodawanie danych testowych!")
    print("\nMożesz teraz:")
    print("1. Uruchomić backend: python backend.py")
    print("2. Otworzyć panel administracyjny: http://localhost:5000/baza-danych.html")
    print("3. Zalogować się hasłem: szlafrok")

if __name__ == '__main__':
    add_test_data()

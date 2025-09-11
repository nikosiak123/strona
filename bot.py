import os
import json
import uuid
import traceback
from flask import Flask, jsonify, request, abort
from flask_cors import CORS
from pyairtable import Api
from datetime import datetime, timedelta, time
import requests

# --- Konfiguracja (bez zmian) ---
AIRTABLE_API_KEY = "patcSdupvwJebjFDo.7e15a93930d15261989844687bcb15ac5c08c84a29920c7646760bc6f416146d"
AIRTABLE_BASE_ID = "appTjrMTVhYBZDPw9"
TUTORS_TABLE_NAME = "Korepetytorzy"
RESERVATIONS_TABLE_NAME = "Rezerwacje"
CLIENTS_TABLE_NAME = "Klienci"

MS_TENANT_ID = "58928953-69aa-49da-b96c-100396a3caeb"
MS_CLIENT_ID = "8bf9be92-1805-456a-9162-ffc7cda3b794"
MS_CLIENT_SECRET = "MQ~8Q~VD9sI3aB19_Drwqndp4j5V_WAjmwK3yaQD"
MEETING_ORGANIZER_USER_ID = "8cf07b71-d305-4450-9b70-64cb5be6ecef"

api = Api(AIRTABLE_API_KEY)
tutors_table = api.table(AIRTABLE_BASE_ID, TUTORS_TABLE_NAME)
reservations_table = api.table(AIRTABLE_BASE_ID, RESERVATIONS_TABLE_NAME)
clients_table = api.table(AIRTABLE_BASE_ID, CLIENTS_TABLE_NAME)

app = Flask(__name__)
CORS(app)

WEEKDAY_MAP = {
    0: "Poniedziałek", 1: "Wtorek", 2: "Środa", 3: "Czwartek",
    4: "Piątek", 5: "Sobota", 6: "Niedziela"
}

last_fetched_schedule = {}

def parse_time_range(time_range_str):
    try:
        if not time_range_str or '-' not in time_range_str: return None, None
        start_str, end_str = time_range_str.split('-')
        start_time = datetime.strptime(start_str.strip(), '%H:%M').time()
        end_time = datetime.strptime(end_str.strip(), '%H:%M').time()
        return start_time, end_time
    except ValueError:
        print(f"BŁĄD: Nie można przetworzyć zakresu czasu: '{time_range_str}'")
        return None, None

def generate_teams_meeting_link(meeting_subject):
    print(f"INFO: Generowanie linku Teams dla: '{meeting_subject}'")
    token_url = f"https://login.microsoftonline.com/{MS_TENANT_ID}/oauth2/v2.0/token"
    token_data = {'grant_type': 'client_credentials', 'client_id': MS_CLIENT_ID, 'client_secret': MS_CLIENT_SECRET, 'scope': 'https://graph.microsoft.com/.default'}
    token_r = requests.post(token_url, data=token_data)
    if token_r.status_code != 200:
        print(f"BŁĄD TEAMS API (TOKEN): {token_r.status_code} - {token_r.text}")
        return None
    access_token = token_r.json().get('access_token')
    if not access_token: return None
    meetings_url = f"https://graph.microsoft.com/v1.0/users/{MEETING_ORGANIZER_USER_ID}/onlineMeetings"
    headers = {'Authorization': f'Bearer {access_token}', 'Content-Type': 'application/json'}
    start_time = datetime.utcnow() + timedelta(minutes=5)
    end_time = start_time + timedelta(hours=1)
    meeting_payload = {"subject": meeting_subject, "startDateTime": start_time.strftime('%Y-%m-%dT%H:%M:%SZ'), "endDateTime": end_time.strftime('%Y-%m-%dT%H:%M:%SZ'), "lobbyBypassSettings": {"scope": "everyone"}, "allowedPresenters": "everyone"}
    meeting_r = requests.post(meetings_url, headers=headers, data=json.dumps(meeting_payload))
    if meeting_r.status_code == 201:
        print("SUKCES: Link Teams wygenerowany.")
        return meeting_r.json().get('joinUrl')
    print(f"BŁĄD TEAMS API (MEETING): {meeting_r.status_code} - {meeting_r.text}")
    return None

def find_reservation_by_token(token):
    if not token: return None
    try:
        return reservations_table.first(formula=f"{{ManagementToken}} = '{token}'")
    except Exception as e:
        print(f"Błąd podczas wyszukiwania tokenu: {e}")
        return None

def is_cancellation_allowed(record):
    fields = record.get('fields', {})
    lesson_date_str = fields.get('Data')
    lesson_time_str = fields.get('Godzina')
    if not lesson_date_str or not lesson_time_str: return False
    lesson_datetime = datetime.strptime(f"{lesson_date_str} {lesson_time_str}", "%Y-%m-%d %H:%M")
    return (lesson_datetime - datetime.now()) > timedelta(hours=12)

@app.route('/api/verify-client')
def verify_client():
    client_id = request.args.get('clientID')
    if not client_id:
        abort(400, "Brak identyfikatora klienta.")
    client_id = client_id.strip()
    formula = f"{{ClientID}} = '{client_id}'"
    try:
        client_record = clients_table.first(formula=formula)
        if not client_record:
            abort(404, "Klient o podanym identyfikatorze nie istnieje.")
        fields = client_record.get('fields', {})
        return jsonify({"firstName": fields.get('Imię'), "lastName": fields.get('Nazwisko')})
    except Exception as e:
        traceback.print_exc()
        abort(500, "Wewnętrzny błąd serwera podczas weryfikacji klienta.")

@app.route('/api/get-schedule')
def get_schedule():
    global last_fetched_schedule
    try:
        start_date_str = request.args.get('startDate')
        if not start_date_str: abort(400, "Brak parametru startDate")
        start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
        end_date = start_date + timedelta(days=7)
        tutor_templates = tutors_table.all()
        formula = f"AND(IS_AFTER({{Data}}, DATETIME_PARSE('{start_date - timedelta(days=1)}', 'YYYY-MM-DD')), IS_BEFORE({{Data}}, DATETIME_PARSE('{end_date}', 'YYYY-MM-DD')))"
        reservations_records = reservations_table.all(formula=formula)
        booked_slots = set()
        for record in reservations_records:
            fields = record.get('fields', {})
            if 'Korepetytor' in fields and 'Data' in fields and 'Godzina' in fields:
                key = (fields['Korepetytor'], fields['Data'], fields['Godzina'])
                booked_slots.add(key)
        available_slots = []
        for template in tutor_templates:
            fields = template.get('fields', {})
            tutor_name = fields.get('Imię i Nazwisko')
            if not tutor_name: continue
            for day_offset in range(7):
                current_date = start_date + timedelta(days=day_offset)
                day_of_week = current_date.weekday()
                day_column_name = WEEKDAY_MAP[day_of_week]
                time_range_str = fields.get(day_column_name)
                if not time_range_str: continue
                start_time, end_time = parse_time_range(time_range_str)
                if not start_time or not end_time: continue
                current_slot_time = start_time
                while current_slot_time < end_time:
                    slot_time_str = current_slot_time.strftime('%H:%M')
                    current_date_str = current_date.strftime('%Y-%m-%d')
                    if (tutor_name, current_date_str, slot_time_str) not in booked_slots:
                        available_slots.append({"tutor": tutor_name, "date": current_date_str, "time": slot_time_str})
                    current_slot_datetime = datetime.combine(current_date, current_slot_time)
                    next_slot_datetime = current_slot_datetime + timedelta(hours=1)
                    current_slot_time = next_slot_datetime.time()
        last_fetched_schedule = available_slots
        return jsonify(available_slots)
    except Exception as e:
        traceback.print_exc()
        abort(500, "Wewnętrzny błąd serwera podczas pobierania grafiku.")

@app.route('/api/create-reservation', methods=['POST'])
def create_reservation():
    try:
        data = request.json
        client_uuid = data.get('clientID')
        if not client_uuid: abort(400, "Brak ClientID w zapytaniu.")
        
        client_record = clients_table.first(formula=f"{{ClientID}} = '{client_uuid.strip()}'")
        if not client_record: abort(404, "Klient o podanym identyfikatorze nie istnieje.")
        
        client_id_record = client_record['id']
        first_name = client_record['fields'].get('Imię')
        teams_link = generate_teams_meeting_link(f"Korepetycje: {data['subject']} dla {first_name}")
        if not teams_link: abort(500, "Nie udało się wygenerować linku Teams.")
        
        management_token = str(uuid.uuid4())
        
        tutor_for_reservation = data['tutor']
        if tutor_for_reservation == 'Dowolny dostępny':
            # ### POPRAWKA ###
            # Używamy zmiennej globalnej 'last_fetched_schedule', która przechowuje ostatnio wygenerowany grafik
            found_slot = next((slot for slot in last_fetched_schedule if slot['date'] == data['selectedDate'] and slot['time'] == data['selectedTime']), None)
            if found_slot:
                tutor_for_reservation = found_slot['tutor']
            else:
                abort(500, "Wybrany termin stał się niedostępny.")
        
        new_reservation = {
            "Klient": [client_id_record], "Korepetytor": tutor_for_reservation,
            "Data": data['selectedDate'], "Godzina": data['selectedTime'],
            "Przedmiot": data['subject'], "ManagementToken": management_token
        }
        reservations_table.create(new_reservation)
        return jsonify({"teamsUrl": teams_link, "managementToken": management_token, "clientID": client_uuid})
    except Exception as e:
        traceback.print_exc()
        abort(500, "Błąd serwera podczas zapisu rezerwacji.")

@app.route('/api/get-client-dashboard')
def get_client_dashboard():
    client_id = request.args.get('clientID')
    if not client_id: abort(400, "Brak identyfikatora klienta.")
    client_record = clients_table.first(formula=f"{{ClientID}} = '{client_id.strip()}'")
    if not client_record: abort(404, "Nie znaleziono klienta.")
    reservation_ids = client_record['fields'].get('Rezerwacje', [])
    if not reservation_ids:
        return jsonify({"clientName": client_record['fields'].get('Imię'), "upcomingLessons": [], "pastLessons": []})
    reservations_filter = "OR(" + ",".join([f"RECORD_ID()='{rid}'" for rid in reservation_ids]) + ")"
    all_reservations = reservations_table.all(formula=reservations_filter)
    
    upcoming, past = [], []
    for record in all_reservations:
        fields = record['fields']
        lesson_datetime = datetime.strptime(f"{fields['Data']} {fields['Godzina']}", "%Y-%m-%d %H:%M")
        lesson_data = {
            "date": fields['Data'], "time": fields['Godzina'], "tutor": fields.get('Korepetytor', 'N/A'),
            "subject": fields.get('Przedmiot', 'N/A'), "managementToken": fields.get('ManagementToken')
        }
        if lesson_datetime > datetime.now(): upcoming.append(lesson_data)
        else: past.append(lesson_data)
            
    upcoming.sort(key=lambda x: datetime.strptime(f"{x['date']} {x['time']}", "%Y-%m-%d %H:%M"))
    past.sort(key=lambda x: datetime.strptime(f"{x['date']} {x['time']}", "%Y-%m-%d %H:%M"), reverse=True)
    
    return jsonify({"clientName": client_record['fields'].get('Imię'), "upcomingLessons": upcoming, "pastLessons": past})

@app.route('/api/get-reservation-details')
def get_reservation_details():
    token = request.args.get('token')
    record = find_reservation_by_token(token)
    if not record: abort(404, "Nie znaleziono rezerwacji.")
    
    fields = record.get('fields', {})
    client_link = fields.get('Klient')
    student_name = "N/A"
    if client_link:
        try:
            client_record = clients_table.get(client_link[0])
            student_name = client_record.get('fields', {}).get('Imię', 'N/A')
        except: student_name = "Błąd pobierania danych"

    return jsonify({
        "date": fields.get('Data'), "time": fields.get('Godzina'), "tutor": fields.get('Korepetytor'),
        "student": student_name, "isCancellationAllowed": is_cancellation_allowed(record)
    })

@app.route('/api/cancel-reservation', methods=['POST'])
def cancel_reservation():
    token = request.json.get('token')
    record = find_reservation_by_token(token)
    if not record: abort(404, "Nie znaleziono rezerwacji.")
    if not is_cancellation_allowed(record): abort(403, "Nie można odwołać rezerwacji. Pozostało mniej niż 12 godzin.")
    try:
        reservations_table.delete(record['id'])
        return jsonify({"message": "Rezerwacja została pomyślnie odwołana."})
    except Exception as e: abort(500, "Wystąpił błąd podczas odwoływania rezerwacji.")

@app.route('/api/reschedule-reservation', methods=['POST'])
def reschedule_reservation():
    data = request.json
    token = data.get('token')
    new_date = data.get('newDate')
    new_time = data.get('newTime')
    record = find_reservation_by_token(token)
    if not record: abort(404, "Nie znaleziono rezerwacji.")
    if not is_cancellation_allowed(record): abort(403, "Nie można zmienić terminu rezerwacji.")
    try:
        reservations_table.update(record['id'], {"Data": new_date, "Godzina": new_time})
        return jsonify({"message": f"Termin został zmieniony na {new_date} o {new_time}."})
    except Exception as e: abort(500, "Wystąpił błąd podczas zmiany terminu.")

# Usunięto app.run() - nie jest potrzebny w produkcji na Cloud Run
# if __name__ == '__main__':
#     app.run(port=5000, debug=True)

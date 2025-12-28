from flask import Flask
from database_stats import get_stats
from datetime import datetime, timedelta

app = Flask(__name__)

@app.route('/stats')
def stats():
    stats_data = get_stats()
    html = "<h1>Statystyki komentarzy Facebook</h1>"
    
    # Sprawdź, czy skrypt działa (ostatni komentarz w ciągu 1 godziny)
    is_running = False
    last_time = None
    if stats_data:
        latest = stats_data[0]  # Najnowszy rekord
        last_time_str = latest.get('LastCommentTime')
        if last_time_str:
            last_time = datetime.strptime(last_time_str, '%Y-%m-%d %H:%M:%S')
            if datetime.now() - last_time < timedelta(hours=1):
                is_running = True
    
    status = "Tak" if is_running else "Nie"
    last_comment = last_time.strftime('%Y-%m-%d %H:%M:%S') if last_time else "Brak"
    html += f"<p><strong>Skrypt działa:</strong> {status} (ostatni komentarz: {last_comment})</p>"
    
    html += "<table border='1'><tr><th>Data</th><th>Przesłane</th><th>Odrzucone</th><th>Oczekuje</th><th>Ostatni komentarz</th></tr>"
    for stat in stats_data:
        html += f"<tr><td>{stat['Data']}</td><td>{stat['Przeslane']}</td><td>{stat['Odrzucone']}</td><td>{stat['Oczekuje']}</td><td>{stat['LastCommentTime'] or 'Brak'}</td></tr>"
    html += "</table>"
    return html

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)
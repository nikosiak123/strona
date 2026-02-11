"""
statystyki_share.py - Serwer dla udostępniania statystyk komentarzy Facebook
Uruchomić na maszynie z bazą danych, aby udostępnić statystyki dla innych maszyn.
"""

import os
import sys
from flask import Flask, jsonify, send_from_directory, request
from flask_cors import CORS
from datetime import datetime

# Dodaj bieżący katalog do ścieżki (gdzie jest database_stats.py)
sys.path.append(os.path.dirname(__file__))

# Import modułu statystyk
from database_stats import get_stats
from database_hourly_stats import get_hourly_stats

# Dodaj tę stałą na początku, obok innych ścieżek (jeśli nie ma)
STATUS_SCREENSHOTS_DIR = os.path.join(os.path.dirname(__file__), 'status_screenshots')

app = Flask(__name__)
CORS(app)

@app.route('/api/facebook-stats', methods=['GET'])
def get_facebook_stats():
    """Zwraca statystyki komentarzy Facebook z dodatkowymi informacjami."""
    try:
        from datetime import datetime, timedelta
        stats_data = get_stats()

        # Sprawdź, czy skrypt analizy działa (ostatni komentarz w ciągu 1 godziny)
        is_running = False
        last_comment_time = None
        if stats_data:
            latest = stats_data[0]  # Najnowszy rekord (zakładamy sortowanie DESC)
            last_time_str = latest.get('LastCommentTime')
            if last_time_str:
                try:
                    last_comment_time = datetime.strptime(last_time_str, '%Y-%m-%d %H:%M:%S')
                    if datetime.now() - last_comment_time < timedelta(hours=1):
                        is_running = True
                except ValueError:
                    pass  # Błąd parsowania daty

        # Zwróć dane z dodatkowymi informacjami
        return jsonify({
            "stats": stats_data,
            "isRunning": is_running,
            "lastCommentTime": last_comment_time.strftime('%Y-%m-%d %H:%M:%S') if last_comment_time else "Brak"
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/facebook-hourly-stats', methods=['GET'])
def get_facebook_hourly_stats():
    """Zwraca godzinowe statystyki z ostatnich 48 godzin w porządku chronologicznym."""
    try:
        # Pobieramy dane (są posortowane od najnowszych do najstarszych)
        stats_data = get_hourly_stats(limit=48)
        # Odwracamy listę, aby na wykresie były w porządku chronologicznym (od najstarszych do najnowszych)
        return jsonify({"stats": stats_data[::-1]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/facebook-status-screenshots', methods=['GET'])
def get_status_screenshots():
    """Zwraca listę screenshotów statusu."""
    try:
        if not os.path.exists(STATUS_SCREENSHOTS_DIR):
            return jsonify({"screenshots": []})

        screenshots = []
        for filename in os.listdir(STATUS_SCREENSHOTS_DIR):
            if filename.endswith('.png'):
                # Format nazwy: STATUS_YYYYMMDD_HHMMSS.png
                try:
                    # Pobieramy datę modyfikacji pliku dla sortowania
                    filepath = os.path.join(STATUS_SCREENSHOTS_DIR, filename)
                    timestamp = os.path.getmtime(filepath)
                    dt_object = datetime.fromtimestamp(timestamp)
                    
                    screenshots.append({
                        'filename': filename,
                        'timestamp': dt_object.strftime('%Y-%m-%d %H:%M:%S'),
                        'raw_timestamp': timestamp
                    })
                except Exception:
                    continue

        # Sortuj od najnowszych
        sorted_screenshots = sorted(screenshots, key=lambda x: x['raw_timestamp'], reverse=True)
        return jsonify({"screenshots": sorted_screenshots})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/download-status-screenshot', methods=['GET'])
def download_status_screenshot():
    """Pobiera plik screenshota statusu."""
    try:
        filename = request.args.get('file')
        if not filename:
            return jsonify({"error": "Brak parametru file"}), 400

        if not os.path.exists(os.path.join(STATUS_SCREENSHOTS_DIR, filename)):
            return jsonify({"error": "Plik nie istnieje"}), 404

        return send_from_directory(STATUS_SCREENSHOTS_DIR, filename, as_attachment=True)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/facebook-errors', methods=['GET'])
def get_facebook_errors():
    """Zwraca listę błędów skryptu Facebook."""
    try:
        debug_logs_dir = os.path.join(os.path.dirname(__file__), 'debug_logs')
        if not os.path.exists(debug_logs_dir):
            return jsonify({"errors": []})

        errors = []
        for filename in os.listdir(debug_logs_dir):
            if filename.startswith('ERROR_') and filename.endswith(('.png', '.html')):
                # Parse filename: ERROR_location_YYYYMMDD_HHMMSS.ext
                name_without_ext = filename.rsplit('.', 1)[0]
                parts = name_without_ext.split('_')
                if len(parts) >= 4 and len(parts[-1]) == 6 and len(parts[-2]) == 8:
                    timestamp_str = parts[-2] + '_' + parts[-1]
                    location = '_'.join(parts[1:-2])
                    try:
                        timestamp = datetime.strptime(timestamp_str, '%Y%m%d_%H%M%S')
                        ext = filename.split('.')[-1]
                        errors.append({
                            'filename': filename,
                            'location': location,
                            'timestamp': timestamp,
                            'type': ext
                        })
                    except ValueError:
                        continue

        # Group by location and timestamp
        error_groups = {}
        for error in errors:
            key = (error['location'], error['timestamp'])
            if key not in error_groups:
                error_groups[key] = {'png': None, 'html': None, 'timestamp': error['timestamp'], 'location': error['location']}
            error_groups[key][error['type']] = error['filename']

        # Sort by timestamp descending
        sorted_groups = sorted(error_groups.values(), key=lambda x: x['timestamp'], reverse=True)

        result = []
        for group in sorted_groups:
            result.append({
                'timestamp': group['timestamp'].isoformat(),
                'location': group['location'],
                'png': group['png'],
                'html': group['html']
            })

        return jsonify({"errors": result})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/download-error', methods=['GET'])
def download_error():
    """Pobiera plik błędu."""
    try:
        filename = sys.modules[__name__].__dict__.get('request').args.get('file')
        if not filename:
            return jsonify({"error": "Brak parametru file"}), 400

        debug_logs_dir = os.path.join(os.path.dirname(__file__), 'debug_logs')
        file_path = os.path.join(debug_logs_dir, filename)

        if not os.path.exists(file_path):
            return jsonify({"error": "Plik nie istnieje"}), 404

        return send_from_directory(debug_logs_dir, filename, as_attachment=True)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/')
def index():
    """Prosta strona główna."""
    return "<h1>Serwer statystyk Facebook</h1><p>Endpoints: /api/facebook-stats, /api/facebook-errors, /api/download-error</p>"

if __name__ == '__main__':
    # Uruchom serwer na porcie 5000 (można zmienić)
    print("--- Uruchamianie serwera statystyki_share.py na porcie 5000 ---")
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)
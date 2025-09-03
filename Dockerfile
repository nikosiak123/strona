# Użyj oficjalnego, lekkiego obrazu Python
FROM python:3.11-slim

# Ustaw zmienną środowiskową, aby logi z Pythona pojawiały się od razu
ENV PYTHONUNBUFFERED True

# Ustaw katalog roboczy wewnątrz kontenera
WORKDIR /app

# Skopiuj plik z zależnościami i zainstaluj je
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Skopiuj resztę kodu aplikacji do kontenera
COPY . .

# Uruchom serwer Gunicorn. Cloud Run oczekuje aplikacji na porcie 8080.
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "1", "--threads", "8", "backend:app"]
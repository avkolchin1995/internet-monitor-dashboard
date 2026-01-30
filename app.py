from flask import Flask, render_template, jsonify
from internet_monitor import InternetMonitor
import threading
import time

app = Flask(__name__)
monitor = InternetMonitor()

# Кешируем результаты на 5 секунд для уменьшения нагрузки
cached_stats = {}
cache_lock = threading.Lock()
cache_time = 0
CACHE_TTL = 5  # секунд

def update_cache():
    global cached_stats, cache_time
    while True:
        with cache_lock:
            cached_stats = monitor.get_all_stats()
            cache_time = time.time()
        time.sleep(10)  # Обновление каждые 10 секунд

# Запускаем фоновый поток для обновления кеша
update_thread = threading.Thread(target=update_cache, daemon=True)
update_thread.start()

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/stats')
def get_stats():
    with cache_lock:
        # Если кеш устарел, обновляем его
        if time.time() - cache_time > CACHE_TTL:
            cached_stats = monitor.get_all_stats()
        return jsonify(cached_stats)

@app.route('/api/logs')
def get_logs():
    try:
        with open('internet_events.log', 'r', encoding='utf-8') as f:
            lines = f.readlines()[-50:]  # Последние 50 строк
        return jsonify({'logs': lines})
    except FileNotFoundError:
        return jsonify({'logs': []})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
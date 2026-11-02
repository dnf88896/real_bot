import threading
import urllib.request
import webbrowser

URL = 'http://127.0.0.1:8765'
try:
    with urllib.request.urlopen(URL + '/api/state', timeout=2) as response:
        running = response.status == 200
except OSError:
    running = False

if running:
    webbrowser.open(URL)
else:
    import runpy
    threading.Timer(1, lambda: webbrowser.open(URL)).start()
    runpy.run_path('server.py', run_name='__main__')

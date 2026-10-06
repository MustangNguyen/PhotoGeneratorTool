#!/usr/bin/env python3
"""Open Puzzle Atelier from the desktop, starting its local server when needed."""
import argparse
import fcntl
import json
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
URL = 'http://127.0.0.1:8787'


def running():
    try:
        with urllib.request.urlopen(URL + '/api/settings', timeout=1) as response:
            data = json.load(response)
        return isinstance(data, dict) and 'codex_available' in data and data.get('provider') in {'codex', 'antigravity', 'openai'}
    except (OSError, ValueError):
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--no-browser', action='store_true', help='Verify/start the server without opening a browser.')
    args = parser.parse_args()
    data = ROOT / 'data'
    data.mkdir(exist_ok=True)
    with (data / '.launcher.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not running():
            with (data / 'launcher.log').open('a') as log:
                process = subprocess.Popen([sys.executable, str(ROOT / 'app.py')], cwd=ROOT,
                                           stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                           start_new_session=True)
            for _ in range(80):
                if running():
                    break
                if process.poll() is not None:
                    raise RuntimeError('Không khởi động được tool. Xem data/launcher.log trong thư mục PhotoGeneratorTool.')
                time.sleep(0.25)
            else:
                raise RuntimeError('Tool chưa sẵn sàng. Xem data/launcher.log rồi mở lại shortcut.')
    if not args.no_browser:
        subprocess.Popen(['xdg-open', URL], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
    print(URL)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        message = str(error)
        if shutil.which('zenity'):
            subprocess.run(['zenity', '--error', '--title=Puzzle Atelier', '--text=' + message], check=False)
        elif shutil.which('notify-send'):
            subprocess.run(['notify-send', 'Puzzle Atelier', message], check=False)
        print(message, file=sys.stderr)
        sys.exit(1)

"""Local Windows start/stop helper, never exposed by the Web/API."""
import argparse
import ctypes
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / '.secrets'


def alive(pid):
    kernel = ctypes.windll.kernel32
    handle = kernel.OpenProcess(0x00100000, False, pid)
    if not handle:
        return False
    try:
        return kernel.WaitForSingleObject(handle, 0) == 258
    finally:
        kernel.CloseHandle(handle)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['start', 'stop', 'status'])
    args = parser.parse_args()
    RUNTIME.mkdir(exist_ok=True)
    pid_file = RUNTIME / 'worker.pid'
    stop_file = RUNTIME / 'worker.stop'
    pid = int(pid_file.read_text()) if pid_file.exists() else None
    running = pid is not None and alive(pid)
    if args.command == 'start':
        if running:
            print(json.dumps({'running': True, 'pid': pid}))
            return
        stop_file.unlink(missing_ok=True)
        with (RUNTIME / 'worker-console.log').open('ab') as stream:
            process = subprocess.Popen([sys.executable, '-m', 'resource_simulator', 'run',
                                         '--db', str(RUNTIME / 'worker.sqlite3'),
                                         '--cloud-config', str(RUNTIME / 'cloud.toml'),
                                         '--stop-file', str(stop_file)], cwd=ROOT, stdout=stream,
                                        stderr=stream, creationflags=subprocess.CREATE_NO_WINDOW)
        pid_file.write_text(str(process.pid))
        time.sleep(1)
        if process.poll() is not None:
            raise RuntimeError('Worker did not start; inspect private worker.log')
        print(json.dumps({'running': True, 'pid': process.pid}))
    elif args.command == 'stop':
        if running:
            stop_file.touch()
            deadline = time.monotonic() + 20
            while alive(pid) and time.monotonic() < deadline:
                time.sleep(.2)
            if alive(pid):
                raise RuntimeError('Worker is still stopping; no forced termination performed')
        print(json.dumps({'running': False}))
    else:
        print(json.dumps({'running': running, 'pid': pid if running else None}))


if __name__ == '__main__':
    main()

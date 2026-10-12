"""Network-free process tree: detached grandchild, ignored TERM, local heartbeat."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

role, directory, mode = sys.argv[1:]
root = Path(directory)
(root / (role + '.pid')).write_text(str(os.getpid()))
if role != 'wrapper':
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    signal.signal(signal.SIGINT, signal.SIG_IGN)
if role == 'wrapper':
    subprocess.Popen([sys.executable, __file__, 'child', directory, mode])
    deadline = time.monotonic() + 5
    while not (root / 'ready').exists():
        if time.monotonic() > deadline:
            sys.exit(99)
        time.sleep(0.005)
    if mode == 'early-success':
        sys.exit(0)
    if mode == 'early-failure':
        sys.exit(7)
elif role == 'child':
    subprocess.Popen([sys.executable, __file__, 'grandchild', directory, mode], start_new_session=True)
else:
    (root / 'ready').write_text('ready')
while True:
    with (root / (role + '.heartbeat')).open('ab') as heartbeat:
        heartbeat.write(b'.')
    time.sleep(0.01)

"""Single-container Databricks Apps entrypoint for a LiveKit voice agent.

Binds the stdlib web/token server (app/web_server.py) to the app port within
seconds so the platform health check passes, then boots the LiveKit agent
worker (app/agent.py) BEHIND it in a private venv at /tmp/agent-venv. The Apps
image ships a shared Python env whose preinstalled packages collide with the
agent's pinned LiveKit stack, so that stack is never installed into it: it lives
in agent-requirements.txt (a filename the platform auto-installer ignores) and
installs into the isolated venv at boot.

Adapt: change the two subprocess paths if your web tier / worker live elsewhere.
"""

import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).parent
os.chdir(ROOT)

os.environ.setdefault("DATABRICKS_APP_PORT", "8000")
os.environ["PYTHONUNBUFFERED"] = "1"

# Authenticate the app with the injected PAT (DATABRICKS_TOKEN from a secret).
# Apps ALSO inject OAuth service-principal creds; the Databricks SDK refuses
# ambiguous auth, so drop the OAuth creds and let the PAT win.
os.environ.pop("DATABRICKS_CLIENT_ID", None)
os.environ.pop("DATABRICKS_CLIENT_SECRET", None)

# The runtime may inject DATABRICKS_HOST without a scheme.
_host = os.environ.get("DATABRICKS_HOST", "")
if _host and not _host.startswith("http"):
    os.environ["DATABRICKS_HOST"] = f"https://{_host}"


def log(msg: str) -> None:
    print(f"[boot] {msg}", flush=True)


log(f"python {sys.version.split()[0]} at {sys.executable}")
web = subprocess.Popen([sys.executable, "app/web_server.py"])
log(f"web server pid {web.pid} binding 0.0.0.0:{os.environ['DATABRICKS_APP_PORT']}")

agent_proc = None  # type: subprocess.Popen | None


def _step(desc, cmd):
    print(f"[agent-boot] {desc} @ {time.strftime('%H:%M:%S')}", flush=True)
    res = subprocess.run(cmd, capture_output=True, text=True)
    tail = "\n".join((res.stdout + "\n" + res.stderr).strip().splitlines()[-10:])
    if res.returncode != 0:
        print(f"[agent-boot] step failed (exit {res.returncode}):\n{tail}", flush=True)
        raise RuntimeError(f"{desc} failed")
    if tail:
        print(tail, flush=True)


def boot_agent():
    global agent_proc
    venv_py = "/tmp/agent-venv/bin/python"
    try:
        _step("creating isolated venv", [sys.executable, "-m", "venv", "/tmp/agent-venv"])
        if shutil.which("uv"):
            _step("installing agent deps with uv",
                  ["uv", "pip", "install", "--python", venv_py, "-r", "agent-requirements.txt"])
        else:
            _step("installing agent deps with pip (the long step)",
                  [venv_py, "-m", "pip", "install", "--no-cache-dir", "--prefer-binary",
                   "--progress-bar", "off", "-r", "agent-requirements.txt"])
        os.environ.setdefault("HF_HOME", "/tmp/hf")
        _step("downloading model files", [venv_py, "app/agent.py", "download-files"])
        print(f"[agent-boot] starting worker @ {time.strftime('%H:%M:%S')}", flush=True)
        agent_proc = subprocess.Popen([venv_py, "app/agent.py", "start"])
    except Exception as exc:  # never take the web tier down with us
        print(f"[agent-boot] FAILED: {exc}", flush=True)


threading.Thread(target=boot_agent, daemon=True).start()


def shutdown(signum, _frame):
    log(f"signal {signum} received; stopping worker and web server")
    for proc in (agent_proc, web):
        if proc is not None and proc.poll() is None:
            proc.terminate()
    deadline = time.time() + 5
    for proc in (agent_proc, web):
        if proc is None:
            continue
        while proc.poll() is None and time.time() < deadline:
            time.sleep(0.2)
        if proc.poll() is None:
            proc.kill()
    sys.exit(0)


signal.signal(signal.SIGTERM, shutdown)
signal.signal(signal.SIGINT, shutdown)

# The app lives and dies with the web server.
sys.exit(web.wait())

import os, subprocess, sys

app_dir = os.path.join(os.path.dirname(__file__), "app")
os.chdir(app_dir)
subprocess.run([sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", "8000", "--reload"])


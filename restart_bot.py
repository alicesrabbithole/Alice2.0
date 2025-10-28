import subprocess
import time

while True:
    print("🔁 Starting bot...")
    subprocess.call(["python", "main.py"])
    print("🔄 Bot exited. Restarting in 2 seconds...")
    time.sleep(2)

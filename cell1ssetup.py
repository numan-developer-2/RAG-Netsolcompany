# CELL 1 - INSTALL & SETUP (Run this first)
# Works on local Windows/macOS/Linux and Google Colab.

import platform
import subprocess
import sys


PACKAGES = [
    "playwright",
    "beautifulsoup4",
    "lxml",
    "requests",
    "nest-asyncio",
    "pandas",
    "openpyxl",
    "pypdf",
]


def run_command(command):
    print(f"Running: {' '.join(command)}")
    subprocess.run(command, check=True)


print("Installing Python packages...")
run_command([sys.executable, "-m", "pip", "install", "-q", *PACKAGES])

print("Installing Playwright Chromium browser...")
run_command([sys.executable, "-m", "playwright", "install", "chromium"])

if platform.system().lower() == "linux":
    print("Installing Linux browser dependencies...")
    run_command([sys.executable, "-m", "playwright", "install-deps", "chromium"])
else:
    print("Skipping Playwright install-deps because this is only required on Linux/Colab.")

print("\nSetup complete. Now run Cell 2.")

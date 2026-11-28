"""Start Filament Forge and open it in your browser."""

import argparse
import socket
import sys
import threading
import webbrowser

import uvicorn

from forge import paths
from forge.app import DATA_DIR, create_app


def _port_in_use(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def main() -> None:
    sys.stdout.reconfigure(line_buffering=True)  # show messages straight away in the window
    ap = argparse.ArgumentParser(description="Filament Forge")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true", help="don't open a browser tab")
    args = ap.parse_args()
    url = f"http://127.0.0.1:{args.port}"

    if _port_in_use(args.port):
        # Already running (e.g. double-clicked twice): just open it.
        print(f"Filament Forge is already running at {url}")
        if not args.no_browser:
            webbrowser.open(url)
        return

    # Older versions kept data inside the app folder; move it to the per-user folder once.
    moved = paths.migrate_old_data(paths.app_dir() / "data", DATA_DIR)
    if moved:
        print(f"Moved your data to {DATA_DIR} ({', '.join(moved)}).")
    print(f"Your data folder: {DATA_DIR}")

    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    print(f"Filament Forge running at {url}")
    print("Keep this window open while you use it. Close it (or press Ctrl+C) to quit; the AI stops too.")
    # Bound to 127.0.0.1 so only this computer can reach it.
    uvicorn.run(create_app(), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # keep the window open on Windows so the error can be read
        print(f"\nFilament Forge stopped with an error: {e}")
        if getattr(sys, "frozen", False):
            input("Press Enter to close…")
        raise

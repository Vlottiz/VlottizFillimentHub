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


def report(gcode: str, port: int) -> int:
    """Orca's post-processing hook: tell the running app what this print uses.

    Orca runs this after every slice you export or send, with the G-code file's path.
    It only reads the file, never changes it, and never stops Orca from exporting.
    """
    import json
    import os
    import urllib.request
    from pathlib import Path

    from forge import spools

    try:
        raw = Path(gcode).read_bytes()
    except OSError as e:
        print(f"Filament Forge: couldn't read {gcode}: {e}")
        return 0
    usage = spools.parse_usage(spools.read_gcode_bytes(raw))
    # Orca gives the real output name here; the path it passes may be a temp file.
    job = Path(os.environ.get("SLIC3R_PP_OUTPUT_NAME") or gcode).stem
    body = json.dumps({"job": job, "usage": usage}).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/usage/report", data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            print("Filament Forge:", json.loads(r.read()).get("message"))
    except OSError:
        # The app isn't open: keep the numbers so they're counted next time it starts.
        from forge.app import DATA_DIR
        pending = DATA_DIR / "pending_usage.jsonl"
        pending.parent.mkdir(parents=True, exist_ok=True)
        with pending.open("a", encoding="utf-8") as f:
            f.write(body.decode() + "\n")
        print("Filament Forge isn't running; this print will be counted when it starts.")
    return 0


def main() -> None:
    sys.stdout.reconfigure(line_buffering=True)  # show messages straight away in the window
    ap = argparse.ArgumentParser(description="Filament Forge")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true", help="don't open a browser tab")
    ap.add_argument("--report", metavar="GCODE", help="(used by Orca) count the filament a G-code file uses")
    args, _extra = ap.parse_known_args()
    if args.report:
        sys.exit(report(args.report, args.port))
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

"""Start Filament Forge and open it in your browser."""

import argparse
import threading
import webbrowser

import uvicorn

from forge.app import create_app


def main() -> None:
    ap = argparse.ArgumentParser(description="Filament Forge")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true", help="don't open a browser tab")
    args = ap.parse_args()
    url = f"http://127.0.0.1:{args.port}"
    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    print(f"Filament Forge running at {url}  (Ctrl+C to stop)")
    # Bound to 127.0.0.1 so only this computer can reach it.
    uvicorn.run(create_app(), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()

"""python -m launcher [--no-displays] [--port 8080] [--log-port 8765]"""

from launcher.main import main

if __name__ == "__main__":
    raise SystemExit(main())

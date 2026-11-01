"""Panopti Local Interactive Playground Server.

Launches a zero-dependency local web playground server to test prompt attacks,
live pre-execution intercepts, and invariant graphs.
"""

from __future__ import annotations

import http.server
import socketserver
import sys
import threading
import webbrowser
from pathlib import Path

from panopti._banner import PANOPTI_ASCII_BANNER, print_banner


def launch_local_playground(port: int = 8080) -> int:
    """Launch local HTTP server hosting panopti/playground.html."""
    html_file = Path(__file__).parent / "playground.html"
    if not html_file.exists():
        print(f"Error: Playground file not found at {html_file}", file=sys.stderr)
        return 1

    content = html_file.read_bytes()

    class PlaygroundHTTPHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, format: str, *args: object) -> None:
            # Suppress normal request logging for clean terminal
            pass

    print_banner()
    print("================================================================================")
    print("  👁️  PANOPTI SENTINEL: 1-CLICK INTERACTIVE WEB PLAYGROUND")
    print("================================================================================")
    print(f"  • Local Playground URL : http://127.0.0.1:{port}")
    print("  • Pre-Execution Engine : ONLINE (Live in-browser simulation)")
    print("  • Press Ctrl+C in this terminal to shut down the playground server.")
    print("================================================================================\n")

    try:
        with socketserver.TCPServer(("127.0.0.1", port), PlaygroundHTTPHandler) as httpd:
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
            try:
                webbrowser.open(f"http://127.0.0.1:{port}")
            except Exception:
                pass
            print(f"✅ Playground server is running at http://127.0.0.1:{port}")
            print("Navigate to the URL in your browser to test interactive attack vectors.")
            try:
                while True:
                    threading.Event().wait(1.0)
            except KeyboardInterrupt:
                print("\nShutting down playground server...")
                httpd.shutdown()
                return 0
    except OSError as e:
        print(f"Error binding to port {port}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(launch_local_playground())

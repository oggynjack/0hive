"""
Simple HTTP server for the Gemini Web2API dashboard.
Serves dashboard.html at / with CORS headers.
No external dependencies — stdlib only.
Port 8083.
"""
from http.server import HTTPServer, SimpleHTTPRequestHandler
import os
import sys

os.chdir(os.path.dirname(os.path.abspath(__file__)))


class DashboardHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            self.path = "/dashboard.html"
        return super().do_GET()

    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.end_headers()

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8083
    server = HTTPServer(("0.0.0.0", port), DashboardHandler)
    print(f"Dashboard server: http://localhost:{port}")
    server.serve_forever()

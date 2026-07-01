"""临时 hello-world 服务，用于验证 Cloudflare Tunnel 链路。用完可删。
用法：python hello_server.py [端口]   默认 8080
"""
import sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8080


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = (
            "✅ hello world —— Cloudflare Tunnel 链路已打通！\n"
            f"你请求的路径: {self.path}\n"
            f"服务器时间: {datetime.now():%Y-%m-%d %H:%M:%S}\n"
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        print("请求:", self.address_string(), self.path)


if __name__ == "__main__":
    print(f"hello server 监听 http://127.0.0.1:{PORT}")
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()

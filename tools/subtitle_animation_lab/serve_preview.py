"""Serve a saved local gallery with byte ranges, so MP4 frame seeking works."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit
import argparse
import mimetypes
import re


def handler_for(root: Path):
    root = root.resolve()

    class Handler(BaseHTTPRequestHandler):
        def do_HEAD(self):
            self.send_file(False)

        def do_GET(self):
            self.send_file(True)

        def send_file(self, body: bool):
            requested = unquote(urlsplit(self.path).path).lstrip("/") or "预览与验收.html"
            path = (root / requested).resolve()
            if not path.is_relative_to(root) or not path.is_file():
                self.send_error(404)
                return
            size = path.stat().st_size
            start, end, status = 0, size - 1, 200
            requested_range = self.headers.get("Range")
            if requested_range:
                match = re.fullmatch(r"bytes=(\d*)-(\d*)", requested_range)
                if match and (match[1] or match[2]):
                    if match[1]:
                        start = int(match[1])
                        end = min(end, int(match[2])) if match[2] else end
                    else:
                        start = max(0, size - int(match[2]))
                    status = 206
                if not match or start > end or start >= size:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.end_headers()
                    return
            self.send_response(status)
            self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(end - start + 1))
            if status == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            if body:
                try:
                    with path.open("rb") as source:
                        source.seek(start)
                        remaining = end - start + 1
                        while remaining > 0:
                            chunk = source.read(min(65536, remaining))
                            if not chunk:
                                break
                            self.wfile.write(chunk)
                            remaining -= len(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        def log_message(self, *_):
            pass

    return Handler


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8789)
    args = parser.parse_args()
    if not (args.output_dir / "预览与验收.html").is_file():
        parser.error("Run build_report.py before previewing")
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler_for(args.output_dir))
    print(f"Local preview: http://127.0.0.1:{args.port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

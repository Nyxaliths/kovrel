"""Throwaway Kovrel server for browser tests. Own settings folder. Auto-approves new devices after 2 s."""
import os, sys, tempfile, threading
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
tmp = tempfile.mkdtemp(prefix="kovrel-dev-")
os.environ["APPDATA"] = tmp
os.environ["KOVREL_FILES_DIR"] = os.path.join(os.environ["APPDATA"], "files")
sys.path.insert(0, ROOT)
import security, server

def approve_later(req):
    threading.Timer(float(os.environ.get("APPROVE_DELAY", "2")), security.approve, args=(req["id"],)).start()
security.approval_callback = approve_later
https = "--https" in sys.argv
port = 8795 if not https else 8794
srv = server.make_server(port, local=True, https=https)
print(f"READY {srv.scheme}://localhost:{port} PIN={security.current_pin()} APPDATA={tmp}", flush=True)
srv.serve_forever()

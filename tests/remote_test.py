"""Remote access (Tailscale) rules, with a throwaway APPDATA. Tailscale itself isn't needed: the test
pretends connections come from a Tailscale address, and makes its own stand-in Tailscale certificate."""
import datetime, http.client, json, os, socket, ssl, sys, tempfile, threading, time
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
tmp = tempfile.mkdtemp(prefix="kovrel-remote-")
os.environ["APPDATA"] = tmp
os.environ["KOVREL_FILES_DIR"] = os.path.join(tmp, "files")
sys.path.insert(0, ROOT)
import security, server

bad = []
def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond: bad.append(name)

# ---- address zones
Z = server.client_zone
check("zones: this PC, home network, Tailscale, internet",
      [Z("127.0.0.1"), Z("192.168.1.20"), Z("10.0.0.5"), Z("172.20.1.1"), Z("169.254.3.3"), Z("100.101.102.103"), Z("8.8.8.8"), Z("100.63.0.1"), Z("junk")]
      == ["local", "lan", "lan", "lan", "lan", "tailnet", "public", "public", "public"])

# ---- turning it on needs a strong password
try: security.set_remote(True); check("PIN can't be used with remote access", False)
except ValueError as e: check("PIN can't be used with remote access", "12 characters" in str(e))
security.set_secret("eleven char", "password")
try: security.set_remote(True); check("11-character password refused", False)
except ValueError: check("11-character password refused", True)
PW = "correct horse battery staple"
security.set_secret(PW, "password"); security.set_remote(True)
check("12+ character password: remote access on", security.get("remote") is True)
security.reset_pin()
check("switching back to a PIN turns remote access off", security.get("remote") is False)
security.set_secret(PW, "password"); security.set_remote(True)
try: security.set_remote_idle(7); check("odd idle time refused", False)
except ValueError: check("odd idle time refused", True)
security.set_remote_idle(15)

# ---- Tailscale status parsing (sample of `tailscale status --json`)
sample = {"BackendState": "Running", "Self": {"DNSName": "gaming-pc.tail1234.ts.net.", "TailscaleIPs": ["100.80.1.2", "fd7a:115c:a1e0::1"]},
          "CertDomains": ["gaming-pc.tail1234.ts.net"]}
st = server.parse_tailscale_status(sample)
check("Tailscale status parsed", st == {"installed": True, "running": True, "ip": "100.80.1.2", "name": "gaming-pc.tail1234.ts.net",
                                        "cert_domains": ["gaming-pc.tail1234.ts.net"], "error": ""}, str(st))
check("stopped Tailscale is not 'running'", not server.parse_tailscale_status({"BackendState": "Stopped", "Self": {}})["running"])
real = server.tailscale_status()
print(f"     Tailscale on this PC: installed={real['installed']} running={real['running']}")

# ---- the server, with connections pretending to come from wherever we say
srv = server.make_server(8789, local=True, https=False)
threading.Thread(target=srv.serve_forever, daemon=True).start()
security.approval_callback = lambda r: None
where = {"zone": "lan"}
server.client_zone = lambda ip: where["zone"]

def call(cmd, body=None, token=""):
    c = http.client.HTTPConnection("127.0.0.1", 8789, timeout=10)
    c.request("POST", "/api/" + cmd, json.dumps(body or {}), {"Content-Type": "application/json", "X-Token": token})
    r = c.getresponse(); d = r.read()
    try: return r.status, json.loads(d)
    except ValueError: return r.status, d
def get(path):
    c = http.client.HTTPConnection("127.0.0.1", 8789, timeout=10); c.request("GET", path); r = c.getresponse(); r.read(); return r.status

where["zone"] = "public"
check("internet address: page refused", get("/") == 403)
check("internet address: API refused even with remote on", call("info")[0] == 403)
where["zone"] = "tailnet"; security.set_remote(False)
s, d = call("info"); check("Tailscale address while remote is off: refused", s == 403 and "turned off" in d["error"])
security.set_secret(PW, "password"); security.set_remote(True)
check("Tailscale address while remote is on: page loads", get("/") == 200)

s, d = call("login", {"secret": PW, "device": "far-phone-0001", "name": "Unknown phone"})
check("a NEW phone through Tailscale waits for Allow on the PC", s == 200 and d["status"] == "pending", str(d))
check("...and the PC shows the approval pop-up", len(security.pending_requests()) == 1)
security.deny(d["req"])

where["zone"] = "lan"                              # at home: approve the phone once
s, d = call("login", {"secret": PW, "device": "my-phone-0001", "name": "My phone"})
security.approve(d["req"])
s, d = call("login_status", {"req": d["req"], "device": "my-phone-0001"}); T = d["token"]
s, d = call("ping", token=T); check("at home: ping says not remote", s == 200 and d["remote"] is False)

where["zone"] = "tailnet"
s, d = call("ping", token=T); check("away: the approved phone works and ping says remote", s == 200 and d["remote"] is True)
s, d = call("login", {"secret": "wrong password!", "device": "my-phone-0001", "name": "My phone"}); check("away: wrong password refused", s == 401)
security.CFG["devices"]["my-phone-0001"]["last_seen"] = time.time() - 16 * 60
s, d = call("ping", token=T); check("away: unused for longer than the lock time -> signed out", s == 401)
check("...but still a trusted phone", "my-phone-0001" in security.CFG["devices"])
s, d = call("login", {"secret": PW, "device": "my-phone-0001", "name": "My phone"})
check("...and the password gets it back in without approval", s == 200 and d["status"] == "ok")
T2 = d["token"]
security.CFG["devices"]["my-phone-0001"]["last_seen"] = time.time() - 16 * 60
where["zone"] = "lan"
s, d = call("ping", token=T2); check("at home the remote lock time doesn't apply", s == 200)

security.reset_pin(); where["zone"] = "tailnet"
check("PIN again: remote access off and refused", call("ping", token=T2)[0] == 403)
log = security.AUDIT_FILE.read_text()
check("activity log has the remote events", all(x in log for x in ("remote_access", "remote_session_locked")))
srv.shutdown(); srv.server_close()

# ---- Tailscale's trusted certificate is served to phones that use the Tailscale name
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
TS = "gaming-pc.tail1234.ts.net"
key = ec.generate_private_key(ec.SECP256R1()); now = datetime.datetime.now(datetime.timezone.utc)
nm = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, TS)])
cert = (x509.CertificateBuilder().subject_name(nm).issuer_name(nm).public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5)).not_valid_after(now + datetime.timedelta(days=90))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(TS)]), False).sign(key, hashes.SHA256()))
server.TS_CERT_FILE.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
server.TS_KEY_FILE.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
check("saved Tailscale certificate recognised", server.tailscale_cert_info()[0] == TS and server.tailscale_cert_info()[1] >= 89)
server.client_zone = lambda ip: "lan"
srv = server.make_server(8788, local=True, https=True)
threading.Thread(target=srv.serve_forever, daemon=True).start()
def served_name(sni):
    ctx = ssl._create_unverified_context()
    with socket.create_connection(("127.0.0.1", 8788), timeout=5) as raw, ctx.wrap_socket(raw, server_hostname=sni) as s:
        c = x509.load_der_x509_certificate(s.getpeercert(binary_form=True))
        return c.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.DNSName)
check("asking for the Tailscale name gets the Tailscale certificate", served_name(TS) == [TS])
check("asking by address gets the PC's own certificate", "localhost" in served_name(None))
srv.shutdown(); srv.server_close()

print("\nRESULT:", f"{len(bad)} FAILED: {bad}" if bad else "all passed")
sys.exit(1 if bad else 0)

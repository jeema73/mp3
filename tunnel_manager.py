import subprocess
import re
import time
import threading
import os
import json
import atexit
import qrcode
import urllib.request

TUNNEL_URL = None
TUNNEL_PROC = None

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"subdomain": "ginami"}

def get_public_ip():
    try:
        return urllib.request.urlopen('https://api.ipify.org', timeout=3).read().decode('utf-8').strip()
    except Exception:
        return "119.192.16.22"

def save_qr_code(url: str):
    try:
        static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
        os.makedirs(static_dir, exist_ok=True)
        qr_path = os.path.join(static_dir, "mobile_qr.png")
        qr = qrcode.QRCode(box_size=8, border=2)
        qr.add_data(url)
        img = qr.make_image(fill_color="black", back_color="white")
        img.save(qr_path)
    except Exception as e:
        print("[QR Error]:", e)

def _run_localtunnel(subdomain: str = "ginami", port: int = 8088):
    global TUNNEL_URL, TUNNEL_PROC
    cmd = ["cmd.exe", "/c", f"npx --yes localtunnel --port {port} --subdomain {subdomain}"]
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
        )
        TUNNEL_PROC = proc

        def cleanup():
            try:
                proc.terminate()
            except Exception:
                pass
        atexit.register(cleanup)

        start = time.time()
        while time.time() - start < 20:
            line = proc.stdout.readline()
            if not line and proc.poll() is not None:
                break
            if line:
                m = re.search(r'https://[a-zA-Z0-9-]+\.loca\.lt', line)
                if m:
                    TUNNEL_URL = m.group(0)
                    save_qr_code(TUNNEL_URL)
                    host_ip = get_public_ip()
                    print("\n" + "="*65)
                    print("  [SmartPhone Localtunnel Connected!]")
                    print(f"  Fixed Short URL: {TUNNEL_URL}")
                    print(f"  * 1st time mobile access password (Host IP): {host_ip}")
                    print("="*65 + "\n")
                    return True
        return False
    except Exception as e:
        print("[Localtunnel Error]:", e)
        return False

def _run_cloudflare(port: int = 8088):
    global TUNNEL_URL, TUNNEL_PROC
    cloudflared_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cloudflared.exe")
    if not os.path.exists(cloudflared_path):
        return False

    cmd = [cloudflared_path, "tunnel", "--url", f"http://127.0.0.1:{port}"]
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            errors="ignore",
            bufsize=1,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
        )
        TUNNEL_PROC = proc

        def cleanup():
            try:
                proc.terminate()
            except Exception:
                pass
        atexit.register(cleanup)

        start = time.time()
        while time.time() - start < 30:
            line = proc.stderr.readline()
            if not line and proc.poll() is not None:
                break
            if line:
                m = re.search(r'https://[a-zA-Z0-9-]+\.trycloudflare\.com', line)
                if m:
                    TUNNEL_URL = m.group(0)
                    save_qr_code(TUNNEL_URL)
                    print("\n" + "="*65)
                    print(f"  [Cloudflare Fallback URL]: {TUNNEL_URL}")
                    print("="*65 + "\n")
                    return True
        return False
    except Exception as e:
        print("[Cloudflare Error]:", e)
        return False

def _run_tunnel_thread(port: int = 8088):
    cfg = load_config()
    subdomain = cfg.get("subdomain", "ginami").strip() or "ginami"

    # 1. Localtunnel 초단축 고정 주소 시도 (https://ginami.loca.lt)
    if _run_localtunnel(subdomain, port):
        # 성공 시 계속 유지
        while TUNNEL_PROC and TUNNEL_PROC.poll() is None:
            time.sleep(1)
        return

    # 2. 실패 시 Cloudflare 터널로 안전하게 전환
    _run_cloudflare(port)

def start_tunnel_background(port: int = 8088):
    t = threading.Thread(target=_run_tunnel_thread, args=(port,), daemon=True)
    t.start()

def get_tunnel_info():
    global TUNNEL_URL
    return {
        "active": TUNNEL_URL is not None,
        "url": TUNNEL_URL,
        "qr_image": "/static/mobile_qr.png" if TUNNEL_URL else None,
        "host_ip": get_public_ip()
    }

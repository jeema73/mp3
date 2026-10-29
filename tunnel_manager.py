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
    return {"mode": "direct", "subdomain": "ginami-mp3"}

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

def _run_localtunnel(subdomain: str = "ginami-mp3", port: int = 8088):
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
    global TUNNEL_URL
    cfg = load_config()
    mode = cfg.get("mode", "direct")

    # 1. 다이렉트 IP 모드 (가장 빠르고 깔끔한 직접 연결, 중간 인증창 없음)
    if mode == "direct":
        host_ip = get_public_ip()
        TUNNEL_URL = f"http://{host_ip}:{port}"
        save_qr_code(TUNNEL_URL)
        print("\n" + "="*65)
        print("  [SmartPhone Direct IP Mode Connected!]")
        print(f"  Direct Mobile URL: {TUNNEL_URL}")
        print("  * No intermediate service, instant high-speed connection")
        print("="*65 + "\n")
        return

    subdomain = cfg.get("subdomain", "ginami-mp3").strip() or "ginami-mp3"

    # 2. Localtunnel 초단축 고정 주소 시도
    if _run_localtunnel(subdomain, port):
        while TUNNEL_PROC and TUNNEL_PROC.poll() is None:
            time.sleep(1)
        return

    # 3. 실패 시 Cloudflare 터널로 안전하게 전환
    _run_cloudflare(port)

def start_tunnel_background(port: int = 8088):
    t = threading.Thread(target=_run_tunnel_thread, args=(port,), daemon=True)
    t.start()

def get_tunnel_info():
    global TUNNEL_URL
    host_ip = get_public_ip()
    cfg = load_config()
    mode = cfg.get("mode", "direct")
    url = TUNNEL_URL or f"http://{host_ip}:8088"
    return {
        "active": True,
        "mode": mode,
        "url": url,
        "qr_image": "/static/mobile_qr.png",
        "host_ip": host_ip
    }

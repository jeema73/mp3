import subprocess
import re
import time
import threading
import os
import json
import atexit
import qrcode

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
    return {}

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

def _run_ngrok(token: str, domain: str, port: int = 8088):
    global TUNNEL_URL
    try:
        from pyngrok import ngrok, conf
        conf.get_default().auth_token = token
        
        options = {"addr": port}
        if domain:
            options["domain"] = domain
            
        tunnel = ngrok.connect(**options)
        TUNNEL_URL = tunnel.public_url
        save_qr_code(TUNNEL_URL)
        
        print("\n" + "="*65)
        print("  [SmartPhone ngrok Fixed Domain Connected!]")
        print(f"  Mobile Fixed URL: {TUNNEL_URL}")
        print("  * This URL is permanently fixed and will NOT change!")
        print("="*65 + "\n")
        return True
    except Exception as e:
        print("[ngrok Error]:", e)
        print("  -> Falling back to Cloudflare tunnel...")
        return False

def _run_tunnel_thread(port: int = 8088):
    global TUNNEL_URL, TUNNEL_PROC
    
    cfg = load_config()
    token = cfg.get("ngrok_authtoken", "").strip()
    domain = cfg.get("ngrok_domain", "").strip()
    
    # 1. ngrok 설정이 있으면 영구 고정 주소로 시작
    if token:
        if _run_ngrok(token, domain, port):
            return

    # 2. 토큰이 없거나 ngrok 오류 시 기존 Cloudflare 자동 대체
    cloudflared_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cloudflared.exe")
    if not os.path.exists(cloudflared_path):
        print("[Tunnel] cloudflared.exe not found.")
        return
    
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
        while time.time() - start < 35:
            line = proc.stderr.readline()
            if not line and proc.poll() is not None:
                break
            if line:
                m = re.search(r'https://[a-zA-Z0-9-]+\.trycloudflare\.com', line)
                if m:
                    TUNNEL_URL = m.group(0)
                    save_qr_code(TUNNEL_URL)
                    
                    print("\n" + "="*65)
                    print("  [SmartPhone Cloudflare Tunnel Connected]")
                    print(f"  Mobile URL: {TUNNEL_URL}")
                    print("  * PC web page top-right [SmartPhone QR] can be scanned with camera.")
                    print("="*65 + "\n")
                    break
                    
        while proc.poll() is None:
            proc.stderr.readline()

    except Exception as e:
        print("[Tunnel Error]:", e)

def start_tunnel_background(port: int = 8088):
    t = threading.Thread(target=_run_tunnel_thread, args=(port,), daemon=True)
    t.start()

def get_tunnel_info():
    global TUNNEL_URL
    cloud_url = os.environ.get("RENDER_EXTERNAL_URL") or os.environ.get("APP_URL")
    if cloud_url and not TUNNEL_URL:
        TUNNEL_URL = cloud_url.rstrip('/')
        save_qr_code(TUNNEL_URL)

    return {
        "active": TUNNEL_URL is not None,
        "url": TUNNEL_URL,
        "qr_image": "/static/mobile_qr.png" if TUNNEL_URL else None
    }

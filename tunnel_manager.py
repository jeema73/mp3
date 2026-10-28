import subprocess
import re
import time
import threading
import os
import atexit
import qrcode

TUNNEL_URL = None
TUNNEL_PROC = None

def _run_tunnel_thread(port: int = 8088):
    global TUNNEL_URL, TUNNEL_PROC
    cloudflared_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cloudflared.exe")
    if not os.path.exists(cloudflared_path):
        print("[Tunnel] cloudflared.exe 를 찾을 수 없습니다. (로컬 접속만 가능)")
        return
    
    cmd = [cloudflared_path, "tunnel", "--url", f"http://127.0.0.1:{port}"]
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
        while time.time() - start < 35:
            line = proc.stderr.readline()
            if not line and proc.poll() is not None:
                break
            if line:
                m = re.search(r'https://[a-zA-Z0-9-]+\.trycloudflare\.com', line)
                if m:
                    TUNNEL_URL = m.group(0)
                    static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
                    os.makedirs(static_dir, exist_ok=True)
                    qr_path = os.path.join(static_dir, "mobile_qr.png")
                    
                    try:
                        qr = qrcode.QRCode(box_size=8, border=2)
                        qr.add_data(TUNNEL_URL)
                        img = qr.make_image(fill_color="black", back_color="white")
                        img.save(qr_path)
                    except Exception as qre:
                        print(f"[Tunnel QR Error]: {qre}")
                    
                    print("\n" + "="*65)
                    print("  [SmartPhone Cloudflare Tunnel Connected]")
                    print(f"  Mobile URL: {TUNNEL_URL}")
                    print("  * PC web page top-right [SmartPhone QR] can be scanned with camera.")
                    print("="*65 + "\n")
                    break
                    
        # 계속 버퍼 비워주기
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
        try:
            static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
            os.makedirs(static_dir, exist_ok=True)
            qr_path = os.path.join(static_dir, "mobile_qr.png")
            if not os.path.exists(qr_path):
                qr = qrcode.QRCode(box_size=8, border=2)
                qr.add_data(TUNNEL_URL)
                img = qr.make_image(fill_color="black", back_color="white")
                img.save(qr_path)
        except Exception:
            pass

    return {
        "active": TUNNEL_URL is not None,
        "url": TUNNEL_URL,
        "qr_image": "/static/mobile_qr.png" if TUNNEL_URL else None
    }

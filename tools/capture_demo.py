import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEMO_DIR = ROOT / "demo"
PORT = 8123
URL = f"http://127.0.0.1:{PORT}/"


def main():
    DEMO_DIR.mkdir(exist_ok=True)
    server = subprocess.Popen(
        [sys.executable, str(ROOT / "server.py"), "--demo", "--port", str(PORT), "--no-browser"],
        cwd=ROOT,
    )
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 800})
            page.goto(URL, wait_until="load")
            page.wait_for_timeout(1200)
            page.screenshot(path=str(DEMO_DIR / "01_idle.png"))

            page.evaluate(
                """() => {
                  const set = (id, v) => { const el = document.getElementById(id); el.value = v; el.dispatchEvent(new Event('change')); };
                  set('speed', 100);
                  set('dwell', 0.5);
                  set('min', 0);
                  set('max', 50);
                  set('cycles', 0);
                  document.querySelector('.volt-btn[data-v="6.0"]').click();
                }"""
            )
            page.wait_for_timeout(400)
            page.click("#btn-start")
            page.wait_for_timeout(2500)
            page.screenshot(path=str(DEMO_DIR / "02_running.png"))

            frames = []
            for i in range(14):
                frames.append(page.screenshot())
                page.wait_for_timeout(400)
            page.screenshot(path=str(DEMO_DIR / "03_running_later.png"))

            page.click("#btn-stop")
            page.wait_for_timeout(1200)
            page.screenshot(path=str(DEMO_DIR / "04_stopped.png"))

            try:
                from PIL import Image
                import io

                imgs = [Image.open(io.BytesIO(f)).convert("P", palette=Image.ADAPTIVE) for f in frames]
                imgs[0].save(
                    str(DEMO_DIR / "dashboard-demo.gif"),
                    save_all=True,
                    append_images=imgs[1:],
                    duration=400,
                    loop=0,
                )
            except ImportError:
                print("Pillow not installed, skipping GIF")

            browser.close()
    finally:
        server.terminate()
        server.wait(timeout=10)
    print("demo screenshots written to", DEMO_DIR)


if __name__ == "__main__":
    main()

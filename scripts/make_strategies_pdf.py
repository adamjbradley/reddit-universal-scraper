"""Regenerate STRATEGIES.pdf from STRATEGIES.md.

One command:  python scripts/make_strategies_pdf.py
Renders the Markdown to styled HTML (python-markdown) then prints it to PDF with headless
Edge/Chrome (no extra dependencies on Windows). Run after editing STRATEGIES.md.
"""
import pathlib
import shutil
import subprocess
import sys
import time

import markdown

ROOT = pathlib.Path(__file__).resolve().parents[1]
MD = ROOT / "STRATEGIES.md"
HTML = ROOT / "STRATEGIES.html"
PDF = ROOT / "STRATEGIES.pdf"

CSS = """
@page { size: A4; margin: 18mm 16mm; }
* { box-sizing: border-box; }
body { font-family: -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
       font-size: 11.5px; line-height: 1.5; color: #1a1a1a; max-width: 900px; margin: 0 auto; }
h1 { font-size: 24px; border-bottom: 3px solid #d62728; padding-bottom: 6px; margin-top: 0; }
h2 { font-size: 17px; border-bottom: 1px solid #ccc; padding-bottom: 3px; margin-top: 22px; color: #c0392b; }
h3 { font-size: 13.5px; margin-top: 16px; color: #2c3e50; }
hr { border: none; border-top: 1px solid #e0e0e0; margin: 18px 0; }
code { background: #f4f4f4; padding: 1px 4px; border-radius: 3px; font-size: 10.5px;
       font-family: "Cascadia Code", Consolas, monospace; }
pre { background: #f7f7f7; padding: 10px; border-radius: 5px; overflow-x: auto; }
table { border-collapse: collapse; width: 100%; margin: 10px 0; font-size: 10.5px; }
th, td { border: 1px solid #ccc; padding: 5px 8px; text-align: left; vertical-align: top; }
th { background: #f0f0f0; }
tr:nth-child(even) { background: #fafafa; }
blockquote { border-left: 4px solid #f0ad4e; background: #fff9ec; margin: 12px 0;
             padding: 8px 14px; color: #5a4a2a; }
a { color: #2980b9; text-decoration: none; }
ul, ol { padding-left: 22px; }
h2, h3 { page-break-after: avoid; }
table, pre, blockquote { page-break-inside: avoid; }
"""

_BROWSERS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
]


def _browser():
    for p in _BROWSERS:
        if pathlib.Path(p).exists():
            return p
    for name in ("msedge", "chrome", "chromium", "google-chrome"):
        found = shutil.which(name)
        if found:
            return found
    return None


def main():
    body = markdown.markdown(MD.read_text(encoding="utf-8"),
                             extensions=["tables", "fenced_code", "sane_lists", "toc"])
    HTML.write_text(f"<!doctype html><html><head><meta charset='utf-8'><title>Trading "
                    f"Strategies</title><style>{CSS}</style></head><body>{body}</body></html>",
                    encoding="utf-8")
    browser = _browser()
    if not browser:
        print("No Edge/Chrome found; wrote STRATEGIES.html only.")
        return 1
    if PDF.exists():
        PDF.unlink()
    subprocess.run([browser, "--headless", "--disable-gpu", "--no-pdf-header-footer",
                    f"--print-to-pdf={PDF}", HTML.as_uri()], check=False)
    # Headless Edge writes the PDF asynchronously; wait until its size stabilises before
    # deleting the HTML, or the render gets truncated (a tiny/blank PDF).
    last, stable = -1, 0
    for _ in range(40):  # up to ~8s
        time.sleep(0.2)
        size = PDF.stat().st_size if PDF.exists() else 0
        if size > 0 and size == last:
            stable += 1
            if stable >= 3:
                break
        else:
            stable = 0
        last = size
    HTML.unlink(missing_ok=True)
    ok = PDF.exists() and PDF.stat().st_size > 0
    print(f"{'wrote' if ok else 'FAILED'} {PDF}" + (f" ({PDF.stat().st_size:,} bytes)" if ok else ""))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

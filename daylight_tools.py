#!/usr/bin/env python3
"""
Daylight – pomocné nástroje pro Maxe (frontend a testy).
Potřebuje jen Python 3.8+, nic se neinstaluje.

  python daylight_tools.py url --start URL_A --result URL_B   vloží webhooky do daylight.html
  python daylight_tools.py url --clear                        vymaže URL (DEMO režim, čistá verze do repozitáře)
  python daylight_tools.py test cv/jana.pdf                   pošle CV ze souboru (.pdf .docx .md .html .txt)
  python daylight_tools.py test cv/                           pošle všechna CV ze složky, jedno po druhém
  python daylight_tools.py test cv/jana.pdf --jmeno "Jana Nováková" --kontext "Alfa s.r.o., Praha" --cil "Ověř Python"
  python daylight_tools.py test 3                             (volitelné) případ č. 3 ze souboru pripady.txt
  python daylight_tools.py text cv/jana.pdf                   jen ukáže, jaký text se ze souboru vytáhne (nic neposílá)
  python daylight_tools.py shoda                              spočítá "z N tvrzení správně K" a vytvoří ground-truth.md
  python daylight_tools.py klice [složka]                     hledá klíče a webhook URL před odevzdáním

Na Windows pište místo "python" příkaz "py".
"""
import argparse
import csv
import glob
import io
import json
import re
import shutil
import statistics
import sys
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from html.parser import HTMLParser
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HTML_DEFAULT = "daylight.html"
START_RE = re.compile(r'(const\s+WEBHOOK_START\s*=\s*")([^"]*)(";)')
RESULT_RE = re.compile(r'(const\s+WEBHOOK_RESULT\s*=\s*")([^"]*)(";)')
LABEL = {"verified": "OVERENO", "partial": "CASTECNE",
         "contradicted": "ROZPOR", "unverifiable": "NEOVERITELNE"}
OUT_DIR = Path("vysledky")


def die(msg):
    print("CHYBA: " + msg)
    sys.exit(1)


# ------------------------------------------------------------------ url
def read_urls(html_path):
    h = Path(html_path).read_text(encoding="utf-8")
    a, b = START_RE.search(h), RESULT_RE.search(h)
    if not a or not b:
        die("V %s jsem nenašel řádky 'const WEBHOOK_START' a 'const WEBHOOK_RESULT'." % html_path)
    return a.group(2), b.group(2)


def cmd_url(a):
    p = Path(a.html)
    if not p.exists():
        die("Soubor %s neexistuje. Spusťte příkaz ve složce, kde leží daylight.html." % a.html)
    h = p.read_text(encoding="utf-8")
    if not START_RE.search(h) or not RESULT_RE.search(h):
        die("V souboru nejsou řádky WEBHOOK_START / WEBHOOK_RESULT (jiná verze webu?).")
    if a.clear:
        start = result = ""
    else:
        if not (a.start and a.result):
            die("Zadejte obě URL: --start URL_SCENARE_A --result URL_SCENARE_B (nebo --clear).")
        start, result = a.start.strip(), a.result.strip()
        for u in (start, result):
            if not u.startswith("https://"):
                die("URL musí začínat https:// (dostal jsem: %s)" % u[:40])
        if start == result:
            print("POZOR: obě URL jsou stejné. START má patřit scénáři A, RESULT scénáři B.")
        if "make.com" not in start or "make.com" not in result:
            print("POZOR: URL nevypadá jako Make webhook (hook.eu1.make.com/...). Zkontrolujte ji.")
    zal = p.parent / "_zaloha"
    zal.mkdir(exist_ok=True)
    shutil.copy(p, zal / (p.name + ".bak"))
    h = START_RE.sub(lambda m: m.group(1) + start + m.group(3), h, count=1)
    h = RESULT_RE.sub(lambda m: m.group(1) + result + m.group(3), h, count=1)
    if "sleep(3000)" in h:
        h = h.replace("sleep(3000)", "sleep(6000)")
        print("Interval dotazování změněn z 3 s na 6 s (šetří operace v Make).")
    p.write_text(h, encoding="utf-8")
    if start and result:
        print("Hotovo. Web je v ŽIVÉM režimu. Otevřete ho v prohlížeči; dole musí svítit 'Živý režim: napojeno na Make.com'.")
    else:
        print("Hotovo. URL jsou prázdné, web běží v DEMO režimu (simulace). Tuto verzi dejte do repozitáře.")
    print("Záloha původní verze: _zaloha/%s.bak (obsahuje staré URL, do repozitáře ji nedávejte)." % p.name)


# ------------------------------------------------------------------ test
def parse_cases(path):
    p = Path(path)
    if not p.exists():
        die("Soubor %s neexistuje." % path)
    txt = "\n".join(l for l in p.read_text(encoding="utf-8").splitlines() if not l.startswith("#"))
    parts = re.split(r"(?m)^===\s*PRIPAD\s+(\d+)([^\n]*)\n", txt)
    cases = {}
    for i in range(1, len(parts), 3):
        num, title, body = int(parts[i]), parts[i + 1].strip(" :=-"), parts[i + 2]
        m = re.search(r"(?m)^CV:[ \t]*\n", body)
        head, cv = (body[:m.start()], body[m.end():]) if m else (body, "")
        f = {}
        for line in head.splitlines():
            k, sep, v = line.partition(":")
            if sep and k.strip() in ("JMENO", "KONTEXT", "CIL"):
                f[k.strip()] = v.strip()
        cases[num] = {"title": title, "name": f.get("JMENO", ""), "context": f.get("KONTEXT", ""),
                      "goal": f.get("CIL", ""), "cv": cv.strip()}
    return cases


def http(method, url, data=None, timeout=60):
    headers = {"User-Agent": "daylight-test/1.0"}
    if data is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded;charset=UTF-8"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read().decode("utf-8", errors="replace")


SUPPORTED = {".pdf", ".docx", ".md", ".markdown", ".html", ".htm", ".txt"}
MAX_CV = 20000


def read_text_file(p):
    raw = Path(p).read_bytes()
    for enc in ("utf-8-sig", "cp1250"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            pass
    return raw.decode("utf-8", errors="replace")


def extract_docx(p):
    try:
        with zipfile.ZipFile(p) as z:
            root = ET.fromstring(z.read("word/document.xml"))
    except (zipfile.BadZipFile, KeyError, ET.ParseError):
        die("%s nevypadá jako platný .docx (starý .doc nejde, uložte ho znovu jako .docx)." % p)
    W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    lines = []
    for para in root.iter(W + "p"):
        parts = []
        for el in para.iter():
            if el.tag == W + "t" and el.text:
                parts.append(el.text)
            elif el.tag == W + "tab":
                parts.append("\t")
            elif el.tag in (W + "br", W + "cr"):
                parts.append("\n")
        lines.append("".join(parts))
    return "\n".join(lines)


class _Html(HTMLParser):
    BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "ul", "ol", "table", "header", "footer"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head"):
            self.skip += 1
        if tag in self.BLOCK:
            self.out.append("\n")
        if tag in ("td", "th"):
            self.out.append(" ")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head"):
            self.skip = max(0, self.skip - 1)
        if tag in self.BLOCK:
            self.out.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)


def extract_html(p):
    h = _Html()
    h.feed(read_text_file(p))
    return "".join(h.out)


def extract_pdf(p):
    err, hint = "", ""
    try:
        from pypdf import PdfReader
        return "\n".join((pg.extract_text() or "") for pg in PdfReader(str(p)).pages)
    except ImportError:
        err, hint = "knihovna pypdf není nainstalovaná", " Nainstalujte ji příkazem: pip install pypdf"
    except Exception as e:
        err, hint = "soubor je poškozený, prázdný nebo zaheslovaný (%s)" % e, " Zkuste PDF znovu uložit nebo vyexportovat."
    try:
        r = subprocess.run(["pdftotext", "-layout", str(p), "-"], capture_output=True, timeout=60)
        if r.returncode == 0:
            return r.stdout.decode("utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError):
        pass
    die("PDF %s se nepodařilo přečíst: %s.%s" % (p.name, err, hint))


def extract_text(path):
    p = Path(path)
    if not p.is_file():
        die("Soubor %s neexistuje." % path)
    ext = p.suffix.lower()
    if ext == ".pdf":
        t = extract_pdf(p)
    elif ext == ".docx":
        t = extract_docx(p)
    elif ext in (".html", ".htm"):
        t = extract_html(p)
    elif ext in (".md", ".markdown", ".txt"):
        t = read_text_file(p)
    else:
        die("Formát %s neumím. Podporuji: %s" % (ext or "(bez přípony)", ", ".join(sorted(SUPPORTED))))
    t = re.sub(r"[ \t]+\n", "\n", t.replace("\r\n", "\n").replace("\r", "\n"))
    t = re.sub(r"\n{3,}", "\n\n", t).strip()
    if len(t) < 40:
        die("Ze souboru %s vyšlo skoro nic textu (%d znaků). Je to sken nebo obrázek? Uložte CV jako text/PDF z Wordu." % (p.name, len(t)))
    return t


def guess_name(cv):
    for line in cv.splitlines():
        line = line.strip(" #*\t-")
        if not line:
            continue
        if len(line) <= 60 and len(line.split()) <= 5 and not re.search(r"[\d@:/]", line):
            return line
        break
    return ""


def cmd_text(a):
    t = extract_text(a.co)
    print(t)
    print("\n--- %d znaků, %d řádků. Odhad jména: %r" % (len(t), t.count("\n") + 1, guess_name(t)))


def run_case(a, label, title, name, context, goal, cv):
    start, result = a.start, a.result
    if not (start and result):
        start, result = read_urls(a.html)
    if not (start and result):
        die("Nemám URL webhooků. Nejdřív spusťte: python daylight_tools.py url --start ... --result ...")
    if len(cv) > MAX_CV:
        print("POZOR: CV má %d znaků, ořezávám na %d." % (len(cv), MAX_CV))
        cv = cv[:MAX_CV]

    rid = str(uuid.uuid4())
    body = urllib.parse.urlencode({"id": rid, "name": name, "context": context,
                                   "goal": goal, "cv": cv}).encode("utf-8")
    print("\n=== %s: %s" % (label, title))
    print("Jméno: %s | Kontext: %s | Cíl: %s | CV: %d znaků" % (name, context or "(prázdný)", goal, len(cv)))
    t0 = time.time()
    try:
        status, txt = http("POST", start, body)
    except (OSError, ValueError) as e:
        die("Odeslání na scénář A selhalo: %s. Je scénář zapnutý (ON) a URL správná?" % e)
    print("Scénář A přijal data (HTTP %s, odpověď %r). Čekám na výsledek, dotaz každých %d s." % (status, txt[:20], a.poll))

    report = None
    while time.time() - t0 < a.timeout:
        time.sleep(a.poll)
        s = int(time.time() - t0)
        try:
            _, txt = http("GET", result + ("&" if "?" in result else "?") + "id=" + urllib.parse.quote(rid))
            d = json.loads(txt)
            if isinstance(d, dict) and isinstance(d.get("report"), str):
                d = json.loads(d["report"])
        except (OSError, ValueError):
            print("  %3d s: zatím nic" % s)
            continue
        if isinstance(d, dict) and "claims" in d:
            report = d
            break
        print("  %3d s: pending" % s)
    if report is None:
        print("CHYBA: výsledek nedorazil do %d s. V Make → History zkontrolujte, kde scénář A skončil." % a.timeout)
        return False
    elapsed = time.time() - t0

    OUT_DIR.mkdir(exist_ok=True)
    jp, cp = OUT_DIR / ("pripad-%s.json" % label), OUT_DIR / ("pripad-%s.csv" % label)
    if cp.exists():
        cp.rename(OUT_DIR / ("pripad-%s.%d.old" % (label, int(time.time()))))
    jp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    claims = report.get("claims") or []
    warn = []
    with open(cp, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(["tvrzeni", "vraceno", "confidence", "pocet_url", "urls", "pravda", "poznamka"])
        for cl in claims:
            ev = [e.get("url", "") for e in (cl.get("evidence") or [])]
            w.writerow([cl.get("text", ""), cl.get("status", ""), cl.get("confidence", ""),
                        len(ev), " | ".join(ev), "", ""])
            if cl.get("status") == "verified" and not ev:
                warn.append("'%s' je OVERENO bez URL (prompt to zakazuje)" % cl.get("text", "")[:50])

    print("\nHotovo za %.0f s." % elapsed)
    print("Shoda identity: %s | %s" % (report.get("identityConfidence"), report.get("summary", "")[:160]))
    for cl in claims:
        print("  [%-12s] %.2f  %s" % (LABEL.get(cl.get("status"), cl.get("status")),
                                     float(cl.get("confidence") or 0), cl.get("text", "")[:70]))
    if len(report.get("interviewQuestions") or []) != 5:
        warn.append("interviewQuestions nemá přesně 5 otázek")
    if elapsed > 120:
        warn.append("běh trval déle než 2 minuty (cíl z návodu)")
    for x in warn:
        print("POZOR: " + x)

    with open(OUT_DIR / "casy.txt", "a", encoding="utf-8") as fh:
        fh.write("%s;%s;%.0f s\n" % (datetime.now().strftime("%d.%m. %H:%M"), label, elapsed))
    secs = [float(l.strip().split(";")[2].split()[0]) for l in open(OUT_DIR / "casy.txt", encoding="utf-8") if l.strip()]
    print("\nUloženo: %s a %s" % (jp, cp))
    print("Do sloupce 'pravda' v CSV doplňte ano / ne / nelze (nelze = pravda, ale veřejně nedohledatelná).")
    print("Naměřené časy: %s | medián %.0f s (číslo pro pitch)" % (
        ", ".join("%.0f s" % x for x in secs), statistics.median(secs)))
    return True


def safe_label(stem):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("_") or "cv"


def cmd_test(a):
    co = a.co
    if co.isdigit():
        cases = parse_cases(a.pripady)
        c = cases.get(int(co))
        if not c:
            die("Případ %s v %s nenašel. Mám případy: %s" % (co, a.pripady, sorted(cases)))
        if any("DOPLNIT" in v for v in (c["name"], c["context"], c["goal"], c["cv"])) or not c["cv"]:
            die("Případ %s má nevyplněná pole (DOPLNIT). Upravte soubor %s, nebo použijte CV jako soubor." % (co, a.pripady))
        run_case(a, co, c["title"], c["name"], c["context"], c["goal"], c["cv"])
        return

    p = Path(co)
    if p.is_dir():
        files = sorted(f for f in p.iterdir() if f.is_file() and f.suffix.lower() in SUPPORTED and not f.name.startswith(("~", ".")))
        if not files:
            die("Ve složce %s není žádné CV (%s)." % (co, ", ".join(sorted(SUPPORTED))))
        if a.jmeno:
            die("--jmeno se nedá použít u celé složky (každé CV má jiné jméno). Pošlete soubory jednotlivě.")
        print("Pošlu %d CV: %s" % (len(files), ", ".join(f.name for f in files)))
    elif p.is_file():
        files = [p]
    else:
        die("'%s' není číslo případu, soubor ani složka." % co)

    used = set()
    for f in files:
        try:
            cv = extract_text(f)
            name = a.jmeno or guess_name(cv)
            if not name:
                die("Z %s neumím odhadnout jméno. Přidejte --jmeno \"Jméno Příjmení\"." % f.name)
            if not a.jmeno:
                print("Jméno z CV: %r (jiné zadejte přes --jmeno)" % name)
            label = safe_label(f.stem)
            if label in used:
                label = label + "-" + f.suffix.lower().lstrip(".")
            used.add(label)
            run_case(a, label, f.name, name, a.kontext, a.cil, cv)
        except SystemExit:
            if len(files) == 1:
                raise
            print("Přeskakuji %s a pokračuji dalším souborem." % f.name)


# ------------------------------------------------------------------ shoda
OK_FOR = {"ano": {"verified", "partial"}, "ne": {"contradicted", "unverifiable"}, "nelze": {"unverifiable"}}
SYN = {"a": "ano", "yes": "ano", "y": "ano", "true": "ano", "n": "ne", "no": "ne", "false": "ne",
       "nelze": "nelze", "nedohledatelne": "nelze"}


def read_csv_flex(path):
    raw = Path(path).read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1250")
    first = text.splitlines()[0] if text.strip() else ""
    delim = ";" if first.count(";") >= first.count(",") else ","
    return list(csv.DictReader(io.StringIO(text), delimiter=delim))


def cmd_shoda(a):
    files = sorted(glob.glob(str(OUT_DIR / "pripad-*.csv")))
    if not files:
        die("Ve složce vysledky/ nejsou žádné pripad-*.csv. Nejdřív pusťte testy.")
    total = ok = false_ver = missed = skipped = 0
    md = ["# Validace (ground truth)", "", "Pravda: ano = tvrzení v CV je pravdivé, ne = vymyšlené, nelze = pravdivé, ale veřejně nedohledatelné.", ""]
    for f in files:
        rows = read_csv_flex(f)
        n = ok_c = 0
        md += ["## " + Path(f).stem, "", "| Tvrzení z CV | Pravda | Vrátil agent | Správně |", "|---|---|---|---|"]
        for r in rows:
            truth = SYN.get((r.get("pravda") or "").strip().lower(), (r.get("pravda") or "").strip().lower())
            got = (r.get("vraceno") or "").strip()
            if truth not in OK_FOR:
                skipped += 1
                continue
            try:
                urls = int(float(r.get("pocet_url") or 0))
            except ValueError:
                urls = 0
            good = got in OK_FOR[truth] and not (got == "verified" and urls == 0)
            n += 1
            ok_c += good
            if truth == "ne" and got == "verified":
                false_ver += 1
            if truth == "ano" and got == "unverifiable":
                missed += 1
            md.append("| %s | %s | %s | %s |" % ((r.get("tvrzeni") or "").replace("|", "/"), truth, got, "ano" if good else "ne"))
        md.append("")
        print("%s: z %d tvrzení správně %d" % (Path(f).stem, n, ok_c))
        total += n
        ok += ok_c
    if total == 0:
        die("Ve sloupci 'pravda' nic není vyplněné (ano / ne / nelze).")
    pct = 100.0 * ok / total
    line = "Z %d tvrzení správně %d (%.0f %%). Falešně ověřeno: %d. Pravdivé, ale agent nic nenašel: %d." % (
        total, ok, pct, false_ver, missed)
    print("\n" + line)
    if skipped:
        print("(%d řádků bez vyplněné pravdy jsem přeskočil.)" % skipped)
    md += ["## Souhrn", "", line, ""]
    Path(a.vystup).write_text("\n".join(md), encoding="utf-8")
    print("Tabulka pro repozitář uložena do %s" % a.vystup)


# ------------------------------------------------------------------ klice
PATTERNS = [
    ("OpenAI klíč", r"sk-[A-Za-z0-9_\-]{20,}"),
    ("Apify token", r"apify_api_[A-Za-z0-9]{10,}"),
    ("Make webhook URL", r"https://hook\.[a-z0-9.\-]*(?:make|integromat)\.[a-z.]*/[A-Za-z0-9]+"),
    ("Bearer token", r"(?i)bearer\s+[A-Za-z0-9._\-]{20,}"),
]
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".obsidian", "_zaloha"}


def cmd_klice(a):
    root, found = Path(a.slozka), 0
    for p in sorted(root.rglob("*")):
        if not p.is_file() or any(part in SKIP_DIRS for part in p.parts) or p.stat().st_size > 5_000_000:
            continue
        if p.suffix.lower() in {".mp4", ".mov", ".png", ".jpg", ".jpeg", ".gif", ".zip", ".pdf", ".webm"}:
            continue
        try:
            lines = p.read_text(encoding="utf-8").splitlines()
        except (UnicodeDecodeError, OSError):
            continue
        for no, line in enumerate(lines, 1):
            for name, rx in PATTERNS:
                for m in re.finditer(rx, line):
                    found += 1
                    print("NALEZENO %-17s %s:%d  %s…" % (name, p, no, m.group(0)[:12]))
    if found:
        print("\n%d nálezů. Smažte je před odevzdáním. U webu: python daylight_tools.py url --clear" % found)
        sys.exit(1)
    print("Nic podezřelého jsem v '%s' nenašel." % root)


# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser(description="Daylight – nástroje pro frontend a testy")
    sub = ap.add_subparsers(dest="cmd", required=True)

    u = sub.add_parser("url", help="vloží webhook URL do daylight.html")
    u.add_argument("--start")
    u.add_argument("--result")
    u.add_argument("--clear", action="store_true", help="vymaže URL (DEMO režim)")
    u.add_argument("--html", default=HTML_DEFAULT)
    u.set_defaults(fn=cmd_url)

    t = sub.add_parser("test", help="pošle CV (soubor, složku nebo číslo případu) přes celý řetěz")
    t.add_argument("co", help="soubor .pdf/.docx/.md/.html/.txt, složka s CV, nebo číslo případu z pripady.txt")
    t.add_argument("--jmeno", help="jméno kandidáta (jinak se odhadne z prvního řádku CV)")
    t.add_argument("--kontext", default="", help="firma/škola/město pro rozlišení osob")
    t.add_argument("--cil", default="Ověř zkušenosti a vzdělání z CV")
    t.add_argument("--html", default=HTML_DEFAULT)
    t.add_argument("--start")
    t.add_argument("--result")
    t.add_argument("--pripady", default="pripady.txt")
    t.add_argument("--timeout", type=int, default=180)
    t.add_argument("--poll", type=int, default=6, help="sekundy mezi dotazy (každý dotaz stojí operace v Make)")
    t.set_defaults(fn=cmd_test)

    x = sub.add_parser("text", help="ukáže text vytažený ze souboru (nic neposílá)")
    x.add_argument("co")
    x.set_defaults(fn=cmd_text)

    s = sub.add_parser("shoda", help="spočítá shodu s ground truth")
    s.add_argument("--vystup", default="ground-truth.md")
    s.set_defaults(fn=cmd_shoda)

    k = sub.add_parser("klice", help="hledá klíče a webhook URL")
    k.add_argument("slozka", nargs="?", default=".")
    k.set_defaults(fn=cmd_klice)

    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()

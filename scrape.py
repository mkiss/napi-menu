"""Napi menü gyűjtő – Gödör (godor.hu) és Csekő Kávéház Debrecen.

Futtatás:  python scrape.py
Kimenet:   docs/index.html  (ezt szolgálja ki a GitHub Pages)
"""
import datetime as dt
import html
import json
import os
import re
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

TZ = ZoneInfo("Europe/Budapest")
ROOT = Path(__file__).parent
OUT = ROOT / "docs" / "index.html"
UA = {"User-Agent": "Mozilla/5.0 (napi-menu-gyujto; GitHub Actions)"}

CSEKO_NAPI = "https://csekokavehaz.hu/debrecen/napi-kinalat-2/"
CSEKO_HETI = "https://csekokavehaz.hu/debrecen/napi-kinalat/"
GODOR_PDF = "https://www.godor.hu/_data/_vfs/heti.pdf"
GODOR_JOVO = "https://www.godor.hu/_data/_vfs/jovo.pdf"

# Ezeket a kategóriákat egyik étteremnél sem tesszük ki (kisbetűvel):
KIHAGY_KATEGORIA = {"levesek", "leves", "böhöm-boglya", "saláták-savanyúságok", "savanyúságok", "saláták", "saláta"}
# Savanyúságok, amelyek más kategóriában (pl. Saláták) szerepelnek:
SAVANYUSAG = re.compile(r"csemege uborka|cékla|vegyes vágott|ecetes|csalamádé|savanyú káposzta|savanyúság", re.I)


def kell_kategoria(nev):
    return nev.strip().lower() not in KIHAGY_KATEGORIA


def kell_etel(nev):
    return not SAVANYUSAG.search(nev)


NAPOK = ["hétfő", "kedd", "szerda", "csütörtök", "péntek", "szombat", "vasárnap"]
HONAPOK = ["január", "február", "március", "április", "május", "június", "július",
           "augusztus", "szeptember", "október", "november", "december"]


def get(url):
    r = requests.get(url, headers=UA, timeout=45)
    r.raise_for_status()
    return r


# ---------------------------------------------------------------- Csekő
def cseko():
    """A 'Napi kínálat' oldal HTML-ből olvasható: h1/h2 = kategória, h3 = étel, utána ár."""
    soup = BeautifulSoup(get(CSEKO_NAPI).text, "html.parser")
    for t in soup(["script", "style", "noscript"]):
        t.decompose()

    cats, cur, started = [], None, False
    for s in soup.find_all(string=True):
        text = " ".join(s.split())
        if not text:
            continue
        h = s.find_parent(["h1", "h2", "h3", "h4"])
        level = h.name if h else None
        if level == "h1" and text.lower() == "napi kínálat":
            started = True
            continue
        if not started:
            continue
        if "debrecen@" in text or text.startswith("+36"):
            break  # elértük a láblécet
        if level in ("h1", "h2"):
            cur = {"nev": text, "etelek": []}
            cats.append(cur)
        elif level in ("h3", "h4"):
            if cur is None:
                cur = {"nev": "Kínálat", "etelek": []}
                cats.append(cur)
            cur["etelek"].append({"nev": text, "ar": ""})
        elif re.fullmatch(r"[\d\s.]+Ft.*", text) and cur and cur["etelek"] and not cur["etelek"][-1]["ar"]:
            cur["etelek"][-1]["ar"] = text
    for c in cats:
        c["etelek"] = [x for x in c["etelek"] if kell_etel(x["nev"])]
    cats = [c for c in cats if c["etelek"] and kell_kategoria(c["nev"])]
    if not cats:
        raise ValueError("Nem találtam ételeket a Csekő napi kínálat oldalán.")
    return cats


def cseko_heti_kep():
    """A heti menü csak képként van fent; a kép linkjét adjuk tovább."""
    try:
        soup = BeautifulSoup(get(CSEKO_HETI).text, "html.parser")
        for img in soup.find_all("img"):
            src = img.get("src") or ""
            if "uploads" in src and "heti" in src.lower():
                return src
    except Exception:
        pass
    return CSEKO_HETI


# ---------------------------------------------------------------- Gödör
GODOR_OLDAL = "https://www.godor.hu/menurendeles"
GODOR_XHR = "https://www.godor.hu/call/xhr-menu-order-weekly-menu"


def godor():
    """A menürendelő oldal a heti menüt JSON-ban tölti be; ugyanezt kérjük le mi is."""
    with requests.Session() as s:
        s.headers.update(UA)
        r = s.get(GODOR_OLDAL, timeout=45)
        r.raise_for_status()
        meta = BeautifulSoup(r.text, "html.parser").find("meta", attrs={"name": "csrfToken"})
        if not meta:
            raise ValueError("Nem találom a csrfToken-t a Gödör menürendelő oldalán.")
        tok = meta["content"]
        r = s.post(GODOR_XHR, timeout=45,
                   headers={"x-csrf-token": tok, "X-Requested-With": "XMLHttpRequest",
                            "Accept": "application/json", "Referer": GODOR_OLDAL},
                   data={"lang": "hu", "csrfToken": tok, "operation": "get-weekly-menu-list",
                         "offset": "0", "direction": ""})
        r.raise_for_status()
        data = r.json()
    if not data.get("success"):
        raise ValueError("A Gödör szervere hibát jelzett.")
    return data["menu"]["dates"]


def godor_nap(napok, nap):
    """Egy nap csoportjai: [{nev, ar, etelek:[{kod, nev, ar}]}] vagy None, ha zárva/nincs."""
    d = napok.get(nap.isoformat())
    if not d or d.get("is-vacation") or not d.get("is-published") or not d.get("groups"):
        return None
    csoportok = []
    for g in d["groups"].values():
        if not kell_kategoria(g.get("name", "")):
            continue
        etelek = []
        for p in sorted(g.get("products", {}).values(), key=lambda x: x.get("order", 0)):
            m = re.match(r"\s*\(([^)]*)\)\s*(.*)", p.get("name", ""))
            kod, nev = (m.group(1), m.group(2)) if m else ("", p.get("name", ""))
            if not kell_etel(nev):
                continue
            if p.get("is-sold-out-flag"):
                nev += " (elfogyott)"
            etelek.append({"kod": kod, "nev": nev, "ar": ""})
        if etelek:
            ar = g.get("normal-price-formatted", "")
            kis = g.get("small-price")
            if kis:
                ar += f", kis adag {g.get('small-price-formatted')}"
            csoportok.append({"nev": g.get("name", ""), "ar": ar, "etelek": etelek})
    return csoportok or None


# ---------------------------------------------------------------- HTML
e = html.escape


def datum_hu(d):
    return f"{NAPOK[d.weekday()].capitalize()}, {HONAPOK[d.month - 1]} {d.day}."


def sorok(etelek):
    out = []
    for it in etelek:
        kod = f'<span class="kod">{e(it["kod"])}</span>' if it.get("kod") else ""
        ar = f'<span class="pont"></span><span class="ar">{e(it["ar"])}</span>' if it.get("ar") else ""
        out.append(f'<li>{kod}<span class="nev">{e(it["nev"])}</span>{ar}</li>')
    return "<ul>" + "".join(out) + "</ul>"


def blokk(cim, etelek):
    return f'<div class="csoport"><h3>{e(cim)}</h3>{sorok(etelek)}</div>'


def godor_html(today, napok, hiba):
    fej = """<header class="etterem"><h2>Gödör</h2>
<p class="info">Hajdúsági Étterem · hétköznap 11–15 · <a href="tel:+3652322223">52 322 223</a></p></header>"""
    linkek = (f'<p class="forras"><a href="{GODOR_OLDAL}">Rendelés (reggel 9-ig)</a> '
              f'<a href="{GODOR_PDF}">Heti menü (PDF)</a> <a href="{GODOR_JOVO}">Jövő heti menü (PDF)</a></p>')
    if hiba:
        return f'<section>{fej}<p class="uzenet">A mai menüt nem sikerült betölteni. Nézd meg a rendelő oldalukon.</p>{linkek}</section>'
    csoportok = godor_nap(napok, today)
    if not csoportok:
        uz = "Hétvégén zárva." if today.weekday() >= 5 else "Ma nincs menü (zárva vagy még nem tették fel)."
        return f'<section>{fej}<p class="uzenet">{uz}</p>{linkek}</section>'
    body = "".join(
        f'<div class="csoport"><h3>{e(c["nev"])} <span class="csoportar">{e(c["ar"])}</span></h3>{sorok(c["etelek"])}</div>'
        for c in csoportok)
    return f"<section>{fej}{body}{linkek}</section>"


def cseko_html(today, cats, kep, hiba):
    fej = """<header class="etterem"><h2>Csekő Kávéház</h2>
<p class="info">Debrecen · kedd–vasárnap 9–20 · <a href="tel:+36302509420">30 250 9420</a></p></header>"""
    linkek = f'<p class="forras"><a href="{CSEKO_NAPI}">Napi kínálat az oldalukon</a> <a href="{e(kep)}">Heti menü (kép)</a></p>'
    if today.weekday() == 0:
        return f'<section>{fej}<p class="uzenet">Hétfőn zárva.</p>{linkek}</section>'
    if hiba:
        return f'<section>{fej}<p class="uzenet">A mai kínálatot nem sikerült betölteni. Nézd meg az oldalukon.</p>{linkek}</section>'
    body = "".join(blokk(c["nev"], c["etelek"]) for c in cats)
    return f"<section>{fej}{body}{linkek}</section>"


def render(today, now, godor_part, cseko_part):
    tpl = (ROOT / "template.html").read_text("utf-8")
    return (tpl.replace("{{DATUM}}", e(datum_hu(today)))
               .replace("{{FRISSITVE}}", now.strftime("%H:%M"))
               .replace("{{GODOR}}", godor_part)
               .replace("{{CSEKO}}", cseko_part))


def main():
    now = dt.datetime.now(TZ)
    today = now.date()

    try:
        g, g_err = godor(), None
    except Exception as ex:
        g, g_err = {}, ex
        print("Gödör hiba:", ex)
    try:
        c, c_err = cseko(), None
    except Exception as ex:
        c, c_err = [], ex
        print("Csekő hiba:", ex)

    page = render(today, now,
                  godor_html(today, g, g_err),
                  cseko_html(today, c, cseko_heti_kep(), c_err))
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(page, "utf-8")
    print("Kész:", OUT)


if __name__ == "__main__":
    main()

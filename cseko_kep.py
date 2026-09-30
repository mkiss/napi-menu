"""Csekő heti menü kép -> szöveg (Tesseract OCR, magyar nyelvi csomaggal).

A kép szerkezetét nem fix koordinátákból, hanem a zöld színű elemekből olvassuk ki:
  - a zöld napfejlécekből (oszlopok, napnevek),
  - a zöld A / B körökből (a két főétel szakaszai),
  - a kártyák zöld alsó szegélyéből.
A leveseket kihagyjuk (a scrape.py sem mutatja őket).

Használat:  beolvas(kep_url) -> {"napok": {"2026-09-30": {"A": "...", "B": "..."}}, "arak": {"A": "...", ...}}
"""
import datetime as dt
import difflib
import io
import re
import unicodedata

import numpy as np
import requests
from PIL import Image

ZOLD = np.array([133, 192, 45])
NAPOK = ["hetfo", "kedd", "szerda", "csutortok", "pentek", "szombat", "vasarnap"]
HONAPOK = ["januar", "februar", "marcius", "aprilis", "majus", "junius", "julius",
           "augusztus", "szeptember", "oktober", "november", "december"]
UA = {"User-Agent": "Mozilla/5.0 (napi-menu-gyujto; GitHub Actions)"}
NBSP = " "


def ascii_kisbetu(s):
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


# ---------------------------------------------------------------- kép letöltése
def nagy_valtozat(url):
    """A WordPress a kép kicsinyített változatait is kiteszi (-1200x800); a teljes méretűt kérjük."""
    alap = re.sub(r"-\d+x\d+(?=\.\w+$)", "", url)
    kiterj = alap.rsplit(".", 1)
    jeloltek = [kiterj[0] + "-scaled." + kiterj[1], alap, url] if len(kiterj) == 2 else [url]
    for u in jeloltek:
        try:
            r = requests.get(u, headers=UA, timeout=60)
            if r.ok and r.headers.get("content-type", "").startswith("image"):
                return Image.open(io.BytesIO(r.content)).convert("RGB")
        except Exception:
            continue
    raise ValueError("Nem sikerült letölteni a Csekő heti menü képét.")


# ---------------------------------------------------------------- alakfelismerés
def zold_maszk(arr, tol=60):
    d = arr.astype(np.int32) - ZOLD
    return (d * d).sum(axis=2) < tol * tol


def szilard(maszk, k):
    """Csak a legalább k×k-s teljesen zöld foltok maradnak (a vékony vonalak, ikonok kiesnek)."""
    c = np.pad(maszk.astype(np.int32).cumsum(0).cumsum(1), ((1, 0), (1, 0)))
    s = c[k:, k:] - c[:-k, k:] - c[k:, :-k] + c[:-k, :-k]
    ki = np.zeros(maszk.shape, bool)
    ki[k // 2:k // 2 + s.shape[0], k // 2:k // 2 + s.shape[1]] = s >= 0.95 * k * k
    return ki


def csoportok(bool_tomb, max_lyuk, min_hossz=1):
    """Egymás melletti True értékek csoportjai [(kezdet, veg)], a max_lyuk-nál kisebb hézagokat áthidalja."""
    idx = np.flatnonzero(bool_tomb)
    if idx.size == 0:
        return []
    ki, kezd, elozo = [], idx[0], idx[0]
    for i in idx[1:]:
        if i - elozo > max_lyuk:
            ki.append((int(kezd), int(elozo)))
            kezd = i
        elozo = i
    ki.append((int(kezd), int(elozo)))
    return [(a, b) for a, b in ki if b - a + 1 >= min_hossz]


def elrendezes(kep):
    """-> (fejlec_sav, [oszlop dict]) ; oszloponként: x0, x1, alja, a_top, b_top, szoveg_x0"""
    arr = np.asarray(kep)
    H, W = arr.shape[:2]
    zold = zold_maszk(arr)

    # Napfejlécek: a legfelső széles zöld sáv
    sor = zold.sum(axis=1)
    savok = csoportok(sor > 0.3 * sor.max(), int(0.015 * H), int(0.03 * H))
    if not savok:
        raise ValueError("Nem találom a zöld napfejléceket a képen.")
    y0, y1 = savok[0]

    # Oszlopok: a fejlécsáv függőlegesen zöld oszlopai
    oszlopszam = zold[y0:y1].sum(axis=0)
    oszlopok = csoportok(oszlopszam > 0.3 * (y1 - y0), int(0.015 * W), int(0.08 * W))
    if not oszlopok:
        raise ValueError("Nem találom az oszlopokat a képen.")

    k = max(5, round(W / 280))
    out = []
    for x0, x1 in oszlopok:
        szel = x1 - x0 + 1
        # kártya alsó szegélye: az első sor, ahol a zöld átér a kártya szélességének nagy részén
        alja = int(0.75 * H)
        # (a vékony, 1 pixeles elválasztó vonalakat kihagyjuk: a szegély legalább 3 sor vastag)
        teli = zold[:, x0:x1 + 1].sum(axis=1) >= 0.7 * szel
        for y in range(y1 + int(0.03 * H), H - 2):
            if teli[y] and teli[y + 1] and teli[y + 2]:
                alja = y
                break
        # A / B körök a bal sávban
        sav = szilard(zold[y1:alja, x0:x0 + int(0.3 * szel)], k)
        korok = csoportok(sav.any(axis=1), 8, int(0.02 * H))
        if len(korok) < 2:
            out.append({"x0": x0, "x1": x1, "alja": alja, "korok": None})
            continue
        korok = korok[-2:]  # a leves ikonja, ha mégis átcsúszna, kiesik
        (a0, _), (b0, _) = korok
        kor_jobb = int(np.flatnonzero(sav.any(axis=0)).max()) + x0 + k // 2
        out.append({"x0": x0, "x1": x1, "alja": alja,
                    "a_top": y1 + a0 - k // 2, "b_top": y1 + b0 - k // 2,
                    "szoveg_x0": kor_jobb + int(0.012 * W)})
    return (y0, y1), out


# ---------------------------------------------------------------- OCR
def tinta_sotet(kep_reszlet, kuszob=130):
    """Sötét szöveg fehér alapon (a halvány szürke elválasztó vonalak és a zöld keret kiesik)."""
    g = np.asarray(kep_reszlet.convert("L"))
    return Image.fromarray(np.where(g < kuszob, 0, 255).astype(np.uint8))


def tinta_feher(kep_reszlet, kuszob=215):
    """Fehér szöveg zöld alapon -> fekete szöveg fehér alapon."""
    a = np.asarray(kep_reszlet).min(axis=2)
    return Image.fromarray(np.where(a > kuszob, 0, 255).astype(np.uint8))


def tinta_zold_vagy_sotet(kep_reszlet):
    """Az árak zöld betűk; a dobozok vonalait (hosszú egyenes futások) töröljük."""
    arr = np.asarray(kep_reszlet)
    m = zold_maszk(arr, 75) | (np.asarray(kep_reszlet.convert("L")) < 110)
    for tengely in (1, 0):
        t = m if tengely == 1 else m.T
        for i in range(t.shape[0]):
            for a, b in csoportok(t[i], 1, 60):
                t[i, a:b + 1] = False
    return Image.fromarray(np.where(m, 0, 255).astype(np.uint8))


def keretez(kep, px=24):
    ki = Image.new("L", (kep.width + 2 * px, kep.height + 2 * px), 255)
    ki.paste(kep, (px, px))
    return ki


def ocr(kep, psm):
    import pytesseract
    kep = keretez(kep)
    for nyelv in ("hun", "hun+eng", "eng"):
        try:
            return pytesseract.image_to_string(kep, lang=nyelv, config=f"--psm {psm}")
        except pytesseract.TesseractError as ex:
            print(f"Tesseract '{nyelv}' nyelv hiba: {ex}")
    raise ValueError("A Tesseract nem futtatható (magyar nyelvi csomag telepítve van?).")


def etel_szoveg(nyers):
    s = " ".join(nyers.split())
    s = re.sub(r"[|_~=«»\\]", "", s)
    s = re.sub(r"\s+([,.;:])", r"\1", s)
    s = re.sub(r"^[^\wÁÉÍÓÖŐÚÜŰáéíóöőúüű]+", "", s).strip(" ,;:-")
    s = " ".join(s.split())
    return (s[:1].upper() + s[1:]) if len(s) >= 3 else ""


def nap_indexe(nyers, sorszam):
    s = re.sub(r"[^a-z]", "", ascii_kisbetu(nyers))
    talalat = difflib.get_close_matches(s, NAPOK, n=1, cutoff=0.6)
    return NAPOK.index(talalat[0]) if talalat else None


def kezdo_datum(nyers, ma):
    """A címsorból (pl. '2026. SZEPTEMBER 29. - OKTÓBER 02.') a hét első napja."""
    s = ascii_kisbetu(nyers)
    m = re.search(r"(20\d\d)\D{0,3}([a-z]{5,10})\D{0,3}(\d{1,2})", s)
    if m:
        hon = difflib.get_close_matches(m.group(2), HONAPOK, n=1, cutoff=0.6)
        if hon:
            try:
                return dt.date(int(m.group(1)), HONAPOK.index(hon[0]) + 1, int(m.group(3)))
            except ValueError:
                pass
    print("Csekő heti menü: a dátum nem olvasható a címsorból, az aktuális hetet feltételezem.")
    return ma - dt.timedelta(days=ma.weekday())


def ar_szoveg(szamok):
    n = int(re.sub(r"\D", "", szamok))
    return f"{n:,}".replace(",", NBSP) + f"{NBSP}Ft" if 500 <= n <= 20000 else ""


def arak_kiolvas(nyers):
    s = ascii_kisbetu(nyers)
    ar = r"(\d[\d .]{1,6}\d)\s*f"
    minta = {"A": r"(?<![a-z])a\s*m[a-z]{2,4}\W{0,3}" + ar,
             "B": r"(?<![a-z])b\s*m[a-z]{2,4}\W{0,3}" + ar,
             "F": r"f[a-z]{0,2}e?t[a-z]l\W{0,3}" + ar}
    ki = {}
    for kulcs, p in minta.items():
        m = re.search(p, s)
        if m:
            ki[kulcs] = ar_szoveg(m.group(1))
    if len(ki) < 3:  # tartalék: a hármas ezres árak sorrendben (A, B, Főétel)
        mind = [ar_szoveg(x) for x in re.findall(r"(\d\s?\d{3})\s*f", s)]
        for kulcs, v in zip("ABF", [x for x in mind if x]):
            ki.setdefault(kulcs, v)
    return ki


# ---------------------------------------------------------------- fő függvény
def beolvas(url, ma=None):
    ma = ma or dt.datetime.now().date()
    kep = nagy_valtozat(url)
    (y0, y1), oszlopok = elrendezes(kep)
    W, H = kep.size

    cim = ocr(tinta_sotet(kep.crop((int(0.28 * W), int(0.10 * H), int(0.73 * W), y0 - int(0.003 * H)))), 6)
    kezdet = kezdo_datum(cim, ma)

    napok, hasznalt = {}, set()
    for i, o in enumerate(oszlopok):
        if not o.get("a_top"):
            print(f"Csekő heti menü: a(z) {i + 1}. oszlopban nem találtam A/B jelet.")
            continue
        fejlec = ocr(tinta_feher(kep.crop((o["x0"], y0, o["x1"], y1))), 7)
        wd = nap_indexe(fejlec, i)
        if wd is None or wd in hasznalt:
            wd = (kezdet.weekday() + i) % 7
        hasznalt.add(wd)
        datum = kezdet + dt.timedelta(days=(wd - kezdet.weekday()) % 7)

        m = int(0.008 * H)
        x0, x1 = o["szoveg_x0"], o["x1"] - int(0.005 * W)
        a = kep.crop((x0, o["a_top"] - m, x1, o["b_top"] - int(0.018 * H)))
        b = kep.crop((x0, o["b_top"] - m, x1, o["alja"] - 4))
        etelek = {"A": etel_szoveg(ocr(tinta_sotet(a), 6)), "B": etel_szoveg(ocr(tinta_sotet(b), 6))}
        napok[datum.isoformat()] = {k: v for k, v in etelek.items() if v}

    alja = max(o["alja"] for o in oszlopok)
    arak_kep = kep.crop((int(0.05 * W), alja + int(0.01 * H), int(0.75 * W), alja + int(0.13 * H)))
    arak = arak_kiolvas(ocr(tinta_zold_vagy_sotet(arak_kep), 11))
    if not napok:
        raise ValueError("A Csekő heti menü képéből nem sikerült ételeket kiolvasni.")
    return {"napok": napok, "arak": arak}


if __name__ == "__main__":
    import json
    import sys
    print(json.dumps(beolvas(sys.argv[1]), ensure_ascii=False, indent=2))

# Napi menü – Debrecen

Minden reggel automatikusan összegyűjti a Gödör és a Csekő Kávéház mai menüjét, és kitesz belőle egy oldalt a GitHub Pagesre. Teljesen ingyenes, API-kulcs nem kell.

## Beüzemelés (egyszer, kb. 5 perc)

1. Hozz létre egy új, **nyilvános** repót a GitHubon, és töltsd fel ennek a mappának a tartalmát (az `.github` mappát is).
2. **Settings → Actions → General → Workflow permissions**: válaszd a *Read and write permissions* opciót.
3. **Actions** fül → *Napi menü frissítése* → **Run workflow** (első futtatás).
4. **Settings → Pages**: Source = *Deploy from a branch*, Branch = `main`, mappa = `/docs`.

Pár perc múlva az oldal elérhető: `https://<felhasználónév>.github.io/<repó-neve>/`

## Honnan jönnek az adatok

- **Gödör**: a godor.hu/menurendeles oldal ugyanabból a belső adatforrásból tölti be a heti menüt, amit a szkript is lekér (napokra, kategóriákra bontva, árakkal).
- **Csekő**: a csekokavehaz.hu „Napi kínálat” oldala, amit szövegként dolgoz fel.
- Ha valamelyik oldal nem elérhető vagy megváltozik a szerkezete, az oldal ezt jelzi, és linkeli az eredetit.

## Testreszabás

- Frissítés időpontja: `.github/workflows/frissites.yml`, `cron` sor (UTC időben).
- Kihagyott kategóriák (levesek, saláták, Böhöm-Boglya, savanyúságok): `KIHAGY_KATEGORIA`, a savanyúságok szűrése: `SAVANYUSAG` a `scrape.py`-ban.
- Kinézet: `template.html`.

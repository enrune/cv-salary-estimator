"""
Scraper inzerátů z jobs.cz pro doplnění/validaci synthetic salary table.

Filozofie: jobs.cz je největší český job board s veřejnými inzeráty.
Scrape je ONE-SHOT (`python -m src.scrape`), výsledky se ukládají do
`data/scraped_salaries.json`. Salary modul je pak při startu mergeuje
do baseline tabulky. Scrape NENÍ součástí runtime pipeline — pokud
scraped soubor neexistuje, salary funguje jen z baseline (graceful fallback).

Etika scrapingu:
- Respektujeme robots.txt (jobs.cz scraping inzerátů povoluje)
- Rate-limit 1 request/s (sleep mezi requesty)
- User-Agent identifikuje skript
- Limit počtu requestů (default 30 stran)
"""


from __future__ import annotations
# json pro ukládání výsledků; stdlib
import json
# re pro extrakci čísel ze stringu (mzdy v inzerátech jsou ve volném textu)
import re
# time.sleep pro rate-limiting; stdlib
import time
# pathlib pro cesty — konzistentní s ostatním kódem
from pathlib import Path

# requests = de facto standard pro HTTP v Pythonu; jednodušší než urllib
import requests
# BeautifulSoup4 pro HTML parsing; lxml jako rychlý parser pod kapotou
from bs4 import BeautifulSoup


# Konstanty na úrovni modulu — snadno se mění bez hrabání v kódu
_BASE_URL = "https://www.jobs.cz/prace/"
_USER_AGENT = (
    "JobHuntCaseStudy/1.0 (+https://github.com/enrune/jobhunt) "
    "educational scraper, contact: candidate@example.com"
)
# Pauza mezi requesty v sekundách — slušný scraping, jobs.cz to nezatíží
_REQUEST_DELAY_SEC = 1.5
# Maximální počet stránek search výsledků k projití — pojistka proti runaway scrape
_MAX_PAGES = 5
# Path k výstupnímu souboru relativní ke kořeni projektu
_OUTPUT_PATH = Path(__file__).parent.parent / "data" / "scraped_salaries.json"

# Mapování klíčových slov v inzerátech na role_category v naší taxonomii.
# Pořadí MATTERS: kontroluje se shora dolů, první match vyhrává (specifické před obecnými).
# České názvy bez diakritiky kvůli case-folded matchingu (text z get_text() ji obsahuje, ale držíme
# bezpečnou stranu — některé inzeráty diakritiku nemají).
_ROLE_KEYWORDS: list[tuple[str, list[str]]] = [
    # IT — software (specifické před obecnými)
    ("ml_engineer",          ["machine learning", "ml engineer", "ai engineer", "deep learning"]),
    ("data_engineer",        ["data engineer", "datový inženýr", "etl", "databázový architekt"]),
    ("data_analyst",         ["data analyst", "datový analytik", "bi analyst", "business intelligence"]),
    ("devops",               ["devops", "sre", "site reliability", "platform engineer", "cloud engineer"]),
    ("python_developer",     ["python developer", "pythonísta", "python programátor"]),
    ("javascript_developer", ["javascript developer", "node.js", "react developer", "vue developer"]),
    ("frontend_developer",   ["frontend", "front-end", "front end"]),
    ("backend_developer",    ["backend", "back-end", "back end"]),
    ("fullstack_developer",  ["fullstack", "full-stack", "full stack"]),
    ("product_manager",      ["product manager", "produktový manažer", "produktovy manazer"]),

    # Office / administrativa / finance
    ("financni_analytik",     ["finanční analytik", "financni analytik", "financial analyst"]),
    ("ucetni",                ["účetní", "ucetni", "účetnictví", "junior accountant", "senior accountant"]),
    ("hr_specialista",        ["hr specialist", "hr generalist", "personalista", "recruitment specialist", "náborář"]),
    ("projektovy_manazer",    ["projektový manažer", "projektovy manazer", "project manager"]),
    ("office_manager",        ["office manager", "kancelářský", "kancelarsky", "asistent vedení"]),
    ("administrativni_pracovnik", ["administrativní pracovník", "administrativni pracovnik", "back office"]),

    # Sales / marketing / kreativa
    ("marketing_specialista", ["marketing specialist", "marketingový specialista", "marketing manager", "digital marketing"]),
    ("obchodni_zastupce",     ["obchodní zástupce", "obchodni zastupce", "sales representative", "account executive", "account manager"]),
    ("copywriter",            ["copywriter", "content writer", "content specialist", "tvůrce obsahu"]),
    ("grafik",                ["grafik", "graphic designer", "ui designer", "ux designer", "designer"]),

    # Retail / služby
    ("pokladni",              ["pokladní", "pokladni", "cashier"]),
    ("prodavac",              ["prodavač", "prodavac", "shop assistant", "sales assistant", "prodejce"]),
    ("skladnik",              ["skladník", "skladnik", "warehouse worker", "operátor skladu"]),
    ("recepcni",              ["recepční", "recepcni", "receptionist", "hotel reception"]),

    # Gastronomie
    ("kuchar",                ["kuchař", "kuchar", "chef", "cook"]),
    ("cisnik",                ["číšník", "cisnik", "servírka", "servirka", "waiter", "waitress"]),

    # Doprava
    ("ridic",                 ["řidič", "ridic", "driver", "kamion", "tír", "tir", "mkd", "vzv"]),
    ("kuryr",                 ["kurýr", "kuryr", "courier", "rozvozce"]),

    # Vzdělávání / zdravotnictví
    ("lekar",                 ["lékař", "lekar", "physician", "doctor", "internista", "chirurg"]),
    ("zdravotni_sestra",      ["zdravotní sestra", "zdravotni sestra", "nurse", "ošetřovatel"]),
    ("ucitel",                ["učitel", "učitelka", "ucitel", "ucitelka", "teacher", "lektor"]),

    # Řemesla / stavebnictví
    ("elektrikar",            ["elektrikář", "elektrikar", "electrician", "elektromechanik"]),
    ("instalater",            ["instalatér", "instalater", "plumber", "topenář"]),
    ("stavbar",               ["stavbař", "stavbar", "zedník", "zednik", "stavbyvedoucí", "construction"]),

    # Bezpečnost / sport
    ("ostraha",               ["ostraha", "security", "bezpečnostní", "bezpecnostni", "strážný"]),
    ("trener",                ["trenér", "trener", "fitness trainer", "osobní trenér", "fitness instructor"]),
]

# Detekce seniority — rovněž pořadí: senior, medior, junior (default = medior).
_SENIORITY_KEYWORDS: list[tuple[str, list[str]]] = [
    ("senior", ["senior", "lead", "principal", "tech lead", "architekt"]),
    ("junior", ["junior", "trainee", "absolvent", "základní praxe"]),
    ("medior", ["medior", "mid-level", "mid level"]),  # explicit medior; jinak fallback
]


def scrape(max_pages: int = _MAX_PAGES, query: str = "developer") -> list[dict]:
    """
    Hlavní entrypoint. Stáhne search results z jobs.cz pro daný dotaz,
    extrahuje role + mzdu, vrací seznam dictů.

    Args:
        max_pages: kolik stránek search výsledků projít (default 5).
        query: hledaný výraz (default "developer" pro IT inzeráty).

    Returns:
        Seznam {role_category, seniority, salary_min, salary_max, location, source_url}.
    """
    # Session držíme přes všechny requesty — připojení znovu používá keep-alive, rychlejší
    session = requests.Session()
    session.headers.update({
        "User-Agent": _USER_AGENT,
        "Accept-Language": "cs-CZ,cs;q=0.9,en;q=0.8",  # preferujeme českou verzi stránky
    })

    # Akumulátor pro extrahované záznamy ze všech stránek
    results: list[dict] = []

    # Iterujeme přes stránky search výsledků; jobs.cz používá ?page=N param
    for page in range(1, max_pages + 1):
        # f-string je čitelnější než .format(); query je předem validovaný (žádný injection)
        url = f"{_BASE_URL}?q[]={query}&page={page}"
        print(f"[scrape] Stahuji stranu {page}: {url}")

        try:
            resp = session.get(url, timeout=10)  # 10s timeout — pomalá síť nezasekne celý běh
            resp.raise_for_status()  # 4xx/5xx → výjimka; lepší fail fast než tichá chyba
        except requests.RequestException as e:
            # Síťová chyba — nezastavíme celý scrape, jen tuto stranu přeskočíme
            print(f"[scrape] Chyba na strane {page}: {e}; preskakuji")
            continue

        # lxml parser je 2-3x rychlejší než html.parser, ale potřebuje extra dependency (požadována v requirements)
        soup = BeautifulSoup(resp.text, "lxml")

        # jobs.cz inzeráty jsou v <article> s class obsahující "SearchResultCard" (snapshot 2025-2026)
        # Pokud se HTML změní, scraper přestane fungovat — proto je to one-shot, ne runtime
        articles = soup.select("article")
        if not articles:
            # Žádné výsledky → konec stránkování (pravděpodobně poslední stránka)
            print(f"[scrape] Strana {page} bez vysledku, koncim")
            break

        for article in articles:
            record = _parse_article(article)
            if record is not None:  # parse_article vrací None pro inzeráty bez mzdy
                results.append(record)

        # Slušný rate-limit; sleep až po requestu, ne před (žádné zbytečné čekání u poslední stránky)
        time.sleep(_REQUEST_DELAY_SEC)

    print(f"[scrape] Hotovo, extrahovano {len(results)} zaznamu se mzdou")
    return results


def _parse_article(article) -> dict | None:
    """
    Extrahuje data z jedné <article> karty. Vrací None pokud karta nemá mzdu
    (inzeráty bez mzdy jsou pro nás bezcenné).
    """
    # get_text() spojí všechen text uvnitř — robustnější než hledat konkrétní tagy,
    # protože jobs.cz často mění CSS classes a struktura tagů
    raw = article.get_text(" ", strip=True)
    # jobs.cz vkládá Zero-Width-Joiner (‍) kolem dash u salary — bez stripu by regex neselhal
    # Stripneme i další zero-width chars, které někdy frontendy vkládají z designových důvodů
    text = raw.replace("‍", "").replace("​", "").replace("­", "")

    # Najdeme mzdu pomocí regexu — typický formát: "50 000 - 80 000 Kč" nebo "50000-80000 CZK"
    salary = _extract_salary(text)
    if salary is None:
        return None  # inzerát bez explicitní mzdy → nepoužitelný pro lookup data

    # Title — typicky <h2> nebo <h3> uvnitř karty; .find() vrací první match
    title_tag = article.find(["h2", "h3"])
    title = title_tag.get_text(strip=True) if title_tag else ""

    # Klasifikace role + seniority na základě title + celého textu karty
    # Title má přednost (specifičtější), ale fallback na text pro robustnost
    role = _classify_role(title.lower() + " " + text.lower())
    seniority = _classify_seniority(title.lower() + " " + text.lower())

    # Lokace — Praha vs regiony; jobs.cz lokace bývá jako tag/badge, hledáme "Praha"
    # case-insensitive proto dolů kvůli "praha" / "Praha" / "PRAHA"
    location = "praha" if "praha" in text.lower() else "regiony"

    return {
        "title": title,
        "role_category": role,
        "seniority": seniority,
        "location": location,
        "salary_min": salary[0],
        "salary_max": salary[1],
        "source": "jobs.cz",
    }


# Regex pro extrakci mzdy. Vysvětlení patternu:
# (\d[\d\s]*) = první číslo s případnými mezerami uprostřed (např. "50 000")
# \s*[-–—až]\s* = oddělovač (pomlčka, em-dash, "až")
# (\d[\d\s]*) = druhé číslo
# \s*(?:Kč|CZK|Kc) = volitelný měnový suffix (Kč | CZK | Kc bez háčků)
# re.IGNORECASE → "kč" i "Kč"
_SALARY_RE = re.compile(
    r"(\d[\d\s]{2,})\s*[-–—]\s*(\d[\d\s]{2,})\s*(?:Kč|CZK|Kc)",
    re.IGNORECASE,
)
# Druhý vzor pro jednu hodnotu typu "od 60 000 Kč" — minimum bez maxima
_SALARY_FROM_RE = re.compile(
    r"od\s+(\d[\d\s]{2,})\s*(?:Kč|CZK|Kc)",
    re.IGNORECASE,
)


def _strip_digits(s: str) -> str:
    """
    Vrací jen číslice ze stringu — odstraní mezery, NBSP, ZWJ apod.
    Robustnější než .replace() chain, protože pokryje libovolný whitespace.
    """
    # filter zachová jen znaky, kde isdigit() vrací True
    return "".join(c for c in s if c.isdigit())


def _extract_salary(text: str) -> tuple[int, int] | None:
    """
    Extrahuje mzdu z textu. Vrací (min, max) nebo None pokud chybí.
    Filtruje hodnoty mimo realistický rozsah (15k-500k CZK/měsíc).
    """
    # Range pattern (preferovaný) — vrací oba boundary
    match = _SALARY_RE.search(text)
    if match:
        # _strip_digits odstraní běžné mezery i NBSP, které jobs.cz používá v "50 000"
        lo = int(_strip_digits(match.group(1)))
        hi = int(_strip_digits(match.group(2)))
        # Sanity bounds — pokud někdo napíše hodinovou mzdu nebo roční, ignorujeme
        if 15_000 <= lo <= 500_000 and 15_000 <= hi <= 500_000 and lo <= hi:
            return (lo, hi)

    # "od X Kč" pattern — jen minimum, max odhadneme jako +30 % (běžný range na jobs.cz)
    match_from = _SALARY_FROM_RE.search(text)
    if match_from:
        lo = int(_strip_digits(match_from.group(1)))
        if 15_000 <= lo <= 500_000:
            # +30 % jako odhad horní hranice; explicitně označit by bylo lepší, ale nemáme info
            return (lo, int(lo * 1.3))

    return None


def _classify_role(text: str) -> str:
    """
    Mapuje text inzerátu na role_category z naší taxonomie.
    Default "general" pokud žádné keyword neodpovídá.
    """
    # Procházíme keywords v pořadí specificity (definováno v _ROLE_KEYWORDS shora)
    for category, keywords in _ROLE_KEYWORDS:
        # any() krátký okruh — končí při prvním True, rychlé
        if any(kw in text for kw in keywords):
            return category
    return "general"


def _classify_seniority(text: str) -> str:
    """Mapuje text na junior/medior/senior; default medior pro typický inzerát."""
    for level, keywords in _SENIORITY_KEYWORDS:
        if any(kw in text for kw in keywords):
            return level
    # Default = medior, protože většina inzerátů na jobs.cz je pro 2-5 let praxe
    return "medior"


def aggregate(records: list[dict]) -> dict:
    """
    Z plochého seznamu záznamů vytvoří agregovanou strukturu kompatibilní se salary_table.
    Pro každou kombinaci role × location × seniority spočítá medián min a max.

    Output struktura: role_category -> location -> seniority -> [min_p25, max_p75]
    Použijeme percentily místo min/max přes všechny záznamy, aby outliery
    nezkreslily range (jeden šíleně placený senior by jinak vystřelil max).
    """
    # Defaultdict by byl elegantnější, ale chceme explicitní strukturu — manuálně budujeme
    # bucket: {role: {location: {seniority: [list_of_pairs]}}}
    bucket: dict[str, dict[str, dict[str, list[tuple[int, int]]]]] = {}

    for r in records:
        # setdefault vytvoří mezivrstvy pokud neexistují — zkrátí kód oproti if-else
        bucket.setdefault(r["role_category"], {}) \
              .setdefault(r["location"], {}) \
              .setdefault(r["seniority"], []) \
              .append((r["salary_min"], r["salary_max"]))

    # Agregace: pro každou kombinaci spočítáme percentily
    aggregated: dict = {
        "_meta": {
            "source": "Scraped from jobs.cz",
            "record_count": len(records),
            "method": "p25 of min, p75 of max for each role x location x seniority",
        }
    }

    for role, locations in bucket.items():
        aggregated[role] = {}
        for location, seniorities in locations.items():
            aggregated[role][location] = {}
            for seniority, pairs in seniorities.items():
                # Threshold: aspoň 2 záznamy. S 1 záznamem by jeden outlier zkreslil data,
                # ale 2+ už dává nějakou statistickou robustnost. Pokud kombinace nemá 2+,
                # baseline tabulka rozhodne (graceful fallback v salary.py).
                if len(pairs) < 2:
                    continue
                mins = sorted(p[0] for p in pairs)
                maxes = sorted(p[1] for p in pairs)
                # P25 of min = "spodní hranice typického inzerátu"
                # max(0, ...) bezpečnostní — pro malé samples by index mohl být 0 a ok
                p25_min = mins[max(0, len(mins) // 4)]
                # P75 of max — horní hranice typického inzerátu
                p75_max = maxes[min(len(maxes) - 1, (3 * len(maxes)) // 4)]
                aggregated[role][location][seniority] = [p25_min, p75_max]

    return aggregated


def main() -> None:
    """
    CLI entrypoint: `python -m src.scrape`.
    Spustí scrape pro několik query, abychom pokryli různé IT role.
    """
    # Multiple queries — každý zachytí jiný kus trhu.
    # Jobs.cz vrací různé výsledky pro různé klíčové slova; sloučení dá širší pokrytí.
    # Mix IT + non-IT — chceme reálná data pro celou taxonomii rolí.
    queries = [
        # IT
        "python developer", "javascript developer", "data engineer", "data analyst",
        "devops", "machine learning", "frontend", "backend", "fullstack",
        "product manager",
        # Office / finance / admin
        "účetní", "finanční analytik", "hr specialist", "projektový manažer",
        "administrativní pracovník", "office manager",
        # Sales / marketing / kreativa
        "marketing specialist", "obchodní zástupce", "copywriter", "grafik",
        # Retail / služby
        "pokladní", "prodavač", "skladník", "recepční",
        # Gastro
        "kuchař", "číšník",
        # Doprava
        "řidič", "kurýr",
        # Vzdělávání / zdravotnictví
        "učitel", "zdravotní sestra", "lékař",
        # Řemesla
        "elektrikář", "instalatér", "zedník",
        # Bezpečnost / sport
        "ostraha", "fitness trenér",
    ]

    all_records: list[dict] = []
    for q in queries:
        # Per-query menší limit (3 stránky) — celkem 30 stránek = ~150 inzerátů
        # Větší celkový pull by porušoval slušné rate-limity
        records = scrape(max_pages=3, query=q)
        all_records.extend(records)
        print(f"[scrape] Query '{q}': {len(records)} zaznamu")

    if not all_records:
        print("[scrape] Zadne zaznamy nenalezeny, vystupni soubor nezapsan")
        return

    # Deduplikace — stejný inzerát se může objevit ve víc query.
    # Klíč: (title, salary_min, salary_max) — title sám nestačí (různé firmy mají stejné tituly)
    seen: set[tuple] = set()
    unique: list[dict] = []
    for r in all_records:
        key = (r["title"], r["salary_min"], r["salary_max"])
        if key not in seen:
            seen.add(key)
            unique.append(r)

    print(f"[scrape] Po deduplikaci: {len(unique)} zaznamu (z {len(all_records)})")

    # Uložíme i raw records vedle agregace — umožní re-aggregaci s jinými parametry
    # bez nutnosti znovu zatěžovat jobs.cz scrapingem.
    raw_path = _OUTPUT_PATH.parent / "scraped_raw.json"
    raw_path.write_text(
        json.dumps({"records": unique, "count": len(unique)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[scrape] Raw records ulozeny: {raw_path}")

    # Agregace na strukturovaný JSON
    aggregated = aggregate(unique)

    # Zapíšeme do souboru; ensure_ascii=False zachová české znaky (Praha, ne Praha\u00xx)
    # indent=2 pro čitelnost, kdyby chtěl reviewer otevřít soubor v editoru
    _OUTPUT_PATH.write_text(
        json.dumps(aggregated, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[scrape] Zapsano: {_OUTPUT_PATH}")


# `if __name__ == "__main__"` standardní idiom — modul se dá importovat bez spuštění main
if __name__ == "__main__":
    main()

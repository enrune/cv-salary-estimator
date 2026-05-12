"""
Salary estimation: CV + Score → SalaryEstimate.

Multi-faktorový výpočet:
1. Načti baseline tabulku (data/salary_table.json)
2. Pokud existuje data/scraped_salaries.json, mergni ji do baseline (scraped má prioritu)
3. Klasifikuj seniorita podle score (junior/medior/senior)
4. Detekuj location z CV (Praha vs regiony)
5. Lookup [min, max] range pro role × location × seniority
6. Posuň polohu v range podle score (vyšší score → blíže max)
7. Aplikuj bonus za in-demand skills (+5 % za každou high-value, max +20 %)
"""


from __future__ import annotations
# json pro načtení tabulek; stdlib
import json
# functools.lru_cache pro caching načtení JSON souborů — IO se neopakuje
from functools import lru_cache
# Path pro cesty
from pathlib import Path

# Vlastní typy
from src.models import CV, Score, SalaryEstimate
from src.debug import DebugTrace


# Cesty k datovým souborům — relativní ke kořeni projektu
_BASELINE_PATH = Path(__file__).parent.parent / "data" / "salary_table.json"
_SCRAPED_PATH = Path(__file__).parent.parent / "data" / "scraped_salaries.json"

# In-demand skills, které dávají bonus k mzdě (CZK %)
# Méně skills než ve score.py — chceme jen ty, kde trh skutečně platí premium
_PREMIUM_SKILLS: dict[str, float] = {
    "aws": 0.05, "kubernetes": 0.07, "rust": 0.08, "go": 0.05,
    "ml": 0.07, "llm": 0.08, "rag": 0.07, "pytorch": 0.06,
    "spark": 0.05, "airflow": 0.04, "terraform": 0.05,
}
# Cap na sumě bonusů; jinak by někdo s "AWS, K8s, Rust, ML, LLM" dostal +35 %
_MAX_SKILL_BONUS = 0.20


@lru_cache(maxsize=1)
def _load_salary_table() -> dict:
    """
    Načte baseline tabulku + případně mergne scraped data.
    @lru_cache: načítáme jednou, držíme v paměti — stejný proces nečte JSON opakovaně.
    """
    # Baseline — vždy musí existovat (commitnutý v repu)
    baseline = json.loads(_BASELINE_PATH.read_text(encoding="utf-8"))

    # Scraped — volitelný; pokud chybí, jen použijeme baseline
    if _SCRAPED_PATH.exists():
        scraped = json.loads(_SCRAPED_PATH.read_text(encoding="utf-8"))
        # Merge: scraped data přepíšou baseline tam, kde existují
        # Jdeme do hloubky 3 (role → location → seniority)
        for role, role_data in scraped.items():
            if role.startswith("_"):  # _meta a podobné metadata neslučujeme
                continue
            for location, loc_data in role_data.items():
                for seniority, range_pair in loc_data.items():
                    # Setdefault zajistí, že struktura existuje, pak overwrite
                    baseline.setdefault(role, {}).setdefault(location, {})[seniority] = range_pair

    return baseline


def estimate_salary(cv: CV, score: Score, *, trace: DebugTrace | None = None) -> SalaryEstimate:
    """Hlavní funkce: CV + Score → SalaryEstimate."""
    # Krok 1: load tabulku (cached)
    table = _load_salary_table()
    bonuses_applied: list[str] = []

    # Krok 2: detekce role — primárně z cv.role_category (nastavil LLM v parse.py)
    role = cv.role_category
    if role not in table:
        # Defenzivně: pokud LLM vrátil neznámou kategorii, fallback na "general"
        # Toto by se nemělo stávat (Literal type vynucuje, ale runtime safety)
        bonuses_applied.append(f"role '{role}' nenalezena, fallback na 'general'")
        role = "general"

    # Krok 3: detekce location
    location = _detect_location(cv)

    # Krok 4: detekce seniority — PRIMÁRNĚ podle let praxe, NE podle score.
    # Důvod: non-IT pozice (pokladní, řidič, sestra...) mají strukturálně nižší
    # skills score, protože v _HIGH_VALUE_SKILLS je víc IT-zaměřených dovedností.
    # Roky praxe jsou univerzální signál, který funguje napříč odvětvími.
    seniority = _seniority_from_years(cv.years_experience)

    # Krok 5: lookup baseline range
    # Defenzivně .get() s defaults pro případ chybějící kombinace v tabulce
    role_data = table[role]
    location_data = role_data.get(location, role_data.get("praha", {}))  # fallback na Prahu
    range_pair = location_data.get(seniority)

    if range_pair is None:
        # Žádná data pro tuto kombinaci — vrátíme general fallback range
        range_pair = table["general"].get(location, table["general"]["praha"])[seniority]
        bonuses_applied.append(f"chybi data pro {role}/{location}/{seniority}, fallback general")

    base_min, base_max = range_pair[0], range_pair[1]

    # Krok 6: posun v range podle score
    # Score 30 (spodek seniority kategorie) → spodní 1/3 range
    # Score 70 (top kategorie) → horní 1/3 range
    # Lineární interpolace mezi seniorita boundary
    sen_lo, sen_hi = _seniority_bounds(seniority)
    if sen_hi > sen_lo:
        # Pozice 0..1 v rámci seniorita kategorie
        position = (score.total - sen_lo) / (sen_hi - sen_lo)
        position = max(0.0, min(1.0, position))  # clamp na [0, 1]
    else:
        position = 0.5  # bezpečný default

    # Range má šířku (base_max - base_min). Posuneme aktuální range o 50 % šířky
    # podle pozice, takže score 0% v kategorii = spodní polovina, 100% = horní polovina
    range_width = base_max - base_min
    shift = int(range_width * 0.25 * (position - 0.5))  # max ±12.5% range
    adj_min = base_min + shift
    adj_max = base_max + shift

    # Krok 7: bonus za premium skills
    skills_lower = {s.lower() for s in cv.skills}
    bonus_pct = 0.0
    for skill, pct in _PREMIUM_SKILLS.items():
        if skill in skills_lower:
            bonus_pct += pct
            bonuses_applied.append(f"+{int(pct*100)}% za {skill}")
    bonus_pct = min(_MAX_SKILL_BONUS, bonus_pct)  # cap

    if bonus_pct > 0:
        adj_min = int(adj_min * (1 + bonus_pct))
        adj_max = int(adj_max * (1 + bonus_pct))

    # Sanity: zaokrouhlení na tisícovky pro lidsky čitelný výstup ("85 000" ne "84 723")
    adj_min = round(adj_min / 1000) * 1000
    adj_max = round(adj_max / 1000) * 1000

    estimate = SalaryEstimate(
        min_czk=adj_min,
        max_czk=adj_max,
        role_detected=role,
        seniority=seniority,
        location=location,
        bonuses_applied=bonuses_applied,
    )

    if trace is not None:
        trace.log(
            "salary",
            role_input=cv.role_category,
            role_used=role,
            location=location,
            seniority=seniority,
            score_total=score.total,
            base_range=[base_min, base_max],
            position_in_seniority=round(position, 2),
            shift_applied=shift,
            bonus_pct=round(bonus_pct, 3),
            final_range=[adj_min, adj_max],
            bonuses_applied=bonuses_applied,
        )

    return estimate


def _detect_location(cv: CV) -> str:
    """Vrací 'praha' nebo 'regiony' podle CV.location."""
    # Pokud LLM nevyplnil location, default praha (60 % IT pozic v ČR je v Praze)
    if cv.location is None:
        return "praha"

    loc_lower = cv.location.lower()
    # Praha + spelling varianty
    if any(kw in loc_lower for kw in ["praha", "prague", "pražsk"]):
        return "praha"

    # Vše ostatní = regiony (Brno, Ostrava, Plzeň, ...)
    return "regiony"


def _seniority_from_years(years: float) -> str:
    """
    Mapuje roky relevantní praxe na seniority kategorii.
    Univerzální napříč obory: pokladní/lékař/programátor s 5 lety = senior bez ohledu na score.
    Hranice:
      - 0–2 roky → junior
      - 2–6 let → medior
      - 6+ let  → senior
    """
    if years < 2:
        return "junior"
    if years < 6:
        return "medior"
    return "senior"


def _seniority_bounds(seniority: str) -> tuple[int, int]:
    """
    Vrací (low, high) SCORE boundaries pro danou seniority kategorii.
    Používá se pro POZICI v platovém range — score interpoluje, kam v range padneme.
    Ne pro detekci seniority samotnou (to dělá _seniority_from_years podle praxe).
    """
    # Liberálnější horní hranice pro juniora — non-IT junior může mít score ~50 (Excel, AJ, soft)
    # Senior začíná až od 65, aby nezkresloval non-IT (kde málokdo dosáhne 80+ kvůli skills mapě)
    return {
        "junior": (0, 50),
        "medior": (50, 75),
        "senior": (75, 100),
    }[seniority]

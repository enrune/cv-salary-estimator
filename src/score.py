"""
Scoring: vyextrahované CV → Score (0–100) s rozpisem.

Žádný LLM tady — čistá deterministická heuristika. Důvod:
1. Vysvětlitelnost — můžeme přesně říct "máš 67 protože X+Y+Z"
2. Reprodukovatelnost — stejné CV vždy stejné skóre, snadné testování
3. Cena — žádný LLM token spotřebovaný

Vzorec:
  total = 0.40 * experience + 0.30 * skills + 0.15 * education + 0.15 * soft
"""


from __future__ import annotations
# CV a Score modely — sdílené Pydantic typy
from src.models import CV, Score, ScoreBreakdown
from src.debug import DebugTrace


# Váhy musí součet 1.0; centralizované konstanty pro snadnou úpravu
_W_EXPERIENCE = 0.40   # nejvyšší váha — roky praxe je tradičně silný proxy seniority
_W_SKILLS = 0.30       # technické dovednosti — druhý nejdůležitější faktor v IT
_W_EDUCATION = 0.15    # vzdělání má omezený efekt po pár letech praxe
_W_SOFT = 0.15         # leadership/komunikace — důležité pro senior, irelevantní pro junior

# Sanity check, aby váhy seděly; poznáme bug v testech, kdyby se měnily a zapomněli sečíst
assert abs(_W_EXPERIENCE + _W_SKILLS + _W_EDUCATION + _W_SOFT - 1.0) < 1e-9, "Váhy se musí sečíst do 1.0"


# In-demand skills mají vyšší váhu — odráží reálnou poptávku na trhu (květen 2026)
# Hodnoty: kolik bodů (max 100) přidá každá detekovaná skill.
# Pokrýváme IT i non-IT skills, aby pipeline fungovala pro libovolnou pozici.
_HIGH_VALUE_SKILLS: dict[str, int] = {
    # === IT ===
    # Cloud & infra
    "aws": 8, "azure": 7, "gcp": 7, "kubernetes": 8, "docker": 5, "terraform": 6,
    # Data & ML
    "python": 6, "sql": 5, "pandas": 4, "spark": 7, "airflow": 6, "dbt": 5,
    "pytorch": 8, "tensorflow": 7, "langchain": 6, "llm": 7, "rag": 7,
    # Backend
    "go": 6, "rust": 7, "java": 5, "kotlin": 5, "node.js": 5, "fastapi": 5,
    # Frontend
    "react": 5, "typescript": 6, "next.js": 5, "vue": 4,
    # DevOps
    "ci/cd": 5, "github actions": 4, "linux": 4, "bash": 3,
    # Databáze
    "postgres": 4, "mongodb": 4, "redis": 4, "elasticsearch": 5,

    # === Office / administrativa / finance ===
    "excel": 4, "ms office": 3, "powerpoint": 2, "word": 2,
    "sap": 6, "oracle": 5, "pohoda": 4, "money s3": 3,  # ERP systémy v ČR
    "účetnictví": 5, "ucetnictvi": 5, "daňová evidence": 4, "danova evidence": 4,
    "fakturace": 3, "mzdy": 4, "personalistika": 4,
    "power bi": 5, "tableau": 5, "looker": 4,

    # === Marketing / sales / kreativa ===
    "google ads": 6, "facebook ads": 5, "meta ads": 5, "linkedin ads": 4,
    "google analytics": 5, "ga4": 5, "seo": 5, "sem": 4,
    "hubspot": 5, "salesforce": 6, "mailchimp": 3,
    "photoshop": 4, "illustrator": 4, "indesign": 3, "figma": 5, "canva": 2,
    "copywriting": 4, "content marketing": 4, "social media": 3,

    # === Jazyky (úroveň B2+ se počítá) ===
    "angličtina": 4, "anglictina": 4, "english": 4, "b2 english": 5, "c1 english": 6, "c2 english": 7,
    "němčina": 5, "nemcina": 5, "german": 5,  # v ČR žádaná
    "francouzština": 3, "spanish": 3, "italština": 3, "russian": 3,

    # === Doprava / řemesla / služby (certifikáty a praktické dovednosti) ===
    "řidičský průkaz b": 2, "ridicsky prukaz b": 2,
    "řidičský průkaz c": 5, "ridicsky prukaz c": 5,
    "řidičský průkaz d": 5, "ridicsky prukaz d": 5,
    "průkaz e": 6, "prukaz e": 6,  # B+E, C+E
    "vzv": 4, "vysokozdvižný vozík": 4,
    "svářečský průkaz": 5, "svarecsky prukaz": 5,
    "vyhláška 50": 4, "vyhlaska 50": 4,  # elektrikáři
    "adr": 4,  # přeprava nebezpečných věcí
    "tachograf": 3,

    # === Gastronomie / retail ===
    "hccp": 3, "haccp": 3,  # hygienické předpisy
    "pokladna": 2, "ekasa": 2, "barista": 3, "sommelier": 4,
    "obsluha zákazníků": 2, "obsluha zakazniku": 2, "customer service": 3,

    # === Vzdělávání / zdravotnictví ===
    "pedagogická způsobilost": 5, "pedagogicka zpusobilost": 5,
    "registrace nelp": 5,  # zdrav. sestry
    "atestace": 6,  # lékaři
    "iva": 3, "ivf": 3,
}

# Soft skills s váhou — leadership a mentoring nejvíce, protože indikují seniora
_SOFT_SKILL_WEIGHTS: dict[str, int] = {
    "leadership": 25, "mentoring": 20, "team lead": 25, "tech lead": 25,
    "architect": 20, "stakeholder": 15, "agile": 10, "scrum": 10,
    "communication": 10, "presenting": 10, "decision": 15, "ownership": 15,
}


def score_cv(cv: CV, *, trace: DebugTrace | None = None) -> Score:
    """Spočítá Score pro dané CV. Pure function — žádné side effects."""
    # Spočítáme každou složku zvlášť, pro debug window i obhajobu
    exp_score = _experience_score(cv.years_experience)
    skills_score = _skills_score(cv.skills)
    edu_score = _education_score(cv)
    soft_score = _soft_score(cv)

    # Vážený součet; round() zajistí int místo floatu (Pydantic Score.total je int)
    total = round(
        _W_EXPERIENCE * exp_score
        + _W_SKILLS * skills_score
        + _W_EDUCATION * edu_score
        + _W_SOFT * soft_score
    )
    # Clamp na [0, 100] — defenzivně, kdyby se některý sub-skóre přetekl
    total = max(0, min(100, total))

    breakdown = ScoreBreakdown(
        experience=exp_score,
        skills=skills_score,
        education=edu_score,
        soft=soft_score,
    )

    if trace is not None:
        trace.log(
            "score",
            total=total,
            breakdown=breakdown.model_dump(),
            weights={
                "experience": _W_EXPERIENCE,
                "skills": _W_SKILLS,
                "education": _W_EDUCATION,
                "soft": _W_SOFT,
            },
            formula=f"{_W_EXPERIENCE}*{exp_score} + {_W_SKILLS}*{skills_score} + "
                    f"{_W_EDUCATION}*{edu_score} + {_W_SOFT}*{soft_score} = {total}",
        )

    return Score(total=total, breakdown=breakdown)


def _experience_score(years: float) -> int:
    """
    Mapuje roky praxe na 0–100. Sigmoidní křivka:
    0 let → 0, 5 let → ~70, 10 let → ~95, 15+ let → ~100.
    Důvod sigmoid: lineární mapping by penalizoval seniory s 15 lety vs. 10 lety,
    ale rozdíl mezi 1 a 5 lety je obrovský (junior → medior).
    """
    # Hranice 0 — žádná praxe = 0 bodů
    if years <= 0:
        return 0
    # Lineární do 2 let (junior fáze) — strmá křivka, každý rok hodně přidá
    if years <= 2:
        return int(years * 25)  # 0→0, 1→25, 2→50
    # Pomalejší růst 2-5 let (medior fáze)
    if years <= 5:
        return int(50 + (years - 2) * 8)  # 2→50, 5→74
    # Ještě pomalejší 5-10 let (senior fáze, diminishing returns)
    if years <= 10:
        return int(74 + (years - 5) * 4)  # 5→74, 10→94
    # Cap na 100 pro 15+ let
    return min(100, int(94 + (years - 10) * 1))  # 10→94, 16→100


def _skills_score(skills: list[str]) -> int:
    """
    Spočítá skills score. Algoritmus:
    1. Sečíst hodnoty in-demand skills, které kandidát má (case-insensitive)
    2. Přičíst +1 za každou další skill (general breadth)
    3. Cap na 100
    """
    # Lowercase pro porovnání; set pro O(1) lookup a deduplikaci
    skill_set = {s.lower().strip() for s in skills}

    # Iterujeme přes high-value skills, sečteme body za detekované
    # `if` check místo .get() s default 0, protože chceme jen detekované, ne penalizovat chybějící
    high_value_total = sum(
        weight for skill_name, weight in _HIGH_VALUE_SKILLS.items()
        if skill_name in skill_set
    )

    # Ostatní skills — +1 za každou nad rámec high-value (do limitu 20)
    # Pokud někdo má 30 random skills, neznamená to že je 30× lepší
    other_count = len(skill_set - set(_HIGH_VALUE_SKILLS.keys()))
    breadth_bonus = min(20, other_count)

    # Součet, cap na 100
    return min(100, high_value_total + breadth_bonus)


def _education_score(cv: CV) -> int:
    """
    Vzdělání → 0–100. Bere nejvyšší úroveň ze seznamu education.
    Mapping: SS=40, Bc=70, Mgr=85, MBA=85, PhD=100, other=30.
    """
    # Pokud nemá žádné vzdělání v CV, default 30 (předpokládáme aspoň SŠ neuvedeno)
    if not cv.education:
        return 30

    # Mapping úrovní na body. Centralizované, snadno upravit.
    level_points = {
        "SS": 40,
        "Bc": 70,
        "Mgr": 85,
        "MBA": 85,
        "PhD": 100,
        "other": 30,
    }

    # Vezmeme MAX přes všechny education entries — kandidát se hodnotí podle nejvyššího
    # `.get(level, 30)` defenzivně pro případ, kdy LLM vrátí neznámou hodnotu
    return max(level_points.get(edu.level, 30) for edu in cv.education)


def _soft_score(cv: CV) -> int:
    """
    Soft skills score. Sčítá body za detekované leadership/communication signály
    z `cv.soft_skills` (vyplněno LLM v parse) i z `cv.experience[*].description`.
    """
    # Začneme z soft_skills přímo
    soft_set = {s.lower() for s in cv.soft_skills}

    # Doplníme z popisů rolí — některé soft signály jsou tam, ale LLM je nemusel
    # vždy přesunout do soft_skills. Defenzivní detekce.
    job_descriptions = " ".join(
        (job.description or "").lower() for job in cv.experience
    )

    total = 0
    for keyword, weight in _SOFT_SKILL_WEIGHTS.items():
        # in operator funguje pro substring v stringu i element v setu
        if keyword in soft_set or keyword in job_descriptions:
            total += weight

    return min(100, total)

"""
Fallback parser CV — bez LLM, čistě regex + keyword matching.

Použije se, když uživatel nemá OpenRouter API klíč. Kvalita je horší než s LLM
(LLM rozumí kontextu, regex jen patternům), ale pipeline funguje end-to-end.
"""


from __future__ import annotations
# re pro regex, datetime pro vypocet let praxe ze stringovych datumu
import re
from datetime import datetime

from src.models import CV, Education


# Email — standardni RFC 5322 zjednoduseny pattern
_EMAIL_RE = re.compile(r'[\w.+-]+@[\w-]+\.[\w.-]+')
# Telefon — ceske formaty (+420 / 9 cislic, mezery i bez nich)
_PHONE_RE = re.compile(r'(?:\+420\s?)?\d{3}\s?\d{3}\s?\d{3}')
# Praha — preferovana lokace; jinak prvni mesto ze seznamu
_PRAHA_RE = re.compile(r'\b(praha|prague|pražsk)', re.IGNORECASE)
_CITIES = ['Brno', 'Ostrava', 'Plzeň', 'Liberec', 'Olomouc', 'Hradec Králové',
           'Pardubice', 'Zlín', 'Vítkov', 'Opava', 'Karviná', 'Most']

# Education keywords — bere se NEJVYSSI nalezeny titul (kontroluje shora dolu)
_EDU_PATTERNS: list[tuple[str, str]] = [
    ('PhD', r'\b(?:PhD|Ph\.D|doktor)\b'),
    ('MBA', r'\bMBA\b'),
    ('Mgr', r'\b(?:Mgr\.?|Ing\.?|magist[er]|MUDr\.?|JUDr\.?)\b'),
    ('Bc',  r'\b(?:Bc\.?|bakal[ar])\b'),
    ('SS',  r'\b(?:matur|gymnáz|střední)\b'),
]

# Datumy pro vypocet let praxe — "08/2022 - dosud" i "2020-2023" formaty
_DATE_RANGE_RE = re.compile(
    r'(\d{1,2}\s*[/.\-]\s*)?(\d{4})\s*(?:[-–—−]|do|az|až)\s*'
    r'(\d{1,2}\s*[/.\-]\s*)?(\d{4}|dosud|present|současnost|nyní)',
    re.IGNORECASE,
)

# Soft skills keywords — analogicky score.py _SOFT_SKILL_WEIGHTS
_SOFT_KEYWORDS = [
    'leadership', 'mentor', 'team lead', 'tech lead', 'architect', 'stakeholder',
    'agile', 'scrum', 'communication', 'presenting', 'decision', 'ownership',
    'samostatn', 'odpovědnost', 'vedení', 'architektur',
]

# Role keywords — synchronizovano s scrape.py _ROLE_KEYWORDS, poradi specificke -> obecne
_ROLE_KEYWORDS: list[tuple[str, list[str]]] = [
    ('ml_engineer',          ['machine learning', 'ml engineer', 'ai engineer', 'deep learning', 'rag', 'llm engineer']),
    ('data_engineer',        ['data engineer', 'datový inženýr', 'etl pipeline']),
    ('data_analyst',         ['data analyst', 'datový analytik', 'bi analyst']),
    ('devops',               ['devops', 'sre', 'site reliability', 'platform engineer']),
    ('python_developer',     ['python developer', 'pythonista', 'python programator']),
    ('javascript_developer', ['javascript developer', 'node.js developer']),
    ('frontend_developer',   ['frontend developer', 'front-end developer']),
    ('backend_developer',    ['backend developer', 'back-end developer']),
    ('fullstack_developer',  ['fullstack', 'full-stack', 'full stack']),
    ('product_manager',      ['product manager', 'produktový manažer']),
    ('it_specialist',        ['it support', 'it specialist', 'sysadmin', 'system administrator', 'helpdesk', 'it konzultant']),
    ('programmer',           ['java developer', 'c# developer', 'c++ developer', 'go developer', 'programátor']),
    ('inzenyr',              ['strojní inženýr', 'elektroinženýr', 'konstruktér', 'mechanical engineer']),
    ('architekt',            ['architekt', 'architect']),
    ('vedec',                ['výzkumný pracovník', 'researcher', 'scientist', 'akademický pracovník']),
    ('ucetni',               ['účetní', 'účetnictví', 'accountant']),
    ('financni_analytik',    ['finanční analytik', 'financial analyst']),
    ('hr_specialista',       ['hr specialist', 'personalista', 'recruitment']),
    ('marketing_specialista',['marketing specialist', 'marketingový specialista']),
    ('obchodni_zastupce',    ['obchodní zástupce', 'sales representative', 'account manager']),
    ('grafik',               ['grafik', 'graphic designer', 'ui designer', 'ux designer']),
    ('pravnik',              ['právník', 'advokát', 'koncipient', 'lawyer']),
    ('lekar',                ['lékař', 'physician', 'doctor']),
    ('ucitel',               ['učitel', 'teacher', 'lektor']),
    ('zdravotni_sestra',     ['zdravotní sestra', 'nurse']),
    ('pokladni',             ['pokladní', 'cashier']),
    ('prodavac',             ['prodavač', 'shop assistant']),
    ('ridic',                ['řidič', 'driver', 'kamion']),
    ('elektrikar',           ['elektrikář', 'electrician']),
    ('kuchar',               ['kuchař', 'chef', 'cook']),
    # Catch-all: jakykoliv "developer" -> programmer (low priority)
    ('programmer',           ['developer']),
]


def parse_cv_fallback(text: str) -> CV:
    """
    Hlavni fallback funkce: text CV -> Pydantic CV bez pouziti LLM.
    Vraci stejny typ jako parse_cv_llm, ale s mensi presnosti.
    """
    text_lower = text.lower()

    name = _extract_name(text)
    email = _extract_email(text)
    phone = _extract_phone(text)
    location = _extract_location(text)
    years = _extract_years_experience(text)
    role = _classify_role(text_lower)
    skills = _extract_skills(text_lower)
    soft = _extract_soft_skills(text_lower)
    education = _extract_education(text)
    languages = _extract_languages(text)

    # Summary = prvni 50-400 znaku po slove "PREDSTAVENI" nebo "PROFIL"
    sm = re.search(r'(?:představení|summary|profil)\s*:?\s*\n([^\n]{50,400})', text, re.IGNORECASE)
    summary = sm.group(1).strip() if sm else None

    return CV(
        name=name,
        email=email,
        phone=phone,
        location=location,
        summary=summary,
        education=education,
        experience=[],  # regex nedokaze spolehlive parsovat job entries
        skills=skills,
        languages=languages,
        soft_skills=soft,
        role_category=role,
        years_experience=years,
    )


def _extract_name(text: str) -> str | None:
    # Prvni neprazdny radek vypadajici jako 2-3 slova kapitalizovana
    for line in text.split('\n')[:5]:
        line = line.strip()
        if re.match(r'^[A-ZÀ-Ž][a-zÀ-Ž]+(?:\s+[A-ZÀ-Ž][a-zÀ-Ž]+){1,2}$', line):
            return line
    return None


def _extract_email(text: str) -> str | None:
    m = _EMAIL_RE.search(text)
    return m.group(0) if m else None


def _extract_phone(text: str) -> str | None:
    m = _PHONE_RE.search(text)
    return m.group(0).strip() if m else None


def _extract_location(text: str) -> str | None:
    if _PRAHA_RE.search(text):
        return 'Praha'
    for city in _CITIES:
        if city.lower() in text.lower():
            return city
    return None


def _extract_years_experience(text: str) -> float:
    """
    Sectae delky datumovych intervalu — POUZE z casti CV mezi sekcemi
    "PRAXE/EXPERIENCE/ZKUSENOSTI" a dalsi velkou sekci (VZDELANI/SKILLS).
    Diky tomu nezahrnuje datumy ze vzdelavaci sekce (skola 2014-2022 by jinak
    pridala 8 let praxe).
    """
    # Najdi zacatek sekce praxe
    work_start_re = re.compile(
        r'(?:^|\n)\s*(?:PRAXE|PRAXÍ|EXPERIENCE|ZKUŠENOSTI|PRACOVNÍ ZKUŠENOSTI)\b',
        re.IGNORECASE,
    )
    # Najdi zacatek nasledujici sekce (cokoli jineho velkym pismem na vlastnim radku)
    next_section_re = re.compile(
        r'\n\s*(?:VZDĚLÁNÍ|EDUCATION|SKILLS|DOVEDNOSTI|ZNALOSTI|ZÁJMY|JAZYKY|LANGUAGES)\b',
        re.IGNORECASE,
    )

    start_m = work_start_re.search(text)
    if start_m:
        work_text = text[start_m.end():]
        # Oriznem pri dalsi sekci
        end_m = next_section_re.search(work_text)
        if end_m:
            work_text = work_text[:end_m.start()]
    else:
        # Fallback: kdyz nenajdeme sekci PRAXE, vezmeme cely text (ale rizikove)
        work_text = text

    current_year = datetime.now().year
    total = 0.0
    for m in _DATE_RANGE_RE.finditer(work_text):
        try:
            start = int(m.group(2))
            end_tok = m.group(4).lower()
            end = current_year if end_tok in ('dosud', 'present', 'současnost', 'nyní') else int(end_tok)
            if 1990 <= start <= current_year + 1 and start <= end <= current_year + 1:
                total += end - start
        except (ValueError, TypeError):
            continue
    return round(total * 2) / 2  # zaokrouhleni na 0.5


def _classify_role(text_lower: str) -> str:
    for category, keywords in _ROLE_KEYWORDS:
        if any(kw in text_lower for kw in keywords):
            return category
    return 'general'


def _extract_skills(text_lower: str) -> list[str]:
    # Inline import abychom predesli kruhovemu importu
    from src.score import _HIGH_VALUE_SKILLS
    found: list[str] = []
    seen: set[str] = set()
    for skill_name in _HIGH_VALUE_SKILLS:
        if skill_name in text_lower and skill_name not in seen:
            seen.add(skill_name)
            found.append(skill_name)
    return found


def _extract_soft_skills(text_lower: str) -> list[str]:
    return [kw for kw in _SOFT_KEYWORDS if kw in text_lower]


def _extract_education(text: str) -> list[Education]:
    for level, pattern in _EDU_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return [Education(level=level)]
    return []


def _extract_languages(text: str) -> list[str]:
    langs: list[str] = []
    if re.search(r'\b(češtin|czech|cesky)\b', text, re.IGNORECASE):
        langs.append('Čeština')
    if re.search(r'\b(angličtin|english)\b', text, re.IGNORECASE):
        # Pokus o detekci urovne (B2, C1...)
        m = re.search(r'(angličtin\w*|english)[^\n]*?\b([ABC][12])\b', text, re.IGNORECASE)
        langs.append(f'Angličtina {m.group(2)}' if m else 'Angličtina')
    if re.search(r'\b(němčin|german|deutsch)\b', text, re.IGNORECASE):
        langs.append('Němčina')
    return langs

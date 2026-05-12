"""
Pydantic datové modely sdílené přes celou pipeline.

Proč separátní soubor: každý modul (parse, score, salary, explain) musí umět
importovat stejné třídy bez kruhových importů. Centralizace = jediný zdroj pravdy
o tvaru dat, který reviewer vidí na jednom místě.
"""


from __future__ import annotations
# Literal omezuje hodnoty pole na vyjmenovaný seznam — Pydantic to při validaci vynutí
from typing import Literal

# BaseModel = třída, ze které se dědí; Field umožňuje doplnit popis a omezení (min/max)
from pydantic import BaseModel, Field


class Education(BaseModel):
    # Volby: school + field volitelné, protože některá CV mají jen "Bc, ČVUT" bez detailu
    school: str | None = Field(None, description="Název školy")  # None default kvůli neúplným CV
    field: str | None = Field(None, description="Studijní obor")  # None default kvůli neúplným CV
    # year_finished int aby šlo počítat věk vzdělání; None pokud probíhá nebo neuvedeno
    year_finished: int | None = Field(None, description="Rok dokončení")
    # Literal vynutí kanonické hodnoty — LLM nemůže vrátit "magisterský" místo "Mgr"
    level: Literal["SS", "Bc", "Mgr", "PhD", "MBA", "other"] = Field(
        "other",  # default "other" pro případ, kdy LLM nedokáže klasifikovat
        description="Úroveň vzdělání: SS=středoškolské, Bc, Mgr, PhD, MBA, other",
    )


class Job(BaseModel):
    # company povinné, protože bez firmy nemá pozice smysl pro scoring
    company: str = Field(..., description="Název firmy")  # ... = required
    # role povinné — slouží ke klasifikaci role v salary lookup
    role: str = Field(..., description="Pozice / titul role")
    # Datum jako string místo datetime — LLM často vrací "2020" nebo "leden 2021"
    # parsovat to na datetime by bylo křehké, tak držíme volný string
    start: str | None = Field(None, description="Začátek (volný formát, např. '2021-01' nebo '2021')")
    end: str | None = Field(None, description="Konec; 'present' pokud stále trvá")
    # description používá explain.py pro extrakci leadership/management signálů
    description: str | None = Field(None, description="Popis role / odpovědnosti")
    # technologies samostatně — score.py je počítá zvlášť od obecných skills
    technologies: list[str] = Field(default_factory=list, description="Technologie použité v roli")


class CV(BaseModel):
    """Strukturované CV vyextrahované LLM z volného textu."""
    name: str | None = Field(None, description="Jméno kandidáta")
    # Email a telefon nemusí být nutné, ale ukazují kompletnost CV
    email: str | None = None
    phone: str | None = None
    # location — Praha vs regiony ovlivňuje salary range, proto extrahujeme
    location: str | None = Field(None, description="Lokace kandidáta (město, region)")
    # summary = profilový text na začátku CV; zachycuje sebepojetí kandidáta
    summary: str | None = Field(None, description="Profilové shrnutí")
    # default_factory=list zajistí prázdný seznam místo None — žádný null check v navazujícím kódu
    education: list[Education] = Field(default_factory=list)
    experience: list[Job] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list, description="Technické dovednosti")
    languages: list[str] = Field(default_factory=list, description="Jazykové znalosti")
    soft_skills: list[str] = Field(default_factory=list, description="Měkké dovednosti vyvozené LLM")
    # role_category je předem klasifikovaná pro salary lookup — whitelist hodnot
    # Pokud LLM nedokáže klasifikovat, vrátí "general" (fallback v salary.py)
    # Pokrýváme IT i non-IT pozice; názvy bez diakritiky pro JSON/Python kompatibilitu
    role_category: Literal[
        # IT — software
        "python_developer", "javascript_developer", "frontend_developer",
        "backend_developer", "fullstack_developer", "programmer",
        # IT — data / ML
        "data_engineer", "data_analyst", "ml_engineer",
        # IT — infra / produkt / support
        "devops", "product_manager", "it_specialist",
        # Inženýrství / věda (ne IT)
        "inzenyr", "architekt", "vedec",
        # Office / administrativa / finance
        "ucetni", "financni_analytik", "hr_specialista",
        "administrativni_pracovnik", "projektovy_manazer", "office_manager",
        "manazer", "konzultant", "analytik",
        # Sales / marketing / kreativa / služby
        "marketing_specialista", "obchodni_zastupce", "copywriter", "grafik",
        "realitni_makler", "pojistovak",
        # Právní
        "pravnik",
        # Retail / služby
        "pokladni", "prodavac", "skladnik", "recepcni",
        # Gastronomie
        "kuchar", "cisnik",
        # Doprava / mechanika
        "ridic", "kuryr", "mechanik",
        # Vzdělávání / zdravotnictví
        "ucitel", "zdravotni_sestra", "lekar", "fyzioterapeut",
        # Řemesla / stavebnictví
        "elektrikar", "instalater", "stavbar",
        # Beauty / osobní služby
        "kadernik",
        # Bezpečnost / sport / kreativa
        "ostraha", "trener", "fotograf",
        # Fallback (poslední možnost)
        "general",
    ] = Field("general", description="Detekovaná kategorie role pro salary lookup")
    # years_experience pro scoring — LLM musí spočítat, ne my, protože data jsou v CV ve volném formátu
    years_experience: float = Field(0.0, ge=0, description="Celkové roky relevantní praxe")


class ScoreBreakdown(BaseModel):
    """Rozpis scoringu — každá složka samostatně, aby šla v debug window vysvětlit."""
    # Všechny složky 0–100; vážení viz score.py. Hodnoty validuje Pydantic (ge/le).
    experience: int = Field(..., ge=0, le=100, description="Skóre zkušeností (váha 40 %)")
    skills: int = Field(..., ge=0, le=100, description="Skóre technických dovedností (váha 30 %)")
    education: int = Field(..., ge=0, le=100, description="Skóre vzdělání (váha 15 %)")
    soft: int = Field(..., ge=0, le=100, description="Skóre měkkých dovedností a leadershipu (váha 15 %)")


class Score(BaseModel):
    total: int = Field(..., ge=0, le=100, description="Celkové seniority skóre 0–100")
    breakdown: ScoreBreakdown


class SalaryEstimate(BaseModel):
    min_czk: int = Field(..., ge=0, description="Spodní hranice odhadu mzdy v CZK/měsíc")
    max_czk: int = Field(..., ge=0, description="Horní hranice odhadu mzdy v CZK/měsíc")
    role_detected: str = Field(..., description="Role použitá pro lookup")
    seniority: Literal["junior", "medior", "senior"] = Field(..., description="Detekovaná seniorita")
    location: Literal["praha", "regiony"] = Field(..., description="Detekovaná lokace")
    # bonuses_applied transparentně ukazuje, jaké modifikátory salary upravily — pro debug window
    bonuses_applied: list[str] = Field(default_factory=list, description="Aplikované bonusy/penalizace")


class Result(BaseModel):
    """Finální výstup pipeline — to, co reviewer uvidí jako JSON / v UI."""
    cv: CV
    score: Score
    salary: SalaryEstimate
    # Český narativní text, ~150-250 slov
    explanation: str = Field(..., description="České vysvětlení skóre a platu")
    strengths: list[str] = Field(..., description="Silné stránky kandidáta")
    gaps: list[str] = Field(..., description="Slabiny / chybějící dovednosti")
    # Min 3 doporučení = sanity check vyžaduje aspoň 3
    recommendations_for_30pct: list[str] = Field(
        ..., min_length=1, description="Konkrétní kroky pro +30 % platu"
    )
    # warnings ze sanity check — null-safe prázdný seznam pokud vše OK
    warnings: list[str] = Field(default_factory=list, description="Sanity check varování")

"""
Explain: CV + Score + Salary → české vysvětlení + doporučení pro +30 % platu.

Druhý a poslední LLM krok pipeline. Klíčové pro reviewera — ukazuje,
že umíme z čísel vytvořit srozumitelnou narrativu a dát konkrétní akční doporučení.

Strategie:
- Posíláme do promptu KOMPLETNÍ kontext (CV JSON + score breakdown + salary range)
- Žádáme JSON odpověď s pevně danou strukturou (4 sekce)
- Doporučení musí být MĚŘITELNÁ a NAVÁZANÁ na detekované gapy (priorita 5 zadání)
"""

import json
from pydantic import BaseModel, Field, ValidationError

from src.llm import complete
from src.models import CV, Score, SalaryEstimate
from src.debug import DebugTrace


# Lokální Pydantic model pro explanation output. Nedáváme ho do models.py,
# protože je to interní formát LLM odpovědi — jen tady jeden krok pipeline ho potřebuje.
class _ExplanationPayload(BaseModel):
    explanation: str = Field(..., min_length=50, description="Souvislý český text 150-250 slov")
    strengths: list[str] = Field(..., min_length=2, description="Aspoň 2 silné stránky")
    gaps: list[str] = Field(..., min_length=1, description="Aspoň 1 slabina/mezera")
    recommendations_for_30pct: list[str] = Field(
        ..., min_length=3, description="Aspoň 3 konkrétní doporučení pro +30 % platu"
    )


_SYSTEM_PROMPT = """Jsi zkušený český kariérní poradce a HR profesionál.
Tvým úkolem je analyzovat CV kandidáta, jeho seniority skóre a odhad mzdy,
a sepsat věcné, lidské a akční vysvětlení V ČEŠTINĚ.

Pravidla:
- Vrať POUZE validní JSON, žádný markdown, žádný komentář.
- Tón: kolega-poradce, ne robot. Buď konkrétní, ne obecný.
- "explanation": 150-250 slov, vysvětli proč takové skóre a plat (opírej se o čísla z breakdown).
- "strengths": 2-4 konkrétní silné stránky kandidáta z CV.
- "gaps": 1-3 konkrétní slabiny/mezery (chybějící skill, krátká praxe, chybějící leadership...).
- "recommendations_for_30pct": MINIMÁLNĚ 3 konkrétní, MĚŘITELNÉ doporučení pro +30 % platu.
  Každé doporučení musí být akční (např. "Naučit se Kubernetes a získat CKAD certifikaci do 6 měsíců"),
  ne obecné (NE: "Zlepšit se v cloudu"). Naváž doporučení na detekované gaps a salary bonusy."""


_OUTPUT_SCHEMA = """{
  "explanation": "150-250 slov českého textu",
  "strengths": ["silná stránka 1", "silná stránka 2", ...],
  "gaps": ["mezera 1", "mezera 2", ...],
  "recommendations_for_30pct": [
    "konkrétní akční doporučení 1 s časovým horizontem",
    "konkrétní akční doporučení 2",
    "konkrétní akční doporučení 3"
  ]
}"""


def explain(
    cv: CV,
    score: Score,
    salary: SalaryEstimate,
    *,
    trace: DebugTrace | None = None,
) -> _ExplanationPayload:
    """Hlavní funkce modulu — vrátí strukturované vysvětlení."""
    # Sestavíme kontext do promptu — kompletní informace
    # model_dump_json je rychlejší než model_dump + json.dumps a zachová Pydantic formátování
    # exclude_none=True odstraní null fields → kratší prompt → méně tokenů
    cv_json = cv.model_dump_json(exclude_none=True, indent=2)
    score_json = score.model_dump_json(indent=2)
    salary_json = salary.model_dump_json(indent=2)

    # Cílový plat pro +30 % — počítaný explicitně, aby LLM nemusel matematizovat
    target_min = int(salary.min_czk * 1.30)
    target_max = int(salary.max_czk * 1.30)

    user_prompt = f"""Analyzuj následující data kandidáta a vytvoř strukturované vysvětlení.

CV (parsované):
{cv_json}

Score breakdown (váhy: experience 40%, skills 30%, education 15%, soft 15%):
{score_json}

Salary estimate:
{salary_json}

Cíl pro +30 %: {target_min:,} - {target_max:,} CZK/měsíc.
(Aktuální range: {salary.min_czk:,} - {salary.max_czk:,} CZK)

Schéma JSON pro výstup:
{_OUTPUT_SCHEMA}

Vrať JSON podle schématu."""

    # Vyšší temperature než parse — chceme přirozeně znějící text, ne deterministickou extrakci
    raw = complete(
        system=_SYSTEM_PROMPT,
        user=user_prompt,
        json_mode=True,
        temperature=0.4,  # 0.4 = trochu kreativity, ale stále kontrola
        trace=trace,
        step_name="explain",
    )

    # Parse + validate (analogicky k parse.py, ale jednodušší — explanation je povinná hned napoprvé)
    try:
        cleaned = _strip_code_fences(raw)
        data = json.loads(cleaned)
        return _ExplanationPayload.model_validate(data)
    except (json.JSONDecodeError, ValidationError) as err:
        # Retry s feedbackem
        retry_prompt = f"""{user_prompt}

Předchozí pokus selhal: {err}
Oprav JSON přesně podle schématu. Vrať POUZE JSON."""
        raw = complete(
            system=_SYSTEM_PROMPT,
            user=retry_prompt,
            json_mode=True,
            temperature=0.2,  # nižší pro retry
            trace=trace,
            step_name="explain_retry",
        )
        cleaned = _strip_code_fences(raw)
        data = json.loads(cleaned)
        return _ExplanationPayload.model_validate(data)


def _strip_code_fences(text: str) -> str:
    """Odstraní ```json ... ``` wrapping pokud je tam."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
    return cleaned.strip()

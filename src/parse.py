"""
Parse: plain text CV → strukturovaná Pydantic CV instance přes LLM.

Klíčový krok pipeline. Tady se nestrukturovaný text převádí na typovaná data,
která pak score.py / salary.py / explain.py mohou bezpečně používat.

Strategie: instruct LLM s ostrým schema definicí + příklady, vyžádat JSON,
validovat přes Pydantic. Pokud validace selže, jeden retry s feedback do promptu.
"""


from __future__ import annotations
# json pro parsování LLM odpovědi; stdlib, vždy dostupné
import json

# ValidationError = výjimka, kterou Pydantic hodí když LLM vrátí špatný tvar
from pydantic import ValidationError

# Vlastní moduly — relativní import (.) by fungoval jen v package, používáme absolutní
from src.llm import complete
from src.models import CV
from src.debug import DebugTrace


# Maximální délka CV textu poslaného do LLM. Důvod limitu:
# (a) kontrola tokenového rozpočtu — 6000 znaků ≈ 1500 tokenů
# (b) ochrana před zneužitím — kdyby někdo nahrál 100stránkovou knihu
# Reálná CV mají 1-3 stránky → 2000-5000 znaků, takže 6000 pokryje 99 % případů
_MAX_CV_CHARS = 6000


# Systémový prompt definuje "kdo" je LLM a co má dělat. Drží se českého jazyka,
# protože CV jsou české a chceme aby model "myslel česky" (lepší přesnost na českých termínech).
_SYSTEM_PROMPT = """Jsi expert na analýzu životopisů a HR data v České republice.
Tvým úkolem je extrahovat strukturované informace z CV do JSON podle daného schématu.

Pravidla:
- Vrať POUZE validní JSON, žádný markdown, žádné code fences, žádný komentář.
- Pokud informace v CV chybí, použij null (pro string pole) nebo prázdný seznam (pro list pole).
- Pole `years_experience` spočítej jako součet relevantní praxe (zaokrouhli na 0.5).
- Pole `role_category` musí být jedna z: python_developer, javascript_developer, data_engineer,
  ml_engineer, data_analyst, devops, fullstack_developer, backend_developer,
  frontend_developer, product_manager, general. Vyber tu nejbližší. Pokud nic nesedí, použij "general".
- Pole `level` u education: SS=středoškolské, Bc=bakalář, Mgr=magistr, PhD=doktorát, MBA, other.
- Pole `soft_skills` ODVOĎ z popisů rolí (např. "vedl tým 5 lidí" → "leadership").
- Lokace: pokud kandidát uvádí Prahu nebo Pražský kraj, dej "Praha", jinak konkrétní město."""


# JSON schema string pro vložení do user promptu. Důvod: LLM lépe respektuje schema,
# které vidí přímo v promptu, než spoléhání na response_format=json_object samotný.
# Schema je odvozeno z Pydantic CV — kdyby se měnilo, sjednotit ručně (akceptovaný tradeoff).
_SCHEMA_HINT = """{
  "name": "string nebo null",
  "email": "string nebo null",
  "phone": "string nebo null",
  "location": "string nebo null",
  "summary": "string nebo null",
  "education": [
    {
      "school": "string nebo null",
      "field": "string nebo null",
      "year_finished": "integer nebo null",
      "level": "SS|Bc|Mgr|PhD|MBA|other"
    }
  ],
  "experience": [
    {
      "company": "string POVINNÉ",
      "role": "string POVINNÉ",
      "start": "string nebo null (např. '2021' nebo '2021-03')",
      "end": "string nebo null ('present' pokud trvá)",
      "description": "string nebo null",
      "technologies": ["seznam stringů"]
    }
  ],
  "skills": ["technické dovednosti, např. Python, SQL, Docker"],
  "languages": ["jazyky s úrovní, např. 'angličtina C1'"],
  "soft_skills": ["odvozené z popisů, např. 'leadership', 'mentoring'"],
  "role_category": "python_developer|javascript_developer|data_engineer|ml_engineer|data_analyst|devops|fullstack_developer|backend_developer|frontend_developer|product_manager|general",
  "years_experience": "číslo (float), např. 3.5"
}"""


def parse_cv(text: str, *, trace: DebugTrace | None = None) -> CV:
    """
    Hlavní funkce modulu — text → CV.

    Args:
        text: surový text z ingest.extract_text()
        trace: DebugTrace pro logování (volitelný)

    Returns:
        Pydantic CV instance s vyplněnými poli (může obsahovat None/prázdné seznamy).

    Raises:
        ValueError: pokud LLM dvakrát vrátí nevalidní strukturu (po retry).
    """
    # Truncate na max chars; ochrana před nákladnými requesty u dlouhých dokumentů
    # `[:limit]` je bezpečné i pro kratší stringy (Python neudělá out-of-bounds)
    truncated = text[:_MAX_CV_CHARS]

    # Pokud došlo k truncate, doplníme do trace info — pro obhajobu při review
    was_truncated = len(text) > _MAX_CV_CHARS

    # User prompt obsahuje schema + samotný text CV. Strukturováno pro čitelnost LLM.
    user_prompt = f"""Schéma JSON, podle kterého extrahuj data:
{_SCHEMA_HINT}

Text CV:
---
{truncated}
---

Vrať JSON podle schématu."""

    # První pokus — json_mode=True vynutí validní JSON na úrovni modelu
    raw = complete(
        system=_SYSTEM_PROMPT,
        user=user_prompt,
        json_mode=True,  # vynutí JSON output (response_format=json_object)
        temperature=0.1,  # velmi nízká — extrakce má být deterministická, ne kreativní
        trace=trace,
        step_name="parse",
    )

    # Pokus o parse + validaci. Wrapped v try, abychom mohli udělat retry.
    try:
        cv = _parse_and_validate(raw)
    except (json.JSONDecodeError, ValidationError) as first_err:
        # Retry s explicitním feedbackem chyby. LLM tak ví, co konkrétně bylo špatně.
        # Toto je standardní pattern pro "self-healing" structured outputs.
        retry_prompt = f"""{user_prompt}

Předchozí pokus selhal s touto chybou:
{first_err}

Oprav JSON tak, aby přesně odpovídal schématu výše. Vrať POUZE JSON, nic jiného."""

        raw = complete(
            system=_SYSTEM_PROMPT,
            user=retry_prompt,
            json_mode=True,
            temperature=0.0,  # ještě nižší temperature pro retry — chceme deterministickou opravu
            trace=trace,
            step_name="parse_retry",
        )

        # Druhý pokus — pokud selže i ten, raisuje výše, pipeline padne s jasnou chybou
        try:
            cv = _parse_and_validate(raw)
        except (json.JSONDecodeError, ValidationError) as second_err:
            # Raise jako ValueError s kontextem — uživatel/Streamlit to ukáže ve `st.error`
            raise ValueError(
                f"LLM nedokázal vrátit validní strukturu CV ani po retry: {second_err}"
            ) from second_err

    # Doplníme info o parse kroku do trace (mimo to, co loguje llm.complete)
    if trace is not None:
        trace.log(
            "parse_summary",
            input_chars=len(text),
            truncated=was_truncated,
            truncated_chars=len(truncated),
            parsed_skills_count=len(cv.skills),
            parsed_jobs_count=len(cv.experience),
            detected_role=cv.role_category,
            years_experience=cv.years_experience,
        )

    return cv


def _parse_and_validate(raw: str) -> CV:
    """
    Pomocná funkce: raw string → CV (s parse + validate).
    Oddělené, aby šlo zavolat dvakrát (první pokus + retry) bez duplikace kódu.
    """
    # Někdy LLM přidá markdown wrapping i s json_mode (model bug). Strip pro jistotu.
    cleaned = raw.strip()
    # Code fences (```json ... ```) — odstranit, pokud se objeví
    if cleaned.startswith("```"):
        # Heuristicky najdeme první newline a poslední ``` a vyřízneme střed
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()

    # json.loads → dict; raisuje JSONDecodeError pro nevalidní JSON (zachytí volající)
    data = json.loads(cleaned)

    # Pydantic model_validate → CV; raisuje ValidationError pro nesprávný tvar (zachytí volající)
    return CV.model_validate(data)

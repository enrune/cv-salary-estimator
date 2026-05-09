"""
Pipeline: orchestrace všech kroků.

Tady se spojuje ingest → parse → score → salary → explain → validate.
Každý krok je čistá funkce — pipeline je jen sekvence volání.

Reviewer bude pravděpodobně číst tento soubor jako první (priorita 5 zadání:
"logická sousledná pipeline"), takže tady musí být nejvíc komentářů a nejjasnější
struktura.
"""

# Path pro typing a manipulaci s cestami
from pathlib import Path

# Loguru pro logování — jednodušší než stdlib logging, automaticky formátuje
from loguru import logger

# Všechny moduly pipeline — explicitní importy, žádné `from X import *`
from src.ingest import extract_text
from src.parse import parse_cv
from src.score import score_cv
from src.salary import estimate_salary
from src.explain import explain
from src.validate import sanity_check
from src.debug import DebugTrace
from src.models import Result


def run(path: str | Path) -> tuple[Result, DebugTrace]:
    """
    Spustí celou pipeline na souboru CV.

    Args:
        path: cesta k PDF/DOCX souboru

    Returns:
        Tuple (Result, DebugTrace).
        Result = výsledek pro UI/API, DebugTrace = volitelné debug info.
    """
    # DebugTrace na začátku — zachytává timestamp pro celkovou latenci
    trace = DebugTrace()

    # Loguru log pro server-side debugging — nezávisle na Streamlit/CLI
    logger.info(f"Pipeline start: {path}")

    # ---------- Krok 1: Ingest ----------
    # PDF/DOCX → plain text. Žádný LLM, jen knihovny.
    text = extract_text(path)
    trace.log(
        "ingest",
        path=str(path),
        chars=len(text),
        first_500=text[:500],  # ukázka pro debug window — 500 chars stačí pro orientaci
    )
    logger.info(f"Ingest: extrahováno {len(text)} znaků")

    if not text.strip():
        # Prázdný text → další kroky by selhaly. Fail fast s jasnou chybou.
        raise ValueError("Z CV se nepodařilo extrahovat žádný text. Je soubor prázdný nebo skenovaný?")

    # ---------- Krok 2: Parse (LLM) ----------
    # Plain text → strukturované Pydantic CV.
    cv = parse_cv(text, trace=trace)
    logger.info(f"Parse: role={cv.role_category}, years={cv.years_experience}, skills={len(cv.skills)}")

    # ---------- Krok 3: Score ----------
    # CV → Score (heuristika, čistý Python, žádný LLM).
    score = score_cv(cv, trace=trace)
    logger.info(f"Score: total={score.total}, breakdown={score.breakdown.model_dump()}")

    # ---------- Krok 4: Salary ----------
    # CV + Score → Salary range. Lookup tabulky + bonusy.
    salary = estimate_salary(cv, score, trace=trace)
    logger.info(f"Salary: {salary.min_czk:,}-{salary.max_czk:,} CZK ({salary.seniority} {salary.role_detected})")

    # ---------- Krok 5: Explain (LLM) ----------
    # Vše dohromady → české vysvětlení + doporučení.
    explanation = explain(cv, score, salary, trace=trace)
    logger.info(f"Explain: {len(explanation.recommendations_for_30pct)} doporučení")

    # ---------- Sestavení Result ----------
    # Sanity check běží na poskládaném Result; warnings se přidají zpátky do Result.
    # Vytvoříme dočasný Result bez warnings, pak doplníme.
    result = Result(
        cv=cv,
        score=score,
        salary=salary,
        explanation=explanation.explanation,
        strengths=explanation.strengths,
        gaps=explanation.gaps,
        recommendations_for_30pct=explanation.recommendations_for_30pct,
        warnings=[],  # vyplníme za chvíli
    )

    # ---------- Krok 6: Sanity check ----------
    warnings = sanity_check(result)
    result.warnings = warnings
    trace.log("validate", warnings_count=len(warnings), warnings=warnings)

    if warnings:
        # Logujeme jako warning, ne error — pipeline doběhla, jen máme upozornění
        logger.warning(f"Sanity check varování: {warnings}")

    logger.info(f"Pipeline hotová za {trace.total_duration_ms():.0f}ms, "
                f"tokens={trace.total_tokens()}, cost=${trace.total_cost():.4f}")

    return result, trace

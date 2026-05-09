"""
Sanity checks výsledku pipeline.

Účel: zachytit nesmysly před tím, než se zobrazí uživateli.
Nejde o exhaustivní testy — jen o základní konzistenci, kterou
umíme ověřit jednoduchými pravidly (nezávislé na konkrétním CV).

Výstup: list[str] varování. Prázdný = vše OK. Plný = něco se zdá podivné,
ale nezastavujeme pipeline (jen upozorníme).
"""


from __future__ import annotations
from src.models import Result


# Realistické rozsahy CZK pro IT pozice — ručně nastavené hranice
_MIN_REALISTIC_SALARY = 15_000  # spodek (junior part-time, brigádníci)
_MAX_REALISTIC_SALARY = 500_000  # strop (CTO, principal engineer)


def sanity_check(result: Result) -> list[str]:
    """
    Vrátí seznam varování. Implementováno jako čistá funkce — žádné side effects.
    """
    # Akumulátor pro varování — list místo print, aby šlo zobrazit v UI
    warnings: list[str] = []

    # 1) Score range — Pydantic už validoval ge/le, ale dvojitá kontrola pro jistotu
    if not (0 <= result.score.total <= 100):
        warnings.append(f"Score mimo rozsah 0-100: {result.score.total}")

    # 2) Salary min < max — zkontrolujeme, že jsme nezpřeházeli pole
    if result.salary.min_czk >= result.salary.max_czk:
        warnings.append(
            f"Salary min ({result.salary.min_czk}) >= max ({result.salary.max_czk})"
        )

    # 3) Realistický salary range
    if result.salary.min_czk < _MIN_REALISTIC_SALARY:
        warnings.append(
            f"Salary min příliš nízký: {result.salary.min_czk:,} CZK"
        )
    if result.salary.max_czk > _MAX_REALISTIC_SALARY:
        warnings.append(
            f"Salary max příliš vysoký: {result.salary.max_czk:,} CZK"
        )

    # 4) Konzistence seniority vs. score
    # Junior s vysokým score = nesoulad (možná špatná detekce role v score)
    sen = result.salary.seniority
    score = result.score.total
    if sen == "junior" and score > 60:
        warnings.append(f"Seniorita 'junior' nesedí se score {score} (>60)")
    if sen == "senior" and score < 50:
        warnings.append(f"Seniorita 'senior' nesedí se score {score} (<50)")

    # 5) Doporučení — minimálně 3 položky (vyžadováno explicitně v zadání)
    if len(result.recommendations_for_30pct) < 3:
        warnings.append(
            f"Méně než 3 doporučení (je {len(result.recommendations_for_30pct)})"
        )

    # 6) Vysvětlení — minimální délka, aby to nebyla jen jedna věta
    if len(result.explanation) < 100:
        warnings.append(f"Vysvětlení příliš krátké: {len(result.explanation)} znaků")

    # 7) Detekované role — pokud je "general", reviewer ví, že detekce nezachytila konkrétní roli
    if result.salary.role_detected == "general":
        warnings.append("Role detekována jako 'general' — možná nezachycená v whitelistu")

    # 8) Kandidát bez praxe ale ne junior
    if result.cv.years_experience == 0 and result.salary.seniority != "junior":
        warnings.append(
            f"0 let praxe ale seniorita '{result.salary.seniority}'"
        )

    return warnings

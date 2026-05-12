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

    # 4) Konzistence seniority vs. praxe vs. score
    # Seniorita se primárně určuje z let praxe (univerzální napříč obory), ne ze score.
    # Score se používá jen pro pozici v range; non-IT může mít legitimně nižší score (15-40),
    # aniž by to znamenalo junioritu. Proto kontrolujeme primárně praxi:
    sen = result.salary.seniority
    years = result.cv.years_experience
    score = result.score.total
    # Sanity: seniority kategorie musí sedět s roky praxe (s odpustí výjimkou self-trained)
    if sen == "senior" and years < 4:
        warnings.append(f"Seniorita 'senior' ale jen {years} let praxe (< 4)")
    if sen == "junior" and years > 4:
        warnings.append(f"Seniorita 'junior' ale {years} let praxe (> 4)")
    # Sekundárně: extrémní rozdíl score vs. seniority u IT-rolí (kde je škála skills široká)
    it_roles = {"python_developer", "javascript_developer", "frontend_developer",
                "backend_developer", "fullstack_developer", "data_engineer",
                "data_analyst", "ml_engineer", "devops"}
    if result.salary.role_detected in it_roles:
        if sen == "junior" and score > 65:
            warnings.append(f"IT junior se score {score} (>65) — možná podceněná seniorita")
        if sen == "senior" and score < 45:
            warnings.append(f"IT senior se score {score} (<45) — chybí dovednosti pro level")

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

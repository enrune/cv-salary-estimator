"""
Fallback explainer — generuje cesky text bez pouziti LLM.

Template-based pristup: vezme cisla z Score/Salary, doplni do predpripravenych
sablonek a vrati stejnou strukturu jako LLM explain(). Vystup nezni tak prirozene
jako LLM, ale dava srozumitelne vysvetleni a smysluplna doporuceni.
"""


from __future__ import annotations
from pydantic import BaseModel, Field

from src.models import CV, Score, SalaryEstimate


class _ExplanationPayload(BaseModel):
    """Stejna struktura jako v explain.py — aby pipeline.py nemusel rozlisovat."""
    explanation: str = Field(..., min_length=50)
    strengths: list[str] = Field(..., min_length=2)
    gaps: list[str] = Field(..., min_length=1)
    recommendations_for_30pct: list[str] = Field(..., min_length=3)


# Generic doporuceni podle role_category - kdyz LLM nemame, pouzijeme tato
# Recovery: pokud rola nedopovida, sahnu pro "general" sablonu
_ROLE_RECOMMENDATIONS: dict[str, list[str]] = {
    'python_developer': [
        'Získat certifikaci AWS / GCP (Solutions Architect Associate) do 6 měsíců — cloud expertise pridava 5-10 % k platu.',
        'Naučit se Kubernetes (CKAD certifikace) do 6 měsíců — high-demand skill v 2026.',
        'Aktivně se zapojit do open-source projektu nebo si vést tech blog — buduje portfolio a viditelnost.',
    ],
    'javascript_developer': [
        'Hluběji se naučit TypeScript a Next.js (App Router, Server Components) do 3 měsíců.',
        'Získat zkušenosti s testováním (Jest, Playwright) — testing skills zvedají senioritu.',
        'Vést alespoň jeden vlastní projekt jako tech lead — leadership signál pro seniorní pozice.',
    ],
    'frontend_developer': [
        'Doplnit knowledge o React 18 features (Server Components, Suspense) nebo Vue 3.',
        'Naučit se základy backendu (Node.js / API design) — fullstack devops přidává 10-15 %.',
        'Optimalizovat performance/accessibility — Core Web Vitals + WCAG 2.2.',
    ],
    'backend_developer': [
        'Naučit se Kubernetes a Terraform — DevOps overlap zvedá plat o 10-15 %.',
        'Hluběji se ponořit do system design (distributed systems, message queues).',
        'Mentoring juniorů a code review — leadership signal pro seniorní level.',
    ],
    'fullstack_developer': [
        'Specializovat se na jednu silnou stranu (frontend nebo backend expertise) - jistá specializace pridava cenu.',
        'Naučit se infrastructure-as-code (Terraform, Pulumi).',
        'Vést end-to-end produkt feature - tech leadership signal.',
    ],
    'data_engineer': [
        'Naučit se Spark + Airflow do hloubky - klicove pro senior data engineering pozice.',
        'Cloud data platform certifikace (AWS Data Analytics Specialty nebo dbt Certification).',
        'Postavit data pipeline end-to-end pro vlastni portfolio projekt.',
    ],
    'data_analyst': [
        'Posunout se k advanced SQL + Python (pandas, numpy) do 6 mesicu.',
        'Naucit se Tableau nebo Power BI na expert level + ziskat certifikaci.',
        'Vest aspon jeden data product od pozadavku po deploy - product-thinking signal.',
    ],
    'ml_engineer': [
        'Postavit a deploynout production-grade ML pipeline (MLOps) do 6 mesicu.',
        'Spcialirovat na LLM Engineering (RAG, fine-tuning, agentic patterns) - hot v 2026.',
        'Publikovat alespon jeden technicky clanek / open-source repo s ML projektem.',
    ],
    'devops': [
        'Získat certifikaci CKA + AWS Solutions Architect Professional do 12 mesicu.',
        'Naucit se observability stack (Prometheus, Grafana, OpenTelemetry).',
        'Vest infrastructure migration nebo nasazeni IaC v ramci aktualni firmy.',
    ],
    'product_manager': [
        'Ziskat formalni PM certifikaci (Pragmatic Marketing, AIPMM) do 6 mesicu.',
        'Naucit se SQL a basic data analytics pro data-driven decisions.',
        'Vest cross-functional initiativu s merestratelnymi business outcomes.',
    ],
    'it_specialist': [
        'Doplnit programatorske dovednosti (Python pro automatizaci) - shift k DevOps/Sysadmin Senior.',
        'Ziskat certifikaci RHCSA nebo AWS Cloud Practitioner do 6 mesicu.',
        'Naucit se Infrastructure-as-Code (Ansible, Terraform) - automatizace zvedá plat.',
    ],
    'inzenyr': [
        'Specializovat se na konkretni domenu (automotive, energetika, IoT) - domain expertise pridava cenu.',
        'Doplnit software/programovaci dovednosti (Python, Matlab) - kombinace HW+SW se ceni.',
        'Ziskat formalni certifikaci v oboru (PMP, Six Sigma, atd.).',
    ],
    'pravnik': [
        'Slozit advokatske zkousky a zacit budovat vlastni klientelu.',
        'Specializovat se na rust oblast (compliance, M&A, IP, GDPR) - specialiste vydělavaji 30-50 % víc.',
        'Naucit se anglicky pravni jazyk na C1 - mezinarodni klienti.',
    ],
    'pokladni': [
        'Absolvovat kurz "Manager prodejny" nebo "Vedouci smeny" do 9 mesicu - posun na supervisor pozici.',
        'Rozsirit znalosti retailu o reporting/inventory management.',
        'Doplnit komunikacni a managerske skills.',
    ],
    'ridic': [
        'Ziskat profesni prukaz vyssi kategorie (B+E, C+E, D) - vyssi kategorie = vyssi plat.',
        'Naucit se ADR (preprava nebezpecnych veci) - specializace zvyrazni o 10-20 %.',
        'Mezinarodni preprava nebo dlouhodobe smeny - vyssi rate.',
    ],
}

# Generic gaps podle role (jednoduche)
_ROLE_GAPS: dict[str, list[str]] = {
    'python_developer':  ['Chybi explicitní cloud/Kubernetes zkusenosti', 'Mozna chybi leadership/mentoring signaly'],
    'ml_engineer':       ['MLOps a deployment best practices', 'Production-scale experience'],
    'devops':            ['Pokrocila kubernetes / multi-cloud architektura', 'Cost optimization a FinOps'],
    'data_engineer':     ['Spark/Airflow production experience', 'Modern data stack (dbt, Snowflake)'],
    'pravnik':           ['Chybi explicitni specializace', 'Mezinarodni jazyk B2+'],
    'pokladni':          ['Chybi manazerska zkusenost', 'Reporting / data analytics skills'],
    'it_specialist':     ['Chybi programatorske / scripting skills', 'Cloud expertise (AWS, Azure)'],
}


def explain_fallback(cv: CV, score: Score, salary: SalaryEstimate) -> _ExplanationPayload:
    """
    Generuje strukturovane vysvetleni bez LLM.
    Vystup je deterministicky - stejne CV vzdy stejne vysvetleni.
    """
    target_min = int(salary.min_czk * 1.30)
    target_max = int(salary.max_czk * 1.30)

    # === EXPLANATION (cca 150 slov, template-based) ===
    breakdown = score.breakdown
    # Najdi dominantnu (nejvyssi) a nejslabsi (nejnizsi) slozku
    components = {
        'zkušenosti': breakdown.experience,
        'technické dovednosti': breakdown.skills,
        'vzdělání': breakdown.education,
        'soft skills (leadership, ownership)': breakdown.soft,
    }
    best_component = max(components, key=components.get)
    worst_component = min(components, key=components.get)

    explanation = (
        f"Kandidát {cv.name or '(jméno neuvedeno)'} dosáhl celkového seniority skóre "
        f"{score.total}/100, což odpovídá kategorii {salary.seniority.upper()} pro pozici "
        f"{salary.role_detected.replace('_', ' ')} v lokaci {salary.location}. "
        f"Tento odhad se opírá o {cv.years_experience} let praxe. "
        f"Nejsilnější stránkou je {best_component} ({components[best_component]}/100), "
        f"naopak nejvíce prostoru pro rozvoj nabízí {worst_component} ({components[worst_component]}/100). "
        f"Aktuální odhad mzdy v ČR je {salary.min_czk:,} – {salary.max_czk:,} CZK / měsíc. "
        f"Pro dosažení cíle +30 % (tj. {target_min:,} – {target_max:,} CZK) "
        f"je třeba zaměřit se na vyrovnání slabé složky skóre a získat měřitelné výsledky "
        f"v aktuální roli. Konkrétní doporučení viz níže.\n\n"
        f"⚠️ Vysvětlení bylo generováno bez LLM (template-based fallback). "
        f"Pro kvalitnější a personalizované vysvětlení vložte OpenRouter API klíč."
    ).replace(',', ' ')  # ceske formátování s mezerou

    # === STRENGTHS ===
    strengths: list[str] = []
    if breakdown.experience >= 60:
        strengths.append(f"Solidní praxe ({cv.years_experience} let) v relevantní oblasti")
    if breakdown.skills >= 50:
        strengths.append(f"Široký skillset ({len(cv.skills)} detekovaných technických dovedností)")
    if breakdown.education >= 70:
        strengths.append("Adekvátní úroveň vzdělání pro danou pozici")
    if breakdown.soft >= 30:
        strengths.append("Detekované signály leadershipu nebo ownership v popisu rolí")
    if len(cv.skills) > 0 and any(s in {'aws', 'kubernetes', 'rust', 'llm', 'rag'} for s in cv.skills):
        strengths.append("Premium / in-demand dovednosti (cloud, containers, AI)")
    # Zarucime minimum 2
    if len(strengths) < 2:
        strengths += [
            f"Detekovaná role: {salary.role_detected.replace('_', ' ')}",
            "Pipeline zpracovala CV bez chyb",
        ]

    # === GAPS ===
    gaps = _ROLE_GAPS.get(salary.role_detected, _ROLE_GAPS.get('python_developer', []))
    if breakdown.soft < 20:
        gaps = ["Chybí signály leadershipu / mentoring v popisu zkušeností"] + gaps
    if not gaps:
        gaps = ["Chybí detailní popis odpovědností a dosažených výsledků v praxi"]

    # === RECOMMENDATIONS ===
    recs = _ROLE_RECOMMENDATIONS.get(salary.role_detected)
    if recs is None or len(recs) < 3:
        # Generic fallback
        recs = [
            f"Vyrovnat slabou složku skóre ({worst_component}) — investovat 3-6 měsíců do cíleného rozvoje.",
            "Získat formální certifikaci v oboru — relevantní certifikace přidává typicky 5-15 % k platu.",
            "Vést viditelný projekt s měřitelným výsledkem (např. úspora času/nákladů firmě) — buduje seniority case.",
        ]

    return _ExplanationPayload(
        explanation=explanation,
        strengths=strengths[:4],  # max 4
        gaps=gaps[:3],            # max 3
        recommendations_for_30pct=recs[:5],  # max 5
    )

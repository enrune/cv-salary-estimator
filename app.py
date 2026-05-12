"""
Streamlit UI — upload CV → výsledek + debug window.

Spustit: `streamlit run app.py`

První spuštění: pokud chybí OPENROUTER_API_KEY (v .env nebo env), UI ukáže
formulář pro vložení klíče. Po uložení se klíč zapíše do .env a UI se přenačte.
Tím je celý setup po `pip install` zvládnutelný jediným spuštěním Streamlitu —
žádné manuální editování .env souborů.
"""


from __future__ import annotations
# os pro environ a path manipulaci s klíčem
import os
# tempfile pro dočasné uložení uploadu (pdfplumber/python-docx potřebují file path)
import tempfile
# Path pro manipulaci s cestami
from pathlib import Path

# Streamlit — alias `st` je standardní konvence
import streamlit as st
# dotenv načte .env při startu i po jeho zápisu — overrride=True pro re-load
from dotenv import load_dotenv


# ---------- Page config (musí být první Streamlit volání) ----------
st.set_page_config(
    page_title="Job Fit & Salary Estimator",
    page_icon="💼",  # emoji v záložce browseru
    layout="wide",  # wide = víc místa pro debug expandery
)


# ---------- API key handling (před import pipeline modulů, které key potřebují) ----------
# Načteme .env do os.environ (no-op pokud .env neexistuje)
load_dotenv(override=True)

# Cesta k .env relativně ke scriptu — funguje i při spuštění z jiné CWD
_ENV_PATH = Path(__file__).parent / ".env"


def _save_env(api_key: str, model: str = "google/gemini-2.5-flash") -> None:
    """
    Zapíše .env s API klíčem a default modelem.
    Vyhradně override-write — bez parse stávajícího .env, protože v této fázi
    je jediný relevantní obsah ten, co tu nastavíme.
    """
    # f-string + multi-line; \n na konci zachová Unix line endings (Windows je tolerantní)
    content = (
        "# Vygenerováno automaticky Streamlit UI při prvním spuštění\n"
        f"OPENROUTER_API_KEY={api_key}\n"
        f"OPENROUTER_MODEL={model}\n"
    )
    # encoding='utf-8' explicitně, aby fungovalo i na Windows s ne-UTF default
    _ENV_PATH.write_text(content, encoding="utf-8")
    # Aktualizujeme i runtime env, aby nemusel uživatel restartovat Streamlit
    os.environ["OPENROUTER_API_KEY"] = api_key
    os.environ["OPENROUTER_MODEL"] = model


# Pokud klíč chybí, ukážeme onboarding formulář a zastavíme zbytek skriptu
if not os.getenv("OPENROUTER_API_KEY"):
    st.title("💼 Job Fit & Salary Estimator")
    st.markdown("### 🔑 První spuštění — zadej OpenRouter API klíč")
    st.info(
        "Klíč získáš zdarma na **[openrouter.ai](https://openrouter.ai)** "
        "(stačí registrace a ~$1 kreditu na desítky analyzovaných CV). "
        "Klíč se uloží do souboru `.env` ve složce projektu a víc se na něj nebudu ptát."
    )

    # st.form sdružuje inputy a submit — uživatel může Enterem odeslat
    with st.form("api_key_form"):
        # type="password" maskuje vstup hvězdičkami — bezpečnost při screen-sharingu
        key_input = st.text_input(
            "OPENROUTER_API_KEY",
            type="password",
            placeholder="sk-or-v1-...",
            help="Formát: 'sk-or-v1-' + 64 hex znaků",
        )
        # Selectbox pro model — default první (gemini-flash), doporučeno popisem
        model_input = st.selectbox(
            "Model (lze změnit později v .env)",
            options=[
                "google/gemini-2.5-flash",
                "anthropic/claude-haiku-4.5",
                "openai/gpt-4o-mini",
                "deepseek/deepseek-chat",
            ],
            index=0,
            help="gemini-2.5-flash je nejlevnější a rychlý, dobrá čeština",
        )
        # form_submit_button = jediný submit pro celý form
        submitted = st.form_submit_button("Uložit a pokračovat", type="primary")

    if submitted:
        # Validace tvaru — OpenRouter klíče začínají 'sk-or-' (sk-or-v1-)
        cleaned = key_input.strip()
        if not cleaned.startswith("sk-or-"):
            st.error("Klíč musí začínat 'sk-or-v1-'. Zkontroluj kopírování (mezery / nezvolené znaky).")
        elif len(cleaned) < 20:
            st.error("Klíč je příliš krátký — pravděpodobně nedokopírovaný.")
        else:
            _save_env(cleaned, model_input)
            st.success("✅ Klíč uložen. Načítám aplikaci…")
            # st.rerun() znovu spustí celý script od začátku → load_dotenv načte nový .env
            st.rerun()

    # Stop — bez klíče zbytek skriptu nemá smysl spouštět (LLM by spadl)
    st.stop()


# ---------- Až sem se dostaneme jen s validním klíčem ----------
# Importy pipeline modulů jsou až tady, aby se nepokoušely o LLM před vyplněním klíče
from src.pipeline import run  # noqa: E402
from src.models import Result  # noqa: E402
from src.debug import DebugTrace  # noqa: E402


# ---------- Cache wrapper ----------
@st.cache_data(show_spinner=False)
def _cached_run(file_bytes: bytes, suffix: str) -> tuple[Result, DebugTrace]:
    """
    Cached pipeline run — pokud uživatel uploadne stejný soubor, nepálíme tokeny.
    Cache key: hash file_bytes (Streamlit ho spočítá automaticky pro bytes).
    """
    # Pipeline potřebuje file path, ne bytes — uložíme do temp souboru
    # delete=False protože context manager pdfplumber potřebuje vlastní file handle
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name

    try:
        return run(tmp_path)
    finally:
        # Cleanup temp souboru i při výjimce
        Path(tmp_path).unlink(missing_ok=True)


# ---------- Sidebar ----------
st.sidebar.title("⚙️ Nastavení")

# Debug toggle — default OFF, aby běžný demo vypadal čistě
debug_mode = st.sidebar.checkbox(
    "🐞 Debug mode", value=False,
    help="Zobrazí všechny mezikroky pipeline, prompty, raw LLM odpovědi a cost",
)

st.sidebar.markdown("---")
st.sidebar.markdown(
    "**Pipeline kroky:**\n"
    "1. Ingest (PDF/DOCX → text)\n"
    "2. Parse (LLM → struktura)\n"
    "3. Score (heuristika)\n"
    "4. Salary (lookup + bonusy)\n"
    "5. Explain (LLM → česky)\n"
    "6. Validate (sanity check)"
)
st.sidebar.markdown("---")
# Tlačítko na reset API klíče — pro případ, že chce uživatel změnit
if st.sidebar.button("🔁 Změnit API klíč", help="Vymaže .env a vyžádá nový klíč"):
    if _ENV_PATH.exists():
        _ENV_PATH.unlink()  # smaže .env
    # Vyčistíme i runtime, aby další rerun viděl prázdno
    os.environ.pop("OPENROUTER_API_KEY", None)
    st.rerun()


# ---------- Main UI ----------
st.title("💼 Job Fit & Salary Estimator")
st.caption("Nahraj CV (PDF nebo DOCX) → seniority skóre, odhad mzdy, doporučení v češtině.")

# File uploader — accept_multiple_files=False (default) pro single CV
uploaded = st.file_uploader(
    "Nahraj CV",
    type=["pdf", "docx"],
    help="Podporované formáty: PDF, DOCX. Max ~10 MB.",
)

if uploaded is None:
    # Bez uploadu zobrazíme info banner a skončíme
    st.info("👆 Nahraj CV pro analýzu.")
    st.stop()  # st.stop = elegantní early-exit, ne return (Streamlit má vlastní run model)


# Zobrazíme jméno souboru a velikost — uživatel vidí, co se zpracovává
st.success(f"📄 Načten soubor: **{uploaded.name}** ({uploaded.size:,} B)")

# Spuštění pipeline se spinnerem (loading indicator)
with st.spinner("Analyzuji CV — extrakce, parsing, scoring, vysvětlení..."):
    try:
        # Extension z názvu souboru — uppercase normalizace ne, suffix je už lowercase v pdfplumber
        suffix = "." + uploaded.name.rsplit(".", 1)[1].lower()
        result, trace = _cached_run(uploaded.getvalue(), suffix)
    except Exception as e:
        # Jakákoli výjimka v pipeline → ukážeme jako error a zastavíme
        st.error(f"❌ Pipeline selhala: {e}")
        st.exception(e)  # rozbalitelný traceback pro debugging
        st.stop()


# ---------- Hlavní výsledek ----------
st.markdown("---")

# Tři sloupce nahoře: score, salary, seniority
col1, col2, col3 = st.columns(3)

with col1:
    # st.metric vykreslí velkou hodnotu s popiskem — přehledné
    st.metric("Seniority Score", f"{result.score.total} / 100")

with col2:
    # Salary range — hlavní hodnota median, sub-popisek range
    median = (result.salary.min_czk + result.salary.max_czk) // 2
    st.metric(
        "Odhad mzdy",
        f"{median:,} CZK".replace(",", " "),  # české formátování (mezera, ne čárka)
        f"{result.salary.min_czk:,} - {result.salary.max_czk:,}".replace(",", " "),
    )

with col3:
    # Detekovaná seniorita + role
    st.metric("Seniorita", result.salary.seniority.upper())
    st.caption(f"Role: {result.salary.role_detected} | {result.salary.location}")


# Sanity warnings (pokud nějaké jsou)
if result.warnings:
    st.warning("⚠️ Sanity check varování:\n" + "\n".join(f"- {w}" for w in result.warnings))


st.markdown("### 🧠 Vysvětlení")
st.write(result.explanation)


col_str, col_gap = st.columns(2)
with col_str:
    st.markdown("### ✅ Silné stránky")
    for s in result.strengths:
        st.markdown(f"- {s}")

with col_gap:
    st.markdown("### ⚠️ Slabiny / mezery")
    for g in result.gaps:
        st.markdown(f"- {g}")


# +30 % cíl — cílový plat počítaný z aktuálního
target_min = int(result.salary.min_czk * 1.30)
target_max = int(result.salary.max_czk * 1.30)
st.markdown(
    f"### 🎯 Doporučení pro +30 % "
    f"(cíl: {target_min:,} - {target_max:,} CZK)".replace(",", " ")
)
for r in result.recommendations_for_30pct:
    st.markdown(f"- {r}")


# Expander s parsovaným CV — sekundární info, default sbalený
with st.expander("📋 Parsované CV (JSON)"):
    st.json(result.cv.model_dump())


# ---------- Debug window ----------
if debug_mode:
    st.markdown("---")
    st.markdown("## 🐞 Debug Trace")

    # Souhrn nahoře
    summary_cols = st.columns(3)
    with summary_cols[0]:
        st.metric("Total tokens", f"{trace.total_tokens():,}")
    with summary_cols[1]:
        st.metric("Total cost (orientačně)", f"${trace.total_cost():.4f}")
    with summary_cols[2]:
        st.metric("Total time", f"{trace.total_duration_ms():.0f} ms")

    # Každý krok ve svém expanderu
    for i, step in enumerate(trace.steps, start=1):
        # Title expanderu obsahuje název kroku + čas / tokens, pokud existují
        meta_parts = []
        if "duration_ms" in step:
            meta_parts.append(f"{step['duration_ms']:.0f}ms")
        if "tokens" in step:
            meta_parts.append(f"{step['tokens']} tok")
        meta = f" — {' · '.join(meta_parts)}" if meta_parts else ""

        with st.expander(f"{i}. {step['name']}{meta}", expanded=False):
            # Pro LLM kroky zobrazíme prompty a odpovědi specificky (čitelnější než raw JSON)
            if "system_prompt" in step:
                st.markdown("**System prompt:**")
                st.code(step["system_prompt"], language="text")
                st.markdown("**User prompt:**")
                st.code(step["user_prompt"], language="text")
                st.markdown("**Raw response:**")
                st.code(step["raw_response"], language="json")
                # Zbytek meta (tokens, cost, model)
                meta_data = {k: v for k, v in step.items()
                             if k not in {"name", "system_prompt", "user_prompt", "raw_response"}}
                st.json(meta_data)
            else:
                # Non-LLM kroky — celý dict jako JSON
                st.json({k: v for k, v in step.items() if k != "name"})

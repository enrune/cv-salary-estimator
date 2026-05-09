"""
LLM klient — tenký wrapper nad OpenAI SDK nakonfigurovaný pro OpenRouter.

Proč OpenRouter: jediný API klíč → přístup ke 300+ modelům (Claude, Gemini, GPT, atd.).
Můžeme experimentovat s modely bez měnění kódu (jen env var).

Proč openai SDK místo httpx: OpenRouter je 100% kompatibilní s OpenAI Chat Completions API,
takže oficiální SDK funguje out-of-the-box. Žádný custom HTTP klient = méně bugů.
"""

# os pro environment variables — stdlib, není třeba instalovat
import os
import time  # perf_counter pro měření latence LLM volání

# OpenAI = jediná třída, kterou potřebujeme (chat.completions.create)
from openai import OpenAI

# load_dotenv načte .env soubor do os.environ — žádné hardcoded klíče v kódu
from dotenv import load_dotenv

# DebugTrace pro logování LLM volání (model, tokens, cost, latence)
from src.debug import DebugTrace

# Načteme .env při importu modulu — lazy alternativa by byla bezpečnější,
# ale tady chceme aby OPENROUTER_API_KEY byl dostupný hned
load_dotenv()


# Konstanty na úrovni modulu — nemění se za runtime, pojmenované jasně
# OpenRouter base URL — kompatibilní s OpenAI API
_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# Default model — gemini-2.5-flash má dobrý poměr cena/kvalita/rychlost a zvládá češtinu
# Přepíše se pokud je v .env nastavený OPENROUTER_MODEL
_DEFAULT_MODEL = os.getenv("OPENROUTER_MODEL", "google/gemini-2.5-flash")


def _get_client() -> OpenAI:
    """
    Vytvoří OpenAI klienta s OpenRouter base URL.
    Privátní funkce — uživatel modulu volá rovnou complete(), klient je interní detail.
    """
    # API klíč z env — žádný fallback, pokud chybí, ať to padne hned s jasnou chybou
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        # ValueError lépe vystihuje "konfigurace chybí" než RuntimeError
        raise ValueError(
            "OPENROUTER_API_KEY není nastaven. Zkopíruj .env.example na .env a vyplň klíč."
        )

    # OpenRouter doporučuje posílat HTTP-Referer a X-Title pro statistiky/leaderboard
    # default_headers zajistí, že se přidají ke každému requestu
    return OpenAI(
        api_key=api_key,
        base_url=_OPENROUTER_BASE_URL,
        default_headers={
            "HTTP-Referer": "https://github.com/enrune/jobhunt",  # OpenRouter to využije pro analytics
            "X-Title": "Job Fit & Salary Estimator",  # Lidsky čitelný název v OpenRouter dashboardu
        },
    )


def complete(
    system: str,
    user: str,
    *,  # vše po hvězdičce musí být keyword-only — zabraňuje záměně argumentů
    model: str | None = None,
    temperature: float = 0.2,  # nízká pro konzistentní extrakci; vyšší by halucinovala
    json_mode: bool = False,
    trace: DebugTrace | None = None,
    step_name: str = "llm",
) -> str:
    """
    Pošle prompt do LLM, vrátí raw text odpovědi.

    Argumenty:
        system: systémová instrukce (role/kontext modelu)
        user: uživatelský prompt (data + úkol)
        model: override default modelu; None → použít _DEFAULT_MODEL
        temperature: 0.2 default — nízká, ale ne nula (nula někdy způsobí degeneraci u některých modelů)
        json_mode: True → response_format=json_object, vynutí validní JSON v odpovědi
        trace: pokud je předán, zaloguje se model, tokens, cost, latence
        step_name: jméno kroku v debug trace (parse / explain)

    Vrací:
        Raw text odpovědi LLM (bez parsování — to dělá volající přes Pydantic).
    """
    # Lazy klient — vytvoří se při prvním volání, ne při importu
    client = _get_client()

    # Použijeme předaný model nebo default; explicitní None check kvůli typingu
    chosen_model = model if model is not None else _DEFAULT_MODEL

    # Sestavení parametrů; response_format jen když chceme JSON, jinak ho neposíláme
    # (některé modely nepodporují response_format a hodí 400, pokud je předán)
    kwargs: dict = {
        "model": chosen_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
    }
    if json_mode:
        # response_format=json_object je standard OpenAI; OpenRouter ho proxyuje pro modely co umí
        kwargs["response_format"] = {"type": "json_object"}

    # Měření latence — perf_counter je monotonický, neovlivní ho změny systémového času
    start = time.perf_counter()
    response = client.chat.completions.create(**kwargs)
    duration_ms = (time.perf_counter() - start) * 1000  # *1000 pro milisekundy

    # Extrakce textu — choices[0] protože n=1 (default), .message.content je samotný text
    content = response.choices[0].message.content or ""  # `or ""` proti None edge case

    # Logování do debug trace, pokud je k dispozici
    if trace is not None:
        # OpenRouter vrací usage podobně jako OpenAI; někdy chybí, pak default 0
        usage = response.usage
        prompt_tokens = usage.prompt_tokens if usage else 0
        completion_tokens = usage.completion_tokens if usage else 0
        total_tokens = usage.total_tokens if usage else 0

        # Hrubý odhad ceny — přesná cena přijde z OpenRouter API jen při speciálním requestu;
        # pro debug stačí orientační. Hodnoty jsou pro gemini-2.5-flash ($0.075/$0.30 per 1M).
        # Toto NENÍ přesná fakturace, jen orientace pro reviewera.
        cost_usd = (prompt_tokens * 0.075 + completion_tokens * 0.30) / 1_000_000

        trace.log(
            step_name,
            model=chosen_model,
            temperature=temperature,
            json_mode=json_mode,
            system_prompt=system,  # Plný prompt pro obhajobu — reviewer chce vidět promptové umění
            user_prompt=user,
            raw_response=content,  # Surová odpověď před parsováním
            tokens=total_tokens,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=cost_usd,
            duration_ms=round(duration_ms, 1),
        )

    return content

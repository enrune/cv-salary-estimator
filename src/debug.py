"""
DebugTrace — sběrač diagnostických informací z každého kroku pipeline.

Proč zvlášť: moduly src/* musí být volatelné z CLI (kde Streamlit není dostupný),
takže nemůžeme volat st.write() přímo v business logice. DebugTrace je čistý
in-memory kontejner, který Streamlit i CLI umí zobrazit (UI z něj kreslí expandery,
CLI ho dumpuje na stderr).
"""

# time pro měření latence — time.perf_counter() je přesnější než time.time() pro krátké intervaly
import time

# dataclass = lehký kontejner bez psaní __init__/__repr__ — jednodušší než BaseModel
# field umožňuje default_factory pro mutable defaults (list/dict) — antipattern by byl `= []`
from dataclasses import dataclass, field

# Any protože každý krok ukládá různě tvarovaná data; nesnažíme se to typovat striktně
from typing import Any


@dataclass
class DebugTrace:
    """Sbírá informace o každém kroku pipeline pro pozdější zobrazení."""
    # steps = sekvence dictů, každý reprezentuje jeden krok
    # default_factory=list — bez něj by všechny instance sdílely jeden list (Python gotcha)
    steps: list[dict[str, Any]] = field(default_factory=list)
    # start_time pro celkovou dobu běhu pipeline; nastaveno při __init__
    start_time: float = field(default_factory=time.perf_counter)

    def log(self, name: str, **data: Any) -> None:
        """
        Přidá záznam o kroku. Použití: trace.log("ingest", chars=1234, pages=2).
        **data je flexibilní, aby každý krok mohl logovat svá specifická pole.
        """
        # Jednoduchý append — bez DB, bez I/O, jen v paměti pro tento běh
        self.steps.append({"name": name, **data})

    def total_tokens(self) -> int:
        """Sečte tokens přes všechny kroky, které je logují (parse, explain)."""
        # sum + generator — efektivnější než list comprehension, protože nevytvoří mezilist
        # .get("tokens", 0) — kroky bez LLM (ingest, score) tokeny nemají, fallback 0
        return sum(s.get("tokens", 0) for s in self.steps)

    def total_cost(self) -> float:
        """Sečte cost ($) přes LLM kroky."""
        # Stejný pattern jako total_tokens — bezpečný fallback na 0.0
        return sum(s.get("cost_usd", 0.0) for s in self.steps)

    def total_duration_ms(self) -> float:
        """Celková doba běhu pipeline v milisekundách."""
        # perf_counter() vrací sekundy, *1000 = ms; čteme aktuální čas vs. start
        return (time.perf_counter() - self.start_time) * 1000

    def to_dict(self) -> dict[str, Any]:
        """Serializovatelná podoba pro JSON / CLI dump."""
        # Souhrnné metriky nahoře, detail steps dole — čitelné pro člověka i stroj
        return {
            "steps": self.steps,
            "summary": {
                "total_tokens": self.total_tokens(),
                "total_cost_usd": round(self.total_cost(), 4),  # zaokrouhlení pro lidskou čitelnost
                "total_duration_ms": round(self.total_duration_ms(), 1),
            },
        }

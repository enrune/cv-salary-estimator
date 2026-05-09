"""
CLI entrypoint: `python run.py samples/sample_cv.pdf [--debug]`

Tenký wrapper nad pipeline.run(). Vrací JSON na stdout — vhodné pro pipe do jq,
ukládání do souboru, nebo strojové zpracování.
"""


from __future__ import annotations
# argparse pro CLI argumenty — stdlib, žádná závislost
import argparse
# json pro pretty-printing výsledku
import json
# sys.stderr pro debug výstup (oddělený od JSON na stdout)
import sys

from src.pipeline import run


def main() -> None:
    """CLI entrypoint."""
    # ArgumentParser s description pro --help
    parser = argparse.ArgumentParser(
        description="Job Fit & Salary Estimator — analyzuje CV, vrátí JSON.",
    )
    # Pozicionální argument: cesta k CV souboru. Required = bez něj nefunguje.
    parser.add_argument(
        "cv_path",
        type=str,
        help="Cesta k CV souboru (PDF nebo DOCX)",
    )
    # --debug flag — pokud zapnutý, na stderr vypíše DebugTrace JSON
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Vypíše debug trace na stderr (mezikroky, prompty, tokens, cost)",
    )

    args = parser.parse_args()

    # Spustíme pipeline; výjimky propaguje (Python je vypíše s tracebackem)
    result, trace = run(args.cv_path)

    # Hlavní výstup: JSON Result na stdout
    # ensure_ascii=False kvůli českým znakům, indent=2 pro čitelnost
    # model_dump() místo model_dump_json() abychom mohli použít json.dumps s ensure_ascii
    print(json.dumps(result.model_dump(), ensure_ascii=False, indent=2))

    # Debug výstup na stderr — neznečistí stdout JSON, takže jde pipeovat
    if args.debug:
        print("\n=== DEBUG TRACE ===", file=sys.stderr)
        print(
            json.dumps(trace.to_dict(), ensure_ascii=False, indent=2),
            file=sys.stderr,
        )


# Standard guard — modul se dá importovat bez spuštění
if __name__ == "__main__":
    main()

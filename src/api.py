"""
FastAPI endpoint — alternativa ke Streamlit UI pro programatický přístup.

Spustit: `uvicorn src.api:app --reload`
Test:    `curl -F file=@samples/sample_cv.pdf http://localhost:8000/analyze`

Sdílí pipeline.run() s CLI i Streamlit — žádná duplicita.
"""

# tempfile + Path pro dočasné uložení uploadu (analogicky k app.py)
import tempfile
from pathlib import Path

# FastAPI = moderní API framework. UploadFile pro multipart upload.
from fastapi import FastAPI, UploadFile, File, HTTPException

from src.pipeline import run
from src.models import Result


# title se zobrazí v auto-generated /docs (Swagger UI)
app = FastAPI(
    title="Job Fit & Salary Estimator API",
    description="POST /analyze s multipart CV (PDF/DOCX) → JSON Result.",
    version="1.0.0",
)


# Health endpoint — užitečné pro deployment (load balancer si může zkontrolovat životnost)
@app.get("/health")
def health() -> dict:
    """Triviální health check. Nezatěžuje LLM."""
    return {"status": "ok"}


# Hlavní endpoint. response_model=Result → FastAPI automaticky validuje response a vygeneruje schema do /docs
@app.post("/analyze", response_model=Result)
async def analyze(file: UploadFile = File(...)) -> Result:
    """
    Analyzuje CV a vrátí Result.

    Body: multipart/form-data, pole `file` = PDF/DOCX soubor.
    """
    # Validace přípony — fail fast s 400 Bad Request, ne 500 Server Error
    filename = file.filename or ""
    suffix = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if suffix not in {".pdf", ".docx"}:
        # HTTPException s status 400 → klient ví, že je problém na jeho straně
        raise HTTPException(
            status_code=400,
            detail=f"Nepodporovaný formát: {suffix}. Povoleno: .pdf, .docx",
        )

    # Načteme bytes z uploadu (async kvůli velkým souborům)
    content = await file.read()

    # Sanity limit na velikost — 10 MB stačí pro každé reálné CV
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(
            status_code=413,  # Payload Too Large
            detail="Soubor je větší než 10 MB",
        )

    # Uložíme do temp souboru (pipeline potřebuje file path) — stejný pattern jako Streamlit
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        # API klienta nezajímá DebugTrace; vrátíme jen Result
        result, _trace = run(tmp_path)
        return result
    except ValueError as e:
        # Pipeline raisuje ValueError pro očekávané chyby (prázdný PDF, špatný JSON od LLM)
        # → 422 Unprocessable Entity — server chápe request, ale obsah nelze zpracovat
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        # Nečekaná chyba — 500
        raise HTTPException(status_code=500, detail=f"Interní chyba: {e}")
    finally:
        # Cleanup temp souboru
        Path(tmp_path).unlink(missing_ok=True)

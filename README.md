# Job Fit & Salary Estimator

AI pipeline, která z PDF/DOCX CV vrátí:
- **Seniority Score** (0–100) složený ze zkušeností, dovedností, vzdělání a soft skills
- **Salary Estimate** v CZK/měsíc (range, např. 80 000 – 110 000)
- **České vysvětlení** + konkrétní doporučení pro **+30 % platu**

Implementace pro AI Case Study (květen 2026).

---

## Rychlý start

```bash
# 1) Závislosti
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# 2) API klíč (OpenRouter — viz https://openrouter.ai)
cp .env.example .env
# vyplň OPENROUTER_API_KEY v .env

# 3) Spustit (3 možnosti)
streamlit run app.py                              # Web UI s debug oknem
python run.py samples/sample_cv.pdf               # CLI → JSON na stdout
uvicorn src.api:app --reload                      # REST API (POST /analyze)
```

---

## Architektura pipeline

```
┌───────────┐  ┌──────────┐  ┌─────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐
│ 1 Ingest  │→ │ 2 Parse  │→ │ 3 Score │→ │ 4 Salary │→ │ 5 Explain│→ │ 6 Validate│
│ PDF/DOCX  │  │ LLM →    │  │ heur.   │  │ lookup + │  │ LLM →    │  │ sanity    │
│ → text    │  │ Pydantic │  │ 0–100   │  │ bonusy   │  │ česky    │  │ checks    │
└───────────┘  └──────────┘  └─────────┘  └──────────┘  └──────────┘  └──────────┘
```

Každý krok je samostatný modul v `src/`, volaný čistou funkcí. `src/pipeline.py` orchestruje vše.

| Krok | Modul | Co dělá |
|---|---|---|
| 1 | `src/ingest.py` | `pdfplumber` (PDF) / `python-docx` (DOCX) → plain text |
| 2 | `src/parse.py` | LLM (`json_mode`) → Pydantic `CV`, retry s feedback při validation error |
| 3 | `src/score.py` | Vážená heuristika: 40 % experience + 30 % skills + 15 % education + 15 % soft |
| 4 | `src/salary.py` | Lookup tabulky × pozice v range podle score × bonus za premium skills (+max 20 %) |
| 5 | `src/explain.py` | LLM dostane CV + score breakdown + salary range → vrací JSON s vysvětlením a 3+ doporučeními |
| 6 | `src/validate.py` | Sanity checks (range, konzistence seniority/score, počty doporučení atd.) |

---

## Přístup k datům

Hybridní strategie pokrývající všechny tři možnosti ze zadání:

### a) Synthetic baseline — `data/salary_table.json`
Ručně sestavená tabulka 10+ rolí × 2 lokace (Praha / regiony) × 3 senioritní úrovně, opřená o veřejně známé vzorce platů (platy.cz, Glassdoor 2026). Vždy přítomná, slouží jako fallback.

### b) Scraping — `src/scrape.py` + `data/scraped_salaries.json`
One-shot skript stahuje veřejné inzeráty z `jobs.cz`, extrahuje role + zmíněnou mzdu, agreguje na percentily a ukládá do JSON. Data se mergují do baseline při startu salary modulu (scraped má prioritu).

```bash
python -m src.scrape       # ~5 min, ~150 inzerátů, slušný rate-limit (1.5 s)
```

Aktuální `data/scraped_salaries.json` byl vygenerován **9. 5. 2026** ze 76 unikátních inzerátů (10 query, 3 stránky každý). Zachovaná raw data jsou v `data/scraped_raw.json` pro re-aggregaci bez re-scrape.

### c) Heuristika — `src/salary.py`
Multi-faktorový výpočet: detekce role (LLM whitelist) → detekce lokace (Praha vs regiony) → lookup baseline range → posun v range podle score → premium-skill bonus.

### Omezení a transparentnost

- Synthetic data jsou **odhad**, ne autoritativní zdroj — reálné platy záleží na firmě, benefitech a vyjednání.
- Scraped data jsou **snapshot** trhu k datu spuštění. Pro aktualizaci stačí znovu spustit `python -m src.scrape`.
- Klasifikace role z inzerátu používá keyword matching; ~30 % inzerátů spadne do `general` kategorie.

---

## Struktura projektu

```
jobhunt/
├── README.md
├── requirements.txt
├── .env.example                 # OPENROUTER_API_KEY=...
├── .gitignore
├── data/
│   ├── salary_table.json        # synthetic baseline
│   ├── scraped_salaries.json    # výstup scraperu (mergne se do baseline)
│   └── scraped_raw.json         # raw inzeráty pro re-aggregaci
├── samples/                     # ukázková CV pro testy
├── src/
│   ├── models.py                # Pydantic CV, Score, SalaryEstimate, Result
│   ├── ingest.py                # PDF/DOCX → text
│   ├── llm.py                   # OpenRouter klient
│   ├── parse.py                 # text → CV (LLM)
│   ├── score.py                 # CV → Score (heuristika)
│   ├── salary.py                # CV+Score → SalaryEstimate
│   ├── explain.py               # vše → české vysvětlení (LLM)
│   ├── validate.py              # sanity_check(result)
│   ├── scrape.py                # jobs.cz scraper
│   ├── debug.py                 # DebugTrace
│   ├── pipeline.py              # orchestrace
│   └── api.py                   # FastAPI endpoint
├── app.py                       # Streamlit UI + debug window
└── run.py                       # CLI entrypoint
```

---

## LLM model

Default: `google/gemini-2.5-flash` (rychlý, levný, dobrá čeština).
Změnit lze v `.env` proměnnou `OPENROUTER_MODEL`. Doporučené alternativy:
- `anthropic/claude-haiku-4.5` — nejlepší čeština, mírně dražší
- `openai/gpt-4o-mini` — kompromis kvalita/cena
- `deepseek/deepseek-chat` — nejlevnější, hor­ší v češtině

Typický run pro 1 CV: ~1500 input + 700 output tokens, ~$0.0003 (gemini-flash) až ~$0.005 (claude-haiku).

---

## Debug mode

V Streamlit UI zaškrtni `🐞 Debug mode` v sidebaru. Zobrazí se:

1. **Ingest** — počet znaků, prvních 500 chars textu
2. **Parse** — full system + user prompt, raw LLM response, parsed Pydantic CV, tokens, cost, latence
3. **Score** — breakdown jednotlivých složek (`experience: 76, skills: 28, ...`), váhy, formula
4. **Salary** — base range, pozice ve seniorita kategorii, shift dle score, bonusy
5. **Explain** — full prompt (vč. score+salary kontextu), raw LLM response, tokens, cost
6. **Souhrn** — total tokens, total cost, total time

V CLI: `python run.py samples/sample_cv.pdf --debug` → debug trace na stderr.

---

## Verifikace

```bash
# Sanity tests (3 různá CV)
python run.py samples/junior_cv.pdf  | jq '.score.total, .salary'
python run.py samples/medior_cv.pdf  | jq '.score.total, .salary'
python run.py samples/senior_cv.pdf  | jq '.score.total, .salary'

# REST endpoint
curl -F file=@samples/sample_cv.pdf http://localhost:8000/analyze | jq '.score.total'
```

Očekávané:
- Junior bez praxe: score < 45, plat 40–70k, doporučení míří na získání zkušeností
- Senior 10+ let s leadershipem: score > 70, plat 100k+, doporučení na management/architekturu
- Neznámá role: pipeline nespadne, `role_detected: "general"`, širší range

---

## Co je v kódu komentováno

Každý řádek Pythonu má krátký komentář vysvětlující **proč** (volba knihovny, parametru, edge case, datového rozhodnutí). Důvod: kód musí jít obhájit při review v dalším kole — čtení od neznámého reviewera musí dát smysl.

---

## Co (vědomě) nedělám

- Žádné fine-tuning / embeddings / vector DB — overkill pro tento rozsah
- Žádné Docker / CI / unit testy — sanity checks v `validate.py` + ruční testy stačí
- Žádný auth / databáze — stateless funkce, žádné session storage
- Scraping je on-demand (`python -m src.scrape`), ne runtime — výsledky se cachují do JSON

---

## Licence

Educational case study — žádná licence pro produkční použití. Scraping z jobs.cz proběhl s
respektem k robots.txt, slušnými rate-limity a identifikujícím User-Agent.

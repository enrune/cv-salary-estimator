# Job Fit & Salary Estimator

AI pipeline, která z PDF/DOCX CV vrátí:
- **Seniority Score** (0–100) složený z dovedností, zkušeností, osobnostních rysů a vzdělání
- **Salary Estimate** v CZK/měsíc (range, např. 80 000 – 110 000)
- **České vysvětlení** + konkrétní doporučení pro **+30 % platu**

Implementace pro AI Case Study (květen 2026).

---

## 🚀 Jak to spustit (2 příkazy)

```bash
pip install -r requirements.txt
streamlit run app.py
```

To je všechno. Při prvním spuštění UI samo vyzve k vložení **OpenRouter API klíče** (zdarma účet na [openrouter.ai](https://openrouter.ai), ~$1 kreditu stačí na desítky CV). Klíč se uloží do `.env` a víc se nikdy neptá.

Po nahrání CV (například `samples/sample_cv_senior.docx`) klikni v sidebaru **🐞 Debug mode** pro zobrazení všech mezikroků pipeline.

**Alternativy spuštění:**
```bash
python run.py samples/sample_cv_senior.docx              # CLI → JSON na stdout
python run.py samples/sample_cv_senior.docx --debug      # + debug trace na stderr
uvicorn src.api:app --reload                             # REST API: POST /analyze
```

> CLI a REST API potřebují klíč v `.env` — buď ho tam Streamlit už uložil, nebo jednorázově `cp .env.example .env` a editovat.

**Doporučená verze Pythonu:** 3.11+. Pro 3.9/3.10 funguje díky `eval-type-backport`, ale doporučuju použít `python -m venv venv && source venv/bin/activate` (Linux/Mac) / `.\venv\Scripts\Activate.ps1` (Windows) před `pip install`.

---

## 🔧 Jak funguje pipeline

```
┌───────────┐  ┌──────────┐  ┌─────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐
│ 1 INGEST  │→ │ 2 PARSE  │→ │ 3 SCORE │→ │ 4 SALARY │→ │ 5 EXPLAIN│→ │ 6 VALIDATE│
│ PDF/DOCX  │  │ LLM →    │  │ heur.   │  │ lookup + │  │ LLM →    │  │ sanity    │
│ → text    │  │ Pydantic │  │ 0–100   │  │ bonusy   │  │ česky    │  │ checks    │
└───────────┘  └──────────┘  └─────────┘  └──────────┘  └──────────┘  └──────────┘
       ▼              ▼            ▼            ▼              ▼             ▼
   raw text        CV objekt    Score      SalaryEstimate   strengths    Result + warnings
                                            (min, max CZK)   gaps + 3+    (JSON pro UI/API)
                                                             recomms
```

Každý krok je **samostatný modul v `src/` jako čistá funkce**, `src/pipeline.py` orchestruje sekvenci. To umožňuje (a) testovat moduly samostatně z REPL, (b) sdílet pipeline mezi CLI / Streamlit / FastAPI bez duplicity.

| # | Modul | Vstup → Výstup | Detail |
|---|---|---|---|
| 1 | `src/ingest.py` | `path → str` | `pdfplumber` (PDF, layout-aware) nebo `python-docx` (DOCX, vč. tabulek) |
| 2 | `src/parse.py` | `str → CV` | LLM s `json_mode` + Pydantic validace. Při chybě **retry s feedbackem** chyby do promptu (self-healing) |
| 3 | `src/score.py` | `CV → Score` | Vážená heuristika: **0.40·exp + 0.30·skills + 0.15·edu + 0.15·soft**. Sigmoidní křivka pro roky praxe |
| 4 | `src/salary.py` | `CV+Score → SalaryEstimate` | (a) lookup baseline range pro role × lokace × seniorita, (b) posun v range podle score (max ±12.5 %), (c) bonus za premium skills (cap +20 %) |
| 5 | `src/explain.py` | `vše → text + lists` | LLM dostane plný kontext (CV JSON + score breakdown + salary range + cíl +30 %) → vrací JSON s explanation, strengths, gaps, **min. 3 měřitelná doporučení** |
| 6 | `src/validate.py` | `Result → list[warnings]` | 8 sanity check pravidel (range, konzistence seniority/score, počet doporučení, realistický plat 15–500k…) |

Detaily skoringu a salary vzorce viz docstring v daném modulu.

---

## 📊 Jak jsem přistoupil k datům

Zadání povoluje scraping / veřejné zdroje / synthetic data. Použil jsem **všechny tři** — každý má jinou roli:

### a) Synthetic baseline — `data/salary_table.json`
Ručně sestavená tabulka **11 rolí × 2 lokace (Praha / regiony) × 3 senioritní úrovně** = 66 platových rozmezí. Hodnoty jsou odvozené z veřejně známých vzorců (platy.cz, Glassdoor 2026). Slouží jako **vždy přítomný fallback** — pipeline funguje, i když chybí scraped data nebo internet.

### b) Scraping — `src/scrape.py` → `data/scraped_salaries.json`
One-shot Python skript, který stahuje veřejné inzeráty z **jobs.cz**:
- 10 různých query (`python developer`, `data engineer`, `devops`, …) × 3 stránky = ~150 inzerátů
- Extrakce mzdy z volného textu (regex tolerantní na NBSP, ZWJ, em-dash)
- Klasifikace role + seniority + lokace (keyword matching → naše taxonomie)
- Deduplikace, agregace na **P25 of min / P75 of max** pro každou kombinaci (eliminuje outliery)
- Slušný scraping: rate-limit 1.5 s, identifikující User-Agent, respekt k robots.txt

Spuštění:
```bash
python -m src.scrape       # ~5 min, vytvoří/aktualizuje JSON
```

Aktuální `data/scraped_salaries.json` vznikl **9. 5. 2026** ze 76 unikátních inzerátů (po dedup z 111). `data/scraped_raw.json` obsahuje surové záznamy pro re-aggregaci bez nutnosti znovu scrapovat.

### c) Heuristika — `src/salary.py`
Spojuje a/ + b/ v multi-faktorovém vzorci:
1. **Load tabulky** (`@lru_cache` na IO) — baseline + merge scraped (scraped má prioritu, kde existuje)
2. **Detekce role** z `cv.role_category` (LLM klasifikuje do whitelistu 11 rolí, fallback `general`)
3. **Detekce lokace** z `cv.location` (klíčová slova: Praha/prague/pražsk → praha, jinak regiony)
4. **Detekce seniority** ze score (≤45 junior, 46–70 medior, 71+ senior) — objektivnější než z titulu v CV
5. **Lookup range** [min, max] CZK
6. **Interpolace v range** podle pozice score v seniorita kategorii (max ±12.5 % šířky range)
7. **Premium-skill bonus** (+5 % AWS, +7 % K8s, +8 % Rust/LLM, …, max +20 %)
8. **Zaokrouhlení** na tisícovky pro čitelnost

### Transparentnost a omezení

- Synthetic data jsou **odhad**, ne autoritativní zdroj — reálné platy závisí na firmě, benefitech a vyjednání.
- Scraped data jsou **snapshot trhu** k datu běhu. Pro refresh: `python -m src.scrape`.
- Klasifikace role z inzerátu používá keyword matching; cca 30 % inzerátů spadne do `general` (nemají typický IT title).
- Kombinace s méně než 2 záznamy ve scrape se zahazují (statisticky nespolehlivé) — baseline pak rozhoduje.

---

## 🐞 Debug mode

V Streamlit UI zaškrtni `🐞 Debug mode` v sidebaru:
1. **Ingest** — počet stránek/znaků, ukázka textu
2. **Parse** — plný system + user prompt, raw LLM response, parsed CV, tokens, cost, latence
3. **Score** — breakdown jednotlivých složek + váhy + textový vzorec `0.4×86 + 0.3×89 + ...`
4. **Salary** — base range, position v seniorita, shift, bonusy, finální range
5. **Explain** — plný prompt (vč. score+salary kontextu), raw LLM, tokens
6. **Validate** — sanity check warnings
7. **Souhrn** — total tokens, total cost, total time

V CLI: `python run.py samples/sample_cv_senior.docx --debug 2>debug.log`.

---

## 📁 Struktura projektu

```
jobhunt/
├── README.md
├── requirements.txt
├── .env.example                 # OPENROUTER_API_KEY=...
├── data/
│   ├── salary_table.json        # synthetic baseline
│   ├── scraped_salaries.json    # výstup scraperu (mergne se do baseline)
│   └── scraped_raw.json         # raw inzeráty pro re-aggregaci
├── samples/
│   └── sample_cv_senior.docx    # ukázkové CV pro test
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

## 🤖 LLM model

Default: `google/gemini-2.5-flash` (rychlý, levný, dobrá čeština). Změnit v `.env` přes `OPENROUTER_MODEL`. Alternativy: `anthropic/claude-haiku-4.5`, `openai/gpt-4o-mini`, `deepseek/deepseek-chat`.

Typický run pro 1 CV: **~4500 tokens, ~$0.0007** (gemini-flash), **~10 s** end-to-end.

---

## ✏️ Komentáře v kódu

Každý řádek Pythonu má krátký komentář vysvětlující **proč** — volba knihovny, parametru, edge case, datového rozhodnutí. Důvod: kód musí jít obhájit při review.

---

## 🚫 Co (vědomě) nedělám

- Žádné fine-tuning / embeddings / vector DB — overkill pro tento rozsah
- Žádné Docker / CI / unit testy — sanity checks v `validate.py` + ruční testy stačí
- Žádný auth / databáze — stateless funkce, žádné session storage
- Scraping běží on-demand (`python -m src.scrape`), ne runtime — výsledky cachované do JSON

---

## 📜 Licence

Educational case study — žádná licence pro produkční použití. Scraping z jobs.cz proběhl s respektem k robots.txt, slušnými rate-limity (1.5 s/request) a identifikujícím User-Agent.

# Job Fit & Salary Estimator

AI systém, který z PDF/DOCX životopisu vrátí:
- **Seniority Score** (0–100)
- **Odhad mzdy** v CZK/měsíc (range)
- **České vysvětlení** + doporučení pro +30 % platu

---

## 🚀 Jak to spustit

```bash
pip install -r requirements.txt
streamlit run app.py
```

Otevře se prohlížeč na `http://localhost:8501`. Repo obsahuje dočasný OpenRouter API klíč v `.env`, takže není potřeba nic nastavovat. Pokud chcete použít vlastní klíč, smažte `.env` a aplikace si o něj při startu řekne formulářem v UI.

V UI nahrajte CV (například `samples/sample_cv_senior.docx`). Pro zobrazení detailů pipeline zaškrtněte v levém sidebaru **🐞 Debug mode**.

**Alternativy spuštění:**
```bash
python run.py samples/sample_cv_senior.docx       # CLI → JSON
uvicorn src.api:app --reload                      # REST API: POST /analyze
```

---

## 🔧 Jak funguje pipeline

Pipeline má 6 kroků, každý je samostatný modul v `src/`:

1. **Ingest** — z PDF/DOCX vytáhne text (pdfplumber / python-docx)
2. **Parse** — LLM převede text na strukturované CV (jméno, role, dovednosti, vzdělání…)
3. **Score** — heuristikou spočítá skóre 0–100: 40 % zkušenosti + 30 % dovednosti + 15 % vzdělání + 15 % soft skills
4. **Salary** — najde platové rozmezí v tabulce podle role + lokace + seniority a upraví ho podle skóre a žádaných dovedností
5. **Explain** — LLM napíše české vysvětlení, silné stránky, slabiny a 3+ konkrétní doporučení pro +30 % platu
6. **Validate** — sanity checky (rozumný range, počet doporučení, konzistence seniority s praxí)

Orchestraci dělá `src/pipeline.py`. Stejnou pipeline volá CLI (`run.py`), web (`app.py`) i REST API (`src/api.py`) — žádná duplicita.

---

## 📊 Jak jsem přistoupil k datům

Zadání povoluje scraping, veřejné zdroje i synthetic data. Použil jsem **kombinaci všech tří**:

1. **Synthetic baseline** (`data/salary_table.json`) — ručně sestavená tabulka 52 rolí × 2 lokace × 3 senioritní úrovně (306 platových rozmezí). Hodnoty jsou odvozené z veřejných zdrojů (platy.cz, Glassdoor). Pipeline díky ní funguje vždy, i bez internetu.

2. **Scraping** (`src/scrape.py` → `data/scraped_salaries.json`) — script stahuje inzeráty z **jobs.cz** přes 35 různých dotazů (IT, retail, gastro, zdravotnictví, řemesla…), agreguje mzdy a uloží do JSON. Aktuální scrape obsahuje **741 unikátních inzerátů**. Spustit: `python -m src.scrape`.

3. **Heuristika** (`src/salary.py`) — výsledná mzda vznikne kombinací: lookup v tabulce (scraped data má přednost před baseline) → posun v range podle skóre → bonus za žádané dovednosti (AWS, Kubernetes, řidičský průkaz C/D, atd., max +20 %) → zaokrouhlení.

### Proč ne jiné zdroje
- **platy.cz** je SPA (data se načítají přes JavaScript), vyžadoval by Selenium/Playwright — víc křehké pro málo dat navíc
- **LinkedIn / Glassdoor** mají anti-bot ochranu nebo zakazují scraping v ToS
- **jobs.cz** je nejstabilnější český zdroj s viditelnými mzdami v ~30 % inzerátů

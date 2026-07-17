# What Is Cas Reading 📖

A dead-simple website that publishes a **daily digest** of new papers, articles, bills,
and policy updates on AI safety, governance, and risk — curated for
[Stephen (Cas) Casper](https://stephencasper.com/).

Each morning, a script asks Claude (with web search) to compile ~10–20 entries, one per
topic, each with a one-sentence summary and links. The newest digest shows at the top;
older ones (last 60 days) are collapsible. Anything older than 60 days auto-deletes.

## How it works

| Piece | What it does |
|---|---|
| `custom_prompt.txt` | The editorial brief Claude follows. **Edit this anytime** to change what gets covered. |
| `generate_digest.py` | Calls Claude + web search, writes `data/<date>.json`, rebuilds `data/index.json`, prunes >60 days. |
| `index.html` | The whole website. Plain static HTML/JS, no build step. Reads the JSON files. |
| `data/` | One JSON file per day, plus `index.json` listing them. |
| `.github/workflows/daily-digest.yml` | Runs the script every morning and commits the result (which redeploys the site). |

The site is hosted free on **GitHub Pages**; the daily job runs free on **GitHub Actions**.
Your only costs are the domain (~$10–15/year) and a few cents of Claude API usage per day.

---

## Part 1 — Run it locally first

You need Python 3.11+ and an Anthropic API key.

### 1a. Get an Anthropic API key
1. Go to <https://console.anthropic.com/>, sign in, and open **Settings → API keys**.
2. Create a key and copy it (starts with `sk-ant-...`).
3. Add a little credit under **Billing** (a few dollars covers months of daily digests).

### 1b. Install and generate a digest
```bash
cd whatiscasreading
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

export ANTHROPIC_API_KEY="sk-ant-..."   # paste your key
python generate_digest.py
```
This writes a fresh `data/<today>.json` and updates `data/index.json`.

### 1c. Preview the site
Open it through a local web server (opening the file directly won't work — browsers block
`fetch()` from `file://`):
```bash
python3 -m http.server 8000
```
Then visit <http://localhost:8000>. Edit `custom_prompt.txt`, re-run the script, refresh.

---

## Part 2 — Put it online (GitHub Pages + Actions, ~free)

### 2a. Push to GitHub
1. Create a free account at <https://github.com> if you don't have one.
2. Create a new **empty** repository (e.g. `whatiscasreading`), public.
3. From this folder:
   ```bash
   git init
   git add .
   git commit -m "Initial commit"
   git branch -M main
   git remote add origin https://github.com/<your-username>/whatiscasreading.git
   git push -u origin main
   ```

### 2b. Add your API key as a secret
In the repo: **Settings → Secrets and variables → Actions → New repository secret**.
- Name: `ANTHROPIC_API_KEY`
- Value: your `sk-ant-...` key

### 2c. Turn on GitHub Pages
**Settings → Pages → Build and deployment → Source: “Deploy from a branch”**,
branch `main`, folder `/ (root)`, then **Save**. After a minute your site is live at
`https://<your-username>.github.io/whatiscasreading/`.

### 2d. Check the daily job
The workflow runs every morning on its own. To test it now: **Actions → Daily digest →
Run workflow**. It generates a digest, commits it, and the commit redeploys Pages.

> **Daylight-saving note:** GitHub cron is UTC-only. `0 10 * * *` lands at **6am during
> EDT (summer)** and **5am during EST (winter)**. If you want it pinned to exactly 6am ET
> year-round, add a second schedule line `- cron: "0 11 * * *"` and have the script no-op
> when it's not ~6am ET — but for a morning reading list, an hour's drift is harmless.

---

## Part 3 — A custom domain (whatiscasreading.net)

1. Buy the domain from any registrar (Namecheap, Cloudflare, Porkbun — ~$10–15/yr).
2. In the repo: **Settings → Pages → Custom domain**, enter `whatiscasreading.net`, Save.
   GitHub creates a `CNAME` file in the repo.
3. At your registrar's DNS settings, add these records (from
   [GitHub's docs](https://docs.github.com/en/pages/configuring-a-custom-domain-for-your-github-pages-site)):
   - Four `A` records for the apex domain → `185.199.108.153`, `185.199.109.153`,
     `185.199.110.153`, `185.199.111.153`
   - One `CNAME` record for `www` → `<your-username>.github.io`
4. Wait for DNS to propagate (minutes to a few hours), then tick **Enforce HTTPS** in
   Pages settings.

That's it — the daily commit keeps the site fresh, and total running cost stays well
under $1/day.

---

## Tweaking it
- **Change coverage:** edit `custom_prompt.txt`.
- **Change retention:** edit `RETENTION_DAYS` in `generate_digest.py`.
- **Change the look:** edit the `<style>` block in `index.html`.
- **Change the run time:** edit the `cron` line in `.github/workflows/daily-digest.yml`.

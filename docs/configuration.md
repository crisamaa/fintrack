# Configuration

Non-secret settings are in `config/settings.yaml` (commented). Secrets and per-deployment limits come from the
environment, set in GitHub under Settings → Secrets and variables → Actions.

| Name | Kind | Default | Meaning |
| --- | --- | --- | --- |
| `GEMINI_API_KEY` | secret | — | Gemini key. Without it, job boards are still read; AI work waits in the queue. |
| `BRAVE_API_KEY` | secret | — | Only for `SEARCH_PROVIDER=brave`. |
| `AI_MONTHLY_CAP_USD` | variable | none | Monthly AI cap at list prices; calls stop $0.10 below it. **Set it before using a billed key.** Unset is fine on the free tier, and every run then warns. |
| `AI_DAILY_REQUESTS` | variable | 400 | Model calls per UTC day; keeps the free tier's daily quota from being hit mid-run. |
| `SEARCH_PROVIDER` | variable | `none` | `none`, `gemini` (Grounding with Google Search) or `brave`. |
| `SEARCH_DAILY_LIMIT` | variable | 20 | Searches per day. 20/day stays inside Gemini 3's 5,000 free grounded prompts a month. |
| `DIGEST_ISSUE` | variable | false | `true`: also open the morning digest as an issue in this repository (GitHub notifies you). |

Costs, as checked on 2026-11-04 (re-check before relying on them):

- GitHub Actions and Pages: free for a public repository.
- `gemini-3.5-flash-lite`: $0.30 per million input tokens, $2.50 per million output tokens; prices are constants in
  `tracker/budget.py` next to `MODEL` in `tracker/ai.py`. The Sources view shows this month's spend at these prices.
- Grounding with Google Search on Gemini 3.x: 5,000 free grounded prompts a month, then $14 per 1,000. Free-tier keys
  may not have access; the run reports the error and continues without search.

Nothing billable is enabled by default: search is off, and with no key no AI call is made.

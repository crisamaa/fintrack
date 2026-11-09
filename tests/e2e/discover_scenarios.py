"""Browser walk-through of the personalised Discover acceptance scenarios (A–K), run by hand, not by CI.

  PYTHONPATH=. .venv/bin/python tests/e2e/build_site.py . /tmp/fintrack-site   # site copy with classified data
  python3 -m http.server 8765 --directory /tmp/fintrack-site &
  .venv/bin/python tests/e2e/discover_scenarios.py /tmp/fintrack-shots          # screenshots go here

Needs Playwright's Chromium (`.venv/bin/python -m playwright install chromium`). It uses a fresh browser profile, so
it never touches your real localStorage.
"""
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

from playwright.sync_api import sync_playwright

SP = sys.argv[1]
Path(SP).mkdir(parents=True, exist_ok=True)
URL = "http://localhost:8765/index.html"
ok = []
def check(name, cond, info=""):
    ok.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + (f"  [{info}]" if info else ""))

def rows(pg): return pg.eval_on_selector_all(".feed-row", "els => els.map(e => e.dataset.id)")
def tab(pg, name): pg.click(f'.tab:has-text("{name}")'); pg.wait_for_timeout(150)
def setlvl(pg, label, lvl):
    pg.locator(f'.fn-row[aria-label="{label}"] .seg-opt:has-text("{lvl}")').click(); pg.wait_for_timeout(60)
def doc(pg): return pg.evaluate("JSON.parse(localStorage.getItem('fintrack.targets.v1'))")

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1440, "height": 900})
    errs = []
    pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto(URL); pg.wait_for_timeout(900)
    # an existing user: Profile preferences and eligibility set before career targets existed
    pg.evaluate("""() => localStorage.setItem('fintrack.profile.v1', JSON.stringify({prefs: {sectors: ['m_and_a'], countries: ['CH'],
      types: []}, languages: [{code: 'en', level: 'fluent'}], work_rights: [{region: 'CH', basis: 'citizenship', confirmed: true}]}))""")
    pg.reload(); pg.wait_for_timeout(900)

    # --- A: first use
    check("A onboarding shown without a target", pg.locator(".steps").count() == 1)
    check("A prefill note from legacy Profile", "Prefilled" in pg.inner_text("#view"))
    pg.click('.chip:has-text("Investment Banking — Switzerland")'); pg.wait_for_timeout(100)
    pg.click('button:has-text("Next")'); pg.wait_for_timeout(150)
    setlvl(pg, "Investment banking", "Strong"); setlvl(pg, "Corporate finance & valuation", "OK"); setlvl(pg, "Equity capital markets", "OK")
    pg.click('button:has-text("Next")'); pg.wait_for_timeout(150)
    pg.screenshot(path=f"{SP}/a3.png", full_page=False)
    pg.click('button:has-text("Next")'); pg.wait_for_timeout(200)
    review = pg.inner_text("#view")
    check("A review shows real preview counts", "With today's data" in review, review.split("With today's data")[1][:80] if "With today's data" in review else "")
    pg.screenshot(path=f"{SP}/a4.png")
    pg.click('button:has-text("Activate target")'); pg.wait_for_timeout(500)
    top = rows(pg)
    check("A populated feed after activation", len(top) > 0, f"{len(top)} top rows")
    check("A breadcrumb shows the target", "Investment Banking" in pg.inner_text("#view-title"))
    check("A detail pane open on first row", pg.locator("#detail:not([hidden]) h2").count() == 1)
    pg.screenshot(path=f"{SP}/foryou.png")

    # --- C: role targeting — no non-finance function among top matches
    metas = pg.eval_on_selector_all(".feed-row .feed-meta", "els => els.map(e => e.innerText)")
    bad = [m for m in metas if any(k in m for k in ["Technology", "HR & recruiting", "Operations", "Marketing", "Data & analytics", "Compliance"])]
    check("C no technology/HR/ops roles in Top matches", not bad, str(bad[:3]))

    # --- D: sections
    for name in ["Newly found", "Closing soon", "Needs review"]:
        tab(pg, name); check(f"D {name} renders", pg.locator(".feed-row").count() > 0 or pg.locator(".state").count() == 1, f"{pg.locator('.feed-row').count()} rows")
    tab(pg, "Closing soon")
    days = pg.eval_on_selector_all(".feed-row .feed-line .days", "els => els.map(e => e.innerText)")
    check("D closing soon shows only dated ones", all(d != "rolling" for d in days), str(days[:6]))
    tab(pg, "Top matches")

    # --- E: efficient review
    first, second = rows(pg)[0], rows(pg)[1]
    pg.click(f'.feed-row[data-id="{second}"]'); pg.wait_for_timeout(150)
    det = pg.inner_text("#detail")
    check("E detail has why/requirements/source", all(s in det for s in ["Why it", "Requirements & eligibility", "Source"]))
    pg.keyboard.press("s"); pg.wait_for_timeout(250)
    check("E save persists to target and Pipeline", doc(pg)["decisions"][doc(pg)["activeId"]][second]["state"] == "saved"
          and json.loads(pg.evaluate("localStorage.getItem('fintrack.tracker.v1')"))[second]["stage"] == "saved")
    pg.keyboard.press("j"); pg.wait_for_timeout(150)
    check("E j moves to the next posting without navigation", pg.evaluate("location.pathname").endswith("index.html") and pg.locator(".feed-row.selected").get_attribute("data-id") != second)
    # typing guard
    pg.click("#search"); pg.keyboard.type("sx"); pg.wait_for_timeout(200)
    check("E shortcuts ignored while typing", pg.evaluate("document.activeElement.id") == "search" and pg.input_value("#search") == "sx")
    pg.fill("#search", ""); pg.keyboard.press("Escape"); pg.wait_for_timeout(150)

    # list scroll position survives selecting and deciding further down
    tab(pg, "Newly found")
    pg.evaluate("document.querySelector('#view').scrollTop = 300"); pg.wait_for_timeout(100)
    lower = pg.eval_on_selector_all(".feed-row", "els => els.filter(e => e.getBoundingClientRect().top > 400).map(e => e.dataset.id)")[0]
    pg.click(f'.feed-row[data-id="{lower}"]'); pg.wait_for_timeout(150)
    before = pg.evaluate("document.querySelector('#view').scrollTop")
    pg.keyboard.press("l"); pg.wait_for_timeout(250)
    after = pg.evaluate("document.querySelector('#view').scrollTop")
    check("E scroll position kept after a decision", before > 0 and abs(after - before) < 5, f"{before} -> {after}")
    pg.keyboard.press("l"); pg.wait_for_timeout(150)
    tab(pg, "Top matches")

    # --- F: dismissal with reason and undo
    victim = rows(pg)[0]
    pg.click(f'.feed-row[data-id="{victim}"]'); pg.wait_for_timeout(100)
    pg.keyboard.press("x"); pg.wait_for_timeout(250)
    check("F dismissed posting leaves the feed", victim not in rows(pg))
    check("F undo bar with optional reasons", pg.locator("#undo:not([hidden]) .chip").count() == 10)
    pg.click('#undo .chip:has-text("Wrong finance field")'); pg.wait_for_timeout(100)
    tid = doc(pg)["activeId"]
    check("F reason recorded", doc(pg)["decisions"][tid][victim]["reason"] == "wrong_field")
    pg.keyboard.press("u"); pg.wait_for_timeout(250)
    check("F undo restores it", victim in rows(pg) and victim not in doc(pg)["decisions"][tid])
    pg.click(f'.feed-row[data-id="{victim}"]'); pg.keyboard.press("x"); pg.wait_for_timeout(250)
    pg.click('.mode-switch button:has-text("All opportunities")'); pg.wait_for_timeout(300)
    pg.fill("#search", "")
    allids = pg.eval_on_selector_all(".grid tbody tr", "els => els.map(e => e.dataset.id)")
    check("F still in All opportunities", victim in allids)
    pg.click('.mode-switch button:has-text("For you")'); pg.wait_for_timeout(300)

    # --- B: second target
    pg.click(".target-menu > summary"); pg.click('.target-menu button:has-text("New target")'); pg.wait_for_timeout(200)
    pg.fill('input[placeholder^="e.g. Investment"]', "Asset Management"); pg.click('button:has-text("Next")'); pg.wait_for_timeout(150)
    setlvl(pg, "Asset management", "Strong"); setlvl(pg, "Quantitative finance", "OK")
    pg.click('button:has-text("Skip to review")'); pg.wait_for_timeout(150); pg.click('button:has-text("Activate target")'); pg.wait_for_timeout(400)
    am_top = rows(pg)
    check("B second target ranks differently", am_top and am_top != top, f"{len(am_top)} vs {len(top)}")
    # --- G: independence
    check("G dismissal not shared", victim not in doc(pg)["decisions"].get(doc(pg)["activeId"], {}))
    t0 = time.time()
    pg.click(".target-menu > summary"); pg.click('.target-menu label:has-text("Investment Banking")'); pg.wait_for_timeout(50)
    pg.wait_for_selector(".feed-row"); switch_ms = (time.time() - t0) * 1000
    check("B switching is fast", switch_ms < 600, f"{switch_ms:.0f} ms incl. clicks")
    foot = pg.inner_text(".feed-foot")
    print("   feed compute:", foot.split("evaluated in")[1].split(".")[0] if "evaluated in" in foot else foot)

    # --- H: learning — dismiss three Financial risk / tech roles with a reason
    pg.click('.mode-switch button:has-text("For you")')
    tab(pg, "Adjacent")
    cand = pg.eval_on_selector_all(".feed-row", "els => els.map(e => [e.dataset.id, e.querySelector('.firm').innerText, e.querySelector('.feed-meta').innerText])")
    print("   adjacent sample:", [c[2] for c in cand[:4]])
    # choose a function with >=3 postings at >=2 firms among all rows in Top+Adjacent
    by = defaultdict(list)
    for i, firm, meta in cand:
        fn = meta.split("\n")[1] if "\n" in meta else meta
        by[fn].append((i, firm))
    pick = next((fn for fn, xs in by.items() if len(xs) >= 3 and len({f for _, f in xs}) >= 2), None)
    check("H found a function to dismiss repeatedly", pick, pick or str({k: len(v) for k, v in by.items()}))
    if pick:
        chosen = []
        for i, firm in by[pick]:
            if len(chosen) < 3 and (len(chosen) < 2 or firm not in {f for _, f in chosen} or len({f for _, f in chosen}) >= 2):
                chosen.append((i, firm))
        prefs_before = json.dumps(doc(pg)["profiles"][doc(pg)["activeId"]]["prefs"])
        for i, _ in chosen:
            pg.click(f'.feed-row[data-id="{i}"]'); pg.keyboard.press("x"); pg.wait_for_timeout(200)
            pg.click('#undo .chip:has-text("Wrong finance field")'); pg.wait_for_timeout(150)
        banner = pg.locator(".suggestion")
        check("H suggestion appears after consistent feedback", banner.count() == 1, banner.inner_text()[:140] if banner.count() else "")
        check("H preferences unchanged until approval", json.dumps(doc(pg)["profiles"][doc(pg)["activeId"]]["prefs"]) == prefs_before)
        pg.screenshot(path=f"{SP}/suggestion.png")
        # --- I: approve, ranking changes, reversible
        if banner.count():
            pg.click('button:has-text("Apply suggestion")'); pg.wait_for_timeout(300)
            after = doc(pg)["profiles"][doc(pg)["activeId"]]
            check("I approval writes a revision", after["revisions"][0]["reason"] == "suggestion" and json.dumps(after["prefs"]) != prefs_before)
            pg.click('button:has-text("Edit target")'); pg.wait_for_timeout(300)
            check("I change is inspectable in history", "Applied a suggestion" in pg.inner_text("#view"))
            pg.click('button:has-text("undo this change")'); pg.wait_for_timeout(200)
            check("I undo restores prior preferences", json.dumps(doc(pg)["profiles"][doc(pg)["activeId"]]["prefs"]) == prefs_before)
            pg.click('button:has-text("Cancel")'); pg.wait_for_timeout(200)

    # --- J: resurfacing — simulate the posting's classification having changed since the dismissal
    tid = doc(pg)["activeId"]
    pg.evaluate("""(tid) => { const d = JSON.parse(localStorage.getItem('fintrack.targets.v1'));
      const [oid, dec] = Object.entries(d.decisions[tid]).find(([, x]) => x.state === 'dismissed' && x.fp);
      window.__res = oid; dec.fp.function = 'hr'; dec.fp.category = 'low'; localStorage.setItem('fintrack.targets.v1', JSON.stringify(d)); }""", tid)
    res = pg.evaluate("window.__res")
    pg.reload(); pg.wait_for_timeout(900)
    tab(pg, "Needs review")
    check("J changed dismissal resurfaces in Needs review", res in rows(pg))
    if res in rows(pg):
        pg.click(f'.feed-row[data-id="{res}"]'); pg.wait_for_timeout(150)
        check("J explained, dismissal kept", "Previously dismissed" in pg.inner_text("#detail") and doc(pg)["decisions"][tid][res]["state"] == "dismissed")
    check("reload keeps the active target", "Investment Banking" in pg.inner_text("#view-title"))

    # --- applied integrates with Pipeline and shows in every target
    tab(pg, "Top matches"); app_id = rows(pg)[0]
    pg.click(f'.feed-row[data-id="{app_id}"]'); pg.keyboard.press("a"); pg.wait_for_timeout(250)
    tr = json.loads(pg.evaluate("localStorage.getItem('fintrack.tracker.v1')"))
    check("applied creates one pipeline record", tr[app_id]["stage"] == "applied" and list(tr).count(app_id) == 1)
    check("applied leaves the queue", app_id not in rows(pg))
    tab(pg, "Saved & applied"); check("applied listed under Saved & applied", app_id in rows(pg))

    # review fixes: applied selects the next row; letters in a <select> do not act; table dismissals stay out of targets
    tab(pg, "Top matches")
    ids_now = rows(pg)
    pg.click(f'.feed-row[data-id="{ids_now[1]}"]'); pg.wait_for_timeout(100)
    pg.click('#detail button:has-text("Mark applied")'); pg.wait_for_timeout(400)
    check("applied moves selection to the next posting", pg.locator(".feed-row.selected").get_attribute("data-id") == ids_now[2])
    sel_id = pg.locator(".feed-row.selected").get_attribute("data-id")
    pg.focus("#detail .role-fix select"); pg.keyboard.press("a"); pg.wait_for_timeout(250)
    check("letters typed in the correction menu do not act", sel_id not in json.loads(pg.evaluate("localStorage.getItem('fintrack.tracker.v1')")))
    pg.click('.mode-switch button:has-text("All opportunities")'); pg.wait_for_timeout(300)
    pg.click(f'.grid tbody tr[data-id="{sel_id}"]'); pg.keyboard.press("x"); pg.wait_for_timeout(200)
    pg.click('.mode-switch button:has-text("For you")'); pg.wait_for_timeout(300)
    check("a table dismissal after targets exist stays out of For you", sel_id in rows(pg))
    pg.click('button:has-text("Edit target")'); pg.wait_for_timeout(200)
    pg.click(".target-menu > summary") if pg.locator(".target-menu").count() else None
    pg.once("dialog", lambda d: d.accept())
    pg.click('.form-actions button:has-text("Delete")'); pg.wait_for_timeout(400)
    check("deleting a target from the editor shows the next one", pg.locator(".feed-row").count() > 0 and "Asset Management" in pg.inner_text("#view-title"))

    # --- K: every other view still works
    for v in ["programs", "firms", "outreach", "pipeline", "deadlines", "sources", "profile"]:
        pg.goto(f"{URL}#{v}"); pg.wait_for_timeout(500)
        check(f"K {v} renders", pg.locator("#view").inner_text().strip() != "")
    pg.goto(f"{URL}#pipeline"); pg.wait_for_timeout(400)
    check("K applied role visible in Pipeline", app_id in pg.eval_on_selector_all(".grid tbody tr", "els => els.map(e => e.dataset.id)"))
    pg.goto(f"{URL}#discover"); pg.wait_for_timeout(500)
    pg.click('.mode-switch button:has-text("All opportunities")'); pg.wait_for_timeout(300)
    pg.click('details[data-key="countries"] > summary'); pg.click('details[data-key="countries"] .opt:has-text("Switzerland") input'); pg.wait_for_timeout(200)
    check("K All opportunities filters still work", pg.locator(".grid tbody tr").count() > 0 and "Clear filters" in pg.inner_text("#filters"))

    # --- responsive
    m = b.new_page(viewport={"width": 390, "height": 844})
    m.on("pageerror", lambda e: errs.append(str(e)))
    m.goto(URL)
    m.evaluate(f"localStorage.setItem('fintrack.targets.v1', {json.dumps(json.dumps(doc(pg)))})"); m.reload(); m.wait_for_timeout(900)
    check("mobile list shows without auto-open detail", m.locator(".feed-row").count() > 0 and m.locator("#detail[hidden]").count() == 1)
    m.locator(".feed-row").first.click(); m.wait_for_timeout(200)
    check("mobile opens full-screen detail", m.locator("#detail:not([hidden])").count() == 1)
    m.screenshot(path=f"{SP}/mobile.png")
    m.click(".detail-close"); m.wait_for_timeout(150)
    check("mobile close returns to the list", m.locator("#detail[hidden]").count() == 1)
    check("no page errors", not errs, str(errs[:3]))
    print(f"\n{sum(c for _, c in ok)}/{len(ok)} checks passed")
    b.close()

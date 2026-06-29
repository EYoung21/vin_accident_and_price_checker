# How to capture the HTML the tool needs (stat.vin + vincheck.info)

`stat.vin` (salvage auctions) and `vincheck.info` (NMVTIS title brands) render
their results with JavaScript behind bot protection. A plain fetch only gets the
empty shell, so the tool shows **❓ UNKNOWN** until you hand it the real page once.
Do this only for cars you're seriously considering — it takes ~30 seconds.

## The capture (do this for each site)

For a VIN like `WAUA7GFF8G1008045`:

### 1. stat.vin
- Open **https://stat.vin/cars/WAUA7GFF8G1008045** (if it blocks, go to
  https://stat.vin and search the VIN).
- Wait for the page to fully load (you should see the lot info / photos).

### 2. vincheck.info
- Open **https://vincheck.info/** and enter the VIN, click the check button.
- Wait for the green/red results to render.

### 3. Grab the RENDERED page (important — not "View Source")
"View Source" / Ctrl-U gives the pre-JavaScript shell, which is useless. Instead:

- Right-click anywhere on the page → **Inspect**
- In the **Elements** panel, right-click the very top `<html>` tag
- Choose **Copy → Copy outerHTML**

### 4. Save it where the tool looks (named by VIN)
Paste what you copied into a file named `<VIN>.html` in the matching folder:

```
automation_html/statvin/WAUA7GFF8G1008045.html
automation_html/vincheck/WAUA7GFF8G1008045.html
```

(Quickest: `pbpaste > automation_html/statvin/WAUA7GFF8G1008045.html` right after
copying, in Terminal.)

### 5. Re-run
```
vincheck
```
It **auto-detects** the file by VIN — no flags needed. The card now shows a real
title/salvage verdict, the auction record, and damage-photo links.

## Notes
- You only need to capture once per VIN; it's cached on disk afterward.
- A VIN that was never wrecked simply won't have a stat.vin record — that's fine.
- If you'd rather paste the HTML to Claude, just send both files and they'll be
  wired in for you.

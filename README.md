# MUBI UK Trending → MDBList → Aggregarr

This automatically mirrors the ranked MUBI UK Trending collection into a **public MDBList static list**. There is no Trakt dependency and no FlixPatrol.

**Every 3 hours:**

MUBI UK Trending → extract ranked films → TMDB match → update MDBList static list → Aggregarr

## One-time setup

### 1. Create one empty MDBList static list

On MDBList, create a **public static movie list** called `MUBI UK Trending`.

You only do this once. The workflow owns the contents from then on.


### 2. Get API keys

- TMDB API key: https://www.themoviedb.org/settings/api
- MDBList API key: MDBList → Preferences → API

MDBList's current API supports adding/removing items from static lists. The API is documented at https://api.mdblist.com/docs.

### 3. Put the repository on GitHub

Create a public GitHub repository and upload this folder.

### 4. Add exactly these GitHub Actions secrets

Repository → Settings → Secrets and variables → Actions:

- `TMDB_API_KEY`
- `MDBLIST_API_KEY`

### 5. Run it once

GitHub → Actions → **Update MUBI UK Trending** → Run workflow.

After it succeeds, your MDBList URL will be:

`https://mdblist.com/lists/YOUR_MDBLIST_USERNAME/mubi-uk-trending` (the exact slug may be shown by MDBList after you create the list)

Use that URL in Aggregarr.

## What it does

- Reads `https://mubi.com/en/gb/collections/trending`
- Preserves the order of the MUBI collection
- Matches titles to TMDB
- Refuses to update the list if fewer than 10 titles match, preventing a bad scrape from wiping the list
- Removes films that have left Trending
- Adds newly trending films
- Runs automatically every 3 hours
- Writes the latest successful snapshot to `data/latest.json`

## Important

The scraper intentionally does **not** substitute Trakt/TMDB popularity for MUBI's ranking. TMDB is used only to resolve MUBI titles to stable IDs so MDBList can store them.

import json, os, re, time, html
from pathlib import Path
import requests
import mubi_trending as m

JINA = 'https://r.jina.ai/'
STATE = Path('data/latest.json')

S = requests.Session()
S.headers.update({
    'User-Agent': 'Mozilla/5.0',
    'Accept-Language': 'en-GB,en;q=0.9',
})


def clean_title(raw):
    raw = html.unescape(' '.join((raw or '').split()).strip())
    # Remove parenthetical year and trailing MUBI metadata such as director names.
    raw = re.sub(r'\s*[\[(]?(?:19|20)\d{2}[\])]?(?:\s+.*)?$', '', raw, flags=re.I)
    raw = re.sub(r'\s+\(?\d{4}\)?\s*$', '', raw)
    return raw.strip(' -–—:') or raw


def jina_page_text(url):
    r = S.get(JINA + url, headers={'Accept': 'text/markdown', 'X-Return-Format': 'markdown'}, timeout=90)
    r.raise_for_status()
    return r.text or ''


def imdb_from_text(text):
    for pat in (
        r'https?://(?:www\.)?imdb\.com/title/(tt\d{7,8})',
        r'\b(tt\d{7,8})\b',
    ):
        m = re.search(pat, text or '', re.I)
        if m:
            return m.group(1)
    return None


def resolve_item(item):
    raw = item.get('raw_title') or item.get('title') or ''
    cleaned = clean_title(raw)
    year = item.get('mubi_year')

    # Exact IMDb mapping from the MUBI page is the strongest possible fallback.
    try:
        text = jina_page_text(item['url'])
        imdb = imdb_from_text(text)
        if imdb:
            hit = m.tmdb_find_imdb(imdb)
            if hit:
                return {
                    'tmdb_id': hit['id'],
                    'title': hit.get('title'),
                    'year': (hit.get('release_date') or '')[:4],
                    'match_method': 'mubi-page-imdb',
                }
    except Exception as exc:
        print(f'  Jina IMDb lookup failed for {raw}: {exc}')

    # Exact/clean-title TMDB searches, with the MUBI year as a scoring signal.
    queries = []
    for q in (cleaned, raw, re.sub(r'\s*\([^)]*\)$', '', raw).strip()):
        if q and q not in queries:
            queries.append(q)

    candidates = {}
    for q in queries:
        for result in m.tmdb_search(q):
            candidates[result['id']] = result
        for result in m.tmdb_multi(q):
            candidates[result['id']] = result
        if year:
            for result in m.tmdb_search(q, year):
                candidates[result['id']] = result

    if not candidates:
        return None

    target = m.norm(cleaned)
    scored = []
    for result in candidates.values():
        names = [result.get('title',''), result.get('original_title','')]
        norms = [m.norm(x) for x in names if x]
        exact = max((1 if n == target else 0 for n in norms), default=0)
        contains = max((1 if target and (target in n or n in target) else 0 for n in norms), default=0)
        sim = max((__import__('difflib').SequenceMatcher(None, target, n).ratio() for n in norms), default=0)
        release = (result.get('release_date') or '')[:4]
        result_year = int(release) if release.isdigit() else None
        year_exact = bool(year and result_year == year)
        year_diff = abs(result_year-year) if year and result_year else 99
        if year and result_year and year_diff > 3 and (exact or contains):
            continue
        score = (15000 if year_exact else 0, 9000 if exact else 0, 4000 if contains else 0, sim*1000, -year_diff, result.get('popularity', 0))
        scored.append((score, result))

    if not scored:
        return None
    scored.sort(key=lambda x: x[0], reverse=True)
    score, best = scored[0]
    release = (best.get('release_date') or '')[:4]
    if year and release.isdigit() and int(release) != year and score[1] == 0 and score[2] == 0 and score[3] < 920:
        return None
    if score[3] < 760 and score[1] == 0 and score[2] == 0:
        return None
    return {
        'tmdb_id': best['id'],
        'title': best.get('title'),
        'year': release,
        'match_method': 'clean-title-year' if year else 'clean-title',
    }


def main():
    if not STATE.exists():
        raise RuntimeError('data/latest.json does not exist; the main MUBI sync did not produce a snapshot.')
    data = json.loads(STATE.read_text(encoding='utf-8'))
    unresolved = data.get('unmatched') or []
    if not unresolved:
        print('No unresolved MUBI films. Nothing else to resolve.')
        return

    matched = data.get('items') or []
    newly = []
    still_unresolved = []
    for item in unresolved:
        hit = resolve_item(item)
        if hit:
            item.update(hit)
            newly.append(item)
            print(f"RESOLVED: {item.get('rank'):02d}. {item.get('title')} -> {hit['tmdb_id']} / {hit.get('title')} [{hit['match_method']}]")
        else:
            still_unresolved.append(item)
            print(f"STILL UNRESOLVED: {item.get('rank'):02d}. {item.get('title')}")

    if not newly:
        print('No additional titles were resolved; keeping the existing MDBList unchanged.')
        return

    matched.extend(newly)
    matched.sort(key=lambda x: x.get('rank', 10**9))
    for i, item in enumerate(matched, 1):
        item['output_rank'] = i

    # Rebuild the destination from the complete set so resolved titles land in the correct MUBI positions.
    m.replace_mdb_list(matched)

    data['items'] = matched
    data['unmatched'] = still_unresolved
    data['updated_at'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    STATE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'Unresolved pass added {len(newly)} films. Remaining unresolved: {len(still_unresolved)}.')


if __name__ == '__main__':
    main()

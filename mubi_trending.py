import os, re, json, time, html, unicodedata
from difflib import SequenceMatcher
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup

MUBI_URL = os.getenv('MUBI_URL', 'https://mubi.com/en/gb/collections/trending')
TMDB_API_KEY = os.environ['TMDB_API_KEY']
MDBLIST_API_KEY = os.environ['MDBLIST_API_KEY']
MDBLIST_LIST_ID = os.getenv('MDBLIST_LIST_ID', '')
MDBLIST_LIST_NAME = os.getenv('MDBLIST_LIST_NAME', 'MUBI UK Trending')
MAX_ITEMS = int(os.getenv('MAX_ITEMS', '50'))
MIN_MATCHES = int(os.getenv('MIN_MATCHES', '10'))
MDB_API = 'https://api.mdblist.com'

S = requests.Session()
S.headers.update({
    'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131 Safari/537.36',
    'Accept-Language': 'en-GB,en;q=0.9',
})


def norm(s):
    s = html.unescape(s or '')
    s = unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode().lower()
    s = re.sub(r'[^a-z0-9]+', ' ', s).strip()
    return re.sub(r'\s+', ' ', s)


def get_mubi_films():
    r = S.get(MUBI_URL, timeout=45)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, 'html.parser')
    films, seen = [], set()
    for a in soup.find_all('a', href=True):
        href = urljoin(MUBI_URL, a['href'])
        path = urlparse(href).path
        m = re.match(r'^/en/gb/films/([^/?#]+)', path)
        if not m:
            continue
        slug = m.group(1)
        if slug in seen:
            continue
        seen.add(slug)
        title = a.get('aria-label') or a.get('title') or a.get_text(' ', strip=True) or slug.replace('-', ' ')
        films.append({'rank': len(films) + 1, 'title': html.unescape(title).strip(), 'slug': slug, 'url': href})

    # Fallback for pages where film cards are embedded in JSON rather than ordinary anchors.
    if len(films) < 5:
        for m in re.finditer(r'https?://mubi\.com/en/gb/films/([A-Za-z0-9][^\"\'<>/?#]*)', r.text):
            slug = m.group(1)
            if slug not in seen:
                seen.add(slug)
                films.append({'rank': len(films) + 1, 'title': slug.replace('-', ' '), 'slug': slug,
                              'url': f'https://mubi.com/en/gb/films/{slug}'})

    if not films:
        raise RuntimeError('MUBI Trending returned no film URLs. Refusing to touch MDBList.')
    return films[:MAX_ITEMS]


def tmdb_search(title):
    r = S.get('https://api.themoviedb.org/3/search/movie', params={
        'api_key': TMDB_API_KEY,
        'query': title,
        'language': 'en-GB',
        'include_adult': 'false',
    }, timeout=30)
    r.raise_for_status()
    results = r.json().get('results', [])
    if not results:
        return None
    q = norm(title)

    def score(x):
        names = [x.get('title', ''), x.get('original_title', '')]
        sims = [SequenceMatcher(None, q, norm(n)).ratio() for n in names if n]
        best = max(sims, default=0)
        exact = 1 if any(norm(n) == q for n in names if n) else 0
        return exact, best, x.get('popularity', 0)

    results.sort(key=score, reverse=True)
    best = results[0]
    s = score(best)
    if s[0] == 0 and s[1] < 0.68:
        return None
    return {'tmdb_id': best['id'], 'title': best.get('title'), 'year': (best.get('release_date') or '')[:4]}


def mdb_params():
    return {'apikey': MDBLIST_API_KEY}


def resolve_list_id():
    if MDBLIST_LIST_ID:
        return str(MDBLIST_LIST_ID)
    r = S.get(f'{MDB_API}/lists/user', params=mdb_params(), timeout=30)
    if r.status_code >= 400:
        raise RuntimeError(f'MDBList user lists lookup failed: {r.status_code} {r.text[:1000]}')
    data = r.json()
    lists = data.get('lists', data if isinstance(data, list) else [])
    for item in lists:
        name = item.get('name') or item.get('title')
        if name == MDBLIST_LIST_NAME:
            lid = item.get('id') or item.get('list_id')
            if lid is not None:
                return str(lid)
    raise RuntimeError(f'Could not find an MDBList list named {MDBLIST_LIST_NAME!r}. Create one public static list with that exact name, then run again.')


def mdb_get_list():
    global MDBLIST_LIST_ID
    MDBLIST_LIST_ID = resolve_list_id()
    r = S.get(f'{MDB_API}/list', params={**mdb_params(), 'id': MDBLIST_LIST_ID}, timeout=30)
    if r.status_code >= 400:
        raise RuntimeError(f'MDBList GET list failed: {r.status_code} {r.text[:1000]}')
    return r.json()


def extract_existing_ids(data):
    ids = []
    for item in data.get('items', []):
        # MDBList responses have historically exposed imdb_id; newer payloads may expose tmdb_id/ids.
        imdb = item.get('imdb_id')
        tmdb = item.get('tmdb_id')
        if not tmdb and isinstance(item.get('ids'), dict):
            tmdb = item['ids'].get('tmdb')
        ids.append({'imdb_id': imdb, 'tmdb_id': tmdb})
    return ids


def mdb_add(tmdb_id):
    r = S.post(f'{MDB_API}/list/add', params=mdb_params(), json={'list_id': MDBLIST_LIST_ID, 'tmdb_id': tmdb_id}, timeout=30)
    if r.status_code >= 400:
        raise RuntimeError(f'MDBList add {tmdb_id} failed: {r.status_code} {r.text[:500]}')


def mdb_remove(item):
    payload = {'list_id': MDBLIST_LIST_ID}
    if item.get('imdb_id'):
        payload['imdb_id'] = item['imdb_id']
    elif item.get('tmdb_id'):
        payload['tmdb_id'] = item['tmdb_id']
    else:
        return
    r = S.post(f'{MDB_API}/list/remove', params=mdb_params(), json=payload, timeout=30)
    if r.status_code >= 400:
        raise RuntimeError(f'MDBList remove failed: {r.status_code} {r.text[:500]}')


def replace_mdb_list(movies):
    current = extract_existing_ids(mdb_get_list())
    wanted = [m['tmdb_id'] for m in movies]
    wanted_set = set(wanted)
    current_tmdb = {x['tmdb_id'] for x in current if x.get('tmdb_id')}

    # Remove anything no longer in MUBI Trending.
    for item in current:
        if item.get('tmdb_id') and item['tmdb_id'] not in wanted_set:
            mdb_remove(item)
            time.sleep(0.15)

    # Add in exact MUBI rank order. Static MDBList lists preserve insertion order.
    for tmdb_id in wanted:
        if tmdb_id not in current_tmdb:
            mdb_add(tmdb_id)
            time.sleep(0.15)


def main():
    raw = get_mubi_films()
    print(f'Extracted {len(raw)} ranked MUBI UK Trending films.')
    matched, unmatched = [], []
    for item in raw:
        try:
            hit = tmdb_search(item['title'])
            if hit:
                item.update(hit)
                matched.append(item)
                print(f"{item['rank']:02d}. {item['title']} -> {hit['tmdb_id']} / {hit['title']}")
            else:
                unmatched.append(item)
                print(f"NO MATCH: {item['rank']:02d}. {item['title']}")
        except Exception as e:
            unmatched.append(item)
            print(f"MATCH ERROR: {item['title']}: {e}")

    if len(matched) < MIN_MATCHES:
        raise RuntimeError(f'Only {len(matched)} titles matched TMDB; refusing to modify MDBList.')

    replace_mdb_list(matched)

    os.makedirs('data', exist_ok=True)
    snapshot = {
        'updated_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'source': MUBI_URL,
        'items': matched,
        'unmatched': unmatched,
        'mdblist_list_id': MDBLIST_LIST_ID,
    }
    with open('data/latest.json', 'w', encoding='utf-8') as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)
    print(f'Updated MDBList static list {MDBLIST_LIST_ID} with {len(matched)} titles.')


if __name__ == '__main__':
    main()

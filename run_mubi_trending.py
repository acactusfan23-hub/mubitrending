import os
import re
import time
import uuid
import html
import urllib.parse
import random
from html.parser import HTMLParser

import mubi_trending

MAX_ITEMS = mubi_trending.MAX_ITEMS
MUBI_URL = mubi_trending.MUBI_URL
JINA_BASE = mubi_trending.JINA_BASE
S = mubi_trending.S

MUBI_API = os.getenv('MUBI_API_URL', 'https://api.mubi.com/v4')
MUBI_COUNTRY = os.getenv('MUBI_COUNTRY', 'GB').upper()
RUN_DEVICE_ID = str(uuid.uuid4())


def api_headers():
    # Mirrors MUBI's anonymous web-client catalogue headers. Client-Country is the
    # important part here: the playable catalogue and its popularity order are
    # territory-specific.
    return {
        'Referer': 'https://mubi.com',
        'Origin': 'https://mubi.com',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) Gecko/20100101 Firefox/131.0',
        'Accept': 'application/json',
        'Client': 'web',
        'Client-Accept-Video-Codecs': 'h265,vp9,h264',
        'Client-Country': MUBI_COUNTRY,
        'Accept-Language': 'en-GB,en;q=0.9',
        'Anonymous_user_id': RUN_DEVICE_ID,
    }


def api_get(path, params=None, timeout=60):
    r = S.get(f'{MUBI_API}{path}', headers=api_headers(), params=params or {}, timeout=timeout)
    r.raise_for_status()
    return r.json()


def scrape_mubi_via_api():
    """Read MUBI GB's playable catalogue in MUBI popularity order.

    The public /collections/trending page is a popularity view, not a normal
    film_group. /browse/films?sort=popularity is therefore the appropriate
    country-aware API source. Series/episodes are skipped because the destination
    MDBList is a movie list; their presence on the webpage must not displace films.
    """
    items = []
    seen = set()
    page = 1

    while page and page <= 20 and len(items) < MAX_ITEMS:
        data = api_get('/browse/films', {
            'sort': 'popularity',
            'playable': 'true',
            'page': page,
            'per_page': 48,
        })
        films = data.get('films') or []
        meta = data.get('meta') or {}
        if not films:
            break

        before = len(items)
        skipped_series = 0
        for film in films:
            if not isinstance(film, dict):
                continue

            # MUBI's browse endpoint can include series episodes. The user's
            # Aggregarr destination is explicitly a movie list, so skip them while
            # preserving the relative order of the films around them.
            if film.get('episode') is not None or film.get('series') is not None:
                skipped_series += 1
                continue

            slug = str(film.get('slug') or '').strip()
            fid = film.get('id')
            dedupe_key = slug or str(fid or '')
            if not dedupe_key or dedupe_key in seen:
                continue
            seen.add(dedupe_key)

            raw_title = (film.get('title') or film.get('title_locale') or film.get('original_title') or slug.replace('-', ' ')).strip()
            year = film.get('year') if isinstance(film.get('year'), int) else None
            items.append({
                'rank': len(items) + 1,
                'title': raw_title,
                'raw_title': raw_title,
                'mubi_year': year,
                'slug': slug,
                'mubi_id': fid,
                'mubi_popularity': film.get('popularity'),
                'url': film.get('web_url') or f'https://mubi.com/en/gb/films/{urllib.parse.quote(slug)}',
            })
            if len(items) >= MAX_ITEMS:
                break

        print(
            f'MUBI GB popularity API: page {page}; received {len(films)}; '
            f'skipped series/episodes {skipped_series}; added {len(items)-before}; total {len(items)}'
        )

        next_page = meta.get('next_page')
        if len(items) >= MAX_ITEMS or not next_page or len(items) == before:
            break
        page = int(next_page)

    if len(items) < 20:
        raise RuntimeError(f'MUBI GB popularity API returned only {len(items)} movies; refusing to trust it.')

    print('MUBI source ranking captured (DIRECT GB POPULARITY API):')
    for item in items[:20]:
        suffix = f" ({item['mubi_year']})" if item.get('mubi_year') else ''
        print(f"  {item['rank']:02d}. {item['title']}{suffix}")
    return items[:MAX_ITEMS]


# --- Existing proven Jina scraper retained unchanged as fallback only. ---
class AnchorParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.items = []
        self._anchor = None
        self._parts = []
        self._alt = ''
    def handle_starttag(self, tag, attrs):
        if tag.lower() != 'a':
            if self._anchor is not None and tag.lower() == 'img':
                amap = dict(attrs)
                self._alt = amap.get('alt') or self._alt
            return
        href = dict(attrs).get('href') or ''
        if '/films/' in href:
            self._anchor = href
            self._parts = []
            self._alt = ''
    def handle_data(self, data):
        if self._anchor is not None and data.strip():
            self._parts.append(data.strip())
    def handle_endtag(self, tag):
        if tag.lower() == 'a' and self._anchor is not None:
            href = html.unescape(self._anchor)
            m = re.search(r'/films/([^/?#]+)', href)
            if m:
                slug = urllib.parse.unquote(m.group(1))
                title = ' '.join(self._parts).strip() or self._alt or slug.replace('-', ' ')
                self.items.append((slug, html.unescape(title), href))
            self._anchor = None
            self._parts = []
            self._alt = ''


def parse_jina_html(text):
    parser = AnchorParser()
    parser.feed(text or '')
    out = []
    seen = set()
    for slug, title, url in parser.items:
        if slug in seen:
            continue
        seen.add(slug)
        clean, year = mubi_trending.title_and_year(title)
        out.append({
            'slug': slug,
            'title': clean,
            'raw_title': title,
            'mubi_year': year,
            'url': url if url.startswith('http') else f'https://mubi.com/en/gb/films/{slug}',
        })
        if len(out) >= MAX_ITEMS:
            break
    return out


def dedupe(seq):
    out = []
    seen = set()
    for item in seq:
        if item['slug'] in seen:
            continue
        seen.add(item['slug'])
        out.append(item)
    return out


def merge_sources(html_items, md_items):
    md_items = dedupe(md_items)
    md_slugs = {x['slug'] for x in md_items}
    recovered = [x for x in dedupe(html_items) if x['slug'] not in md_slugs]
    return md_items + recovered


def get_jina(reader, accept, outfmt, page_num, attempt):
    headers = {'Accept': accept, 'X-Return-Format': outfmt}
    try:
        r = S.get(reader, headers=headers, timeout=90)
        if r.status_code in (429, 500, 502, 503, 504):
            print(f'  Jina {outfmt} attempt {attempt}/4 returned {r.status_code}; retrying...')
            return None
        r.raise_for_status()
        return r
    except Exception as exc:
        if attempt < 4:
            print(f'  Jina {outfmt} attempt {attempt}/4 failed: {exc}; retrying...')
        else:
            print(f'  Jina {outfmt} attempt {attempt}/4 failed permanently: {exc}')
        return None


def scrape_mubi_via_jina_fallback():
    items = []
    seen = set()
    cache_buster = int(time.time())
    for page_num in range(1, 11):
        target = f'{MUBI_URL}?page={page_num}&cb={cache_buster}'
        reader = JINA_BASE.rstrip('/') + '/' + target
        print(f'MUBI Jina FALLBACK: page {page_num}: {target}')
        md_resp = None
        for attempt in range(1, 5):
            md_resp = get_jina(reader, 'text/markdown', 'markdown', page_num, attempt)
            if md_resp is not None:
                break
            time.sleep(2 ** (attempt - 1) + random.random())
        if md_resp is None:
            raise RuntimeError(f'Jina fallback unavailable for page {page_num}. Refusing to modify MDBList.')
        batch = mubi_trending.parse_mubi_markdown(md_resp.text or '')
        before = len(items)
        for x in batch:
            if x['slug'] in seen:
                continue
            seen.add(x['slug'])
            x['rank'] = len(items) + 1
            items.append(x)
            if len(items) >= MAX_ITEMS:
                break
        print(f'  page {page_num}: added {len(items)-before}; total {len(items)}')
        if len(items) >= MAX_ITEMS or len(items) == before:
            break
    if not items:
        raise RuntimeError('Jina fallback returned no MUBI film links.')
    print('MUBI source ranking captured (Jina FALLBACK):')
    for item in items[:15]:
        print(f"  {item['rank']:02d}. {item['title']}")
    return items[:MAX_ITEMS]


def improved_source():
    try:
        return scrape_mubi_via_api()
    except Exception as api_exc:
        print(f'MUBI GB POPULARITY API unavailable: {api_exc}')
        print('Falling back to the existing Jina source; no MDBList update occurs until downstream safety checks pass.')
        return scrape_mubi_via_jina_fallback()


mubi_trending.scrape_mubi_web = improved_source
mubi_trending.main()

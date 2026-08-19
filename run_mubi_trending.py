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


def api_headers():
    # MUBI's documented web API uses these browser-like headers for catalogue/collection browsing.
    return {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131 Safari/537.36',
        'Accept': 'application/json',
        'Accept-Language': 'en-GB,en;q=0.9',
        'Client': 'web',
        'Client-Country': MUBI_COUNTRY,
        'Client-Accept-Audio-Codecs': 'eac3,aac',
        'Client-Accept-Video-Codecs': 'h265,vp9,h264',
        'Anonymous_user_id': str(uuid.uuid4()),
        'Origin': 'https://mubi.com',
        'Referer': 'https://mubi.com/',
    }


def norm(s):
    return re.sub(r'[^a-z0-9]+', ' ', html.unescape(s or '').lower()).strip()


def api_get(path, params=None, timeout=60):
    r = S.get(f'{MUBI_API}{path}', headers=api_headers(), params=params or {}, timeout=timeout)
    r.raise_for_status()
    return r.json()


def find_trending_group():
    """Find the MUBI collection actually named Trending through MUBI's collection API."""
    hits = []
    for page in range(1, 11):
        data = api_get('/browse/film_groups', {
            'sort': 'title',
            'page': page,
            'per_page': 100,
        })
        groups = data.get('film_groups') or []
        if not groups:
            break
        for g in groups:
            title = g.get('title') or ''
            full = g.get('full_title') or ''
            key_title = norm(title)
            key_full = norm(full)
            if key_title == 'trending' or key_full == 'trending' or key_full.endswith(' trending'):
                hits.append(g)
        meta = data.get('meta') or {}
        if not meta.get('next_page') and page > 1:
            break
    if not hits:
        raise RuntimeError('MUBI API returned no film group named Trending for country ' + MUBI_COUNTRY)
    # Prefer an exact title match over a longer descriptive full title.
    hits.sort(key=lambda g: (norm(g.get('title')) != 'trending', len(g.get('full_title') or '')))
    group = hits[0]
    print(f"MUBI API: found Trending collection id={group.get('id')} title={group.get('title')!r} full_title={group.get('full_title')!r} country={MUBI_COUNTRY}")
    return group


def scrape_mubi_via_api():
    group = find_trending_group()
    group_id = group.get('id')
    if group_id is None:
        raise RuntimeError('MUBI Trending collection had no id.')

    items = []
    seen = set()
    for page in range(1, 11):
        data = api_get(f'/film_groups/{group_id}/film_group_items', {
            'include_upcoming': 'true',
            'page': page,
            'per_page': 24,
        })
        batch = data.get('film_group_items') or []
        if not batch:
            break
        before = len(items)
        for entry in batch:
            film = entry.get('film') if isinstance(entry, dict) else None
            if not isinstance(film, dict):
                continue
            slug = str(film.get('slug') or '').strip()
            if not slug or slug in seen:
                continue
            seen.add(slug)
            raw_title = (film.get('title') or film.get('title_locale') or film.get('original_title') or slug.replace('-', ' ')).strip()
            clean, year = mubi_trending.title_and_year(raw_title)
            # API film objects normally include the year separately, which is preferable to parsing card text.
            api_year = film.get('year')
            if isinstance(api_year, int):
                year = api_year
            items.append({
                'rank': len(items) + 1,
                'title': clean,
                'raw_title': raw_title,
                'mubi_year': year,
                'slug': slug,
                'url': film.get('web_url') or f'https://mubi.com/en/gb/films/{urllib.parse.quote(slug)}',
            })
            if len(items) >= MAX_ITEMS:
                break
        print(f'MUBI API Trending: page {page}; received {len(batch)}; added {len(items)-before}; total {len(items)}')
        meta = data.get('meta') or {}
        if len(items) >= MAX_ITEMS or not meta.get('next_page') or len(items) == before:
            break
    if not items:
        raise RuntimeError('MUBI API Trending returned no films.')
    print('MUBI source ranking captured (DIRECT API, GB):')
    for item in items[:15]:
        print(f"  {item['rank']:02d}. {item['title']}")
    return items[:MAX_ITEMS]


# --- Existing Jina scraper retained as a fallback only. ---
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
        print(f'MUBI DIRECT API unavailable: {api_exc}')
        print('Falling back to the existing Jina source without changing the matching/update pipeline.')
        return scrape_mubi_via_jina_fallback()


mubi_trending.scrape_mubi_web = improved_source
mubi_trending.main()

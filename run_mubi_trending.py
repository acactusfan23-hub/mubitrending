import os, re, html, urllib.parse, time, random, uuid
from html.parser import HTMLParser
import mubi_trending

MAX_ITEMS = mubi_trending.MAX_ITEMS
MUBI_URL = mubi_trending.MUBI_URL
JINA_BASE = mubi_trending.JINA_BASE
S = mubi_trending.S
MUBI_API = os.getenv('MUBI_API_URL', 'https://api.mubi.com/v4')
MUBI_COUNTRY = os.getenv('MUBI_COUNTRY', 'GB').upper()


def mubi_api_headers():
    return {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) Gecko/20100101 Firefox/131.0',
        'Accept': 'application/json',
        'Accept-Language': 'en-GB,en;q=0.9',
        'Client': 'web',
        'Client-Country': MUBI_COUNTRY,
        'Client-Accept-Video-Codecs': 'h265,vp9,h264',
        'Anonymous_user_id': str(uuid.uuid4()),
        'Origin': 'https://mubi.com',
        'Referer': 'https://mubi.com/en/gb/collections/trending',
    }


def scrape_exact_mubi_collection():
    """Read the exact public MUBI /collections/trending collection feed.

    /v4/collections/trending/films is the full feed behind the collection page.
    We preserve its source order and skip rows MUBI identifies as a series/episode,
    because the destination MDBList is movie-only.
    """
    source_rows = []
    page = 1
    seen_source = set()

    while page <= 10 and len(source_rows) < MAX_ITEMS:
        r = S.get(
            f'{MUBI_API}/collections/trending/films',
            headers=mubi_api_headers(),
            params={'page': page, 'per_page': 100, 'limit': 100},
            timeout=60,
        )
        r.raise_for_status()
        data = r.json()
        rows = data.get('films') or []
        if not isinstance(rows, list) or not rows:
            break

        before = len(source_rows)
        for film in rows:
            if not isinstance(film, dict):
                continue
            key = str(film.get('id') or film.get('slug') or '')
            if not key or key in seen_source:
                continue
            seen_source.add(key)
            source_rows.append(film)
            if len(source_rows) >= MAX_ITEMS:
                break

        meta = data.get('meta') or {}
        print(f"MUBI exact Trending feed: API page {page}; received {len(rows)}; source items {len(source_rows)}/{MAX_ITEMS}")
        nxt = meta.get('next_page')
        if len(source_rows) >= MAX_ITEMS or len(source_rows) == before or not nxt:
            break
        page = int(nxt)

    # Never trust a mysteriously truncated response. The known collection contains
    # 100 items; requiring 80 keeps a transient/API-shape failure away from MDBList.
    if len(source_rows) < min(80, MAX_ITEMS):
        raise RuntimeError(f'Exact MUBI Trending feed returned only {len(source_rows)} source items.')

    movies = []
    skipped = []
    for source_rank, film in enumerate(source_rows[:MAX_ITEMS], 1):
        title = (film.get('title') or film.get('title_locale') or film.get('original_title') or film.get('slug') or '').strip()
        if not title:
            continue

        # MUBI can expose a series card through an episode-like Film object (e.g.
        # Twin Peaks). Do not let that enter a movie-only MDBList.
        if film.get('episode') is not None or film.get('series') is not None:
            skipped.append((source_rank, title))
            print(f'SKIP SERIES/EPISODE: source #{source_rank:02d}. {title}')
            continue

        slug = str(film.get('slug') or '').strip()
        year = film.get('year') if isinstance(film.get('year'), int) else None
        movies.append({
            'rank': len(movies) + 1,
            'source_rank': source_rank,
            'title': title,
            'raw_title': title,
            'mubi_year': year,
            'slug': slug,
            'mubi_id': film.get('id'),
            'url': film.get('web_url') or (f'https://mubi.com/en/gb/films/{urllib.parse.quote(slug)}' if slug else ''),
        })

    if len(movies) < 70:
        raise RuntimeError(f'Only {len(movies)} movie rows remained after filtering series/episodes.')

    print(f'MUBI exact Trending source captured: {len(source_rows)} collection items, {len(movies)} movies, {len(skipped)} series/episodes skipped.')
    print('MUBI movie ranking captured from exact /collections/trending/films feed:')
    for item in movies[:30]:
        suffix = f" ({item['mubi_year']})" if item.get('mubi_year') else ''
        print(f"  {item['rank']:02d}. {item['title']}{suffix} [MUBI source #{item['source_rank']}]")
    return movies


# -----------------------------------------------------------------------------
# Stable Jina implementation retained ONLY as a fallback if the exact MUBI feed
# is unavailable. This is the version that previously captured 100 items safely.
# -----------------------------------------------------------------------------
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
                amap = dict(attrs); self._alt = amap.get('alt') or self._alt
            return
        href = dict(attrs).get('href') or ''
        if '/films/' in href:
            self._anchor = href; self._parts = []; self._alt = ''
    def handle_data(self, data):
        if self._anchor is not None and data.strip(): self._parts.append(data.strip())
    def handle_endtag(self, tag):
        if tag.lower() == 'a' and self._anchor is not None:
            href = html.unescape(self._anchor); m = re.search(r'/films/([^/?#]+)', href)
            if m:
                slug = urllib.parse.unquote(m.group(1)); title = ' '.join(self._parts).strip() or self._alt or slug.replace('-', ' ')
                self.items.append((slug, html.unescape(title), href))
            self._anchor = None; self._parts = []; self._alt = ''


def parse_jina_html(text):
    parser=AnchorParser(); parser.feed(text or '')
    out=[]; seen=set()
    for slug,title,url in parser.items:
        if slug in seen: continue
        seen.add(slug); clean,year=mubi_trending.title_and_year(title)
        out.append({'slug':slug,'title':clean,'raw_title':title,'mubi_year':year,'url':url if url.startswith('http') else f'https://mubi.com/en/gb/films/{slug}'})
        if len(out)>=MAX_ITEMS: break
    return out


def dedupe(seq):
    out=[]; seen=set()
    for item in seq:
        if item['slug'] in seen: continue
        seen.add(item['slug']); out.append(item)
    return out


def merge_sources(html_items, md_items):
    md_items=dedupe(md_items); md_slugs={x['slug'] for x in md_items}
    recovered=[x for x in dedupe(html_items) if x['slug'] not in md_slugs]
    return md_items + recovered


def get_jina(reader, accept, outfmt, page_num, attempt):
    headers={'Accept':accept,'X-Return-Format':outfmt}
    try:
        r=S.get(reader,headers=headers,timeout=90)
        if r.status_code in (429,500,502,503,504):
            print(f'  Jina {outfmt} attempt {attempt}/4 returned {r.status_code}; retrying...')
            return None
        r.raise_for_status(); return r
    except Exception as exc:
        if attempt<4: print(f'  Jina {outfmt} attempt {attempt}/4 failed: {exc}; retrying...')
        else: print(f'  Jina {outfmt} attempt {attempt}/4 failed permanently: {exc}')
        return None


def scrape_stable_jina_fallback():
    all_items=[]; seen=set()
    for page_num in range(1,11):
        target=f'{MUBI_URL}?page={page_num}'; reader=JINA_BASE.rstrip('/')+'/'+target
        print(f'MUBI Jina FALLBACK: page {page_num}: {target}')

        md_resp=None
        for attempt in range(1,5):
            md_resp=get_jina(reader,'text/markdown','markdown',page_num,attempt)
            if md_resp is not None: break
            time.sleep(2**(attempt-1) + random.random())
        html_resp=None
        for attempt in range(1,4):
            html_resp=get_jina(reader,'text/html','html',page_num,attempt)
            if html_resp is not None: break
            time.sleep(2**(attempt-1) + random.random())
        if md_resp is None:
            raise RuntimeError(f'Jina Markdown unavailable for page {page_num} after retries.')

        md_text=md_resp.text or ''; md_items=mubi_trending.parse_mubi_markdown(md_text)
        html_items=parse_jina_html(html_resp.text or '') if html_resp is not None else []
        page_items=merge_sources(html_items,md_items) if html_items else dedupe(md_items)
        before=len(all_items)
        for item in page_items:
            if item['slug'] in seen: continue
            seen.add(item['slug']); item['rank']=len(all_items)+1; all_items.append(item)
            if len(all_items)>=MAX_ITEMS: break
        print(f'  page {page_num}: added {len(all_items)-before}; total {len(all_items)}')
        if len(all_items)>=MAX_ITEMS or len(all_items)==before: break
    if not all_items: raise RuntimeError('Stable Jina fallback returned no films.')
    print('WARNING: using Jina fallback instead of exact MUBI collection API.')
    return all_items[:MAX_ITEMS]


def improved_source():
    try:
        return scrape_exact_mubi_collection()
    except Exception as exc:
        print(f'EXACT MUBI TRENDING FEED unavailable: {exc}')
        print('Falling back to the previous stable Jina source; downstream safety checks remain active.')
        return scrape_stable_jina_fallback()


mubi_trending.scrape_mubi_web=improved_source
mubi_trending.main()

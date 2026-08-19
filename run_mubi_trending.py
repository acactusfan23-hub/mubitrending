import re, html, urllib.parse
from html.parser import HTMLParser
import mubi_trending

MAX_ITEMS = mubi_trending.MAX_ITEMS
MUBI_URL = mubi_trending.MUBI_URL
JINA_BASE = mubi_trending.JINA_BASE
S = mubi_trending.S

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
        amap = dict(attrs)
        href = amap.get('href') or ''
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
    out=[]; seen=set()
    for slug,title,url in parser.items:
        if slug in seen: continue
        seen.add(slug)
        clean, year = mubi_trending.title_and_year(title)
        out.append({'slug':slug,'title':clean,'raw_title':title,'mubi_year':year,'url':url if url.startswith('http') else f'https://mubi.com/en/gb/films/{slug}'})
        if len(out) >= MAX_ITEMS: break
    return out

def dedupe(seq):
    out=[]; seen=set()
    for item in seq:
        if item['slug'] in seen: continue
        seen.add(item['slug']); out.append(item)
    return out

def merge_sources(html_items, md_items):
    html_items=dedupe(html_items)
    md_items=dedupe(md_items)
    html_slugs={x['slug'] for x in html_items}
    recovered=[x for x in md_items if x['slug'] not in html_slugs]
    return html_items + recovered

def improved_source():
    all_items=[]
    seen=set()
    for page_num in range(1,11):
        target=f'{MUBI_URL}?page={page_num}'
        reader=JINA_BASE.rstrip('/') + '/' + target
        print(f'MUBI via Jina (fresh browser): page {page_num}: {target}')
        print(f'  reader URL: {reader}')

        fresh_headers={
            'X-No-Cache':'true',
            'X-Engine':'browser',
            'X-Return-Format':'markdown',
            'X-Wait-For-Selector':'a[href*="/films/"]',
            'Accept':'text/markdown',
        }
        md_resp=S.get(reader,headers=fresh_headers,timeout=120)
        md_resp.raise_for_status()
        md_text=md_resp.text or ''
        md_items=mubi_trending.parse_mubi_markdown(md_text)

        html_items=[]
        try:
            html_headers={
                'X-No-Cache':'true',
                'X-Engine':'browser',
                'X-Return-Format':'html',
                'X-Wait-For-Selector':'a[href*="/films/"]',
                'Accept':'text/html',
            }
            html_resp=S.get(reader,headers=html_headers,timeout=120)
            html_resp.raise_for_status()
            html_text=html_resp.text or ''
            html_items=parse_jina_html(html_text)
            print(f'  fresh markdown chars={len(md_text)}, markdown films={len(md_items)}; fresh html chars={len(html_text)}, html films={len(html_items)}')
        except Exception as exc:
            print(f'  HTML representation unavailable ({exc}); using fresh Markdown only.')
            print(f'  fresh markdown chars={len(md_text)}, markdown films={len(md_items)}')

        page_items=merge_sources(html_items,md_items) if html_items else dedupe(md_items)
        before=len(all_items)
        for item in page_items:
            if item['slug'] in seen: continue
            seen.add(item['slug'])
            item['rank']=len(all_items)+1
            all_items.append(item)
            if len(all_items)>=MAX_ITEMS: break
        print(f'  page {page_num}: extracted {len(page_items)}; added {len(all_items)-before}; total {len(all_items)}')
        if len(all_items)>=MAX_ITEMS or len(all_items)==before: break

    if not all_items:
        raise RuntimeError('Fresh MUBI extraction returned no film links.')
    print('MUBI source ranking captured (fresh browser):')
    for item in all_items[:15]:
        print(f"  {item['rank']:02d}. {item['title']}")
    return all_items[:MAX_ITEMS]

# Only replace source extraction. Everything downstream remains unchanged.
mubi_trending.scrape_mubi_web = improved_source
mubi_trending.main()

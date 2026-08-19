import re, html, urllib.parse
from html.parser import HTMLParser
from difflib import SequenceMatcher
import requests
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

def merge_sources(html_items, md_items):
    # Prefer HTML ordering because it is closest to the document/card order.
    # Markdown fills any films that Jina omitted from its HTML representation.
    by_slug = {}
    for seq, weight in ((html_items, 0.78), (md_items, 0.22)):
        n=max(len(seq),1)
        for i,item in enumerate(seq):
            slug=item['slug']
            rec=by_slug.setdefault(slug, {'item':item.copy(),'positions':[]})
            rec['positions'].append((i/(n-1) if n>1 else 0.0, weight))
            if seq is html_items:
                rec['item'].update(item)
    ranked=[]
    for slug,rec in by_slug.items():
        positions=rec['positions']
        weighted=sum(p*w for p,w in positions)/sum(w for _,w in positions)
        ranked.append((weighted, slug, rec['item']))
    ranked.sort(key=lambda x:x[0])
    return [x[2] for x in ranked[:MAX_ITEMS]]

def improved_source():
    all_items=[]
    seen=set()
    for page_num in range(1,11):
        target=f'{MUBI_URL}?page={page_num}'
        reader=JINA_BASE.rstrip('/') + '/' + target
        print(f'MUBI via Jina (dual format): page {page_num}: {target}')
        print(f'  reader URL: {reader}')

        # Existing Markdown representation, retained as the fallback/source we know works.
        md_resp=S.get(reader,headers={'Accept':'text/markdown','X-Return-Format':'markdown'},timeout=90)
        md_resp.raise_for_status()
        md_text=md_resp.text or ''
        md_items=mubi_trending.parse_mubi_markdown(md_text)

        # Ask Jina for HTML as a second independent representation of the same page.
        html_items=[]
        try:
            html_resp=S.get(reader,headers={'Accept':'text/html','X-Return-Format':'html'},timeout=90)
            html_resp.raise_for_status()
            html_text=html_resp.text or ''
            html_items=parse_jina_html(html_text)
            print(f'  markdown chars={len(md_text)}, markdown films={len(md_items)}; html chars={len(html_text)}, html films={len(html_items)}')
        except Exception as exc:
            print(f'  HTML representation unavailable ({exc}); using Markdown only.')
            print(f'  markdown chars={len(md_text)}, markdown films={len(md_items)}')

        page_items=merge_sources(html_items,md_items) if html_items else [dict(x) for x in md_items]
        before=len(all_items)
        for item in page_items:
            if item['slug'] in seen: continue
            seen.add(item['slug'])
            item['rank']=len(all_items)+1
            all_items.append(item)
            if len(all_items)>=MAX_ITEMS: break
        print(f'  page {page_num}: reconciled {len(page_items)}; added {len(all_items)-before}; total {len(all_items)}')
        if len(all_items)>=MAX_ITEMS or len(all_items)==before: break

    if not all_items:
        raise RuntimeError('Dual-format MUBI extraction returned no film links.')
    print('MUBI source ranking captured (dual-format):')
    for item in all_items[:15]:
        print(f"  {item['rank']:02d}. {item['title']}")
    return all_items[:MAX_ITEMS]

# Monkey-patch only the source extraction. Everything downstream remains the existing,
# already-working matcher, unresolved resolver and MDBList updater.
mubi_trending.scrape_mubi_web = improved_source
mubi_trending.main()

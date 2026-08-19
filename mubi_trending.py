import os, re, json, time, html, unicodedata
from difflib import SequenceMatcher
import requests
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

MUBI_URL = os.getenv('MUBI_URL', 'https://mubi.com/en/gb/collections/trending')
TMDB_API_KEY = os.environ['TMDB_API_KEY']
MDBLIST_API_KEY = os.environ['MDBLIST_API_KEY']
MDBLIST_LIST_ID = os.getenv('MDBLIST_LIST_ID', '')
MDBLIST_LIST_NAME = os.getenv('MDBLIST_LIST_NAME', 'MUBI UK Trending')
MAX_ITEMS = int(os.getenv('MAX_ITEMS', '100'))
MIN_MATCHES = int(os.getenv('MIN_MATCHES', '80'))
MDB_API = 'https://api.mdblist.com'

S = requests.Session()
S.headers.update({'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131 Safari/537.36','Accept-Language':'en-GB,en;q=0.9'})

def norm(s):
    s=html.unescape(s or '')
    s=unicodedata.normalize('NFKD',s).encode('ascii','ignore').decode().lower()
    return re.sub(r'\s+',' ',re.sub(r'[^a-z0-9]+',' ',s).strip())

def variants(title):
    vals=[title, unicodedata.normalize('NFKD',title).encode('ascii','ignore').decode(), title.replace(':',' '), title.replace('’',"'")]
    out=[]; seen=set()
    for v in vals:
        v=' '.join(v.split()).strip(); k=norm(v)
        if k and k not in seen: seen.add(k); out.append(v)
    return out

def scrape_mubi_web():
    items=[]; seen=set()
    with sync_playwright() as p:
        browser=p.chromium.launch(channel='chrome',headless=True)
        context=browser.new_context(locale='en-GB',extra_http_headers={'Accept-Language':'en-GB,en;q=0.9'})
        page=context.new_page()
        for page_num in range(1,11):
            url=f'{MUBI_URL}?page={page_num}'
            print(f'MUBI webpage: loading page {page_num}: {url}')
            page.goto(url,wait_until='domcontentloaded',timeout=90000)
            try: page.wait_for_load_state('networkidle',timeout=20000)
            except PlaywrightTimeoutError: pass
            page.wait_for_timeout(3000)
            for selector in ['button:has-text("Accept all")','button:has-text("Accept")','button:has-text("Allow all")','button:has-text("I agree")']:
                try: page.locator(selector).first.click(timeout=1200); break
                except Exception: pass
            for _ in range(8): page.mouse.wheel(0,4500); page.wait_for_timeout(500)
            links=page.locator('a[href*="/films/"]').evaluate_all("""
                els=>els.map(a=>({href:a.href,text:(a.innerText||a.textContent||'').trim(),aria:a.getAttribute('aria-label')||'',title:a.getAttribute('title')||'',visible:!!(a.offsetWidth||a.offsetHeight||a.getClientRects().length)})).filter(x=>x.visible)
            """)
            before=len(items)
            for x in links:
                m=re.search(r'https?://mubi\.com/(?:en/)?(?:[a-z]{2}/)?films/([^/?#]+)$',x.get('href',''))
                if not m: continue
                slug=m.group(1)
                if slug in seen: continue
                title=(x.get('aria') or x.get('title') or x.get('text') or '').strip() or slug.replace('-',' ')
                seen.add(slug); items.append({'rank':len(items)+1,'title':html.unescape(title),'slug':slug,'url':x['href']})
                if len(items)>=MAX_ITEMS: break
            print(f'  page {page_num}: added {len(items)-before}; total {len(items)}')
            if len(items)>=MAX_ITEMS or len(items)==before: break
        browser.close()
    if not items: raise RuntimeError('MUBI webpage returned no film links. Refusing to modify MDBList.')
    print('MUBI webpage ranking captured:')
    for x in items[:10]: print(f"  {x['rank']:02d}. {x['title']}")
    return items[:MAX_ITEMS]

def tmdb_search(q):
    r=S.get('https://api.themoviedb.org/3/search/movie',params={'api_key':TMDB_API_KEY,'query':q,'include_adult':'false'},timeout=30); r.raise_for_status(); return r.json().get('results',[])

def tmdb_multi(q):
    r=S.get('https://api.themoviedb.org/3/search/multi',params={'api_key':TMDB_API_KEY,'query':q,'include_adult':'false'},timeout=30); r.raise_for_status(); return [x for x in r.json().get('results',[]) if x.get('media_type')=='movie']

def tmdb_find_imdb(imdb):
    if not imdb:return None
    r=S.get(f'https://api.themoviedb.org/3/find/{imdb}',params={'api_key':TMDB_API_KEY,'external_source':'imdb_id'},timeout=30)
    if not r.ok:return None
    rows=r.json().get('movie_results',[]); return rows[0] if rows else None

def choose_tmdb(item):
    qs=[]
    for t in variants(item['title']):
        if t not in qs: qs.append(t)
    candidates={}
    for q in qs:
        for r in tmdb_search(q): candidates[r['id']]=r
        for r in tmdb_multi(q): candidates[r['id']]=r
    if not candidates:return None
    qnorm=[norm(x) for x in qs]; scored=[]
    for r in candidates.values():
        names=[r.get('title',''),r.get('original_title','')]; nn=[norm(x) for x in names if x]
        exact=max((1 if n in qnorm else 0 for n in nn),default=0)
        sim=max((SequenceMatcher(None,a,b).ratio() for a in qnorm for b in nn),default=0)
        score=(5000 if exact else 0,sim*1000,r.get('popularity',0))
        scored.append((score,r))
    scored.sort(key=lambda x:x[0],reverse=True); best=scored[0][1]
    return {'tmdb_id':best['id'],'title':best.get('title'),'year':(best.get('release_date') or '')[:4],'match_method':'title'}

def enrich_mubi_page(item,page):
    try:
        page.goto(item['url'],wait_until='domcontentloaded',timeout=60000)
        try: page.wait_for_load_state('networkidle',timeout=12000)
        except PlaywrightTimeoutError: pass
        page.wait_for_timeout(1000)
        hrefs=page.locator('a[href*="imdb.com/title/"]').evaluate_all('els=>els.map(a=>a.href)')
        for h in hrefs:
            m=re.search(r'(tt\d{7,8})',h)
            if m:
                hit=tmdb_find_imdb(m.group(1))
                if hit:return hit
        return None
    except Exception: return None

def match_all(items):
    matched=[]; unresolved=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(channel='chrome',headless=True)
        page=browser.new_page(locale='en-GB')
        for item in items:
            hit=choose_tmdb(item)
            if hit:
                item.update(hit); matched.append(item); print(f"{item['rank']:02d}. {item['title']} -> {hit['tmdb_id']} / {hit['title']} [{hit['match_method']}]"); continue
            hit=enrich_mubi_page(item,page)
            if hit:
                item.update({'tmdb_id':hit['id'],'title':hit.get('title'),'year':(hit.get('release_date') or '')[:4],'match_method':'mubi-imdb'}); matched.append(item); print(f"{item['rank']:02d}. {item['title']} -> {hit['id']} / {hit.get('title')} [mubi-imdb]")
            else:
                unresolved.append(item); print(f"NO MATCH: {item['rank']:02d}. {item['title']}")
        browser.close()
    return matched,unresolved

def mdb_params(): return {'apikey':MDBLIST_API_KEY}
def resolve_list_id():
    if MDBLIST_LIST_ID:return str(MDBLIST_LIST_ID)
    r=S.get(f'{MDB_API}/lists/user',params=mdb_params(),timeout=30); r.raise_for_status(); data=r.json(); lists=data if isinstance(data,list) else (data.get('lists',[]) if isinstance(data,dict) else [])
    for x in lists:
        if isinstance(x,dict) and (x.get('name') or x.get('title'))==MDBLIST_LIST_NAME:
            lid=x.get('id') or x.get('list_id')
            if lid is not None:return str(lid)
    raise RuntimeError(f'Could not find MDBList list {MDBLIST_LIST_NAME!r}.')

def mdb_get_items():
    global MDBLIST_LIST_ID
    MDBLIST_LIST_ID=resolve_list_id(); movies=[]; shows=[]; cursor=None
    for _ in range(20):
        params={**mdb_params(),'limit':1000}
        if cursor: params['cursor']=cursor
        r=S.get(f'{MDB_API}/lists/{MDBLIST_LIST_ID}/items',params=params,timeout=30); r.raise_for_status(); data=r.json()
        if not isinstance(data,dict): break
        movies.extend(data.get('movies') or []); shows.extend(data.get('shows') or [])
        p=data.get('pagination') or {}; nxt=p.get('next_cursor') or r.headers.get('X-Next-Cursor')
        if not nxt or nxt==cursor: break
        cursor=nxt
    return {'movies':movies,'shows':shows}

def existing_ids(data):
    return [int(x.get('id') or x.get('tmdb_id')) for x in (data.get('movies') or []) if isinstance(x,dict) and (x.get('id') or x.get('tmdb_id'))]

def mdb_modify(action,ids):
    if not ids:return
    r=S.post(f'{MDB_API}/lists/{MDBLIST_LIST_ID}/items/{action}',params=mdb_params(),json={'movies':[{'tmdb':int(i)} for i in ids]},timeout=60)
    if r.status_code>=400: raise RuntimeError(f'MDBList {action} failed: {r.status_code} {r.text[:500]}')

def get_rank_sequence(data):
    rows=[x for x in (data.get('movies') or []) if isinstance(x,dict)]
    return [int(x.get('id') or x.get('tmdb_id')) for x in sorted(rows,key=lambda x:x.get('rank',10**9)) if x.get('id') or x.get('tmdb_id')]

def rebuild(movies,reverse):
    current=existing_ids(mdb_get_items())
    if current:mdb_modify('remove',current)
    wanted=[m['tmdb_id'] for m in movies]
    ids=list(reversed(wanted)) if reverse else list(wanted)
    mdb_modify('add',ids)
    got=get_rank_sequence(mdb_get_items())
    print('MDBList rank sample:',got[:10])
    return got==wanted

def replace_mdb_list(movies):
    if rebuild(movies,False): print('MDBList API reports source order.'); return
    print('MDBList API reports reverse order; retrying reverse insertion.')
    if rebuild(movies,True): print('MDBList API reports source order after reverse insertion.'); return
    raise RuntimeError('MDBList did not report MUBI source order after either insertion direction.')

def main():
    raw=scrape_mubi_web(); matched,unresolved=match_all(raw); matched.sort(key=lambda x:x['rank'])
    if len(matched)<MIN_MATCHES or len(matched)/len(raw)<0.80:
        raise RuntimeError(f'Only {len(matched)} of {len(raw)} MUBI Trending films matched TMDB. Refusing to modify MDBList. Unresolved: '+', '.join(x['title'] for x in unresolved[:20]))
    for i,x in enumerate(matched,1): x['output_rank']=i
    replace_mdb_list(matched)
    os.makedirs('data',exist_ok=True)
    with open('data/latest.json','w',encoding='utf-8') as f: json.dump({'updated_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'source':'MUBI webpage','items':matched,'unmatched':unresolved,'mdblist_list_id':MDBLIST_LIST_ID},f,ensure_ascii=False,indent=2)
    print(f'Updated MDBList static list {MDBLIST_LIST_ID} with {len(matched)} films from actual MUBI Trending webpage; {len(unresolved)} unresolved.')

if __name__=='__main__': main()

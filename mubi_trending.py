import os, re, json, time, html, unicodedata, uuid
from difflib import SequenceMatcher
import requests

MUBI_URL = os.getenv('MUBI_URL', 'https://mubi.com/en/gb/collections/trending')
MUBI_COLLECTION_ID = os.getenv('MUBI_COLLECTION_ID', '490')
TMDB_API_KEY = os.environ['TMDB_API_KEY']
MDBLIST_API_KEY = os.environ['MDBLIST_API_KEY']
MDBLIST_LIST_ID = os.getenv('MDBLIST_LIST_ID', '')
MDBLIST_LIST_NAME = os.getenv('MDBLIST_LIST_NAME', 'MUBI UK Trending')
MAX_ITEMS = int(os.getenv('MAX_ITEMS', '50'))
MIN_MATCHES = int(os.getenv('MIN_MATCHES', '10'))
MDB_API = 'https://api.mdblist.com'
MUBI_API = 'https://api.mubi.com/v4'

S = requests.Session()
S.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131 Safari/537.36','Accept-Language':'en-GB,en;q=0.9'})

def norm(s):
    s = html.unescape(s or '')
    s = unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode().lower()
    return re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9]+', ' ', s).strip())

def mubi_headers():
    return {'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131 Safari/537.36','Accept':'application/json, text/plain, */*','Accept-Language':'en-GB,en;q=0.9','Client':'web','Client-Country':'GB','Origin':'https://mubi.com','Referer':MUBI_URL,'Anonymous_user_id':str(uuid.uuid4())}

def extract_film_from_payload(data):
    if isinstance(data, dict):
        for key in ('film','data'):
            value = data.get(key)
            if isinstance(value, dict):
                if isinstance(value.get('film'), dict): return value['film']
                if any(k in value for k in ('slug','web_url','title')): return value
        if any(k in data for k in ('slug','web_url','title')): return data
    return None

def extract_titles(film, fallback_title):
    variants=[]
    for key in ('title','original_title','localized_title','english_title'):
        v=film.get(key)
        if isinstance(v,str) and v.strip(): variants.append(v.strip())
    for key in ('titles','alternate_titles','alternative_titles'):
        value=film.get(key)
        if isinstance(value,list):
            for item in value:
                if isinstance(item,str) and item.strip(): variants.append(item.strip())
                elif isinstance(item,dict):
                    for k in ('title','name','value'):
                        v=item.get(k)
                        if isinstance(v,str) and v.strip(): variants.append(v.strip())
        elif isinstance(value,dict):
            for v in value.values():
                if isinstance(v,str) and v.strip(): variants.append(v.strip())
    variants.append(fallback_title)
    out=[]; seen=set()
    for title in variants:
        key=norm(title)
        if key and key not in seen: seen.add(key); out.append(title)
    return out

def extract_year(film):
    for key in ('release_year','year','original_release_year'):
        v=film.get(key)
        if v:
            m=re.search(r'(19|20)\d{2}',str(v))
            if m:return int(m.group(0))
    for key in ('release_date','released_at','release_date_display','released'):
        v=film.get(key)
        if isinstance(v,str):
            m=re.search(r'(19|20)\d{2}',v)
            if m:return int(m.group(0))
    return None

def extract_ids(film):
    ids=film.get('ids') if isinstance(film.get('ids'),dict) else {}
    return (film.get('tmdb_id') or film.get('tmdb') or ids.get('tmdb') or ids.get('tmdbid'), film.get('imdb_id') or film.get('imdb') or ids.get('imdb') or ids.get('imdbid'))

def get_mubi_detail(slug):
    r=S.get(f'{MUBI_API}/films/{slug}',headers=mubi_headers(),timeout=30)
    if r.status_code==404:return None
    r.raise_for_status()
    return extract_film_from_payload(r.json())

def extract_films_from_group_payload(data):
    candidates=[]
    if isinstance(data,dict):
        for key in ('film_group_items','items','films'):
            value=data.get(key)
            if isinstance(value,list): candidates.extend(value)
    elif isinstance(data,list): candidates=data
    films=[]; seen=set()
    for item in candidates:
        film=item.get('film') if isinstance(item,dict) and isinstance(item.get('film'),dict) else item
        if not isinstance(film,dict): continue
        slug=film.get('slug'); web_url=film.get('web_url') or film.get('url') or ''
        if not slug and isinstance(web_url,str):
            m=re.search(r'/films/([^/?#]+)',web_url); slug=m.group(1) if m else None
        if not slug or slug in seen: continue
        if isinstance(web_url,str) and web_url and '/films/' not in web_url: continue
        title=film.get('title') or film.get('original_title')
        if not title: continue
        seen.add(slug); films.append({'title':html.unescape(title),'slug':slug,'url':web_url or f'https://mubi.com/en/gb/films/{slug}'})
    return films

def get_mubi_films_api():
    films=[]; seen=set()
    for page in range(1,20):
        r=S.get(f'{MUBI_API}/film_groups/{MUBI_COLLECTION_ID}/film_group_items',params={'page':page,'per_page':48,'include_upcoming':'true'},headers=mubi_headers(),timeout=30)
        if r.status_code>=400:
            print(f'MUBI API returned {r.status_code}: {r.text[:300]}'); return []
        batch=extract_films_from_group_payload(r.json())
        if not batch: break
        for item in batch:
            if item['slug'] in seen: continue
            detail=get_mubi_detail(item['slug'])
            if not detail:
                print(f'SKIP non-film/unavailable MUBI item: {item["title"]}')
                continue
            titles=extract_titles(detail,item['title']); year=extract_year(detail); tmdb_id,imdb_id=extract_ids(detail)
            item.update({'mubi_titles':titles,'mubi_year':year,'mubi_tmdb_id':tmdb_id,'mubi_imdb_id':imdb_id})
            seen.add(item['slug']); films.append(item)
            if len(films)>=MAX_ITEMS: break
        if len(films)>=MAX_ITEMS or len(batch)<48: break
    for i,x in enumerate(films[:MAX_ITEMS],1): x['rank']=i
    return films[:MAX_ITEMS]

def get_mubi_films():
    films=get_mubi_films_api()
    if not films: raise RuntimeError('MUBI Trending API returned no verified film URLs. Refusing to modify MDBList.')
    print(f'MUBI Trending API: found {len(films)} verified ranked films.')
    return films

def tmdb_search(query,year=None):
    params={'api_key':TMDB_API_KEY,'query':query,'language':'en-GB','include_adult':'false','region':'GB'}
    if year: params['primary_release_year']=year
    r=S.get('https://api.themoviedb.org/3/search/movie',params=params,timeout=30); r.raise_for_status(); return r.json().get('results',[])

def tmdb_find_imdb(imdb_id):
    if not imdb_id: return None
    r=S.get(f'https://api.themoviedb.org/3/find/{imdb_id}',params={'api_key':TMDB_API_KEY,'external_source':'imdb_id','language':'en-GB'},timeout=30)
    if not r.ok:return None
    results=r.json().get('movie_results',[])
    return results[0] if results else None

def choose_tmdb_match(item):
    if item.get('mubi_tmdb_id'):
        r=S.get(f'https://api.themoviedb.org/3/movie/{item["mubi_tmdb_id"]}',params={'api_key':TMDB_API_KEY,'language':'en-GB'},timeout=30)
        if r.ok:
            movie=r.json(); return {'tmdb_id':movie['id'],'title':movie.get('title'),'year':(movie.get('release_date') or '')[:4],'match_method':'mubi-tmdb-id'}
    if item.get('mubi_imdb_id'):
        found=tmdb_find_imdb(item['mubi_imdb_id'])
        if found:
            year=(found.get('release_date') or '')[:4]
            if not item.get('mubi_year') or not year or not year.isdigit() or int(year)==item['mubi_year']:
                return {'tmdb_id':found['id'],'title':found.get('title'),'year':year,'match_method':'mubi-imdb-id'}
    variants=item.get('mubi_titles') or [item['title']]; mubi_year=item.get('mubi_year'); candidates={}
    for title in variants:
        for result in tmdb_search(title,mubi_year): candidates[result['id']]=result
    if not candidates:
        for title in variants:
            for result in tmdb_search(title): candidates[result['id']]=result
    if not candidates:return None
    query_norms=[norm(v) for v in variants if v]; scored=[]
    for result in candidates.values():
        names=[result.get('title',''),result.get('original_title','')]; names_norm=[norm(n) for n in names if n]
        exact=max((1 if n in query_norms else 0 for n in names_norm),default=0)
        similarity=max((SequenceMatcher(None,q,n).ratio() for q in query_norms for n in names_norm),default=0)
        result_year=int((result.get('release_date') or '0000')[:4]) if (result.get('release_date') or '')[:4].isdigit() else None
        year_exact=bool(mubi_year and result_year==mubi_year); year_diff=abs(result_year-mubi_year) if mubi_year and result_year else 99
        if mubi_year and result_year and year_diff>1: continue
        score=(5000 if year_exact else 0,3000 if exact else 0,similarity*1000,-year_diff if mubi_year and result_year else -99,result.get('popularity',0))
        scored.append((score,result))
    if not scored:return None
    scored.sort(key=lambda x:x[0],reverse=True); _,best=scored[0]; best_year=(best.get('release_date') or '')[:4]
    if mubi_year and best_year and best_year.isdigit() and int(best_year)!=mubi_year:return None
    return {'tmdb_id':best['id'],'title':best.get('title'),'year':best_year,'match_method':'title-year'}

def mdb_params(): return {'apikey':MDBLIST_API_KEY}
def resolve_list_id():
    if MDBLIST_LIST_ID:return str(MDBLIST_LIST_ID)
    r=S.get(f'{MDB_API}/lists/user',params=mdb_params(),timeout=30); r.raise_for_status(); data=r.json(); lists=data if isinstance(data,list) else (data.get('lists',[]) if isinstance(data,dict) else [])
    for x in lists:
        if isinstance(x,dict) and (x.get('name') or x.get('title'))==MDBLIST_LIST_NAME:
            lid=x.get('id') or x.get('list_id')
            if lid is not None:return str(lid)
    raise RuntimeError(f'Could not find an MDBList list named {MDBLIST_LIST_NAME!r}. Create one public static list with that exact name.')
def mdb_get_list():
    global MDBLIST_LIST_ID
    MDBLIST_LIST_ID=resolve_list_id(); r=S.get(f'{MDB_API}/lists/{MDBLIST_LIST_ID}/items',params={**mdb_params(),'limit':1000},timeout=30); r.raise_for_status(); return r.json()
def extract_existing_ids(data):
    if not isinstance(data,dict):return []
    out=[]
    for media_type in ('movies','shows'):
        for x in data.get(media_type,[]):
            if not isinstance(x,dict):continue
            ids=x.get('ids') if isinstance(x.get('ids'),dict) else {}; tmdb=x.get('id') or x.get('tmdb_id') or ids.get('tmdb') or ids.get('tmdbid')
            out.append({'imdb_id':x.get('imdb_id'),'tmdb_id':tmdb,'mediatype':media_type[:-1]})
    for x in data.get('items',[]):
        if not isinstance(x,dict):continue
        ids=x.get('ids') if isinstance(x.get('ids'),dict) else {}; out.append({'imdb_id':x.get('imdb_id'),'tmdb_id':x.get('tmdb_id') or x.get('id') or ids.get('tmdb') or ids.get('tmdbid'),'mediatype':x.get('mediatype','movie')})
    return out
def mdb_add(tmdb_id):
    r=S.post(f'{MDB_API}/lists/{MDBLIST_LIST_ID}/items/add',params=mdb_params(),json={'movies':[{'tmdb':tmdb_id}]},timeout=30)
    if r.status_code>=400: raise RuntimeError(f'MDBList add {tmdb_id} failed: {r.status_code} {r.text[:500]}')
def mdb_remove(item):
    payload={'movies':[{'tmdb':item['tmdb_id']}]} if item.get('tmdb_id') else {'movies':[{'imdb':item['imdb_id']}]} if item.get('imdb_id') else None
    if not payload:return
    r=S.post(f'{MDB_API}/lists/{MDBLIST_LIST_ID}/items/remove',params=mdb_params(),json=payload,timeout=30)
    if r.status_code>=400: raise RuntimeError(f'MDBList remove failed: {r.status_code} {r.text[:500]}')
def current_ranked_tmdb_ids(data):
    if not isinstance(data,dict):return []
    ranked=sorted([x for x in (data.get('movies') or []) if isinstance(x,dict)],key=lambda x:x.get('rank',10**9)); ids=[]
    for x in ranked:
        tid=x.get('id') or x.get('tmdb_id')
        if tid: ids.append(int(tid))
    return ids
def rebuild_mdb_list(movies,reverse_add=True):
    current=extract_existing_ids(mdb_get_list())
    for x in current:
        mdb_remove(x); time.sleep(0.12)
    order=list(reversed(movies)) if reverse_add else list(movies)
    for item in order:
        mdb_add(item['tmdb_id']); time.sleep(0.12)
    verify=current_ranked_tmdb_ids(mdb_get_list()); wanted=[m['tmdb_id'] for m in movies]
    return verify==wanted,verify,wanted
def replace_mdb_list(movies):
    ok,verify,wanted=rebuild_mdb_list(movies,reverse_add=True)
    if ok: print('MDBList rank order verified.'); return
    print(f'MDBList rank verification failed after reverse insertion. Got {len(verify)} items; trying forward insertion.')
    ok,verify,wanted=rebuild_mdb_list(movies,reverse_add=False)
    if not ok: raise RuntimeError('MDBList contents updated but rank order could not be verified.')
    print('MDBList rank order verified after forward insertion.')
def main():
    raw=get_mubi_films(); matched=[]; unmatched=[]
    for item in raw:
        try:
            hit=choose_tmdb_match(item)
            if hit:
                item.update(hit); matched.append(item); print(f"{item['rank']:02d}. {item['title']} ({item.get('mubi_year') or '?'}) -> {hit['tmdb_id']} / {hit['title']} ({hit.get('year') or '?'}) [{hit['match_method']}]")
            else:
                unmatched.append(item); print(f"NO MATCH: {item['rank']:02d}. {item['title']} ({item.get('mubi_year') or '?'}) variants={item.get('mubi_titles')}")
        except Exception as e:
            unmatched.append(item); print(f"MATCH ERROR: {item['title']}: {e}")
    if len(matched)<MIN_MATCHES: raise RuntimeError(f'Only {len(matched)} verified MUBI films matched TMDB; refusing to modify MDBList.')
    replace_mdb_list(matched); os.makedirs('data',exist_ok=True)
    with open('data/latest.json','w',encoding='utf-8') as f:
        json.dump({'updated_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'source':MUBI_URL,'items':matched,'unmatched':unmatched,'mdblist_list_id':MDBLIST_LIST_ID},f,ensure_ascii=False,indent=2)
    print(f'Updated MDBList static list {MDBLIST_LIST_ID} with {len(matched)} verified films in MUBI rank order.')
if __name__=='__main__': main()

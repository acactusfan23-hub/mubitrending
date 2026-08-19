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
S.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131 Safari/537.36', 'Accept-Language': 'en-GB,en;q=0.9'})

def norm(s):
    s = html.unescape(s or '')
    s = unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode().lower()
    return re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9]+', ' ', s).strip())

def mubi_headers():
    return {'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131 Safari/537.36','Accept':'application/json, text/plain, */*','Accept-Language':'en-GB,en;q=0.9','Client':'web','Client-Country':'GB','Origin':'https://mubi.com','Referer':MUBI_URL,'Anonymous_user_id':str(uuid.uuid4())}

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
        slug=film.get('slug'); title=film.get('title') or film.get('original_title')
        if not slug and isinstance(film.get('web_url'),str):
            m=re.search(r'/films/([^/?#]+)',film['web_url']); slug=m.group(1) if m else None
        if not slug or not title or slug in seen: continue
        seen.add(slug); films.append({'rank':len(films)+1,'title':html.unescape(title),'slug':slug,'url':film.get('web_url') or f'https://mubi.com/en/gb/films/{slug}'})
    return films

def get_mubi_films_api():
    films=[]
    for page in range(1,10):
        r=S.get(f'{MUBI_API}/film_groups/{MUBI_COLLECTION_ID}/film_group_items',params={'page':page,'per_page':48,'include_upcoming':'true'},headers=mubi_headers(),timeout=30)
        if r.status_code>=400:
            print(f'MUBI API returned {r.status_code}: {r.text[:300]}'); return []
        batch=extract_films_from_group_payload(r.json())
        if not batch: break
        films.extend(batch)
        if len(batch)<48 or len(films)>=MAX_ITEMS: break
    for i,x in enumerate(films[:MAX_ITEMS],1): x['rank']=i
    return films[:MAX_ITEMS]

def get_mubi_films():
    films=get_mubi_films_api()
    if not films:
        raise RuntimeError('MUBI Trending API returned no film URLs. Refusing to modify MDBList.')
    print(f'MUBI Trending API: found {len(films)} ranked film links.')
    return films

def tmdb_search(title):
    r=S.get('https://api.themoviedb.org/3/search/movie',params={'api_key':TMDB_API_KEY,'query':title,'language':'en-GB','include_adult':'false'},timeout=30); r.raise_for_status()
    results=r.json().get('results',[]); q=norm(title)
    if not results:return None
    def score(x):
        names=[x.get('title',''),x.get('original_title','')]; sims=[SequenceMatcher(None,q,norm(n)).ratio() for n in names if n]
        return (1 if any(norm(n)==q for n in names if n) else 0,max(sims,default=0),x.get('popularity',0))
    results.sort(key=score,reverse=True); best=results[0]; s=score(best)
    if s[0]==0 and s[1]<0.68:return None
    return {'tmdb_id':best['id'],'title':best.get('title'),'year':(best.get('release_date') or '')[:4]}

def mdb_params():return {'apikey':MDBLIST_API_KEY}
def resolve_list_id():
    if MDBLIST_LIST_ID:return str(MDBLIST_LIST_ID)
    r=S.get(f'{MDB_API}/lists/user',params=mdb_params(),timeout=30); r.raise_for_status(); data=r.json()
    if isinstance(data, list):
        lists = data
    elif isinstance(data, dict):
        lists = data.get('lists', [])
    else:
        lists = []
    for x in lists:
        if not isinstance(x, dict): continue
        if (x.get('name') or x.get('title'))==MDBLIST_LIST_NAME:
            lid=x.get('id') or x.get('list_id')
            if lid is not None:return str(lid)
    raise RuntimeError(f'Could not find an MDBList list named {MDBLIST_LIST_NAME!r}. Create one public static list with that exact name.')

def mdb_get_list():
    global MDBLIST_LIST_ID
    MDBLIST_LIST_ID=resolve_list_id()
    # MDBList replaced the old /list?id=... route with the documented
    # /lists/{id}/items route. The old route now returns 404.
    r=S.get(f'{MDB_API}/lists/{MDBLIST_LIST_ID}/items',params={**mdb_params(),'limit':1000},timeout=30)
    r.raise_for_status()
    data=r.json()
    # Current API returns an object containing items; tolerate a bare list too.
    if isinstance(data,list):
        return {'items':data}
    if isinstance(data,dict):
        return data
    raise RuntimeError(f'Unexpected MDBList response for list {MDBLIST_LIST_ID}: {type(data).__name__}')

def extract_existing_ids(data):
    out=[]
    for x in data.get('items',[]):
        tmdb=x.get('tmdb_id')
        if not tmdb and isinstance(x.get('ids'),dict):tmdb=x['ids'].get('tmdb')
        out.append({'imdb_id':x.get('imdb_id'),'tmdb_id':tmdb})
    return out

def mdb_add(tmdb_id):
    r=S.post(f'{MDB_API}/list/add',params=mdb_params(),json={'list_id':MDBLIST_LIST_ID,'tmdb_id':tmdb_id},timeout=30)
    if r.status_code>=400:raise RuntimeError(f'MDBList add {tmdb_id} failed: {r.status_code} {r.text[:500]}')

def mdb_remove(item):
    payload={'list_id':MDBLIST_LIST_ID}; payload['imdb_id' if item.get('imdb_id') else 'tmdb_id']=item.get('imdb_id') or item.get('tmdb_id')
    if not payload.get('imdb_id') and not payload.get('tmdb_id'):return
    r=S.post(f'{MDB_API}/list/remove',params=mdb_params(),json=payload,timeout=30)
    if r.status_code>=400:raise RuntimeError(f'MDBList remove failed: {r.status_code} {r.text[:500]}')

def replace_mdb_list(movies):
    current=extract_existing_ids(mdb_get_list()); wanted=[m['tmdb_id'] for m in movies]; wanted_set=set(wanted); current_tmdb={x['tmdb_id'] for x in current if x.get('tmdb_id')}
    for x in current:
        if x.get('tmdb_id') and x['tmdb_id'] not in wanted_set:mdb_remove(x);time.sleep(.15)
    for tid in wanted:
        if tid not in current_tmdb:mdb_add(tid);time.sleep(.15)

def main():
    raw=get_mubi_films(); matched=[]; unmatched=[]
    for item in raw:
        try:
            hit=tmdb_search(item['title'])
            if hit:item.update(hit);matched.append(item);print(f"{item['rank']:02d}. {item['title']} -> {hit['tmdb_id']} / {hit['title']}")
            else:unmatched.append(item);print(f"NO MATCH: {item['rank']:02d}. {item['title']}")
        except Exception as e:unmatched.append(item);print(f"MATCH ERROR: {item['title']}: {e}")
    if len(matched)<MIN_MATCHES:raise RuntimeError(f'Only {len(matched)} titles matched TMDB; refusing to modify MDBList.')
    replace_mdb_list(matched);os.makedirs('data',exist_ok=True)
    with open('data/latest.json','w',encoding='utf-8') as f:json.dump({'updated_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'source':MUBI_URL,'items':matched,'unmatched':unmatched,'mdblist_list_id':MDBLIST_LIST_ID},f,ensure_ascii=False,indent=2)
    print(f'Updated MDBList static list {MDBLIST_LIST_ID} with {len(matched)} titles.')

if __name__=='__main__':main()

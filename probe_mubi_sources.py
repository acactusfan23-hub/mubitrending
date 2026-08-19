import json, re, uuid, requests, os

API='https://api.mubi.com/v4'
EXPECTED=['the non-actor','amores perros','father mother sister brother','dazed and confused','alpha','a useful ghost','orphan']
S=requests.Session()
HEADERS={
    'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) Gecko/20100101 Firefox/131.0',
    'Accept':'application/json',
    'Accept-Language':'en-GB,en;q=0.9',
    'Client':'web',
    'Client-Country':'GB',
    'Client-Accept-Video-Codecs':'h265,vp9,h264',
    'Anonymous_user_id':str(uuid.uuid4()),
    'Origin':'https://mubi.com','Referer':'https://mubi.com/'
}

def norm(x): return re.sub(r'[^a-z0-9]+',' ',str(x or '').lower()).strip()

def titles_from(obj):
    out=[]; seen=set()
    def walk(x):
        if isinstance(x,dict):
            title=x.get('title') or x.get('title_locale') or x.get('original_title')
            slug=x.get('slug')
            if title and (slug or 'year' in x or 'film' in x or 'web_url' in x):
                key=norm(title)
                if key and key not in seen:
                    seen.add(key); out.append(str(title))
            if isinstance(x.get('film'),dict): walk(x['film'])
            for k,v in x.items():
                if k!='film': walk(v)
        elif isinstance(x,list):
            for v in x: walk(v)
    walk(obj)
    return out

def score(titles):
    n=[norm(x) for x in titles[:50]]
    return {e:(n.index(norm(e))+1 if norm(e) in n else None) for e in EXPECTED}

def call(label,path,params=None,save=None):
    try:
        r=S.get(API+path,headers=HEADERS,params=params or {},timeout=30)
        print('\n===',label,'===')
        print('URL',r.url,'STATUS',r.status_code,'CT',r.headers.get('content-type'))
        print('BODY_PREFIX',r.text[:180].replace('\n',' '))
        if not r.ok: return None
        data=r.json()
        if save:
            os.makedirs(os.path.dirname(save),exist_ok=True)
            with open(save,'w',encoding='utf-8') as f: json.dump(data,f,ensure_ascii=False,indent=2)
            print('SAVED',save)
        titles=titles_from(data)
        print('TOP30',json.dumps(titles[:30],ensure_ascii=False))
        print('ANCHORS',json.dumps(score(titles),ensure_ascii=False))
        if path=='/browse/lists':
            lists=data.get('lists') or []
            candidates=[{'title':x.get('title'),'slug':x.get('slug'),'film_count':x.get('film_count')} for x in lists if 'trend' in norm(x.get('title')) or 'trend' in norm(x.get('slug'))]
            print('TRENDING_LIST_CANDIDATES',json.dumps(candidates,ensure_ascii=False))
        if path=='/browse/film_groups':
            gs=data.get('film_groups') or []
            candidates=[{'id':x.get('id'),'title':x.get('title'),'full_title':x.get('full_title')} for x in gs if 'trend' in norm(x.get('title')) or 'trend' in norm(x.get('full_title')) or 'popular' in norm(x.get('title')) or 'popular' in norm(x.get('full_title'))]
            print('GROUP_CANDIDATES',json.dumps(candidates,ensure_ascii=False))
        return data
    except Exception as e:
        print('\n===',label,'=== ERROR',repr(e)); return None

call('list slug trending','/lists/trending/list_films',{'page':1,'per_page':48})
call('browse lists','/browse/lists',{'sort':'popularity','page':1,'per_page':100})
call('browse film groups','/browse/film_groups',{'sort':'title','page':1,'per_page':100})
for label,params in [
    ('browse sort trending',{'sort':'trending','playable':'true','page':1,'per_page':48}),
    ('browse collection trending',{'sort':'title','collection':'trending','playable':'true','page':1,'per_page':48}),
    ('browse list trending',{'sort':'title','list':'trending','playable':'true','page':1,'per_page':48}),
    ('browse film_group trending',{'sort':'title','film_group':'trending','playable':'true','page':1,'per_page':48}),
    ('browse popularity',{'sort':'popularity','playable':'true','page':1,'per_page':48}),
]: call(label,'/browse/films',params)
call('historical group 490','/film_groups/490/film_group_items',{'page':1,'per_page':48,'include_upcoming':'true'})
call('collections trending','/collections/trending',{},save='data/trending_collection.json')
call('collections trending page1','/collections/trending',{'page':1,'per_page':48},save='data/trending_collection_page1.json')
call('collections trending page2','/collections/trending',{'page':2,'per_page':48},save='data/trending_collection_page2.json')
call('collection trending items','/collections/trending/items',{'page':1,'per_page':48})

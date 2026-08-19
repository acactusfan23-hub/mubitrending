import json, re, requests, uuid, os
API='https://api.mubi.com/v4'
S=requests.Session()
H={'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) Gecko/20100101 Firefox/131.0','Accept':'application/json','Accept-Language':'en-GB,en;q=0.9','Client':'web','Client-Country':'GB','Client-Accept-Video-Codecs':'h265,vp9,h264','Anonymous_user_id':str(uuid.uuid4()),'Origin':'https://mubi.com','Referer':'https://mubi.com/en/gb/collections/trending'}
ANCHORS=['the non-actor','amores perros','father mother sister brother','dazed and confused','alpha','phantoms of july','la grazia','sentimental value','die my love','no other choice','the mysterious gaze of the flamingo','a useful ghost','the secret agent','sirat','the mastermind','it was just an accident','the fall','group marriage','aftersun','perfect days','orphan','do the right thing','the substance','moonage daydream','portrait of a lady on fire','shiva baby']
def norm(s):return re.sub(r'[^a-z0-9]+',' ',str(s or '').lower()).strip()
def extract(obj):
    rows=[]
    if isinstance(obj,list): rows=obj
    elif isinstance(obj,dict):
        for key in ('films','items','collection_items','film_items','results','data','film_group_items'):
            if isinstance(obj.get(key),list): rows=obj[key]; break
    out=[]
    for x in rows:
        if not isinstance(x,dict):continue
        f=x.get('film') if isinstance(x.get('film'),dict) else x
        title=f.get('title') or f.get('original_title') or f.get('slug')
        if title:out.append(str(title))
    return out
def pos(ts):
    n=[norm(x) for x in ts]
    return {a:(n.index(norm(a))+1 if norm(a) in n else None) for a in ANCHORS}
def call(path,params=None,method='GET'):
    try:
        r=S.request(method,API+path,headers=H,params=params or {},timeout=30)
        body=r.text
        d=None
        try:d=r.json()
        except:pass
        ts=extract(d)
        print('\n===',method,path,params or {},'===')
        print('STATUS',r.status_code,'URL',r.url,'ALLOW',r.headers.get('Allow'))
        print('HEADERS',json.dumps({k:v for k,v in r.headers.items() if any(z in k.lower() for z in ['link','page','cursor','offset','next','total'])},ensure_ascii=False))
        print('PREFIX',body[:250].replace('\n',' '))
        if isinstance(d,dict):print('KEYS',list(d.keys()))
        print('COUNT',len(ts),'TOP40',json.dumps(ts[:40],ensure_ascii=False))
        print('POSITIONS',json.dumps(pos(ts),ensure_ascii=False))
        return {'status':r.status_code,'titles':ts,'keys':list(d.keys()) if isinstance(d,dict) else [],'prefix':body[:500]}
    except Exception as e:
        print('\nERROR',path,repr(e));return {'error':repr(e)}

results={}
# Exact endpoint baseline.
results['collection']=call('/collections/trending')
# Likely child resources / naming conventions.
paths=['/collections/trending/films','/collections/trending/film_items','/collections/trending/collection_items','/collections/trending/film_group_items','/collections/trending/items','/collections/trending/contents','/collections/trending/entries','/collections/trending/movies','/collections/trending/films.json','/collections/trending.json']
for p in paths:
    results[p]=call(p,{'page':1,'per_page':100,'limit':100})
# Some APIs expose the same collection with include/expand parameters.
for name,params in [
 ('include_films',{'include':'films'}),('include_items',{'include':'items'}),('expand_films',{'expand':'films'}),('with_films',{'with_films':'true'}),('all_films',{'all_films':'true'}),('full',{'full':'true'}),('limit100',{'limit':100}),('per_page100',{'per_page':100}),('page_size100',{'page_size':100}),('count100',{'count':100})]:
    results[name]=call('/collections/trending',params)
# OPTIONS may reveal route methods.
results['options_items']=call('/collections/trending/items',method='OPTIONS')
results['options_films']=call('/collections/trending/films',method='OPTIONS')
os.makedirs('data',exist_ok=True)
with open('data/collection_variants.json','w',encoding='utf-8') as f:json.dump(results,f,ensure_ascii=False,indent=2)

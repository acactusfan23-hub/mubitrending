import json, re, uuid, requests, os

API='https://api.mubi.com/v4'
S=requests.Session()
EXPECTED=['the non-actor','amores perros','father mother sister brother','dazed and confused','alpha','phantoms of july','la grazia','sentimental value','die my love','no other choice','the mysterious gaze of the flamingo','a useful ghost','the secret agent','sirat','the mastermind','it was just an accident','the fall','group marriage','aftersun','perfect days','orphan','shiva baby']

BASE={
    'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) Gecko/20100101 Firefox/131.0',
    'Accept':'application/json',
    'Accept-Language':'en-GB,en;q=0.9',
    'Client':'web',
    'Client-Country':'GB',
    'Client-Accept-Video-Codecs':'h265,vp9,h264',
    'Origin':'https://mubi.com','Referer':'https://mubi.com/en/gb/collections/trending'
}

def norm(x): return re.sub(r'[^a-z0-9]+',' ',str(x or '').lower()).strip()
def titles(data):
    rows=(data.get('films') or []) if isinstance(data,dict) else []
    return [str(x.get('title') or x.get('original_title') or x.get('slug') or '') for x in rows if isinstance(x,dict)]
def positions(ts):
    n=[norm(x) for x in ts]
    return {e:(n.index(norm(e))+1 if norm(e) in n else None) for e in EXPECTED}
def h(extra=None,country='GB'):
    x=dict(BASE); x['Client-Country']=country; x['Anonymous_user_id']='8bd3ee8c-8610-40af-82a3-51c6caef8f2b'
    if extra: x.update(extra)
    return x

def fetch(label,params=None,extra_headers=None,country='GB'):
    try:
        r=S.get(API+'/collections/trending',headers=h(extra_headers,country),params=params or {},timeout=30)
        print('\n===',label,'===')
        print('STATUS',r.status_code,'URL',r.url)
        print('PAGINATION_HEADERS',json.dumps({k:v for k,v in r.headers.items() if any(s in k.lower() for s in ['link','page','cursor','offset','next','total'])},ensure_ascii=False))
        print('BODY_PREFIX',r.text[:160].replace('\n',' '))
        if not r.ok:return None
        d=r.json(); ts=titles(d)
        print('KEYS',list(d.keys()))
        print('COUNT',len(ts),'TOTAL_ITEMS',d.get('total_items'))
        print('TOP30',json.dumps(ts[:30],ensure_ascii=False))
        print('POSITIONS',json.dumps(positions(ts),ensure_ascii=False))
        return d
    except Exception as e:
        print('\n===',label,'=== ERROR',repr(e)); return None

results={}
# Baseline and likely pagination styles.
cases=[
 ('baseline',{}),
 ('limit100',{'limit':100}),
 ('limit48',{'limit':48}),
 ('offset12',{'offset':12}),
 ('offset12limit12',{'offset':12,'limit':12}),
 ('offset12limit48',{'offset':12,'limit':48}),
 ('page2',{'page':2}),
 ('page2per12',{'page':2,'per_page':12}),
 ('page2per48',{'page':2,'per_page':48}),
 ('page_number2',{'page_number':2,'page_size':12}),
 ('jsonapi_page2',{'page[number]':2,'page[size]':12}),
 ('start12',{'start':12,'count':12}),
 ('from12',{'from':12,'size':12}),
 ('skip12',{'skip':12,'take':12}),
 ('cursor12',{'cursor':12}),
 ('include_all',{'include_all':'true','limit':100}),
]
for label,params in cases: results[label]=fetch(label,params)

# Geo/header variations. These are harmless read-only diagnostics.
for label,extra,country in [
 ('gb_referer',{},'GB'),
 ('gb_cf_country',{'CF-IPCountry':'GB'},'GB'),
 ('gb_forwarded_country',{'X-Country-Code':'GB','X-Client-Country':'GB'},'GB'),
 ('gb_forwarded_ip',{'X-Forwarded-For':'81.2.69.142','X-Real-IP':'81.2.69.142'},'GB'),
 ('uk_country_value',{},'UK'),
 ('us_control',{},'US'),
]: results[label]=fetch(label,{},extra,country)

os.makedirs('data',exist_ok=True)
compact={k:{'films':titles(v),'positions':positions(titles(v)),'keys':list(v.keys()) if isinstance(v,dict) else []} for k,v in results.items() if v}
with open('data/collection_variants.json','w',encoding='utf-8') as f:json.dump(compact,f,ensure_ascii=False,indent=2)
if results.get('baseline'):
    with open('data/trending_collection.json','w',encoding='utf-8') as f:json.dump(results['baseline'],f,ensure_ascii=False,indent=2)

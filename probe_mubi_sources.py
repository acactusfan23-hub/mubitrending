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
    'Origin':'https://mubi.com','Referer':'https://mubi.com/'
}

def norm(x): return re.sub(r'[^a-z0-9]+',' ',str(x or '').lower()).strip()

def film_titles(data):
    films=data.get('films') if isinstance(data,dict) else None
    if not isinstance(films,list): return []
    return [str(x.get('title') or x.get('original_title') or x.get('slug') or '') for x in films if isinstance(x,dict)]

def list_field_summary(data):
    out={}
    if not isinstance(data,dict): return out
    for k,v in data.items():
        if isinstance(v,list):
            vals=[]
            for x in v[:30]:
                if isinstance(x,dict): vals.append(x.get('title') or x.get('name') or x.get('slug') or list(x.keys())[:5])
                else: vals.append(x)
            out[k]={'count':len(v),'sample':vals}
    return out

def positions(titles):
    n=[norm(x) for x in titles]
    return {e:(n.index(norm(e))+1 if norm(e) in n else None) for e in EXPECTED}

def fetch_variant(label,anon='random',params=None,country='GB'):
    h=dict(BASE); h['Client-Country']=country
    if anon=='random': h['Anonymous_user_id']=str(uuid.uuid4())
    elif anon=='zero': h['Anonymous_user_id']='00000000-0000-0000-0000-000000000000'
    elif anon=='fixed': h['Anonymous_user_id']='8bd3ee8c-8610-40af-82a3-51c6caef8f2b'
    elif anon=='none': h.pop('Anonymous_user_id',None)
    r=S.get(API+'/collections/trending',headers=h,params=params or {},timeout=30)
    print('\n===',label,'===',r.status_code,r.url)
    print('BODY_PREFIX',r.text[:180].replace('\n',' '))
    if not r.ok: return {'status':r.status_code,'body':r.text[:500]}
    d=r.json(); ts=film_titles(d)
    print('TOP_KEYS',list(d.keys()))
    print('LIST_FIELDS',json.dumps(list_field_summary(d),ensure_ascii=False))
    print('FILMS_COUNT',len(ts))
    print('FILMS_TOP30',json.dumps(ts[:30],ensure_ascii=False))
    print('POSITIONS',json.dumps(positions(ts),ensure_ascii=False))
    return {'status':r.status_code,'keys':list(d.keys()),'lists':list_field_summary(d),'films':ts,'positions':positions(ts),'raw':d}

variants={}
variants['random_1']=fetch_variant('GB random anonymous 1','random')
variants['random_2']=fetch_variant('GB random anonymous 2','random')
variants['no_anon']=fetch_variant('GB no anonymous id','none')
variants['zero_anon']=fetch_variant('GB zero anonymous id','zero')
variants['fixed_anon']=fetch_variant('GB fixed anonymous id','fixed')
variants['page1']=fetch_variant('GB page=1 per_page=48','fixed',{'page':1,'per_page':48})
variants['page2']=fetch_variant('GB page=2 per_page=48','fixed',{'page':2,'per_page':48})
variants['country_param']=fetch_variant('GB plus country query','fixed',{'country':'GB','country_code':'GB'})
variants['us_control']=fetch_variant('US control','fixed',country='US')

os.makedirs('data',exist_ok=True)
compact={k:{kk:vv for kk,vv in v.items() if kk!='raw'} for k,v in variants.items()}
with open('data/collection_variants.json','w',encoding='utf-8') as f: json.dump(compact,f,ensure_ascii=False,indent=2)
# Keep one raw response for structural inspection.
raw=variants.get('fixed_anon',{}).get('raw')
if raw:
    with open('data/trending_collection.json','w',encoding='utf-8') as f: json.dump(raw,f,ensure_ascii=False,indent=2)

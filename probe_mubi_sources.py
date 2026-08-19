import json, requests, uuid, os
API='https://api.mubi.com/v4'
H={'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) Gecko/20100101 Firefox/131.0','Accept':'application/json','Accept-Language':'en-GB,en;q=0.9','Client':'web','Client-Country':'GB','Client-Accept-Video-Codecs':'h265,vp9,h264','Anonymous_user_id':str(uuid.uuid4()),'Origin':'https://mubi.com','Referer':'https://mubi.com/en/gb/collections/trending'}
r=requests.get(API+'/collections/trending/films',headers=H,params={'page':1,'per_page':100,'limit':100},timeout=30)
print('STATUS',r.status_code,r.url)
r.raise_for_status(); data=r.json(); rows=data.get('films') or []
print('META',json.dumps(data.get('meta'),ensure_ascii=False))
print('COUNT',len(rows))
for i,x in enumerate(rows[:35],1):
    print(json.dumps({'rank':i,'id':x.get('id'),'slug':x.get('slug'),'title':x.get('title'),'year':x.get('year'),'episode':x.get('episode'),'series':x.get('series')},ensure_ascii=False))
os.makedirs('data',exist_ok=True)
with open('data/collection_variants.json','w',encoding='utf-8') as f:
    json.dump({'status':r.status_code,'meta':data.get('meta'),'rows':[{'rank':i+1,'id':x.get('id'),'slug':x.get('slug'),'title':x.get('title'),'year':x.get('year'),'episode':x.get('episode'),'series':x.get('series')} for i,x in enumerate(rows)]},f,ensure_ascii=False,indent=2)

import json,time,urllib.request,zipfile
from pathlib import Path
from datetime import datetime
root=Path(__file__).resolve().parent
c=json.loads((root/'config.json').read_text(encoding='utf-8-sig'))
url='http://127.0.0.1:'+str(c['http_port'])
label=input('Recording label (empty/sit/bend/lie): ').strip() or 'unlabelled'
folder=root/'recordings'/datetime.now().strftime('%Y%m%d-%H%M%S')
folder.mkdir(parents=True)
for name,endpoint in [('filters','/api/filters'),('info','/api/info'),('events_before','/api/events')]:
    try:
        with urllib.request.urlopen(url+endpoint,timeout=2) as resp: data=json.load(resp)
        (folder/(name+'.json')).write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8')
    except Exception as exc: print(name,str(exc))
print('Recording 60 seconds. Keep backend running.')
start=time.monotonic()
with (folder/'samples.jsonl').open('w',encoding='utf-8') as f:
    while time.monotonic()-start<60:
        row=dict(timestamp=datetime.now().astimezone().isoformat(),stage=label)
        try:
            with urllib.request.urlopen(url+'/api/state',timeout=2) as resp: state=json.load(resp)
            state.pop('points',None);row['state']=state
        except Exception as exc: row['error']=str(exc)
        f.write(json.dumps(row,ensure_ascii=False)+'\n');f.flush();time.sleep(.1)
try:
    with urllib.request.urlopen(url+'/api/events',timeout=2) as resp: data=json.load(resp)
    (folder/'events_after.json').write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8')
except Exception as exc: print(str(exc))
with zipfile.ZipFile(str(folder)+'.zip','w',zipfile.ZIP_DEFLATED) as z:
    for p in folder.iterdir(): z.write(p,folder.name+'/'+p.name)
print('Saved:',str(folder)+'.zip')

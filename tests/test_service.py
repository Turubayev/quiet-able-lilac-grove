import json
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient
from service import app, TOKEN, DraftRequest, generate

FACTS={'доставка':'По Алматы — 1500 тенге, в течение 2 рабочих дней.', 'наличие':'Наличие уточняет сотрудник.'}

def test_extractive_and_refusal():
    r=generate(DraftRequest(question='Сколько стоит доставка?'),FACTS)
    assert r['draft']==FACTS['доставка'] and r['sources'][0]['excerpt']==r['draft']
    assert generate(DraftRequest(question='Цена телефона?'),FACTS)['status']=='insufficient_facts'

def test_language_and_consent():
    assert generate(DraftRequest(question='Жеткізу құны?',language='kk'),FACTS)['status']=='translation_required'
    with pytest.raises(ValueError):generate(DraftRequest(question='доставка',mode='claude'),FACTS)

def test_local_api_auth_and_origin():
    c=TestClient(app,base_url='http://127.0.0.1:8780')
    assert c.post('/api/draft',json={'question':'доставка'}).status_code==403
    r=c.post('/api/draft',json={'question':'доставка'},headers={'X-Local-Token':TOKEN})
    assert r.status_code==200 and r.json()['review_required']
    assert c.post('/api/draft',json={'question':'доставка'},headers={'X-Local-Token':TOKEN,'Origin':'https://evil.example'}).status_code==403
    assert c.get('/',headers={'Host':'evil.example'}).status_code==403

def test_oversize():
    c=TestClient(app,base_url='http://127.0.0.1:8780')
    assert c.post('/api/draft',content='x'*65537,headers={'X-Local-Token':TOKEN}).status_code==413

def test_provider_citations_and_truncation():
    class Response:
        def __init__(self,data):self.data=data
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self):return json.dumps(self.data).encode()
    a=DraftRequest(question='доставка',mode='claude',consent=True)
    with patch.dict('os.environ',{'ANTHROPIC_API_KEY':'test','ANTHROPIC_MODEL':'test'}):
        for result in [ {'stop_reason':'max_tokens'}, {'content':[{'type':'text','text':'{"draft":"invented","source_ids":["unknown"]}'}]} ]:
            with patch('service.urllib.request.urlopen',return_value=Response(result)):
                with pytest.raises(ValueError):generate(a,FACTS)

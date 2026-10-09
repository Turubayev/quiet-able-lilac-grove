"""Local employee workspace. No automatic customer messaging."""
import json
import os
from pathlib import Path
import secrets
import urllib.error
import urllib.request
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from typing import Literal
from app import payload

ROOT=Path(__file__).parent
TOKEN=secrets.token_urlsafe(32)
app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None)
TOPICS={'доставка':['достав','жеткіз','delivery'], 'оплата':['оплат','төле','төлем','payment'],
        'возвраты':['возврат','қайтар','return'], 'наличие':['налич','бар ма','stock']}

class DraftRequest(BaseModel):
    question:str=Field(min_length=1,max_length=4000)
    language:Literal['ru','kk']='ru'
    mode:Literal['local','claude']='local'
    consent:bool=False

def facts_for(question, facts):
    if not isinstance(facts,dict) or not all(isinstance(k,str) and isinstance(v,str) for k,v in facts.items()):
        raise ValueError('Rules must be a string-to-string object')
    q=question.casefold()
    return {k:v for k,v in facts.items() if any(term in q for term in TOPICS.get(k,[k.casefold()])) and v.strip()}

def generate(data, facts):
    if not data.question.strip(): raise ValueError('Пустой вопрос')
    selected=facts_for(data.question,facts)
    citations=[{'id':k,'excerpt':v} for k,v in selected.items()]
    if not selected:
        return {'status':'insufficient_facts','draft':'Уточните ответ у сотрудника магазина.' if data.language=='ru' else 'Жауапты дүкен қызметкерінен нақтылаңыз.', 'sources':[], 'review_required':True}
    if data.mode=='local':
        if data.language=='kk':
            return {'status':'translation_required','draft':'Правила доступны на русском. Для черновика на казахском выберите Claude и подтвердите передачу данных.', 'sources':citations,'review_required':True}
        return {'status':'extractive_draft','draft':'\n'.join(selected.values()), 'sources':citations,'review_required':True,'provider':'local_rules'}
    if not data.consent: raise ValueError('Подтвердите передачу обезличенного вопроса и выбранных правил в Anthropic')
    key,model=os.environ.get('ANTHROPIC_API_KEY',''),os.environ.get('ANTHROPIC_MODEL','')
    if not key or not model: raise ValueError('Настройте ANTHROPIC_API_KEY и ANTHROPIC_MODEL на сервере')
    body=payload(selected,data.question,'русский' if data.language=='ru' else 'казахский',model)
    body['system']+=' Верни только JSON: {"draft":"текст", "source_ids":["ключ правила"]}. Ссылайся лишь на выбранные правила. Не выдумывай факты.'
    req=urllib.request.Request('https://api.anthropic.com/v1/messages',data=json.dumps(body).encode(),
        headers={'Content-Type':'application/json','x-api-key':key,'anthropic-version':'2023-06-01'},method='POST')
    with urllib.request.urlopen(req,timeout=60) as response: result=json.load(response)
    if result.get('stop_reason')=='max_tokens': raise ValueError('Ответ обрезан. Повторите запрос; черновик не сохранён')
    text='\n'.join(b.get('text','') for b in result.get('content',[]) if b.get('type')=='text')
    parsed=json.loads(text)
    ids=parsed.get('source_ids')
    if not isinstance(parsed.get('draft'),str) or not 1<=len(parsed['draft'].strip())<=16000 or not isinstance(ids,list) or not ids or not all(isinstance(i,str) and i in selected for i in ids):
        raise ValueError('Модель вернула неподтверждённые ссылки или неполный ответ')
    return {'status':'ai_draft','draft':parsed['draft'],'sources':[c for c in citations if c['id'] in ids],
            'review_required':True,'provider':'anthropic','model':model, 'note':'Проверка ссылок не гарантирует достоверность каждого предложения.'}

@app.middleware('http')
async def local_only(request:Request,call_next):
    allowed={'127.0.0.1:8780','localhost:8780'}
    if request.headers.get('host') not in allowed or request.headers.get('origin') not in (None,'http://127.0.0.1:8780','http://localhost:8780'):
        return JSONResponse({'error':'Недопустимый адрес'},status_code=403)
    if request.method=='POST':
        try: size=int(request.headers.get('content-length','0'))
        except ValueError: size=0
        if not 0<size<=65536: return JSONResponse({'error':'Запрос превышает 64 КБ'},status_code=413)
        if request.headers.get('x-local-token')!=TOKEN: return JSONResponse({'error':'Недопустимый запрос'},status_code=403)
    response=await call_next(request)
    response.headers['Cache-Control']='no-store'
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['Content-Security-Policy']="default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; frame-ancestors 'none'"
    return response

@app.get('/')
def workspace():return FileResponse(ROOT/'workspace.html')
@app.get('/workspace.js')
def js():return FileResponse(ROOT/'workspace.js',media_type='text/javascript')
@app.get('/api/config')
def config():return {'token':TOKEN,'claude_configured':bool(os.environ.get('ANTHROPIC_API_KEY') and os.environ.get('ANTHROPIC_MODEL'))}
@app.post('/api/draft')
def draft(data:DraftRequest):
    try:return generate(data,json.loads((ROOT/'shop.json').read_text(encoding='utf-8-sig')))
    except urllib.error.HTTPError as e:raise HTTPException(502,f'Anthropic HTTP {e.code}; проверьте модель и лимиты')
    except (urllib.error.URLError,TimeoutError):raise HTTPException(503,'Провайдер недоступен или время ожидания истекло')
    except (ValueError,OSError,TypeError):raise HTTPException(400,'Не удалось создать подтверждённый черновик. Проверьте правила, согласие и настройку модели.')

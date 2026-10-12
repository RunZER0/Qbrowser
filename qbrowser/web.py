"""Loopback-only local task dashboard and JSON API."""

from __future__ import annotations

import os
import secrets

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from qbrowser.config import load_local_env
from qbrowser.store import JobStore
from qbrowser.document_routes import router as document_router

load_local_env()
app = FastAPI(title="Qbrowser", version="0.1.0", docs_url="/docs")


@app.middleware("http")
async def document_upload_size_guard(request, call_next):
    if request.method == "POST" and "/documents/upload/" in request.url.path:
        raw = request.headers.get("content-length")
        if raw is None:
            return Response(status_code=411, content="Content-Length is required for uploads")
        try:
            size = int(raw)
        except ValueError:
            return Response(status_code=400, content="Invalid Content-Length")
        if size > 101 * 1024 * 1024:
            return Response(status_code=413, content="Document upload exceeds 100 MB")
    return await call_next(request)


def authenticate(authorization: str | None = Header(default=None)):
    token = os.getenv("QBROWSER_API_TOKEN")
    if token and not secrets.compare_digest(authorization or "", f"Bearer {token}"):
        raise HTTPException(401, "Bearer token required")


def store() -> JobStore:
    return JobStore()


app.include_router(document_router, dependencies=[Depends(authenticate)])

class NewJob(BaseModel):
    instruction: str = Field(min_length=1, max_length=50000)
    priority: int = 0
    external_approved: bool = False
    engine: str | None = None
    provider: str | None = None
    model: str | None = None


def safe(action):
    try:
        return action()
    except KeyError:
        raise HTTPException(404, "Job not found")
    except ValueError as exc:
        raise HTTPException(409, str(exc))


@app.get("/api/jobs", dependencies=[Depends(authenticate)])
def list_jobs():
    return store().list()


@app.post("/api/jobs", status_code=201, dependencies=[Depends(authenticate)])
def create_job(request: NewJob):
    return safe(lambda: store().create(request.instruction,
                 priority=request.priority, external_approved=request.external_approved,
                 engine=request.engine, provider=request.provider, model=request.model))


@app.get("/api/jobs/{job_id}", dependencies=[Depends(authenticate)])
def get_job(job_id: str):
    return safe(lambda: store().get(job_id))


@app.get("/api/jobs/{job_id}/artifacts", dependencies=[Depends(authenticate)])
def job_artifacts(job_id: str):
    from qbrowser.artifacts import artifacts
    return safe(lambda: artifacts(store(), job_id))


@app.get("/api/jobs/{job_id}/events", dependencies=[Depends(authenticate)])
def job_events(job_id: str):
    return safe(lambda: store().events(job_id))


class CheckpointUpdate(BaseModel):
    key: str
    state: str
    detail: str = ""


@app.get("/api/jobs/{job_id}/checkpoints", dependencies=[Depends(authenticate)])
def list_checkpoints(job_id: str):
    return safe(lambda: store().checkpoints(job_id))


@app.put("/api/jobs/{job_id}/checkpoints", dependencies=[Depends(authenticate)])
def update_checkpoint(job_id: str, request: CheckpointUpdate):
    return safe(lambda: store().checkpoint(job_id, request.key,
                                            request.state, request.detail))


@app.post("/api/jobs/{job_id}/requeue", dependencies=[Depends(authenticate)])
def requeue(job_id: str):
    return safe(lambda: store().transition(job_id, "queued",
                allowed={"needs_review", "paused", "failed"}))


@app.post("/api/jobs/{job_id}/verified", dependencies=[Depends(authenticate)])
def verified(job_id: str):
    return safe(lambda: store().transition(job_id, "completed", allowed={"verify"},
                 result="Operator verified the final outcome"))


@app.post("/api/jobs/{job_id}/stop", dependencies=[Depends(authenticate)])
def stop_job(job_id: str):
    return safe(lambda: store().request_stop(job_id))


@app.post("/api/jobs/{job_id}/pause", dependencies=[Depends(authenticate)])
def pause(job_id: str):
    return safe(lambda: store().transition(job_id, "paused", allowed={"queued"}))


@app.post("/api/jobs/{job_id}/cancel", dependencies=[Depends(authenticate)])
def cancel(job_id: str):
    return safe(lambda: store().transition(job_id, "cancelled", allowed={
        "queued", "paused", "needs_review", "waiting_approval", "verify"}))


@app.post("/api/jobs/{job_id}/approve-external", dependencies=[Depends(authenticate)])
def approve_external(job_id: str):
    return safe(lambda: store().approve_external(job_id))


HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Qbrowser Workboard</title>
<style>
:root{font:15px system-ui,sans-serif;color-scheme:dark;background:#101521;color:#e5e9f2}
*{box-sizing:border-box}body{max-width:1000px;margin:auto;padding:2rem 1rem}
h1{font-size:1.8rem}p,.meta{color:#9ca9bb}textarea,input,button,select{font:inherit}
textarea,input,select{border:1px solid #425169;border-radius:8px;padding:12px;background:#192334;color:white}
textarea,input{width:100%}
textarea{min-height:95px;resize:vertical}
button{background:#263a59;border:1px solid #47638a;color:white;padding:8px 13px;border-radius:7px;cursor:pointer}
button:hover{background:#365479}button:disabled{opacity:.5}
.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.panel,.job{padding:16px;border:1px solid #344258;border-radius:10px;margin:16px 0;background:#172131}
.job{white-space:normal}.instruction{white-space:pre-wrap;overflow-wrap:anywhere}
.status{font-size:.85rem;text-transform:uppercase;color:#c3e0ff}
small{color:#9ca9bb}label{display:block;margin:12px 0}
pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#0b1421;padding:12px;border-radius:8px;max-height:250px;overflow:auto}
#error{color:#ffbdab}
</style></head>
<body><h1>Qbrowser Workboard</h1>
<p>Persistent tasks for a single local desktop operator. External actions require explicit authorization.</p>
<section class="panel">
<label>Local API bearer token (only if configured) <input id="token" type="password" autocomplete="off" placeholder="Leave blank otherwise"></label>
<label>Task instructions<textarea id="instruction" placeholder="Describe any task to perform on this computer..."></textarea></label>
<div class="row">
<label>Engine <select id="engine"><option value="native">Native multimodal</option><option value="agent_s">Agent S3</option></select></label>
<label>Model provider <select id="provider"><option value="zai">Z.ai GLM</option><option value="deepseek">DeepSeek</option><option value="custom">Custom compatible</option></select></label>
<label>Model override (optional) <input id="model" type="text" placeholder="Default for provider"></label>
</div>
<label><input id="approve" type="checkbox" style="width:auto"> Approve external submissions/actions for this job</label>
<button id="submit">Queue task</button><span id="error" role="alert"></span>
</section>
<div class="row"><h2>Jobs</h2><button id="refresh">Refresh</button><small>Refreshes every 5 seconds</small></div>
<div id="jobs"></div>
<script>
const $=id=>document.getElementById(id);
$('token').value=sessionStorage.getItem('qbrowser_token')||'';
$('token').oninput=()=>sessionStorage.setItem('qbrowser_token',$('token').value);
async function api(path,method='GET',body){
 const token=$('token').value;
 const r=await fetch(path,{method,headers:{'Content-Type':'application/json',...(token?{'Authorization':'Bearer '+token}:{})},
 body:body?JSON.stringify(body):undefined});
 const data=await r.json().catch(()=>({detail:r.statusText}));
 if(!r.ok)throw Error(typeof data.detail==='string'?data.detail:JSON.stringify(data.detail));
 return data;
}
function el(tag,text,className){
 const n=document.createElement(tag);if(text!==undefined)n.textContent=text;
 if(className)n.className=className;return n;
}
async function action(job,id,label){
 if(!confirm(label+' job '+id+'?'))return;
 try{await api('/api/jobs/'+id+'/'+job,'POST');await refresh();}
 catch(e){$('error').textContent=e.message;}
}
async function refresh(){
 try{
  const jobs=await api('/api/jobs');const root=$('jobs');root.replaceChildren();
  if(!jobs.length){root.append(el('p','No jobs yet'));return;}
  for(const j of jobs){
   const card=el('article',undefined,'job');
   card.append(el('div',j.status+' · '+j.id,'status'),el('p',j.instruction,'instruction'));
   card.append(el('small','Attempt '+j.attempt+' · '+(j.engine||'native')+' / '+(j.provider||'configured default')+' · Created '+j.created_at));
   if(j.last_error)card.append(el('pre',j.last_error));
   if(j.result)card.append(el('pre',j.result));
   const bar=el('div',undefined,'row');
   const actions = j.status==='verify'?[['verified','Mark verified']]
    :['needs_review','paused','failed'].includes(j.status)?[['requeue','Requeue']]
    :j.status==='queued'?[['pause','Pause'],['cancel','Cancel']]
    :j.status==='running'&&!j.stop_requested?[['stop','Request stop']]:[];
   if(!j.external_approved&&j.status!=='running'&&j.status!=='completed'&&j.status!=='cancelled')actions.push(['approve-external','Approve external actions']);
   for(const [k,v] of actions){const b=el('button',v);b.onclick=()=>action(k,j.id,v);bar.append(b);}
   const stages=el('button','Milestones');stages.onclick=async()=>{
    try{const stages=await api('/api/jobs/'+j.id+'/checkpoints');
      alert(stages.length?JSON.stringify(stages,null,2):'No milestones recorded');}
    catch(e){$('error').textContent=e.message;}
   };bar.append(stages);
   const files=el('button','Documents');files.onclick=()=>showDocuments(j.id,card);
   bar.append(files);
   const log=el('button','Events');log.onclick=async()=>{
    try{let events=await api('/api/jobs/'+j.id+'/events');alert(JSON.stringify(events,null,2));}
    catch(e){$('error').textContent=e.message;}
   };bar.append(log);card.append(bar);root.append(card);
  }
  $('error').textContent='';
 }catch(e){$('error').textContent=e.message;}
}
$('submit').onclick=async()=>{
 const instruction=$('instruction').value.trim();if(!instruction)return;
 try{await api('/api/jobs','POST',{instruction,external_approved:$('approve').checked,engine:$('engine').value,provider:$('provider').value,model:$('model').value.trim()||null});
 $('instruction').value='';$('approve').checked=false;await refresh();}
 catch(e){$('error').textContent=e.message;}
};
async function rawRequest(path, method='GET', body){
 const token=$('token').value;
 const response=await fetch(path,{method,headers:{...(token?{'Authorization':'Bearer '+token}:{})},body});
 if(!response.ok){let e=await response.json().catch(()=>({detail:response.statusText}));throw Error(e.detail||response.statusText);}
 return response;
}
function documentUrl(id,role,name,action,version){
 return '/api/jobs/'+id+'/documents/'+role+'/'+encodeURIComponent(name)+'/'+action+(version?'?version='+version:'');
}
async function showDocuments(id,card){
 const prev=card.querySelector('.document-list');if(prev)prev.remove();
 const panel=el('section',undefined,'panel document-list');
 const header=el('h3','Documents and versions');panel.append(header);
 const uploader=el('input');uploader.type='file';uploader.accept='.pdf,.docx,.xlsx,.pptx,.md,.txt,.csv,.png,.jpg,.jpeg';
 const role=el('select');for(const type of ['input','output']){
  const o=el('option',type);o.value=type;role.append(o);
 }
 const uploadBtn=el('button','Upload file');
 uploadBtn.onclick=async()=>{
  if(!uploader.files.length)return;
  try{
   const form=new FormData();form.append('file',uploader.files[0]);
   const response=await rawRequest('/api/jobs/'+id+'/documents/upload/'+role.value,'POST',form);
   await response.json();await showDocuments(id,card);
  }catch(e){$('error').textContent=e.message;}
 };
 panel.append(role,uploader,uploadBtn);
 const data=await api('/api/jobs/'+id+'/documents');
 for(const f of data){
  const line=el('div',undefined,'row');
  line.style.margin='8px 0';
  line.append(el('small',f.role+' · '+f.filename+' · v'+f.version+(f.deleted_at?' · expired':'')));
  if(!f.deleted_at){
   const down=el('button','Download');
   down.onclick=async()=>{
    try{
     const response=await rawRequest(documentUrl(id,f.role,f.filename,'download',f.version));
     const blob=await response.blob();const href=URL.createObjectURL(blob);
     const a=el('a');a.href=href;a.download=f.filename;a.click();setTimeout(()=>URL.revokeObjectURL(href),1000);
    }catch(e){$('error').textContent=e.message;}
   };
   const view=el('button','Preview');
   view.onclick=async()=>{
    try{
     const response=await rawRequest(documentUrl(id,f.role,f.filename,'preview',f.version));
     const blob=await response.blob();const href=URL.createObjectURL(blob);window.open(href,'_blank','noopener');
     setTimeout(()=>URL.revokeObjectURL(href),60000);
    }catch(e){$('error').textContent=e.message;}
   };
   const review=el('button','QA');
   review.onclick=async()=>{try{
    const result=await api(documentUrl(id,f.role,f.filename,'review',f.version));
    alert(JSON.stringify(result.analysis,null,2));
   }catch(e){$('error').textContent=e.message;}};
   line.append(view,down,review);
   if(f.version>1){
    const compare=el('button','Compare v'+(f.version-1));
    compare.onclick=async()=>{try{
     const url='/api/jobs/'+id+'/documents/'+f.role+'/'+encodeURIComponent(f.filename)+'/diff?older='+(f.version-1)+'&newer='+f.version;
     const result=await api(url);alert(result.unified_diff||'No text changes');
    }catch(e){$('error').textContent=e.message;}};
    line.append(compare);
   }
  }
  panel.append(line);
 }
 const generate=el('button','Create from JSON');
 generate.onclick=async()=>{
  const filename=prompt('Output filename (.docx, .pdf, .xlsx, .pptx, .txt, .md):');
  if(!filename)return;
  const input=prompt('JSON document data (blocks / sheets / slides):');
  if(!input)return;
  try{
   await api('/api/jobs/'+id+'/documents/generate','POST',{filename,data:JSON.parse(input)});
   await showDocuments(id,card);
  }catch(e){$('error').textContent=e.message;}
 };
 panel.append(generate);card.append(panel);
}
$('refresh').onclick=refresh;setInterval(refresh,5000);refresh();
</script></body></html>"""


@app.get("/", response_class=HTMLResponse)
def index():
    return HTML

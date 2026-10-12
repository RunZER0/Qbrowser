"""Loopback-only local task dashboard and JSON API."""

from __future__ import annotations

import os
import secrets
import asyncio
import json

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from qbrowser.config import load_local_env
from qbrowser.credentials import get_secret
from qbrowser.store import JobStore
from qbrowser.document_routes import router as document_router

load_local_env()
app = FastAPI(title="Qbrowser", version="0.1.0", docs_url="/docs")


@app.middleware("http")
async def document_upload_size_guard(request, call_next):
    if request.method == "POST" and "/documents/upload/" in request.url.path:
        token = get_secret("QBROWSER_API_TOKEN")
        if token and not secrets.compare_digest(
            request.headers.get("authorization", ""), f"Bearer {token}"
        ):
            return Response(status_code=401, content="Bearer token required")
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


async def authenticate(authorization: str | None = Header(default=None)):
    token = get_secret("QBROWSER_API_TOKEN")
    if token and not secrets.compare_digest(authorization or "", f"Bearer {token}"):
        raise HTTPException(401, "Bearer token required")


def store() -> JobStore:
    return JobStore()


def public_jobs(value):
    repository = store()
    if isinstance(value, list):
        return [repository.public_job(item) for item in value]
    if isinstance(value, dict) and "id" in value:
        return repository.public_job(value)
    return value

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
async def list_jobs():
    return safe(lambda: store().list())


@app.get("/api/metrics", dependencies=[Depends(authenticate)])
async def metrics():
    return store().metrics_summary()


@app.post("/api/jobs", status_code=201, dependencies=[Depends(authenticate)])
async def create_job(request: NewJob):
    return public_jobs(safe(lambda: store().create(request.instruction,
                 priority=request.priority, external_approved=request.external_approved,
                 engine=request.engine, provider=request.provider, model=request.model)))


@app.get("/api/jobs/{job_id}", dependencies=[Depends(authenticate)])
async def get_job(job_id: str):
    return public_jobs(safe(lambda: store().get(job_id)))


@app.get("/api/jobs/{job_id}/artifacts", dependencies=[Depends(authenticate)])
async def job_artifacts(job_id: str):
    from qbrowser.artifacts import artifacts
    return safe(lambda: artifacts(store(), job_id))


@app.get("/api/jobs/{job_id}/events", dependencies=[Depends(authenticate)])
async def job_events(job_id: str):
    return safe(lambda: store().events(job_id))


@app.get("/api/jobs/{job_id}/events/stream", dependencies=[Depends(authenticate)])
async def stream_job_events(job_id: str, request: Request, after: int = 0):
    repository = store()
    safe(lambda: repository.get(job_id))
    if after < 0:
        raise HTTPException(422, "Event cursor must be nonnegative")

    async def generate():
        cursor = after
        while not await request.is_disconnected():
            batch = repository.events(job_id, since=cursor)
            for event in batch:
                cursor = event["seq"]
                yield (f"id: {cursor}\nevent: qbrowser\ndata: " +
                       json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n\n")
            if not batch:
                yield ": keep-alive\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(generate(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"})


@app.get("/health/live")
async def liveness():
    return {"live": True}


@app.get("/api/health/ready", dependencies=[Depends(authenticate)])
async def readiness():
    repository = store()
    with repository._connection() as db:
        counts = {row["status"]: row["count"] for row in db.execute(
            "SELECT status,COUNT(*) AS count FROM jobs GROUP BY status")}
    return {"ready": True, "schema_version": repository.schema_version(),
            "jobs_by_status": counts}


@app.get("/api/desktop/screenshot", dependencies=[Depends(authenticate)])
async def current_desktop_screenshot():
    if not get_secret("QBROWSER_API_TOKEN"):
        raise HTTPException(403, "Set QBROWSER_API_TOKEN before enabling live desktop screenshots")
    try:
        from qbrowser.native import Desktop
        image_uri, _ = Desktop().observe()
        import base64
        content = base64.b64decode(image_uri.split(",", 1)[1], validate=True)
        return Response(content=content, media_type="image/jpeg",
                        headers={"Cache-Control": "no-store"})
    except Exception as exc:
        raise HTTPException(503, f"Desktop screenshot unavailable ({type(exc).__name__})") from exc


@app.get("/manifest.webmanifest")
async def manifest():
    return Response(content=json.dumps({
        "name": "Qbrowser Workboard", "short_name": "Qbrowser",
        "start_url": "/", "display": "standalone", "background_color": "#101521",
        "theme_color": "#101521", "icons": [{"src": "/app-icon.svg",
            "sizes": "any", "type": "image/svg+xml", "purpose": "any maskable"}],
    }), media_type="application/manifest+json")


@app.get("/app-icon.svg")
async def app_icon():
    return Response(content=(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128">'
        '<rect width="128" height="128" rx="24" fill="#172131"/>'
        '<path d="M28 100V28h14l44 49V28h14v72H86L42 51v49z" fill="#c3e0ff"/>'
        '</svg>'), media_type="image/svg+xml")


@app.get("/service-worker.js")
async def service_worker():
    script = """const CACHE='qbrowser-shell-v1';
self.addEventListener('install',event=>event.waitUntil(caches.open(CACHE).then(c=>c.addAll(['/','/manifest.webmanifest','/app-icon.svg']))));
self.addEventListener('activate',event=>event.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(k=>k!==CACHE).map(k=>caches.delete(k))))));
self.addEventListener('fetch',event=>{const url=new URL(event.request.url);if(event.request.method!=='GET'||url.pathname.startsWith('/api/'))return;event.respondWith(fetch(event.request).catch(()=>caches.match(event.request)));});
"""
    return Response(content=script, media_type="application/javascript",
                    headers={"Cache-Control": "no-cache"})


class CheckpointUpdate(BaseModel):
    key: str
    state: str
    detail: str = ""


@app.get("/api/jobs/{job_id}/checkpoints", dependencies=[Depends(authenticate)])
async def list_checkpoints(job_id: str):
    return safe(lambda: store().checkpoints(job_id))


@app.get("/api/jobs/{job_id}/plan", dependencies=[Depends(authenticate)])
async def get_plan(job_id: str):
    return safe(lambda: store().plan(job_id))


@app.get("/api/jobs/{job_id}/assessment", dependencies=[Depends(authenticate)])
async def get_assessment(job_id: str):
    return safe(lambda: store().assessment(job_id))


@app.get("/api/jobs/{job_id}/memory", dependencies=[Depends(authenticate)])
async def get_memory(job_id: str):
    return safe(lambda: store().working_memory(job_id))


@app.get("/api/jobs/{job_id}/actions", dependencies=[Depends(authenticate)])
async def get_actions(job_id: str):
    return safe(lambda: store().action_history(job_id))


@app.get("/api/jobs/{job_id}/messages", dependencies=[Depends(authenticate)])
async def get_messages(job_id: str):
    return safe(lambda: store().messages(job_id))


class NewMessage(BaseModel):
    content: str = Field(min_length=1, max_length=8000)


class NewSchedule(BaseModel):
    instruction: str = Field(min_length=1, max_length=50000)
    interval_seconds: int = Field(ge=60, le=31536000)
    first_run_seconds: int | None = Field(default=None, ge=0, le=31536000)
    priority: int = Field(default=0, ge=-100, le=100)
    engine: str | None = None
    provider: str | None = None
    model: str | None = Field(default=None, max_length=120)


class WakeRequest(BaseModel):
    reason: str = Field(default="External event received", min_length=1, max_length=500)


@app.post("/api/jobs/{job_id}/messages", status_code=201, dependencies=[Depends(authenticate)])
async def add_message(job_id: str, request: NewMessage):
    return safe(lambda: store().add_message(job_id, "user", request.content))


@app.get("/api/schedules", dependencies=[Depends(authenticate)])
async def list_schedules():
    return store().list_schedules()


@app.post("/api/schedules", status_code=201, dependencies=[Depends(authenticate)])
async def create_schedule(request: NewSchedule):
    return safe(lambda: store().create_schedule(
        request.instruction, interval_seconds=request.interval_seconds,
        first_run_seconds=request.first_run_seconds, priority=request.priority,
        engine=request.engine, provider=request.provider, model=request.model))


@app.delete("/api/schedules/{schedule_id}", dependencies=[Depends(authenticate)])
async def disable_schedule(schedule_id: str):
    return safe(lambda: store().cancel_schedule(schedule_id))


@app.post("/api/jobs/{job_id}/wake", dependencies=[Depends(authenticate)])
async def wake_job(job_id: str, request: WakeRequest):
    return public_jobs(safe(lambda: store().wake(job_id, request.reason)))


@app.get("/api/jobs/{job_id}/evidence", dependencies=[Depends(authenticate)])
async def list_evidence(job_id: str):
    return safe(lambda: store().evidence(job_id))


@app.get("/api/jobs/{job_id}/evidence/{evidence_id}", dependencies=[Depends(authenticate)])
async def get_evidence(job_id: str, evidence_id: str):
    from qbrowser.evidence import read_screenshot
    return safe(lambda: Response(content=read_screenshot(store(), job_id, evidence_id),
                                 media_type="image/jpeg",
                                 headers={"Cache-Control": "no-store"}))


@app.put("/api/jobs/{job_id}/checkpoints", dependencies=[Depends(authenticate)])
async def update_checkpoint(job_id: str, request: CheckpointUpdate):
    return safe(lambda: store().checkpoint(job_id, request.key,
                                            request.state, request.detail))


@app.post("/api/jobs/{job_id}/requeue", dependencies=[Depends(authenticate)])
async def requeue(job_id: str):
    return public_jobs(safe(lambda: store().transition(job_id, "queued",
                allowed={"needs_review", "paused", "failed"})))


@app.post("/api/jobs/{job_id}/verified", dependencies=[Depends(authenticate)])
async def verified(job_id: str):
    return public_jobs(safe(lambda: store().transition(job_id, "completed", allowed={"verify"},
                 result="Operator verified the final outcome")))


@app.get("/api/jobs/{job_id}/verification-reviews", dependencies=[Depends(authenticate)])
async def verification_reviews(job_id: str):
    return safe(lambda: store().verification_reviews(job_id))


@app.post("/api/jobs/{job_id}/verification-review", dependencies=[Depends(authenticate)])
def verification_review(job_id: str):
    from qbrowser.verification import verify_job
    return safe(lambda: verify_job(store(), job_id))


@app.post("/api/jobs/{job_id}/stop", dependencies=[Depends(authenticate)])
async def stop_job(job_id: str):
    return public_jobs(safe(lambda: store().request_stop(job_id)))


@app.post("/api/jobs/{job_id}/pause", dependencies=[Depends(authenticate)])
async def pause(job_id: str):
    return public_jobs(safe(lambda: store().transition(job_id, "paused", allowed={"queued"})))


@app.post("/api/jobs/{job_id}/cancel", dependencies=[Depends(authenticate)])
async def cancel(job_id: str):
    return public_jobs(safe(lambda: store().transition(job_id, "cancelled", allowed={
        "queued", "paused", "needs_review", "waiting_approval", "waiting_user", "waiting", "verify"})))


@app.post("/api/jobs/{job_id}/approve-external", dependencies=[Depends(authenticate)])
async def approve_external(job_id: str):
    return public_jobs(safe(lambda: store().approve_external(job_id)))


HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Qbrowser Workboard</title><meta name="theme-color" content="#101521"><link rel="manifest" href="/manifest.webmanifest">
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
.thread{border-top:1px solid #344258;margin-top:12px;padding-top:10px}.thread summary{cursor:pointer;color:#c3e0ff}
.message{white-space:pre-wrap;overflow-wrap:anywhere;padding:9px 11px;margin:8px 0;border-radius:8px;background:#101927}
.message.user{border-left:3px solid #73a8ff}.message.assistant{border-left:3px solid #79c79a}
.compose{min-height:65px;margin:8px 0}.evidence-image{max-width:100%;height:auto;margin-top:10px;border:1px solid #425169;border-radius:6px}
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
<label><input id="approve" type="checkbox" style="width:auto"> Pre-approve external actions for the entire job</label>
<button id="submit">Queue task</button><span id="error" role="alert"></span>
</section>
<section class="panel">
<h2>Recurring task</h2>
<label>Task instructions<textarea id="recurring-instruction" placeholder="Describe the repeated task..."></textarea></label>
<div class="row"><label>Interval in seconds <input id="recurring-interval" type="number" min="60" max="31536000" value="86400"></label>
<button id="schedule-add">Save schedule</button></div><div id="schedules"></div>
</section>
<div class="row"><h2>Jobs</h2><button id="refresh">Refresh</button><button id="metrics-toggle">Metrics</button><button id="desktop-shot">View live desktop</button><small>Refreshes every 5 seconds</small></div>
<section id="metrics-panel" class="panel" hidden></section>
<img id="desktop-preview" class="evidence-image" alt="Current local desktop" hidden>
<div id="jobs"></div>
<script>
const $=id=>document.getElementById(id);
const activeLiveStreams=new Set();
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
async function blob(path){
 const token=$('token').value;
 const r=await fetch(path,{headers:token?{'Authorization':'Bearer '+token}:{}});
 if(!r.ok)throw Error((await r.text())||r.statusText);
 return URL.createObjectURL(await r.blob());
}
function el(tag,text,className){
 const n=document.createElement(tag);if(text!==undefined)n.textContent=text;
 if(className)n.className=className;return n;
}
async function liveEvents(id,output,signal){
 const token=$('token').value;
 const response=await fetch('/api/jobs/'+id+'/events/stream',{headers:token?{'Authorization':'Bearer '+token}:{},signal});
 if(!response.ok)throw Error(await response.text()||response.statusText);
 const reader=response.body.getReader(),decoder=new TextDecoder();let pending='';
 while(!signal.aborted){
  const {value,done}=await reader.read();if(done)break;
  pending+=decoder.decode(value,{stream:true});
  const frames=pending.split('\n\n');pending=frames.pop()||'';
  for(const frame of frames){
   const data=frame.split('\n').find(line=>line.startsWith('data: '));if(!data)continue;
   try{const event=JSON.parse(data.slice(6));output.append(document.createTextNode(JSON.stringify(event)+'\n'));output.scrollTop=output.scrollHeight;}
   catch(_){ }
  }
 }
}
async function action(job,id,label){
 if(!confirm(label+' job '+id+'?'))return;
 const path=job==='verification-review'?'/api/jobs/'+id+'/verification-review':'/api/jobs/'+id+'/'+job;
 try{const result=await api(path,'POST',job==='wake'?{reason:'Owner signaled the external event'}:undefined);
  if(job==='verification-review')alert(JSON.stringify(result.report,null,2));await refresh();}
 catch(e){$('error').textContent=e.message;}
}
async function refresh(){
 try{
  const jobs=await api('/api/jobs');const root=$('jobs');root.replaceChildren();
  if(!jobs.length){root.append(el('p','No jobs yet'));return;}
  for(const j of jobs){
   const card=el('article',undefined,'job');
   card.append(el('div',j.status+' · '+j.id,'status'),el('p',j.instruction,'instruction'));
   const usage=(j.input_tokens||0)+(j.output_tokens||0);
   const usageLabel='Tokens '+usage.toLocaleString()+(j.estimated_tokens?' (includes estimates)':'');
   const costLabel=j.cost_configured?' · Estimated cost $'+Number(j.estimated_cost_usd||0).toFixed(6):'';
   const durationLabel=j.execution_ms==null?'':' · Elapsed '+(j.execution_ms/1000).toFixed(1)+'s';
   card.append(el('small','Attempt '+j.attempt+' · '+(j.engine||'native')+' / '+(j.provider||'configured default')+' · GUI actions '+(j.action_count||0)+' · '+usageLabel+costLabel+' · Model time '+((j.model_latency_ms||0)/1000).toFixed(1)+'s'+durationLabel+' · Plan v'+(j.plan_version||0)+' · Created '+j.created_at));
   if(j.pending_question)card.append(el('pre','Qbrowser needs your answer: '+j.pending_question));
   if(j.pending_approval)card.append(el('pre','Approval requested: '+j.pending_approval));
   if(j.last_error)card.append(el('pre',j.last_error));
   if(j.result)card.append(el('pre',j.result));
   const bar=el('div',undefined,'row');
   const actions = j.status==='verify'?[['verification-review','Run independent review'],['verified','Mark verified']]
    :['needs_review','paused','failed'].includes(j.status)?[['requeue','Requeue']]
    :j.status==='queued'?[['pause','Pause'],['cancel','Cancel']]
    :j.status==='waiting'?[['wake','Wake after external event'],['cancel','Cancel']]
    :j.status==='running'&&!j.stop_requested?[['stop','Request stop']]:[];
   if(j.status==='waiting_approval')actions.push(['approve-external','Approve this proposed action']);
   else if(!j.external_approved&&j.status==='queued')actions.push(['approve-external','Pre-approve external actions']);
   for(const [k,v] of actions){const b=el('button',v);b.onclick=()=>action(k,j.id,v);bar.append(b);}
   const stages=el('button','Milestones');stages.onclick=async()=>{
    try{const stages=await api('/api/jobs/'+j.id+'/checkpoints');
      alert(stages.length?JSON.stringify(stages,null,2):'No milestones recorded');}
    catch(e){$('error').textContent=e.message;}
   };bar.append(stages);
   const assessmentBox=el('details',undefined,'thread');
   const assessmentSummary=el('summary','Task assessment');assessmentBox.append(assessmentSummary);
   assessmentSummary.onclick=async()=>{
    if(assessmentBox.dataset.loaded)return;
    try{
     const a=await api('/api/jobs/'+j.id+'/assessment');
     if(!a){assessmentBox.append(el('small','Assessment will be recorded by the agent before execution.'));}
     else{
      assessmentBox.append(el('small',`${a.feasibility.toUpperCase()} · ${a.complexity} complexity · estimated ${a.estimated_steps} actions / ${a.estimated_minutes} minutes`));
      for(const [label,items] of [['Requirements',a.requirements],['Success criteria',a.success_criteria],['Assumptions',a.assumptions],['Open questions',a.ambiguities]]){
       if(items.length)assessmentBox.append(el('p',label+':\n'+items.map(x=>'• '+x).join('\n'),'message'));
      }
     }
     assessmentBox.dataset.loaded='1';
    }catch(e){$('error').textContent=e.message;}
   };card.append(assessmentBox);
   const files=el('button','Documents');files.onclick=()=>showDocuments(j.id,card);
   bar.append(files);
   const activity=el('details',undefined,'thread');
   const activitySummary=el('summary','Live activity');activity.append(activitySummary);
   const activityOutput=el('pre');activityOutput.setAttribute('aria-live','polite');activity.append(activityOutput);
   let streamController;
   activity.addEventListener('toggle',()=>{
    if(activity.open&&!streamController){
     const controller=new AbortController();streamController=controller;activeLiveStreams.add(controller);
     liveEvents(j.id,activityOutput,controller.signal).catch(e=>{
      if(e.name!=='AbortError')$('error').textContent=e.message;
     }).finally(()=>{activeLiveStreams.delete(controller);if(streamController===controller)streamController=null;});
    }else if(!activity.open&&streamController){streamController.abort();activeLiveStreams.delete(streamController);streamController=null;}
   });
   bar.append(activity);card.append(bar);root.append(card);
   const planBox=el('details',undefined,'thread');
   const planSummary=el('summary','Task plan');planBox.append(planSummary);
   planSummary.onclick=async()=>{
    if(planBox.dataset.loaded)return;
    try{const data=await api('/api/jobs/'+j.id+'/plan');
     planBox.append(el('small',data.total?`Progress ${data.completed}/${data.total} (${data.percent}%)`:'No plan saved yet'));
     for(const item of data.items){
      planBox.append(el('p',`${item.state.toUpperCase()} · ${item.title}\nAcceptance: ${item.acceptance}${item.evidence?'\nEvidence: '+item.evidence:''}`,'message'));
     }
     planBox.dataset.loaded='1';
    }catch(e){$('error').textContent=e.message;}
   };card.append(planBox);
   const thread=el('details',undefined,'thread');
   const threadSummary=el('summary','Conversation and steering');thread.append(threadSummary);
   threadSummary.onclick=async()=>{
    if(thread.dataset.loaded)return;
    try{
     const messages=await api('/api/jobs/'+j.id+'/messages');
     const list=el('div');list.className='message-list';
     for(const m of messages)list.append(el('div',`${m.role==='user'?'You':'Qbrowser'} · ${m.created_at}\n${m.content}`,'message '+m.role));
     thread.append(list);
     const input=el('textarea');input.className='compose';input.placeholder='Add an instruction or answer a question';
     const send=el('button','Send');send.onclick=async()=>{
      if(!input.value.trim())return;
      try{await api('/api/jobs/'+j.id+'/messages','POST',{content:input.value});input.value='';await refresh();}
      catch(e){$('error').textContent=e.message;}
     };
     thread.append(input,send);thread.dataset.loaded='1';
    }catch(e){$('error').textContent=e.message;}
   };card.append(thread);
   const evidence=el('details',undefined,'thread');
   const evidenceSummary=el('summary','Screenshot evidence');evidence.append(evidenceSummary);
   evidenceSummary.onclick=async()=>{
    if(evidence.dataset.loaded)return;
    try{
     const items=await api('/api/jobs/'+j.id+'/evidence');
     if(!items.length)evidence.append(el('small','No encrypted milestone screenshots are stored.'));
     for(const item of items){
      const row=el('div',`${item.kind} · ${item.captured_at} · ${item.bytes} encrypted bytes`,'message');
      const view=el('button','View screenshot');view.onclick=async()=>{
       try{const img=el('img');img.className='evidence-image';img.alt='Decrypted local Qbrowser screenshot evidence';img.src=await blob('/api/jobs/'+j.id+'/evidence/'+item.id);row.append(img);}
       catch(e){$('error').textContent=e.message;}
      };row.append(view);evidence.append(row);
     }
     evidence.dataset.loaded='1';
    }catch(e){$('error').textContent=e.message;}
   };card.append(evidence);
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
async function showMetrics(){
 const panel=$('metrics-panel');panel.hidden=!panel.hidden;if(panel.hidden)return;
 try{
  const m=await api('/api/metrics');panel.replaceChildren(el('h3','Usage and reliability'));
  panel.append(el('p',`Jobs ${m.jobs} · Input tokens ${m.input_tokens.toLocaleString()} · Output tokens ${m.output_tokens.toLocaleString()} · Estimated tokens ${m.estimated_tokens.toLocaleString()}`));
  panel.append(el('p',`Estimated cost $${Number(m.estimated_cost_usd).toFixed(4)} across ${m.cost_configured_jobs} configured jobs · Average model latency ${(m.average_model_latency_ms/1000).toFixed(1)}s · Average execution ${(m.average_execution_ms/1000).toFixed(1)}s`));
  for(const provider of m.providers)panel.append(el('p',`${provider.provider} / ${provider.model} · ${provider.jobs} jobs · ${Number(provider.estimated_cost_usd).toFixed(4)} USD estimated`));
  panel.append(el('p','Job states: '+JSON.stringify(m.jobs_by_status)));
 }catch(e){$('error').textContent=e.message;}
}
$('metrics-toggle').onclick=showMetrics;
async function refreshSchedules(){
 try{
  const items=await api('/api/schedules'),root=$('schedules');root.replaceChildren();
  for(const item of items){
   const row=el('div',`${item.enabled?'Active':'Disabled'} · ${item.instruction} · every ${item.interval_seconds}s · next ${item.next_run_at}`,'message');
   if(item.enabled){const stop=el('button','Disable');stop.onclick=async()=>{try{await api('/api/schedules/'+item.id,'DELETE');await refreshSchedules();}catch(e){$('error').textContent=e.message;}};row.append(stop);}
   root.append(row);
  }
 }catch(e){$('error').textContent=e.message;}
}
$('schedule-add').onclick=async()=>{
 const instruction=$('recurring-instruction').value.trim(),interval=Number($('recurring-interval').value);
 if(!instruction)return;
 try{await api('/api/schedules','POST',{instruction,interval_seconds:interval});$('recurring-instruction').value='';await refreshSchedules();}
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
  const latestVersion=Math.max(...data.filter(x=>x.role===f.role&&x.filename===f.filename).map(x=>x.version));
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
   if(f.version<latestVersion){
    const restore=el('button','Restore as new latest');
    restore.onclick=async()=>{
     if(!confirm('Restore '+f.filename+' v'+f.version+' as a new latest revision?'))return;
     try{
      await api('/api/jobs/'+id+'/documents/'+f.role+'/'+encodeURIComponent(f.filename)+'/restore','POST',{version:f.version});
      await showDocuments(id,card);
     }catch(e){$('error').textContent=e.message;}
    };
    line.append(restore);
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
 function stopLiveStreams(){for(const stream of activeLiveStreams)stream.abort();activeLiveStreams.clear();}
window.addEventListener('pagehide',stopLiveStreams);
$('desktop-shot').onclick=async()=>{
 try{$('desktop-preview').src=await blob('/api/desktop/screenshot');$('desktop-preview').hidden=false;}
 catch(e){$('error').textContent=e.message;}
};
if('serviceWorker' in navigator)navigator.serviceWorker.register('/service-worker.js').catch(()=>{});
$('refresh').onclick=refresh;setInterval(()=>{if(!document.activeElement.classList?.contains('compose')&&!activeLiveStreams.size)refresh()},5000);refresh();
refreshSchedules();setInterval(refreshSchedules,30000);
</script></body></html>"""


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTML

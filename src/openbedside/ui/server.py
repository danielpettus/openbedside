"""The human access page: Activity, Devices (registration) and Patients (association).

Standard library only, bound to 127.0.0.1 by default so it is not exposed to a
network until someone decides it should be.

Who may change things:
  * with an admin password set (openbedside set-password): anyone signed in;
  * with no password set: only a browser on this same computer.
Everyone else can look, with patient identifiers masked to their last four
characters. Changes are POSTs that must carry the X-OpenBedside header; a browser
will not send a custom header cross-site without a preflight this server never
answers, and the session cookie is SameSite=Strict.
"""
from __future__ import annotations

import ipaddress
import json
import secrets
import threading
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Optional

from ..core.association import mask
from ..store.registry import DeviceEntry, Registry

SESSION_SECONDS = 8 * 3600

PAGE = r"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>OpenBedside gateway</title>
<style>
 :root{--ink:#1B2A38;--slate:#2C3E50;--mid:#5D7A94;--light:#8FA3B3;--pale:#DCE6EC;--wash:#F2F6F8}
 body{margin:0;font:15px/1.5 -apple-system,Segoe UI,Helvetica,Arial,sans-serif;background:var(--wash);color:var(--ink)}
 header{background:var(--slate);color:#fff;padding:0 20px;display:flex;align-items:center;gap:22px;flex-wrap:wrap}
 header b{font-size:17px;padding:12px 0} nav a{color:#C9D6E0;text-decoration:none;padding:14px 4px;display:inline-block;margin-right:10px;border-bottom:3px solid transparent}
 nav a.on{color:#fff;border-bottom-color:#8FA3B3} .sp{flex:1} header .who{font-size:13px;color:#C9D6E0}
 header button{background:transparent;color:#fff;border:1px solid #8FA3B3;border-radius:5px;padding:4px 10px;cursor:pointer}
 .warn{background:#EEF2F5;color:var(--slate);padding:7px 20px;font-size:13px;border-bottom:1px solid var(--pale)}
 .warn b{color:var(--ink)}
 main{max-width:1150px;margin:0 auto;padding:16px 20px}
 .card{background:#fff;border:1px solid var(--pale);border-radius:8px;padding:14px 16px;margin:0 0 14px}
 h2{font-size:12px;letter-spacing:1.4px;text-transform:uppercase;color:var(--mid);margin:0 0 8px}
 table{width:100%;border-collapse:collapse;font-size:13px} th{text-align:left;color:var(--mid);font-weight:600;border-bottom:1px solid var(--pale);padding:4px 6px}
 td{padding:5px 6px;border-bottom:1px solid #EEF2F5;vertical-align:top} .k{color:var(--mid)}
 .pill{display:inline-block;padding:1px 8px;border-radius:10px;background:var(--pale);font-size:12px}
 .infusing,.associated{background:#DDEBF6}.kvo{background:#FFF3D6}.idle,.paused,.none{background:#EEE}
 .conflict,.review,.alarm{background:#F4E1DD;color:#6B2A20}
 .derived{color:var(--light);font-size:11px}
 .btn{background:var(--slate);color:#fff;border:0;border-radius:5px;padding:5px 11px;font-size:13px;cursor:pointer}
 .btn.alt{background:#fff;color:var(--slate);border:1px solid var(--light)} .btn:disabled{opacity:.4;cursor:default}
 form.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:10px 14px}
 label{font-size:12px;color:var(--mid);display:block} input,select{width:100%;box-sizing:border-box;padding:6px 8px;border:1px solid var(--pale);border-radius:5px;font-size:14px;background:#fff}
 .checks label{display:flex;gap:6px;align-items:center;color:var(--ink);font-size:13px;margin:3px 0} .checks input{width:auto}
 .msg{font-size:13px;margin-top:8px} .err{color:#6B2A20} .ok{color:#2F5E3A}
 .view{display:none} .view.on{display:block}
 dialog{border:1px solid var(--pale);border-radius:8px;padding:18px 20px;max-width:340px}
</style></head><body>
<header><b>OpenBedside</b>
 <nav><a href="#activity" data-v="activity">Activity</a><a href="#devices" data-v="devices">Devices</a><a href="#patients" data-v="patients">Patients</a></nav>
 <span class="sp"></span><span class="who" id="who"></span><span id="ver" class="who"></span></header>
<div class="warn"><b>Not a medical device. Not for clinical use.</b> Test data only: use synthetic patient identifiers, this release has no encryption. Auto-programming works against the simulator only.</div>
<div class="warn" id="pwwarn" style="display:none">No admin password is set, so changes are allowed only from a browser on this computer. Set one with <code>openbedside set-password</code>.</div>
<main>
<section class="view" id="v-activity">
 <div class="card"><h2>Status</h2><div id="status"></div></div>
 <div class="card"><h2>Device connections (adapters)</h2><div id="adapters"></div></div>
 <div class="card"><h2>Devices</h2><div id="devices"></div></div>
 <div class="card"><h2>Events</h2><div id="events"></div></div>
 <div class="card"><h2>Outbound to EHR</h2><div id="outbox"></div></div>
 <div class="card"><h2>Orders received (PCD-03)</h2><div id="orders"></div></div>
</section>
<section class="view" id="v-devices">
 <div class="card"><h2>Seen but not sent</h2><div class="k" style="font-size:13px;margin-bottom:6px">Devices an adapter can hear that are not registered, are retired, or are registered as a different type. Nothing from them reaches the EHR.</div><div id="unreg"></div></div>
 <div class="card"><h2>Registered devices</h2><div id="reg"></div></div>
 <div class="card"><h2 id="formtitle">Register a device</h2>
  <form class="grid" id="devform" onsubmit="return saveDevice(event)">
   <div><label>Device type</label><select name="device_type"><option value="infusion_pump">Infusion pump</option><option value="ventilator">Ventilator</option><option value="other">Other</option></select></div>
   <div><label>Vendor (as the adapter reports it)</label><input name="vendor" required></div>
   <div><label>Device id (serial, as the device reports it)</label><input name="device_id" required></div>
   <div><label>Model</label><input name="model"></div>
   <div><label>Hospital asset tag</label><input name="asset_tag"></div>
   <div><label>EUI-64 (16 hex characters, if it has one)</label><input name="eui64" maxlength="16"></div>
   <div><label>Send to the EHR as (what the nurse scans)</label><select name="ehr_id_from"><option value="serial">Serial number</option><option value="asset">Asset tag</option><option value="eui64">EUI-64</option></select></div>
   <div><label>Unit (point of care)</label><input name="unit"></div>
   <div><label>Room</label><input name="room"></div>
   <div><label>Bed</label><input name="bed"></div>
   <div><label>Facility</label><input name="facility"></div>
   <div><label>Connection binding (only for devices that never send an id)</label><input name="binding" placeholder="adapter:port"></div>
   <div class="checks"><label style="color:var(--mid)">Find this device's patient from</label>
    <label><input type="checkbox" name="assoc_device" checked> the device (wristband scanned at the device)</label>
    <label><input type="checkbox" name="assoc_adt"> ADT: the patient in this device's bed</label>
    <label><input type="checkbox" name="assoc_manual" checked> a person, on the Patients page</label></div>
   <div><label>Notes</label><input name="notes"></div>
   <div style="align-self:end"><button class="btn" id="savebtn">Save</button> <button class="btn alt" type="button" onclick="clearForm()">Clear</button><div class="msg" id="devmsg"></div></div>
  </form></div>
</section>
<section class="view" id="v-patients">
 <div class="card"><h2>Device to patient</h2><div class="k" style="font-size:13px;margin-bottom:6px">Every source allowed for a device must agree, or no patient is sent. A patient change from ADT alone while a device is running waits for a person to confirm.</div><div id="assoc"></div></div>
 <div class="card"><h2>Associate a device with a patient</h2>
  <form class="grid" id="assocform" onsubmit="return setAssoc(event)">
   <div><label>Device</label><select name="key" id="assockey"></select></div>
   <div><label>Patient identifier (MRN)</label><input name="patient_id" required></div>
   <div><label>Assigning authority</label><input name="authority" placeholder="default from config"></div>
   <div style="align-self:end"><button class="btn">Associate</button><div class="msg" id="assocmsg"></div></div>
  </form></div>
 <div class="card"><h2>Census from ADT</h2><div id="census"></div><div class="k" style="font-size:12px;margin-top:6px" id="adtlog"></div></div>
</section>
</main>
<dialog id="login"><form onsubmit="return signIn(event)"><h2>Sign in</h2><label>Admin password</label><input type="password" name="pw" autocomplete="current-password"><div style="margin-top:12px"><button class="btn">Sign in</button> <button class="btn alt" type="button" onclick="document.getElementById('login').close()">Cancel</button></div><div class="msg err" id="loginmsg"></div></form></dialog>
<script>
let S=null;const T={infusion_pump:'Infusion pump',ventilator:'Ventilator',other:'Other'};
function q(v){return v==null?'':((Math.round(v.value*1000)/1000)+' '+v.ucum)}
function esc(s){return String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function loc(l){return l?[l.unit,l.room,l.bed].filter(x=>x).join(' '):''}
function table(head,rows){return rows.length?'<table><tr>'+head.map(h=>'<th>'+h+'</th>').join('')+'</tr>'+rows.join('')+'</table>':'<span class="k">None.</span>'}
async function post(url,body){const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json','X-OpenBedside':'1'},body:JSON.stringify(body)});let j={};try{j=await r.json()}catch(e){}if(!r.ok&&!j.error)j.error='HTTP '+r.status;return j}
function show(){const v=(location.hash||'#activity').slice(1);document.querySelectorAll('.view').forEach(e=>e.classList.toggle('on',e.id=='v-'+v));document.querySelectorAll('nav a').forEach(a=>a.classList.toggle('on',a.dataset.v==v))}
window.addEventListener('hashchange',show);show();
function renderAuth(){const a=S.auth;document.getElementById('pwwarn').style.display=a.password_set?'none':'block';
 document.getElementById('who').innerHTML=a.admin?(a.password_set?'signed in <button onclick="signOut()">Sign out</button>':'this computer: changes allowed'):(a.password_set?'<button onclick="document.getElementById(\'login\').showModal()">Sign in</button>':'view only');
 document.querySelectorAll('#devform button,#devform input,#devform select,#assocform button,#assocform input,#assocform select').forEach(e=>e.disabled=!a.admin)}
function renderActivity(){
 document.getElementById('status').innerHTML='EHR link: <b>'+(S.ehr.connected?'connected':'not connected')+'</b> '+esc(S.ehr.last_error)+' &nbsp; Queue: '+Object.entries(S.outbox.counts).map(([k,v])=>esc(k)+' '+v).join(', ')+(S.unregistered.length?' &nbsp; <a href="#devices">'+S.unregistered.length+' device(s) seen but not sent</a>':'');
 document.getElementById('adapters').innerHTML=table(['Adapter','Connection','Messages','Skipped','Last error'],(S.adapters||[]).map(a=>'<tr><td>'+esc(a.name)+'</td><td><span class="pill '+(a.connected?'infusing':'')+'">'+(a.connected?'connected':'not connected')+'</span></td><td>'+esc(a.messages==null?'n/a':a.messages)+'</td><td>'+esc(a.skipped==null?'n/a':a.skipped)+'</td><td class="k">'+esc(a.last_error)+'</td></tr>'));
 const rows=[];for(const d of S.devices){const as=d.association||{};for(const m of d.modules){for(const c of m.channels){
  rows.push('<tr><td>'+esc(d.ehr_id||d.device_id)+(d.online?'':' <span class="pill">offline</span>')+'<div class="k">'+esc(d.vendor+' '+d.model)+(loc(d.location)?' &middot; '+esc(loc(d.location)):'')+'</div></td><td><span class="pill '+esc(as.state)+'">'+esc(as.patient||as.state||'')+'</span><div class="k">'+esc(as.source||'')+'</div></td><td>'+esc(m.module_id)+'</td><td><span class="pill '+esc(c.status)+'">'+esc(c.status)+'</span></td><td>'+esc(q(c.actual_rate))+'</td><td>'+
  c.sources.map(x=>'<b>'+esc(x.role)+'</b> '+esc(x.drug_name||'')+' '+esc(q(x.rate))+(x.dose_rate?' ('+esc(q(x.dose_rate))+')':'')+'<div class="k">VTBI '+esc(q(x.vtbi))+', remaining '+esc(q(x.vtbi_remaining))+', delivered '+esc(q(x.volume_delivered))+'</div>').join('')+'</td></tr>')}}}
 document.getElementById('devices').innerHTML=table(['Device','Patient','Module','Status','Actual rate','Sources'],rows);
 document.getElementById('events').innerHTML=table(['When (UTC)','Event','Where','Detail'],S.events.map(e=>'<tr><td>'+esc(e.at.slice(11,19))+'</td><td>'+esc(e.kind)+(e.derived?' <span class="derived">derived</span>':'')+'</td><td>'+esc(e.module_id||e.device_id)+'</td><td class="k">'+esc(e.detail)+'</td></tr>'));
 document.getElementById('outbox').innerHTML=table(['#','Control id','Status','Tries','Last result'],S.outbox.recent.map(o=>'<tr><td>'+o.id+'</td><td>'+esc(o.control_id)+'</td><td>'+esc(o.status)+'</td><td>'+o.attempts+'</td><td class="k">'+esc(o.last_result)+'</td></tr>'));
 document.getElementById('orders').innerHTML=table(['Control id','Order','Result','Text'],S.orders.map(o=>'<tr><td>'+esc(o.control_id)+'</td><td>'+esc(o.order)+'</td><td>'+esc(o.result)+'</td><td class="k">'+esc(o.text)+'</td></tr>'));}
function renderDevices(){const ad=S.auth.admin;
 document.getElementById('unreg').innerHTML=table(['Vendor','Device id','Model','Why held','Last seen',''],S.unregistered.map((u,i)=>'<tr><td>'+esc(u.vendor)+'</td><td>'+esc(u.device_id)+'</td><td>'+esc(u.model)+'</td><td>'+esc(u.reason)+'</td><td class="k">'+esc((u.last_seen||'').slice(11,19))+' UTC ('+u.count+')</td><td>'+(ad?'<button class="btn" onclick="fromUnreg('+i+')">'+(u.reason=='not registered'?'Register':'Edit')+'</button>':'')+'</td></tr>'));
 document.getElementById('reg').innerHTML=table(['Type','Vendor','Device id','EHR id','Location','Patient from','Status',''],S.registry.map((e,i)=>'<tr><td>'+esc(T[e.device_type]||e.device_type)+'</td><td>'+esc(e.vendor)+'</td><td>'+esc(e.device_id)+'<div class="k">'+esc(e.model)+'</div></td><td>'+esc(e.ehr_id)+'<div class="k">'+esc(e.ehr_id_from)+'</div></td><td>'+esc(loc(e))+'</td><td class="k">'+[e.assoc_device?'device':'',e.assoc_adt?'ADT':'',e.assoc_manual?'manual':''].filter(x=>x).join(', ')+'</td><td><span class="pill '+(e.status=='active'?'associated':'none')+'">'+esc(e.status)+'</span></td><td>'+(ad?'<button class="btn alt" onclick="editReg('+i+')">Edit</button> <button class="btn alt" onclick="toggleStatus('+i+')">'+(e.status=='active'?'Retire':'Reactivate')+'</button>':'')+'</td></tr>'));}
function renderPatients(){const ad=S.auth.admin;
 document.getElementById('assoc').innerHTML=table(['Device','Location','Association','Patient','Detail',''],S.devices.map(d=>{const a=d.association||{};return '<tr><td>'+esc(d.ehr_id||d.device_id)+'<div class="k">'+esc(d.vendor)+'</div></td><td>'+esc(loc(d.location))+'</td><td><span class="pill '+esc(a.state)+'">'+esc(a.state)+'</span></td><td>'+esc(a.patient||'')+'<div class="k">'+esc(a.source||'')+'</div></td><td class="k">'+esc(a.detail)+'</td><td>'+(ad?(a.state=='review'?'<button class="btn" onclick="confirmNew(\''+esc(d.key)+'\')">Confirm new patient</button> ':'')+(a.manual?'<button class="btn alt" onclick="clearAssoc(\''+esc(d.key)+'\')">Clear manual</button>':''):'')+'</td></tr>'}));
 const sel=document.getElementById('assockey');const cur=sel.value;sel.innerHTML=S.devices.map(d=>'<option value="'+esc(d.key)+'">'+esc((d.ehr_id||d.device_id)+' ('+d.vendor+')')+'</option>').join('');if(cur)sel.value=cur;
 document.getElementById('census').innerHTML=table(['Patient','Visit','Class','Location','Last event'],S.census.map(c=>'<tr><td>'+esc(c.patient_id)+'<div class="k">'+esc(c.authority)+'</div></td><td>'+esc(c.visit)+'</td><td>'+esc(c.patient_class)+'</td><td>'+esc(loc(c))+'</td><td class="k">'+esc(c.last_event)+'</td></tr>'));
 document.getElementById('adtlog').textContent=S.adt_log.length?'Recent ADT: '+S.adt_log.slice(-5).reverse().map(a=>a.event+' '+a.patient+' '+a.result).join(' · '):'No ADT messages received yet.';}
async function tick(){try{const r=await fetch('/api/state');S=await r.json();document.getElementById('ver').textContent='v'+S.version;renderAuth();renderActivity();renderDevices();renderPatients()}catch(e){document.getElementById('status').textContent='Gateway not responding: '+e}}
const F=document.getElementById('devform');
function fill(o){for(const el of F.elements){if(!el.name)continue;if(el.type=='checkbox')el.checked=!!o[el.name];else el.value=o[el.name]==null?'':o[el.name]}}
function clearForm(){F.reset();document.getElementById('formtitle').textContent='Register a device';document.getElementById('devmsg').textContent=''}
function fromUnreg(i){const u=S.unregistered[i];const e=S.registry.find(r=>r.vendor==u.vendor&&r.device_id==u.device_id);if(e){editReg(S.registry.indexOf(e));return}clearForm();fill({vendor:u.vendor,device_id:u.device_id,model:u.model,device_type:u.kind,assoc_device:true,assoc_manual:true,ehr_id_from:'serial'});F.scrollIntoView({behavior:'smooth'})}
function editReg(i){fill(S.registry[i]);document.getElementById('formtitle').textContent='Edit '+S.registry[i].device_id;F.scrollIntoView({behavior:'smooth'})}
async function saveDevice(ev){ev.preventDefault();const o={};for(const el of F.elements){if(el.name)o[el.name]=el.type=='checkbox'?el.checked:el.value.trim()}
 const j=await post('/api/registry',o);const m=document.getElementById('devmsg');if(j.ok){m.className='msg ok';m.textContent='Saved.';tick()}else{m.className='msg err';m.textContent=(j.errors||[j.error]).join('; ')}return false}
async function toggleStatus(i){const e=S.registry[i];await post('/api/registry/status',{vendor:e.vendor,device_id:e.device_id,status:e.status=='active'?'retired':'active'});tick()}
async function setAssoc(ev){ev.preventDefault();const f=document.getElementById('assocform');const j=await post('/api/association',{action:'set',key:f.key.value,patient_id:f.patient_id.value.trim(),authority:f.authority.value.trim()});const m=document.getElementById('assocmsg');m.className='msg '+(j.ok?'ok':'err');m.textContent=j.ok?'Associated.':j.error;if(j.ok){f.patient_id.value='';tick()}return false}
async function clearAssoc(k){await post('/api/association',{action:'clear',key:k});tick()}
async function confirmNew(k){const j=await post('/api/association',{action:'confirm',key:k});const m=document.getElementById('assocmsg');m.className='msg '+(j.ok?'ok':'err');m.textContent=j.ok?'Confirmed.':j.error;tick()}
async function signIn(ev){ev.preventDefault();const f=ev.target;const j=await post('/api/login',{password:f.pw.value});f.pw.value='';if(j.ok){document.getElementById('login').close();tick()}else document.getElementById('loginmsg').textContent=j.error||'Sign in failed';return false}
async function signOut(){await post('/api/logout',{});tick()}
tick();setInterval(tick,2000);
</script></body></html>"""


class Api:
    """What the page can read and do. `state` returns the engine's view; `changed` is
    called (from the web thread) after any change so the engine re-checks devices."""

    def __init__(self, registry: Optional[Registry], state: Callable[[], dict], engine=None,
                 changed: Callable[[], None] = lambda: None, default_authority: str = "HOSP"):
        self.registry = registry
        self.state_fn = state
        self.engine = engine
        self.changed = changed
        self.default_authority = default_authority
        self.sessions: dict[str, float] = {}
        self._lock = threading.Lock()

    # ---- who is asking -------------------------------------------------------------
    def is_admin(self, client_ip: str, token: Optional[str]) -> bool:
        if self.registry is None:
            return False
        if self.registry.has_admin_password():
            with self._lock:
                exp = self.sessions.get(token or "")
            return bool(exp and exp > time.time())
        try:
            return ipaddress.ip_address(client_ip).is_loopback
        except ValueError:
            return False

    def login(self, password: str) -> Optional[str]:
        if self.registry is None or not self.registry.check_admin_password(password):
            time.sleep(1.0)                              # slow down guessing
            return None
        token = secrets.token_urlsafe(32)
        with self._lock:
            self.sessions[token] = time.time() + SESSION_SECONDS
        return token

    def logout(self, token: Optional[str]) -> None:
        with self._lock:
            self.sessions.pop(token or "", None)

    # ---- reading ---------------------------------------------------------------------
    def state(self, admin: bool) -> dict:
        s = self.state_fn()
        reg = self.registry
        show = (lambda p: p) if admin else mask
        for d in s.get("devices", []):
            dec = self.engine.decisions.get(d["key"]) if self.engine else None
            p = d.pop("patient", None)
            d.pop("reported_patient_id", None)
            if dec is not None:
                d["association"] = {"state": dec.state, "patient": show(p["patient_id"]) if p else "",
                                    "source": p["source"] if p else "", "detail": dec.detail,
                                    "manual": bool(reg and reg.get_manual(d["key"]))}
        s["unregistered"] = list(self.engine.unregistered.values()) if self.engine else []
        s["registry"] = []
        s["census"] = []
        if reg is not None:
            for e in reg.devices():
                row = e.__dict__.copy()
                row["ehr_id"] = e.ehr_id()
                s["registry"].append(row)
            s["census"] = [dict(c.__dict__, patient_id=show(c.patient_id)) for c in reg.census()]
        s["auth"] = {"admin": admin, "password_set": bool(reg and reg.has_admin_password())}
        return s

    # ---- changing --------------------------------------------------------------------
    def save_device(self, body: dict) -> dict:
        allowed = {f for f in DeviceEntry.__dataclass_fields__ if f not in ("created", "updated")}
        data = {k: v for k, v in body.items() if k in allowed}
        for b in ("assoc_device", "assoc_adt", "assoc_manual"):
            data[b] = bool(data.get(b))
        for k, v in list(data.items()):
            if not isinstance(v, bool):
                data[k] = str(v or "").strip()[:120]
        if not data.get("status"):
            old = self.registry.get(data.get("vendor", ""), data.get("device_id", ""))
            data["status"] = old.status if old else "active"
        try:
            entry = DeviceEntry(**data)
        except TypeError as e:
            return {"ok": False, "errors": [str(e)]}
        errors = self.registry.save_device(entry)
        if errors:
            return {"ok": False, "errors": errors}
        self.changed()
        return {"ok": True}

    def set_status(self, body: dict) -> dict:
        ok = self.registry.set_status(str(body.get("vendor", "")), str(body.get("device_id", "")),
                                      str(body.get("status", "")))
        self.changed()
        return {"ok": ok} if ok else {"ok": False, "error": "no such device or bad status"}

    def association(self, body: dict) -> dict:
        key = str(body.get("key", ""))
        vendor, _, device_id = key.partition("|")
        entry = self.registry.get(vendor, device_id)
        if entry is None:
            return {"ok": False, "error": "register the device first"}
        action = body.get("action")
        if action == "set":
            pid = str(body.get("patient_id", "")).strip()
            if not pid or any(c in pid for c in "|^~\\&\r\n"):
                return {"ok": False, "error": "a patient identifier is required, without | ^ ~ \\ &"}
            if not entry.assoc_manual:
                return {"ok": False, "error": "this device's registration does not allow manual association"}
            self.registry.set_manual(key, pid, str(body.get("authority", "")).strip()[:40], "page")
        elif action == "clear":
            self.registry.clear_manual(key)
        elif action == "confirm":
            dec = self.engine.decisions.get(key) if self.engine else None
            if not dec or dec.state != "review" or not dec.candidates:
                return {"ok": False, "error": "nothing to confirm for this device"}
            if not entry.assoc_manual:
                return {"ok": False, "error": "enable manual association on this device to confirm"}
            c = dec.candidates[0]
            self.registry.set_manual(key, c.patient_id, c.authority, "page: confirmed ADT change")
        else:
            return {"ok": False, "error": "unknown action"}
        self.changed()
        return {"ok": True}


def serve(host: str, port: int, api: Api) -> ThreadingHTTPServer:
    class H(BaseHTTPRequestHandler):
        def _token(self) -> Optional[str]:
            c = SimpleCookie(self.headers.get("Cookie", ""))
            return c["ob_session"].value if "ob_session" in c else None

        def _admin(self) -> bool:
            return api.is_admin(self.client_address[0], self._token())

        def _send(self, code: int, body: bytes, ctype: str, extra: Optional[list] = None) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            for k, v in extra or []:
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj: Any, code: int = 200, extra: Optional[list] = None) -> None:
            self._send(code, json.dumps(obj, default=str).encode(), "application/json", extra)

        def do_GET(self):
            if self.path == "/api/state":
                self._json(api.state(self._admin()))
            elif self.path in ("/", "/index.html"):
                self._send(200, PAGE.encode(), "text/html; charset=utf-8")
            else:
                self.send_error(404)

        def do_POST(self):
            if self.headers.get("X-OpenBedside") != "1":
                return self._json({"ok": False, "error": "missing X-OpenBedside header"}, 403)
            try:
                n = min(int(self.headers.get("Content-Length", "0")), 20000)
                body = json.loads(self.rfile.read(n) or b"{}")
                if not isinstance(body, dict):
                    raise ValueError
            except ValueError:
                return self._json({"ok": False, "error": "bad JSON"}, 400)
            if api.registry is None:
                return self._json({"ok": False, "error": "registry not available"}, 503)
            if self.path == "/api/login":
                token = api.login(str(body.get("password", "")))
                if not token:
                    return self._json({"ok": False, "error": "wrong password (or no password set)"}, 401)
                return self._json({"ok": True}, extra=[("Set-Cookie", f"ob_session={token}; HttpOnly; "
                                                                      f"SameSite=Strict; Path=/; Max-Age={SESSION_SECONDS}")])
            if self.path == "/api/logout":
                api.logout(self._token())
                return self._json({"ok": True}, extra=[("Set-Cookie", "ob_session=; Path=/; Max-Age=0")])
            if not self._admin():
                return self._json({"ok": False, "error": "sign in to make changes"}, 403)
            routes = {"/api/registry": api.save_device, "/api/registry/status": api.set_status,
                      "/api/association": api.association}
            fn = routes.get(self.path)
            if fn is None:
                return self.send_error(404)
            result = fn(body)
            self._json(result, 200 if result.get("ok") else 400)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer((host, port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv

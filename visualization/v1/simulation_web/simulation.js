import * as THREE from './vendor/three.module.min.js';
import {captureInput,isCurrentInput} from './input_protocol.js';

const $ = (id) => document.getElementById(id);
const keys = new Set();
const clientId = sessionStorage.getItem('simulation.client_id') || crypto.randomUUID();
sessionStorage.setItem('simulation.client_id',clientId);
let mutationChain = Promise.resolve(), mutationRevision = 0, heartbeatSending = false, inputGeneration = 0;
const ownsRun = () => !state?.run_id || state.owner_client_id === clientId;
const supportedKeys = new Set(['KeyW','KeyS','KeyA','KeyD','KeyT','ArrowLeft','ArrowRight','ArrowUp','ArrowDown']);
let catalog, state, selectedId, renderer, scene, camera, raf, pollTimer, heartbeatTimer, resizeObserver;
let disposed = false, polling = false, keySending = false, switching = false;
let lastFrame = performance.now(), fpsStart = lastFrame, frames = 0;
let orbitYaw = -Math.PI / 4, orbitPitch = .55, orbitDistance = 120, dragging;
let receivedAt = performance.now(), priorObjects = new Map();
const meshes = new Map(), trails = new Map(), predictions = new Map();
const diagnostics = window.simulationDiagnostics = {fps:0,frameCount:0,pollLatencyMs:0,keyLatencyMs:0,errors:0};
const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;
const fmt = (n, digits=1) => n!==null && n!==undefined && Number.isFinite(Number(n)) ? Number(n).toFixed(digits) : '—';
const running = () => state?.status === 'running';
function notice(message, error=false) { $('notice').textContent = message; $('notice').classList.toggle('error',error); }
async function api(path, body) {
  const write = body !== undefined;
  const dispatch = async () => {
    const revision = mutationRevision;
    const runAtRequest = state?.run_id;
    if (write && path !== 'create' && !ownsRun()) throw new Error('다른 창에서 조종 중입니다. 이 창은 관찰만 가능합니다.');
    if (write && path === 'keys' && (!running() || !isCurrentInput(body,state,inputGeneration))) { diagnostics.droppedStaleInputs=(diagnostics.droppedStaleInputs||0)+1;return state; }
    if(write && ['select','reset','pause','stop'].includes(path))inputGeneration++;
    const payload = write ? {...(typeof body === 'function' ? body() : body),client_id:clientId,run_id:state?.run_id??null} : undefined;
    if (write && path === 'keys') { payload.object_id=body.object_id; payload.keys=body.keys; delete payload.generation; }
    if (write) mutationRevision++;
    const response = await fetch(`/api/simulation/${path}`,{method:write?'POST':'GET',headers:write?{'Content-Type':'application/json'}:{},body:write?JSON.stringify(payload):undefined});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error?.message || result.error || result.message || `요청 실패 (${response.status})`);
    if (!write && path === 'state' && (revision !== mutationRevision || runAtRequest !== state?.run_id || (state && result.run_id === state.run_id && result.step < state.step))) return null;
    if (write && result.status) applyState(result);
    return result;
  };
  if (!write) return dispatch();
  const pending = mutationChain.then(dispatch);
  mutationChain = pending.catch(()=>{});
  return pending;
}
async function control(action, data={}) {
  try { const result = await api(action,data); applyState(result); return result; }
  catch (error) { diagnostics.errors++; notice(error.message,true); throw error; }
}
function option(value,label) { const item=document.createElement('option');item.value=value;item.textContent=label;return item; }
function addObject() {
  const host=$('object-configs');
  if (host.children.length >= catalog.defaults.max_objects) return;
  const row=document.createElement('div');row.className='object-config';
  const field=(label,name,type='number')=>{const wrap=document.createElement('label');wrap.textContent=label;const input=document.createElement(type==='select'?'select':'input');input.dataset.field=name;input.name=`${name}_${host.children.length}`;if(type!=='select'){input.type=type;input.step='any';input.required=true;}wrap.append(input);row.append(wrap);return input;};
  const kind=field('기체 종류','kind','select');catalog.aircraft.forEach(a=>kind.append(option(a.id,a.label)));
  const scenario=field('자동 시나리오','scenario_id','select');
  const east=field('East (m)','east'),north=field('North (m)','north'),alt=field('고도 (m)','altitude');
  const heading=field('방향 (°)','heading'),speed=field('속도 (m/s)','speed');
  const initialMode=field('초기 모드','initial_mode','select');initialMode.append(option('VTOL','VTOL'),option('CTOL','CTOL'));
  east.value=host.children.length * (catalog.ui?.initial_spacing_m ?? 0);north.value=0;heading.value=0;alt.min=0;alt.max=catalog.limits.max_altitude_m;speed.min=0;heading.min=-360;heading.max=360;
  const remove=document.createElement('button');remove.type='button';remove.textContent='삭제';remove.onclick=()=>{row.remove();updateRows();};row.append(remove);
  initialMode.onchange=()=>{const a=catalog.aircraft.find(a=>a.id===kind.value);if($('start-mode').value==='air'){speed.value=initialMode.value==='CTOL'?a.ctol_initial_speed_mps:0;speed.min=initialMode.value==='CTOL'?a.minimum_airborne_speed_mps:0;}};
  const update=()=>{const a=catalog.aircraft.find(a=>a.id===kind.value);scenario.replaceChildren(...a.scenarios.map(s=>option(s.id,s.label)));alt.value=$('start-mode').value==='ground'?0:a.initial_altitude_m;speed.value=$('start-mode').value==='ground'?0:a.initial_speed_mps;initialMode.parentElement.hidden=!kind.value.toLowerCase().includes('vtol');initialMode.value=kind.value==='fixed_wing'?'CTOL':'VTOL';speed.max=a.max_speed_mps;speed.min=$('start-mode').value==='air'&&initialMode.value==='CTOL'?a.minimum_airborne_speed_mps:0;updateRows();};
  kind.onchange=update;host.append(row);update();
}
function updateRows(){const rows=[...$('object-configs').children];$('object-count').value=rows.length;rows.forEach((row,index)=>{const automatic=$('mode').value==='scenario'||index>0;row.querySelector('[data-field=speed]').disabled=automatic||$('start-mode').value==='ground';row.querySelector('[data-field=speed]').parentElement.hidden=$('start-mode').value==='ground';row.querySelector('[data-field=initial_mode]').disabled=automatic;row.querySelector('[data-field=speed]').title=automatic?'시나리오가 초기 속도와 모드를 결정합니다.':'';row.querySelector('button').disabled=rows.length===1;row.querySelector('[data-field=scenario_id]').disabled=$('mode').value==='manual'&&rows.length===1;});$('add-object').disabled=rows.length>=catalog.defaults.max_objects;}
function readConfig(){return {mode:$('mode').value,start_mode:$('start-mode').value,seed:Number($('seed').value),sigma_m:Number($('sigma').value),objects:[...$('object-configs').children].map(row=>{const val=n=>row.querySelector(`[data-field=${n}]`).value;return {kind:val('kind'),position_enu_m:[Number(val('east')),Number(val('north')),Number(val('altitude'))],heading_deg:Number(val('heading')),initial_speed_mps:Number(val('speed')),initial_mode:val('initial_mode'),scenario_id:val('scenario_id')};}),model:$('model').value===''?null:{plugin_id:catalog.models[Number($('model').value)].plugin_id,run_id:catalog.models[Number($('model').value)].run_id,checkpoint:catalog.models[Number($('model').value)].checkpoint,checkpoint_sha256:catalog.models[Number($('model').value)].checkpoint_sha256,device:$('device').value}};}
async function switchTab(name){
  if(switching)return;switching=true;
  try{if(name==='settings'&&running()&&ownsRun()){keys.clear();await control('pause');}
    for(const n of ['settings','operation']){const active=n===name;$(n).hidden=!active;$(`tab-${n}`).setAttribute('aria-selected',String(active));$(`tab-${n}`).tabIndex=active?0:-1;}
    history.replaceState(null,'',`#${name}`);resize();
  }finally{switching=false;}
}
function clearKeys(){keys.clear();if(state&&running())void sendKeys(true);}
async function safePause(){clearKeys();if(running()&&ownsRun())try{await control('pause');}catch{ /* Error already visible. */ }}
async function sendKeys(edge=false){
 if((keySending&&!edge)||!running()||!ownsRun())return;
 if(!edge)keySending=true;
 const input=captureInput(keys,state,inputGeneration),start=performance.now();
 if(edge)diagnostics.inputEdges=(diagnostics.inputEdges||0)+1;
 try{await api('keys',input);diagnostics.keyLatencyMs=performance.now()-start;}
 catch(error){notice(error.message,true);}finally{if(!edge)keySending=false;}
}

function applyState(next){
  if(!next || !next.status)return;
  priorObjects=new Map((state?.objects||[]).map(o=>[o.id,o]));receivedAt=performance.now();state=next;
  if(!selectedId || !next.objects?.some(o=>o.id===selectedId) || next.config?.mode==='manual')selectedId=next.selected_id || next.objects?.[0]?.id;
  $('connection').textContent=ownsRun()?'Local 연결됨':'관찰 모드 · 다른 창에서 조종 중';$('flight-status').textContent=next.status;$('time').textContent=`${fmt(next.time_s)} s`;
  $('run-id').textContent=next.run_id?`Run: ${next.run_id}`:'';
  const active=['running','paused','ready'].includes(next.status);
  $('config-fields').disabled=active||!ownsRun();$('prepare').disabled=active||!ownsRun();
  const modelBlocked=['loading','failed'].includes(next.inference?.status);$('start').disabled=!ownsRun()||modelBlocked||next.status!=='ready';$('pause').disabled=!ownsRun()||!running();$('resume').disabled=!ownsRun()||modelBlocked||next.status!=='paused';$('stop').disabled=!ownsRun()||!active;$('reset').disabled=!ownsRun()||running();$('export').disabled=!ownsRun()||!next.run_id;
  const selectedNotice=next.objects?.find(o=>o.id===selectedId)?.notice;if(next.error)notice(typeof next.error==='string'?next.error:JSON.stringify(next.error),true);else if(selectedNotice||next.notice)notice(selectedNotice||next.notice);else notice(({ready:'준비되었습니다. Start를 눌러 비행을 시작하세요.',running:'비행 중입니다. Pause로 일시정지할 수 있습니다.',paused:'일시정지했습니다. Resume으로 비행을 이어갑니다.',stopped:'비행이 종료되었습니다. 결과를 확인하거나 새 실행을 준비하세요.',completed:'비행이 완료되었습니다. 평가 결과를 확인하세요.'})[next.status]||'설정에서 새 비행을 준비하세요.');
  const list=$('objects');const signature=(next.objects||[]).map(o=>o.id).join('|');
  if(list.dataset.signature!==signature){list.replaceChildren();for(const o of next.objects||[]){const b=document.createElement('button');b.dataset.id=o.id;b.onclick=async()=>{clearKeys();selectedId=o.id;try{if(state.config?.mode==='manual'&&ownsRun())await control('select',{object_id:o.id});updateSelection();renderEvaluation();}catch{/* Error already visible. */}};list.append(b);}list.dataset.signature=signature;}
  for(const b of list.children){const o=next.objects.find(o=>o.id===b.dataset.id);b.textContent=`${objectLabel(o)} · ${phaseLabel(o.phase)}`;b.title=o.id;b.setAttribute('aria-pressed',String(o.id===selectedId));}
  updateSelection();
  renderEvaluation();
  updateScene();
}
function updateSelection(){const o=state?.objects?.find(o=>o.id===selectedId);if(!o)return;$('selected-title').textContent=objectLabel(o);$('selected-title').title=o.id;
  const entries=[['비행 상태',phaseLabel(o.phase)],['조작 모드',o.control_mode],['속도',`${fmt(o.speed_mps)} m/s`],['고도',`${fmt(o.altitude_m??o.position_enu_m[2])} m`],['출력',fmt(o.power,2)],['천이 진행',`${fmt((o.transition_progress||0)*100,0)} %`]];
  $('telemetry').replaceChildren(...entries.flatMap(([label,value])=>{const dt=document.createElement('dt');dt.textContent=label;const dd=document.createElement('dd');dd.textContent=value;return [dt,dd];}));
  const rotary=['VTOL','helicopter','rotor'].includes(o.control_mode);
  $('keys-help').textContent=state.config?.mode==='scenario'?'시나리오 자동 비행\n대상 선택은 카메라만 변경합니다.':rotary?'W / S  출력 증가 / 감소\nA / D  좌 / 우 yaw\n↑ / ↓  아래 / 위 pitch\n← / →  좌 / 우 roll\nT  천이 명령 (VTOL 기체)':'W / S  가속 / 감속\n← / →  좌 / 우 선회\n↑ / ↓  상승 / 하강\nT  천이 명령 (VTOL 기체)';
}
function objectLabel(o){
  const kindLabel=catalog.aircraft.find(a=>a.id===o.kind)?.label||o.kind;
  const peers=(state?.objects||[]).filter(a=>a.kind===o.kind);
  return `${kindLabel} ${peers.findIndex(a=>a.id===o.id)+1}`;
}
function phaseLabel(phase){return ({ground:'지상',climb:'상승',descent:'하강',cruise:'순항',hover:'Hover',flight:'비행',landed:'착륙',crashed:'충돌 종료',failed:'실패',limit_reached:'한도 도달',transition:'천이 중'})[phase]||phase;}
function metricTable(headers,rows){
 const wrap=document.createElement('div');wrap.className='metric-table-wrap';const table=document.createElement('table');table.className='metric-table';
 const head=document.createElement('thead'),tr=document.createElement('tr');headers.forEach(label=>{const th=document.createElement('th');th.scope='col';th.textContent=label;tr.append(th);});head.append(tr);table.append(head);
 const body=document.createElement('tbody');rows.forEach(values=>{const row=document.createElement('tr');values.forEach((value,i)=>{const cell=document.createElement(i===0?'th':'td');if(i===0)cell.scope='row';cell.textContent=value;row.append(cell);});body.append(row);});table.append(body);wrap.append(table);return wrap;
}
function renderEvaluation(){
 const info=state?.inference||{status:'disabled'};
 const names={disabled:'모델 없이 비행',loading:'모델을 불러오는 중입니다. 준비가 끝나면 Start를 누르세요.',ready:'모델 준비됨 · 관측 이력이 모이면 예측합니다.',failed:'모델 연결 또는 추론에 실패했습니다. 실행을 종료하고 모델을 다시 선택하세요.'};
 $('inference').textContent=(info.status==='ready'&&state?.metrics?.prediction_count>0?'예측 갱신 중':names[info.status]||info.status)+(info.latency_ms!==undefined?`  추론 ${fmt(info.latency_ms)} ms`:'')+(info.skipped?`  건너뛴 요청 ${info.skipped}개`:'')+(info.error?`  ${info.error}`:'');
 const host=$('metrics'),m=state?.metrics;
 if(!m?.overall){host.textContent='아직 평가된 예측이 없습니다.';return;}
 const wasOpen=host.querySelector('details')?.open||false;host.replaceChildren();
 const caption=document.createElement('p');caption.className='help';caption.textContent=`발행 예측 ${m.prediction_count??0}개 · 전체 horizon 미완료 ${m.incomplete_predictions??0}개`;
 host.append(caption);
 const selected=state.objects?.find(o=>o.id===selectedId);const groups=[['전체 기체',m.overall]];if(selected&&m.by_object?.[selectedId])groups.push([objectLabel(selected),m.by_object[selectedId]]);
 const aggregateRows=[];
 for(const [label,group] of groups)for(const [key,name] of [['truth','Simulation truth'],['observed','미래 관측']]){const v=group[key];if(v)aggregateRows.push([label,name,fmt(v.ade_m,2),fmt(v.fde_m,2),String(v.count)]);}
 host.append(metricTable(['대상','비교 기준','ADE (m)','FDE (m)','완료 예측 수'],aggregateRows));
 const horizons=[];for(const [label,group] of groups){const times=Object.keys(group.truth?.horizons||{}).sort((a,b)=>Number(a)-Number(b));for(const h of times){const t=group.truth.horizons[h],o=group.observed?.horizons?.[h];horizons.push([label,`${h} s`,fmt(t.error_m,2),fmt(o?.error_m,2),String(t.count)]);}}
 if(horizons.length)host.append(metricTable(['대상','예측 시점','Truth 오차 (m)','관측 오차 (m)','평가 수'],horizons));
 const phases=Object.entries(m.by_phase||{});if(phases.length){const details=document.createElement('details');details.open=wasOpen;const summary=document.createElement('summary');summary.textContent='비행 단계별 평가';details.append(summary);const rows=[];for(const [phase,group] of phases)for(const [key,name] of [['truth','Truth'],['observed','관측']]){const v=group[key];rows.push([phaseLabel(phase),name,fmt(v.ade_m,2),fmt(v.fde_m,2),String(v.count)]);}details.append(metricTable(['발행 당시 단계','비교 기준','ADE (m)','FDE (m)','완료 예측 수'],rows));host.append(details);}
}
function makeAircraft(kind,color){const group=new THREE.Group();const material=new THREE.MeshStandardMaterial({color,roughness:.65});const dark=new THREE.MeshStandardMaterial({color:0x293b45});
  const part=(geometry,mat,x,y,z)=>{const mesh=new THREE.Mesh(geometry,mat);mesh.position.set(x,y,z);group.add(mesh);return mesh;};
  part(new THREE.CapsuleGeometry(.65,4,4,8),material,0,0,0).rotation.z=-Math.PI/2;
  part(new THREE.SphereGeometry(.58,10,6),dark,1.7,0,.35);
  if(!kind.toLowerCase().includes('helicopter')){part(new THREE.BoxGeometry(1.4,9,.16),material,-.3,0,0);part(new THREE.BoxGeometry(.8,3,.13),material,-2.2,0,.3);part(new THREE.BoxGeometry(1.2,.12,1.4),material,-2,0,.7);}
  if(kind.toLowerCase().includes('helicopter')||kind.toLowerCase().includes('vtol')){part(new THREE.BoxGeometry(.2,8,.07),dark,0,0,1.3);part(new THREE.BoxGeometry(8,.2,.07),dark,0,0,1.3);part(new THREE.BoxGeometry(.13,.13,1.3),dark,0,0,.7);}
  part(new THREE.BoxGeometry(2.8,.15,.15),dark,0,-.8,-.85);part(new THREE.BoxGeometry(2.8,.15,.15),dark,0,.8,-.85);
  return group;
}
function disposeObject(object){object.traverse(o=>{o.geometry?.dispose();if(o.material){if(Array.isArray(o.material))o.material.forEach(m=>m.dispose());else o.material.dispose();}});scene.remove(object);}
function updateLine(map,id,points,color,dashed=false){if(map.has(id))disposeObject(map.get(id));map.delete(id);if(!points||points.length<2)return;const geometry=new THREE.BufferGeometry().setFromPoints(points.map(p=>new THREE.Vector3(...p)));const material=dashed?new THREE.LineDashedMaterial({color,dashSize:2,gapSize:1}):new THREE.LineBasicMaterial({color});const line=new THREE.Line(geometry,material);if(dashed)line.computeLineDistances();scene.add(line);map.set(id,line);}
function updateScene(){if(!scene)return;const ids=new Set((state.objects||[]).map(o=>o.id));for(const [id,mesh] of meshes)if(!ids.has(id)){disposeObject(mesh);meshes.delete(id);}
  state.objects?.forEach((o,i)=>{if(!meshes.has(o.id)){const mesh=makeAircraft(o.kind,i===0?0xf7fbff:0xc5deea);scene.add(mesh);meshes.set(o.id,mesh);}updateLine(trails,o.id,o.history,0x206b8c,true);updateLine(predictions,o.id,o.prediction?.position_enu_m,0xeb783a);});
  for(const map of [trails,predictions])for(const [id,line] of map)if(!ids.has(id)){disposeObject(line);map.delete(id);}drawMap();
}
function drawMap(){const canvas=$('minimap'),ctx=canvas.getContext('2d'),objects=state?.objects||[];ctx.clearRect(0,0,canvas.width,canvas.height);ctx.fillStyle='#eff5f8';ctx.fillRect(0,0,canvas.width,canvas.height);const xs=objects.map(o=>o.position_enu_m[0]),ys=objects.map(o=>o.position_enu_m[1]);const cx=xs.length?(Math.min(...xs)+Math.max(...xs))/2:0,cy=ys.length?(Math.min(...ys)+Math.max(...ys))/2:0;const span=Math.max(100,...xs.map(x=>Math.abs(x-cx)*2),...ys.map(y=>Math.abs(y-cy)*2));const scale=120/span;ctx.fillStyle='#38566a';ctx.font='11px sans-serif';ctx.fillText('North ↑  ·  East →',8,14);ctx.fillText(`${fmt(span,0)} m`,8,151);objects.forEach(o=>{ctx.fillStyle=o.id===selectedId?'#b35317':'#176b89';ctx.beginPath();ctx.arc(110+(o.position_enu_m[0]-cx)*scale,80-(o.position_enu_m[1]-cy)*scale,4,0,Math.PI*2);ctx.fill();});}
function initScene(){scene=new THREE.Scene();scene.background=new THREE.Color(0x9eb9c7);scene.fog=new THREE.Fog(0x9eb9c7,1800,15000);camera=new THREE.PerspectiveCamera(50,1,.1,30000);camera.up.set(0,0,1);camera.position.set(70,-90,60);scene.add(new THREE.HemisphereLight(0xffffff,0x556c73,2));const sun=new THREE.DirectionalLight(0xffffff,2);sun.position.set(50,-40,100);scene.add(sun);const ground=new THREE.Mesh(new THREE.PlaneGeometry(30000,30000),new THREE.MeshStandardMaterial({color:0x79938a,roughness:1}));ground.position.z=-1;scene.add(ground);const grid=new THREE.GridHelper(10000,200,0x56716d,0x718a81);grid.rotation.x=Math.PI/2;grid.position.z=-.9;scene.add(grid);scene.add(new THREE.AxesHelper(30));
 renderer=new THREE.WebGLRenderer({antialias:true,preserveDrawingBuffer:true});renderer.setPixelRatio(Math.min(devicePixelRatio,2));$('viewport').append(renderer.domElement);resizeObserver=new ResizeObserver(resize);resizeObserver.observe($('viewport'));resize();
 const canvas=renderer.domElement;canvas.addEventListener('pointerdown',e=>{if($('camera').value==='chase')return;dragging={x:e.clientX,y:e.clientY};canvas.setPointerCapture(e.pointerId);});canvas.addEventListener('pointermove',e=>{if(!dragging)return;orbitYaw-=(e.clientX-dragging.x)*.008;orbitPitch=Math.max(.08,Math.min(1.45,orbitPitch+(e.clientY-dragging.y)*.006));dragging={x:e.clientX,y:e.clientY};});canvas.addEventListener('pointerup',()=>dragging=null);canvas.addEventListener('pointercancel',()=>dragging=null);canvas.addEventListener('wheel',e=>{e.preventDefault();orbitDistance=Math.max(15,Math.min(20000,orbitDistance*Math.exp(e.deltaY*.001)));},{passive:false});raf=requestAnimationFrame(render);
}
function resize(){if(!renderer)return;const el=$('viewport'),w=el.clientWidth,h=el.clientHeight;if(!w||!h)return;renderer.setSize(w,h);camera.aspect=w/h;camera.updateProjectionMatrix();}
function render(now){if(disposed)return;const dt=Math.min((now-lastFrame)/1000,.1);lastFrame=now;frames++;diagnostics.frameCount++;if(now-fpsStart>1000){diagnostics.fps=frames*1000/(now-fpsStart);frames=0;fpsStart=now;}
 const objects=state?.objects||[];const alpha=running()?Math.min(1,(now-receivedAt)/catalog.ui.poll_ms):1;
 for(const o of objects){const mesh=meshes.get(o.id);if(!mesh)continue;const previous=priorObjects.get(o.id);const p=o.position_enu_m;mesh.position.set(...p);if(previous&&alpha<1)mesh.position.lerpVectors(new THREE.Vector3(...previous.position_enu_m),new THREE.Vector3(...p),alpha);mesh.rotation.z=o.heading_rad||0;}
 const selected=objects.find(o=>o.id===selectedId);const target=new THREE.Vector3();let distance=orbitDistance,yaw=orbitYaw,pitch=orbitPitch;
 if($('camera').value==='overview'&&objects.length){objects.forEach(o=>target.add(new THREE.Vector3(...o.position_enu_m)));target.divideScalar(objects.length);const radius=Math.max(0,...objects.map(o=>new THREE.Vector3(...o.position_enu_m).distanceTo(target)));distance=Math.max(distance,radius*2.7+30);}
 else if(selected){target.copy(meshes.get(selected.id).position);if($('camera').value==='chase'){yaw=(selected.heading_rad||0)+Math.PI;pitch=.27;distance=catalog.ui.chase_distance_m;pitch=Math.atan2(catalog.ui.chase_height_m,distance);}}
 const desired=target.clone().add(new THREE.Vector3(Math.cos(yaw)*Math.cos(pitch)*distance,Math.sin(yaw)*Math.cos(pitch)*distance,Math.sin(pitch)*distance));desired.z=Math.max(desired.z,4);camera.position.lerp(desired,reducedMotion?1:1-Math.exp(-dt/catalog.ui.camera_response_s));camera.lookAt(target);renderer.render(scene,camera);raf=requestAnimationFrame(render);
}
async function poll(){if(disposed)return;if(!polling){polling=true;const start=performance.now();try{applyState(await api('state'));diagnostics.pollLatencyMs=performance.now()-start;}catch(error){$('connection').textContent='연결 확인 필요';notice(error.message,true);}finally{polling=false;}}pollTimer=setTimeout(poll,catalog.ui.poll_ms);}
async function init(){try{catalog=await api('catalog');$('object-count').max=catalog.limits.max_objects;$('seed').value=catalog.defaults.seed;$('mode').value=catalog.defaults.mode;$('start-mode').value=catalog.defaults.start_mode;
 catalog.limits.sigma_m.forEach(s=>$('sigma').append(option(s,`${s} m`)));$('sigma').value=catalog.defaults.sigma_m;
 catalog.models.forEach((m,i)=>$('model').append(option(i,m.label||`${m.plugin_id} / ${m.run_id} / ${m.checkpoint}`)));addObject();initScene();await poll();
 heartbeatTimer=setInterval(()=>{if(running()&&ownsRun()&&!document.hidden&&document.hasFocus()){void sendKeys();if(!heartbeatSending){heartbeatSending=true;void api('heartbeat',{}).catch(e=>notice(e.message,true)).finally(()=>heartbeatSending=false);}}},catalog.ui.heartbeat_ms);
 }catch(error){notice(`초기화 실패: ${error.message}`,true);diagnostics.errors++;}}
$('object-count').onchange=()=>{const input=$('object-count'),count=Number(input.value);if(!Number.isInteger(count)||count<1||count>catalog.limits.max_objects){input.reportValidity();return;}while($('object-configs').children.length<count)addObject();while($('object-configs').children.length>count)$('object-configs').lastElementChild.remove();updateRows();};
$('add-object').onclick=addObject;$('mode').onchange=updateRows;
$('start-mode').onchange=()=>{for(const row of $('object-configs').children){const a=catalog.aircraft.find(a=>a.id===row.querySelector('[data-field=kind]').value);row.querySelector('[data-field=altitude]').value=$('start-mode').value==='ground'?0:a.initial_altitude_m;const mode=row.querySelector('[data-field=initial_mode]');if($('start-mode').value==='ground'&&a.id==='vtol')mode.value='VTOL';const speed=row.querySelector('[data-field=speed]');speed.value=$('start-mode').value==='ground'?0:mode.value==='CTOL'?a.ctol_initial_speed_mps:0;speed.min=$('start-mode').value==='air'&&mode.value==='CTOL'?a.minimum_airborne_speed_mps:0;}updateRows();};
$('config-form').onsubmit=async e=>{e.preventDefault();$('prepare').disabled=true;try{const config=readConfig();applyState(await api('create',config));$('camera').value=config.mode==='manual'?'chase':'overview';await switchTab('operation');if(!state.error)notice('준비되었습니다. Start를 눌러 비행을 시작하세요.');}catch(error){notice(error.message,true);$('prepare').disabled=false;}};
for(const action of ['start','pause','resume','stop','reset'])$(action).onclick=async()=>{clearKeys();try{await control(action);if(['start','resume'].includes(action))$('viewport').focus({preventScroll:true});}catch{ /* Error already visible. */ }};
for(const name of ['settings','operation']){$(`tab-${name}`).onclick=()=>void switchTab(name).catch(e=>notice(e.message,true));$(`tab-${name}`).onkeydown=e=>{if(['ArrowLeft','ArrowRight'].includes(e.key)){e.preventDefault();const other=name==='settings'?'operation':'settings';$(`tab-${other}`).focus();void switchTab(other);}};}
$('camera-left').onclick=()=>orbitYaw+=.2;$('camera-right').onclick=()=>orbitYaw-=.2;$('camera-up').onclick=()=>orbitPitch=Math.min(1.45,orbitPitch+.1);$('camera-down').onclick=()=>orbitPitch=Math.max(.08,orbitPitch-.1);
$('zoom-in').onclick=()=>orbitDistance=Math.max(15,orbitDistance/1.25);$('zoom-out').onclick=()=>orbitDistance=Math.min(20000,orbitDistance*1.25);$('camera-home').onclick=()=>{orbitYaw=-Math.PI/4;orbitPitch=.55;orbitDistance=120;};
$('export').onclick=async()=>{try{renderer.render(scene,camera);const result=await api('export',{png:renderer.domElement.toDataURL('image/png')});notice(`PNG 저장: ${result.path||result.png_path}`);}catch(error){notice(error.message,true);}};
window.addEventListener('keydown',e=>{if(!supportedKeys.has(e.code)||e.target.closest('input,select,textarea,[contenteditable=true]')||!running()||!ownsRun()||$('operation').hidden||state.config?.mode!=='manual')return;e.preventDefault();if(e.repeat)return;keys.add(e.code);void sendKeys(true);});
window.addEventListener('keyup',e=>{if(!supportedKeys.has(e.code))return;keys.delete(e.code);void sendKeys(true);});window.addEventListener('blur',()=>void safePause());document.addEventListener('visibilitychange',()=>{if(document.hidden)void safePause();});
window.addEventListener('pagehide',()=>{disposed=true;keys.clear();if(ownsRun()&&running())navigator.sendBeacon('/api/simulation/pause',new Blob([JSON.stringify({client_id:clientId,run_id:state.run_id})],{type:'application/json'}));clearTimeout(pollTimer);clearInterval(heartbeatTimer);cancelAnimationFrame(raf);if(scene)scene.traverse(o=>{o.geometry?.dispose();o.material?.dispose?.();});resizeObserver?.disconnect();renderer?.dispose();});
void init();

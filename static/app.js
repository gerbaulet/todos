"use strict";
const $=s=>document.querySelector(s), $$=(s,root=document)=>[...root.querySelectorAll(s)];
const columns=[
  ["id","ID",false],["title","Was ist zu tun",true],["assignee","Wer",true],["due_date","Bis wann",true],
  ["completed","Erledigt",true],["dependencies","Abhängigkeiten",true],["project","Projekt",true],
  ["category","Kategorie",true],["tags","Tags",true],["link","Link",true]
];
const optional=["project","category","tags","link"];
const filterControls={search:["search","tl-search"],status:["status","tl-status"],assignee:["f-assignee","tl-assignee"],project:["f-project","tl-project"],category:["f-category","tl-category"],tag:["f-tag","tl-tag"],due:["f-due"]};
const defaults={view:"table",sort:{field:"due_date",dir:1},visibleColumns:[],filters:{status:"active"},zoom:"month",showDependencies:false,timelineScroll:0,recentCommands:[]};
const state={tasks:[],lookups:{assignees:[],projects:[],categories:[],tags:[]},settings:{...defaults},selected:null,editing:null,saveTimer:null,pendingSettings:{},overlay:null,overlayReturnFocus:null,quickIndex:0,paletteIndex:0,paletteItems:[]};

async function api(path,options={}){
  const response=await fetch(path,{...options,headers:{"Content-Type":"application/json",...(options.headers||{})}});
  if(!response.ok){let x={};try{x=await response.json()}catch{};throw Error(x.error||`HTTP ${response.status}`)}
  return response.headers.get("content-type")?.includes("json")?response.json():response;
}
function toast(message,error=false){const el=$("#toast");el.textContent=message;el.className=`show ${error?"error":""}`;clearTimeout(el._t);el._t=setTimeout(()=>el.className="",3200)}
function localDate(){const d=new Date(),off=d.getTimezoneOffset();return new Date(d-off*60000).toISOString().slice(0,10)}
function parseDate(text,base=new Date()){
  let s=String(text||"").trim().toLocaleLowerCase("de"); if(!s)return null;
  const day=new Date(base.getFullYear(),base.getMonth(),base.getDate());
  if(s==="heute")return isoLocal(day); if(s==="morgen"){day.setDate(day.getDate()+1);return isoLocal(day)}
  let m=s.match(/^\+(\d+)\s*(w|wochen?)?$/i); if(m){day.setDate(day.getDate()+(+m[1])*(m[2]?7:1));return isoLocal(day)}
  m=s.match(/^(\d{1,2})\.(\d{1,2})\.?(?:(\d{2}|\d{4}))?$/); if(!m)return undefined;
  let year=m[3]?+m[3]:day.getFullYear(); if(year<100)year+=2000;
  let candidate=new Date(year,+m[2]-1,+m[1]);
  if(candidate.getFullYear()!==year||candidate.getMonth()!==+m[2]-1||candidate.getDate()!==+m[1])return undefined;
  if(!m[3]&&candidate.getTime()<day.getTime()-60*86400000)candidate.setFullYear(year+1);
  return isoLocal(candidate);
}
function quickTokens(input){
  const tokens=[],re=/(?:[@#!%^]"[^"]*"|"[^"]*"|\S+)/g;let match;
  while((match=re.exec(String(input||""))))tokens.push(match[0]);
  return tokens;
}
function parseQuickAdd(input,base=new Date()){
  if((String(input||"").match(/"/g)||[]).length%2)throw Error("Anführungszeichen sind nicht geschlossen.");
  const result={title:"",assignee:null,due_date:null,tags:[],project:null,category:null,dependencies:[],link:null},title=[];
  const single={"@":"assignee","!":"project","%":"category"};
  for(const token of quickTokens(input)){
    if(/^https?:\/\//i.test(token)){
      if(result.link)throw Error("Bitte nur einen Link angeben.");result.link=token;continue;
    }
    const prefix=token[0];
    if("@#!%^".includes(prefix)){
      let value=token.slice(1);if(value.startsWith('"')&&value.endsWith('"'))value=value.slice(1,-1);value=value.trim();
      if(!value)throw Error(`Nach ${prefix} fehlt ein Wert.`);
      if(prefix==="#"){result.tags.push(value);continue}
      if(prefix==="^"){if(!/^\d+$/.test(value))throw Error("Abhängigkeiten müssen Task-IDs sein.");result.dependencies.push(+value);continue}
      const field=single[prefix];if(result[field])throw Error(`${prefix} darf nur einmal vorkommen.`);result[field]=value;continue;
    }
    const date=parseDate(token,base);
    if(date!==undefined){if(result.due_date)throw Error("Bitte nur ein Fälligkeitsdatum angeben.");result.due_date=date;continue}
    if(/^\+/.test(token)||/^\d{1,2}\.\d{1,2}/.test(token))throw Error(`Datum nicht erkannt: ${token}`);
    title.push(token.startsWith('"')&&token.endsWith('"')?token.slice(1,-1):token);
  }
  result.title=title.join(" ").trim();result.tags=[...new Set(result.tags)];result.dependencies=[...new Set(result.dependencies)];
  if(!result.title)throw Error("Bitte einen Aufgabentitel eingeben.");
  return result;
}
function normalizeSearch(value){return String(value??"").toLocaleLowerCase("de").replace(/ß/g,"ss").normalize("NFD").replace(/[\u0300-\u036f]/g,"").replace(/[^a-z0-9]+/g," ").trim().replace(/\s+/g," ")}
function fuzzyRank(query,value){
  const q=normalizeSearch(query),v=normalizeSearch(value);if(!q)return 0;if(q===v)return 0;if(v.startsWith(q))return 10;if(v.includes(q))return 20+v.indexOf(q);
  let at=0;for(const char of q){at=v.indexOf(char,at);if(at<0)return -1;at++}return 100+v.length-q.length;
}
function rankSearchItems(query,items){
  const exactId=String(query||"").trim().match(/^#?(\d+)$/)?.[1];
  return items.map((item,index)=>({...item,score:item.type==="task"&&String(item.id)===exactId?-10:fuzzyRank(query,item.search),index})).filter(x=>x.score===-10||x.score>=0).sort((a,b)=>a.score-b.score||a.index-b.index);
}
function isoLocal(d){return [d.getFullYear(),String(d.getMonth()+1).padStart(2,"0"),String(d.getDate()).padStart(2,"0")].join("-")}
function dateLabel(iso){if(!iso)return "";const [y,m,d]=iso.split("-");return `${d}.${m}.${y}`}
function parseUTC(s){return s?new Date(s):null}
function saveSettings(change){Object.assign(state.settings,change);Object.assign(state.pendingSettings,change);clearTimeout(state.saveTimer);state.saveTimer=setTimeout(()=>{const pending=state.pendingSettings;state.pendingSettings={};api("/api/settings",{method:"PATCH",body:JSON.stringify(pending)}).catch(e=>toast(e.message,true))},350)}

async function load(){
  try{
    const [tasks,lookups,settings]=await Promise.all([api("/api/tasks"),api("/api/lookups"),api("/api/settings")]);
    state.tasks=tasks;state.lookups=lookups;state.settings={...defaults,...settings,filters:{...defaults.filters,...settings.filters}};
    buildControls(); applySettings(); render();
  }catch(e){toast(e.message,true)}
}
function buildControls(){
  for(const name of optional){const col=columns.find(c=>c[0]===name);$("#columns").insertAdjacentHTML("beforeend",`<label><input type="checkbox" value="${name}"> ${col[1]}</label>`)}
  fillLookups();
}
function fillLookups(){
  for(const [key,id] of [["assignees","assignees"],["projects","projects"],["categories","categories"],["tags","tags"]])
    $("#"+id).innerHTML=state.lookups[key].map(x=>`<option value="${esc(x.name)}"></option>`).join("");
  for(const [key,id] of [["assignees","f-assignee"],["assignees","tl-assignee"],["projects","f-project"],["projects","tl-project"],["categories","f-category"],["categories","tl-category"],["tags","f-tag"],["tags","tl-tag"]]){
    const old=$("#"+id).value;$("#"+id).innerHTML='<option value="">Alle</option>'+state.lookups[key].map(x=>`<option>${esc(x.name)}</option>`).join("");$("#"+id).value=old;
  }
  $("#task-options").innerHTML=state.tasks.filter(t=>!t.deleted_at).map(t=>`<option value="#${t.id}">${esc(t.title)}</option>`).join("");
}
function applySettings(){
  const s=state.settings,f=s.filters||{};
  for(const [key,ids] of Object.entries(filterControls))for(const id of ids)$("#"+id).value=f[key]||(key==="status"?"active":"");
  $$("#columns input").forEach(x=>x.checked=(s.visibleColumns||[]).includes(x.value));$("#zoom").value=s.zoom;$("#show-deps").checked=!!s.showDependencies;
}
function esc(value){return String(value??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]))}
function activeColumns(){return columns.filter(c=>!optional.includes(c[0])||(state.settings.visibleColumns||[]).includes(c[0]))}
function filteredTasks(includeDue=true){
  const f=state.settings.filters||{},now=Date.now(),today=localDate(),week=isoLocal(new Date(Date.now()+7*86400000));
  let data=state.tasks.filter(t=>{
    if(state.settings.view==="trash")return !!t.deleted_at;
    if(t.deleted_at)return false;
    if(state.settings.view==="completed")return t.completed;
    if(f.status==="open"&&t.completed)return false;if(f.status==="completed"&&!t.completed)return false;
    if(f.status==="active"&&t.completed&&now-parseUTC(t.completed_at)>30*60000)return false;
    if(f.assignee&&t.assignee!==f.assignee||f.project&&t.project!==f.project||f.category&&t.category!==f.category||f.tag&&!t.tags.includes(f.tag))return false;
    if(includeDue){if(f.due==="overdue"&&(!t.due_date||t.due_date>=today||t.completed))return false;if(f.due==="today"&&t.due_date!==today)return false;if(f.due==="week"&&(!t.due_date||t.due_date<today||t.due_date>week))return false;if(f.due==="none"&&t.due_date)return false}
    const q=(f.search||"").toLocaleLowerCase("de");return !q||[t.title,t.assignee,t.project,t.category,t.link,...t.tags].join(" ").toLocaleLowerCase("de").includes(q);
  });
  if(state.settings.view==="completed")return data.sort((a,b)=>(b.completed_at||"").localeCompare(a.completed_at||""));
  const {field,dir}=state.settings.sort||defaults.sort;
  return data.sort((a,b)=>{let av=a[field],bv=b[field];if(field==="due_date"){av=av||"9999";bv=bv||"9999"}if(Array.isArray(av))av=av.join(",");if(Array.isArray(bv))bv=bv.join(",");return String(av??"").localeCompare(String(bv??""),"de",{numeric:true})*dir});
}
function render(){
  const view=state.settings.view;
  $$(".view").forEach(x=>x.classList.add("hidden"));$$("nav button").forEach(x=>x.classList.toggle("active",x.dataset.view===view));
  $("#tools").classList.toggle("hidden",view==="history"||view==="timeline");
  if(view==="timeline"){$("#timeline-view").classList.remove("hidden");renderTimeline();return}
  if(view==="history"){$("#history-view").classList.remove("hidden");renderHistory();return}
  $("#table-view").classList.remove("hidden");renderTable();
}
function display(t,field){
  if(field==="due_date")return dateLabel(t[field]);if(field==="completed")return t.completed?"✓":"";
  if(field==="dependencies")return t.dependencies.map(x=>"#"+x).join(", ");if(field==="tags")return t.tags.join(", ");return t[field]??"";
}
function renderTable(){
  const cols=activeColumns(),data=filteredTasks(),head=$("#tasks thead tr"),body=$("#tasks tbody");
  head.innerHTML=cols.map(c=>`<th data-field="${c[0]}">${c[1]}${state.settings.sort.field===c[0]?(state.settings.sort.dir>0?" ▲":" ▼"):""}</th>`).join("")+(state.settings.view==="trash"?"<th>Aktion</th>":"");
  body.innerHTML=data.map(t=>`<tr data-id="${t.id}" class="${t.completed?"completed-row":""}">${cols.map(c=>{
    const color=c[0]==="assignee"?(state.lookups.assignees.find(a=>a.name===t.assignee)?.color):null;
    return `<td tabindex="${c[2]?0:-1}" data-field="${c[0]}" title="${esc(display(t,c[0]))}" class="${c[0]==="due_date"&&!t.completed&&t.due_date&&t.due_date<localDate()?"overdue":""}">${color?`<span class="color-dot" style="background:${color}"></span>`:""}${esc(display(t,c[0]))}</td>`}).join("")}${state.settings.view==="trash"?`<td><button class="restore" data-restore="${t.id}">Wiederherstellen</button></td>`:""}</tr>`).join("")||'<tr><td class="empty" colspan="10">Keine Aufgaben in dieser Ansicht.</td></tr>';
  if(state.selected){const cell=$(`tr[data-id="${state.selected.id}"] td[data-field="${state.selected.field}"]`);if(cell)cell.classList.add("cell-selected")}
}
async function updateTask(id,change){
  try{const task=await api(`/api/tasks/${id}`,{method:"PATCH",body:JSON.stringify(change)});replaceTask(task);await refreshLookups();render();toast("Gespeichert");return task}catch(e){toast(e.message,true);throw e}
}
function replaceTask(task){const i=state.tasks.findIndex(t=>t.id===task.id);if(i<0)state.tasks.push(task);else state.tasks[i]=task}
async function refreshLookups(){state.lookups=await api("/api/lookups");fillLookups()}
async function createTask(focus=true){try{const t=await api("/api/tasks",{method:"POST",body:"{}"});state.tasks.push(t);state.settings.view="table";state.selected={id:t.id,field:"title"};await refreshLookups();render();if(focus){const cell=$(`tr[data-id="${t.id}"] td[data-field=title]`);cell?.scrollIntoView({block:"nearest"});editCell(cell)}}catch(e){toast(e.message,true)}}
function selectCell(cell,focus=true){if(!cell||!cell.dataset.field)return;$(".cell-selected")?.classList.remove("cell-selected");cell.classList.add("cell-selected");const id=+cell.closest("tr").dataset.id;state.selected={id,field:cell.dataset.field};if(focus)cell.focus()}
function editCell(cell){
  if(!cell||state.editing||!["title","assignee","due_date","project","category","tags","link","dependencies"].includes(cell.dataset.field))return;
  selectCell(cell,false);const t=state.tasks.find(x=>x.id===state.selected.id),field=cell.dataset.field,old=display(t,field);cell.textContent="";
  const input=document.createElement("input");input.value=old;if({assignee:1,project:1,category:1,tags:1}[field])input.setAttribute("list",field==="assignee"?"assignees":field+"s");if(field==="dependencies")input.setAttribute("list","task-options");
  cell.append(input);state.editing={cell,input,old,field,id:t.id};input.focus();input.setSelectionRange(input.value.length,input.value.length);
  input.addEventListener("keydown",editKey);input.addEventListener("blur",()=>commitEdit("stay"),{once:true});
}
function valueFor(field,value){if(field==="due_date"){const x=parseDate(value);if(x===undefined)throw Error("Datum nicht erkannt.");return x}if(field==="tags")return value.split(",").map(x=>x.trim()).filter(Boolean);if(field==="dependencies")return [...value.matchAll(/#?(\d+)/g)].map(x=>+x[1]);return value}
async function commitEdit(move){
  const e=state.editing;if(!e)return;state.editing=null;const val=e.input.value;
  try{await updateTask(e.id,{[e.field]:valueFor(e.field,val)});if(move==="down")moveCell(1,0,true);if(move==="next")moveCell(0,1,true);if(move==="prev")moveCell(0,-1,true)}catch{render()}
}
function cancelEdit(){const e=state.editing;if(!e)return;state.editing=null;render();setTimeout(()=>{const c=$(`tr[data-id="${e.id}"] td[data-field="${e.field}"]`);selectCell(c)},0)}
function editKey(ev){if(["Escape","Enter","Tab"].includes(ev.key))ev.stopPropagation();if(ev.key==="Escape"){ev.preventDefault();cancelEdit()}else if(ev.key==="Enter"&&(ev.ctrlKey||ev.metaKey)){ev.preventDefault();commitEdit("stay").then(()=>createTask())}else if(ev.key==="Enter"){ev.preventDefault();commitEdit("down")}else if(ev.key==="Tab"){ev.preventDefault();commitEdit(ev.shiftKey?"prev":"next")}}
function moveCell(rows,cols,edit=false){
  if(!state.selected)return;const cells=$$("#tasks tbody td[tabindex='0']"),cur=cells.findIndex(c=>+c.closest("tr").dataset.id===state.selected.id&&c.dataset.field===state.selected.field);if(cur<0)return;
  const current=cells[cur],tr=current.closest("tr"),rowCells=$$("td[tabindex='0']",tr),ci=rowCells.indexOf(current),rowsEls=$$("#tasks tbody tr[data-id]");let ri=rowsEls.indexOf(tr)+rows,ni=ci+cols;
  if(cols){if(ni<0){ri--;ni=rowCells.length-1}else if(ni>=rowCells.length){ri++;ni=0}}
  if(ri>=rowsEls.length&&rows>0){createTask().then(()=>{const c=$(`tr[data-id="${state.selected.id}"] td[data-field="${state.selected.field}"]`);if(edit)editCell(c)});return}
  ri=Math.max(0,Math.min(rowsEls.length-1,ri));const target=$$("td[tabindex='0']",rowsEls[ri])[Math.min(ni,$$("td[tabindex='0']",rowsEls[ri]).length-1)];selectCell(target);if(edit)editCell(target)
}

function onTableKey(ev){
  if(state.editing||ev.target.tagName==="INPUT")return;const cell=ev.target.closest?.("td");if(cell)selectCell(cell,false);if(!state.selected)return;
  if(ev.key.startsWith("Arrow")){ev.preventDefault();moveCell(ev.key==="ArrowDown"?1:ev.key==="ArrowUp"?-1:0,ev.key==="ArrowRight"?1:ev.key==="ArrowLeft"?-1:0)}
  else if(ev.key==="Tab"){ev.preventDefault();moveCell(0,ev.shiftKey?-1:1)}
  else if(ev.key==="Enter"||ev.key==="F2"){ev.preventDefault();if(state.selected.field==="id")openEditor(state.selected.id);else editCell(cell)}
  else if(ev.key===" "&&state.selected.field==="completed"){ev.preventDefault();toggleCurrent()}
  else if((ev.key==="Delete"||ev.key==="Backspace")&&!ev.ctrlKey&&!ev.metaKey){ev.preventDefault();const f=state.selected.field;if(f!=="completed"&&f!=="id")updateTask(state.selected.id,{[f]:["tags","dependencies"].includes(f)?[]:""})}
  else if(ev.key.length===1&&!ev.ctrlKey&&!ev.metaKey&&!ev.altKey&&cell?.tabIndex===0&&state.selected.field!=="completed"){editCell(cell);if(state.editing){state.editing.input.value+=ev.key;state.editing.input.setSelectionRange(state.editing.input.value.length,state.editing.input.value.length);ev.preventDefault()}}
}
function toggleCurrent(){if(!state.selected)return;const t=state.tasks.find(x=>x.id===state.selected.id);if(t)updateTask(t.id,{completed:!t.completed})}
async function deleteCurrent(){if(!state.selected)return;try{replaceTask(await api(`/api/tasks/${state.selected.id}`,{method:"DELETE"}));state.selected=null;render();toast("In den Papierkorb verschoben")}catch(e){toast(e.message,true)}}
async function restoreTask(id){try{replaceTask(await api(`/api/tasks/${id}/restore`,{method:"POST"}));render();toast("Task wiederhergestellt")}catch(e){toast(e.message,true)}}

async function openEditor(id){
  const t=state.tasks.find(x=>x.id===id);if(!t)return;const form=$("#edit-form");$("#edit-id").textContent="#"+id;form.dataset.id=id;
  for(const f of ["title","assignee","due_date","project","category","link"])form.elements[f].value=f==="due_date"?dateLabel(t[f]):t[f]||"";
  form.elements.assignee_color.value=assigneeColor(t.assignee);
  form.elements.tags.value=t.tags.join(", ");form.elements.dependencies.value=t.dependencies.map(x=>"#"+x).join(", ");form.elements.completed.checked=t.completed;
  try{const h=await api(`/api/tasks/${id}/history`);$("#task-history").innerHTML=historyHTML(h)}catch(e){toast(e.message,true)}
  $("#editor").showModal();form.elements.title.focus();form.elements.title.setSelectionRange(form.elements.title.value.length,form.elements.title.value.length)
}
async function saveEditor(ev){ev.preventDefault();const form=$("#edit-form"),id=+form.dataset.id,data=Object.fromEntries(new FormData(form)),color=data.assignee_color;delete data.assignee_color;data.completed=form.elements.completed.checked;data.tags=form.elements.tags.value.split(",");data.dependencies=[...form.elements.dependencies.value.matchAll(/#?(\d+)/g)].map(x=>+x[1]);data.due_date=parseDate(data.due_date);if(data.due_date===undefined)return toast("Datum nicht erkannt.",true);try{const task=await updateTask(id,data),person=state.lookups.assignees.find(x=>x.name===task.assignee);if(person&&person.color!==color){await api(`/api/assignees/${person.id}`,{method:"PATCH",body:JSON.stringify({color})});await refreshLookups();render()}$("#editor").close()}catch(e){toast(e.message,true)}}
function historyHTML(items){return items.map(h=>`<div class="history-item"><time>${esc(new Date(h.timestamp).toLocaleString("de-DE"))}</time><span>#${h.task_id}</span><span>${esc(historyText(h))}</span></div>`).join("")||'<p class="empty">Keine Einträge.</p>'}
function historyText(h){if(h.action==="created")return `Task erstellt: ${h.title||""}`;if(h.action==="deleted")return "Task gelöscht";if(h.action==="restored")return "Task wiederhergestellt";if(h.action==="completed")return "Als erledigt markiert";if(h.action==="reopened")return "Wieder geöffnet";return `${h.field}: ${h.old_value??"–"} → ${h.new_value??"–"}`}
async function renderHistory(){try{const q=$("#h-search").value.toLocaleLowerCase("de"),from=$("#h-from").value,to=$("#h-to").value,action=$("#h-action").value;let items=await api("/api/history");items=items.filter(h=>(!from||h.timestamp.slice(0,10)>=from)&&(!to||h.timestamp.slice(0,10)<=to)&&(!action||h.action===action)&&(!q||[h.task_id,h.title,h.assignee,h.action,h.field,h.old_value,h.new_value].join(" ").toLocaleLowerCase("de").includes(q)));$("#history-list").innerHTML=historyHTML(items)}catch(e){toast(e.message,true)}}

const DAY={week:48,month:18,quarter:8,year:3};
function dayOffset(iso,start){return Math.round((new Date(iso+"T12:00:00")-start)/86400000)}
function timelineMarkerWidth(task){
  const canvas=timelineMarkerWidth.canvas||(timelineMarkerWidth.canvas=document.createElement("canvas")),context=canvas.getContext("2d");
  context.font="14px system-ui";
  return Math.min(220,Math.max(54,Math.ceil(context.measureText(`#${task.id} ${task.title||"(ohne Titel)"}`).width)+18));
}
function renderTimeline(){
  const tasks=filteredTasks(false),dated=tasks.filter(t=>t.due_date),assignees=[...new Set(tasks.map(t=>t.assignee||"Ohne Bearbeiter"))].sort((a,b)=>a.localeCompare(b,"de"));
  const px=DAY[state.settings.zoom]||18,start=new Date();start.setHours(12,0,0,0);start.setDate(start.getDate()-45);const days=500,width=140+days*px;
  let months="";const cursor=new Date(start);cursor.setDate(1);if(cursor<start)cursor.setMonth(cursor.getMonth()+1);while(dayOffset(isoLocal(cursor),start)<days){const left=140+dayOffset(isoLocal(cursor),start)*px,next=new Date(cursor);next.setMonth(next.getMonth()+1);months+=`<div class="tl-month" style="left:${left}px;width:${Math.max(1,(next-cursor)/86400000)*px}px">${cursor.toLocaleDateString("de-DE",{month:"long",year:"numeric"})}</div>`;cursor.setMonth(cursor.getMonth()+1)}
  let markerPositions={},rows="",rowOffset=0;
  for(const name of assignees){
    const same=dated.filter(t=>(t.assignee||"Ohne Bearbeiter")===name).map(t=>({task:t,left:140+dayOffset(t.due_date,start)*px,width:timelineMarkerWidth(t)})).sort((a,b)=>a.left-b.left||a.task.id-b.task.id),laneEnds=[];
    let markers="";
    for(const item of same){
      let lane=laneEnds.findIndex(end=>end+8<=item.left);if(lane<0)lane=laneEnds.length;laneEnds[lane]=item.left+item.width;
      const t=item.task,top=6+lane*32;markerPositions[t.id]={left:item.left,right:item.left+item.width,center:item.left+item.width/2,y:rowOffset+top+14};
      markers+=`<button class="tl-marker ${t.completed?"done":""} ${!t.completed&&t.due_date<localDate()?"overdue":""}" draggable="true" data-id="${t.id}" style="left:${item.left}px;top:${top}px;width:${item.width}px;--color:${assigneeColor(t.assignee)}" title="#${t.id} – ${esc(t.title)} – ${dateLabel(t.due_date)}">#${t.id} ${esc(t.title||"(ohne Titel)")}</button>`;
    }
    const rowHeight=Math.max(62,12+laneEnds.length*32);rows+=`<div class="tl-row" style="height:${rowHeight}px"><div class="tl-label">${esc(name)}</div>${markers}</div>`;rowOffset+=rowHeight;
  }
  const todayX=140+dayOffset(localDate(),start)*px;let svg="";if(state.settings.showDependencies){for(const t of dated)for(const d of t.dependencies){const target=markerPositions[t.id],source=markerPositions[d];if(source&&target){const points=source.center<=target.center?{x1:source.right,x2:target.left}:{x1:source.left,x2:target.right};svg+=`<line x1="${points.x1}" y1="${source.y}" x2="${points.x2}" y2="${target.y}" stroke="#64748b" stroke-width="1.5" marker-end="url(#arrow)"/>`}}}
  const undated=tasks.filter(t=>!t.due_date).map(t=>`<button data-id="${t.id}" class="undated-task">#${t.id} ${esc(t.title||"(ohne Titel)")}</button>`).join("");
  $("#timeline").innerHTML=`<div class="tl-canvas zoom-${state.settings.zoom}" style="width:${width}px"><div class="tl-months">${months}</div><div class="today-line" style="left:${todayX}px"></div>${rows}<svg class="dep-lines" width="${width}" height="${rowOffset}"><defs><marker id="arrow" markerWidth="7" markerHeight="7" refX="6" refY="3" orient="auto"><path d="M0,0 L0,6 L6,3 z" fill="#64748b"/></marker></defs>${svg}</svg><div class="undated"><strong>Ohne Termin</strong>${undated||"Keine Aufgaben"}</div></div>`;
  const scroller=$("#timeline");scroller.scrollLeft=state.settings.timelineScroll||Math.max(0,todayX-scroller.clientWidth/3);
  $$(".tl-marker").forEach(el=>{el.addEventListener("dragstart",e=>e.dataTransfer.setData("text/plain",el.dataset.id));el.addEventListener("click",()=>openEditor(+el.dataset.id));el.addEventListener("keydown",timelineKey)});$$(".undated-task").forEach(el=>el.onclick=()=>openEditor(+el.dataset.id));
  scroller.ondragover=e=>e.preventDefault();scroller.ondrop=e=>{e.preventDefault();const id=+e.dataTransfer.getData("text/plain"),rect=scroller.getBoundingClientRect(),x=e.clientX-rect.left+scroller.scrollLeft-140,index=Math.round(x/px),d=new Date(start);d.setDate(d.getDate()+index);updateTask(id,{due_date:isoLocal(d)})};
}
function assigneeColor(name){return state.lookups.assignees.find(a=>a.name===name)?.color||"#64748b"}
function timelineKey(e){const markers=$$(".tl-marker"),i=markers.indexOf(e.currentTarget),id=+e.currentTarget.dataset.id,t=state.tasks.find(x=>x.id===id);if(e.key==="Enter")return openEditor(id);if(e.shiftKey&&(e.key==="ArrowLeft"||e.key==="ArrowRight")){e.preventDefault();const d=new Date(t.due_date+"T12:00:00");d.setDate(d.getDate()+(e.key==="ArrowRight"?1:-1)*(e.ctrlKey?7:1));return updateTask(id,{due_date:isoLocal(d)})}if(e.key.startsWith("Arrow")){e.preventDefault();markers[Math.max(0,Math.min(markers.length-1,i+(e.key==="ArrowLeft"||e.key==="ArrowUp"?-1:1)))]?.focus()}}

function openOverlay(dialog,input){
  if(state.overlay===dialog)return;
  const returnFocus=state.overlay?state.overlayReturnFocus:document.activeElement;if(state.overlay)closeOverlay(false);
  const other=$("dialog[open]");if(other)other.close();
  state.overlayReturnFocus=returnFocus;state.overlay=dialog;dialog.showModal();setTimeout(()=>input.focus(),0);
}
function closeOverlay(restore=true){
  const dialog=state.overlay,returnFocus=state.overlayReturnFocus;state.overlay=null;state.overlayReturnFocus=null;
  if(dialog?.open)dialog.close();if(restore&&returnFocus?.isConnected)setTimeout(()=>returnFocus.focus(),0);
}
function openQuickAdd(returnFocus=null){
  const input=$("#quick-input");$("#quick-error").textContent="";hideQuickSuggestions();openOverlay($("#quick-add"),input);input.setSelectionRange(input.value.length,input.value.length);
  if(returnFocus)state.overlayReturnFocus=returnFocus;
}
function hideQuickSuggestions(){state.quickIndex=0;$("#quick-suggestions").classList.add("hidden");$("#quick-input").removeAttribute("aria-activedescendant")}
function quickSuggestions(){
  const input=$("#quick-input"),before=input.value.slice(0,input.selectionStart),match=before.match(/(^|\s)([@#!%^])(?:"([^"]*)|([^\s"]*))$/);
  if(!match)return null;const prefix=match[2],query=match[3]??match[4]??"",start=match.index+match[1].length;
  let values=[];
  if(prefix==="^")values=state.tasks.filter(t=>!t.deleted_at).map(t=>({value:String(t.id),label:`#${t.id} – ${t.title||"(ohne Titel)"} – ${t.assignee||"ohne Bearbeiter"} – ${dateLabel(t.due_date)||"ohne Termin"}`,search:`${t.id} ${t.title}`}));
  else{const key={"@":"assignees","#":"tags","!":"projects","%":"categories"}[prefix];values=(state.lookups[key]||[]).map(x=>({value:x.name,label:`${prefix}${x.name}`,search:x.name}))}
  const ranked=values.map(x=>({...x,score:fuzzyRank(query,x.search)})).filter(x=>x.score>=0).sort((a,b)=>a.score-b.score||a.label.localeCompare(b.label,"de")).slice(0,8);
  return {prefix,start,end:input.selectionStart,items:ranked};
}
function renderQuickSuggestions(){
  const found=quickSuggestions(),box=$("#quick-suggestions");box._found=found;if(!found?.items.length)return hideQuickSuggestions();state.quickIndex=Math.min(state.quickIndex,found.items.length-1);
  box.innerHTML=found.items.map((x,i)=>`<button type="button" id="quick-option-${i}" class="command-option ${i===state.quickIndex?"selected":""}" role="option" aria-selected="${i===state.quickIndex}" data-index="${i}"><span class="command-label">${esc(x.label)}</span></button>`).join("");box.classList.remove("hidden");$("#quick-input").setAttribute("aria-activedescendant",`quick-option-${state.quickIndex}`);
}
function acceptQuickSuggestion(){
  const box=$("#quick-suggestions"),found=box._found,item=found?.items[state.quickIndex];if(!item)return false;const input=$("#quick-input"),quoted=item.value.includes(" ")?`"${item.value}"`:item.value,replacement=found.prefix+quoted+" ";input.setRangeText(replacement,found.start,found.end,"end");hideQuickSuggestions();input.focus();return true;
}
async function submitQuickAdd(keepOpen){
  const input=$("#quick-input"),error=$("#quick-error");error.textContent="";
  try{
    const data=parseQuickAdd(input.value),task=await api("/api/tasks",{method:"POST",body:JSON.stringify(data)});state.tasks.push(task);await refreshLookups();
    if(keepOpen){input.value="";hideQuickSuggestions();render();input.focus();toast(`Task #${task.id} erstellt`);return}
    input.value="";hideQuickSuggestions();saveSettings({view:"table"});state.selected={id:task.id,field:"title"};closeOverlay(false);render();const cell=$(`tr[data-id="${task.id}"] td[data-field="title"]`);cell?.scrollIntoView({block:"nearest"});cell?.focus();toast(`Task #${task.id} erstellt`);
  }catch(e){error.textContent=e.message;input.focus()}
}

const paletteCommands=[
  ["new","Neue Aufgabe"],["quick","Schnelleingabe öffnen"],["table","Tabelle öffnen"],["timeline","Timeline öffnen"],["history","Historie öffnen"],["completed","Fertiggestellt öffnen"],["trash","Papierkorb öffnen"],["today","Heute in Timeline"],["overdue","Nur überfällige Tasks anzeigen"],["reset","Filter zurücksetzen"],["columns","Spalten auswählen"],["deps-on","Abhängigkeiten anzeigen"],["deps-off","Abhängigkeiten ausblenden"],["backup","Backup erstellen"]
];
function paletteResults(query){
  const commands=paletteCommands.map(([id,label],order)=>({type:"command",id,label,search:label,order}));
  if(!normalizeSearch(query)){
    const recent=state.settings.recentCommands||[];return commands.sort((a,b)=>{const ai=recent.indexOf(a.id),bi=recent.indexOf(b.id);return (ai<0?100+a.order:ai)-(bi<0?100+b.order:bi)});
  }
  const items=[...commands];
  for(const t of state.tasks)items.push({type:"task",id:t.id,label:`Task #${t.id} – ${t.title||"(ohne Titel)"} – ${t.assignee||"ohne Bearbeiter"}${t.due_date?` – ${dateLabel(t.due_date)}`:""}`,search:`#${t.id} ${t.id} ${t.title} ${t.assignee||""} ${t.project||""} ${t.category||""} ${t.tags.join(" ")}`});
  for(const [key,kind] of [["assignees","Bearbeiter"],["projects","Projekt"],["categories","Kategorie"],["tags","Tag"]])for(const value of state.lookups[key])items.push({type:"lookup",label:`${kind}: ${value.name}`,search:value.name});
  return rankSearchItems(query,items).slice(0,60);
}
function renderPalette(){
  const box=$("#palette-results");state.paletteItems=paletteResults($("#palette-input").value);state.paletteIndex=Math.min(state.paletteIndex,Math.max(0,state.paletteItems.length-1));
  box.innerHTML=state.paletteItems.map((x,i)=>`<button type="button" id="palette-option-${i}" class="command-option ${i===state.paletteIndex?"selected":""}" role="option" aria-selected="${i===state.paletteIndex}" data-index="${i}"><span class="command-kind">${x.type==="command"?"Aktion":x.type==="task"?"Task":"Eintrag"}</span><span class="command-label">${esc(x.label)}</span></button>`).join("")||'<p class="empty">Keine Treffer.</p>';$("#palette-input").setAttribute("aria-activedescendant",`palette-option-${state.paletteIndex}`);box.querySelector(".selected")?.scrollIntoView({block:"nearest"});
}
function openPalette(){const input=$("#palette-input");input.value="";state.paletteIndex=0;renderPalette();openOverlay($("#command-palette"),input)}
function openView(view){saveSettings({view});render()}
function resetFilters(){const filters={...defaults.filters};saveSettings({filters});applySettings();render()}
async function downloadBackup(){try{const r=await fetch("/api/backup",{method:"POST"});if(!r.ok)throw Error("Backup fehlgeschlagen");const a=document.createElement("a");a.href=URL.createObjectURL(await r.blob());a.download=r.headers.get("content-disposition")?.match(/filename="(.+)"/)?.[1]||"todo-backup.sqlite";a.click();URL.revokeObjectURL(a.href)}catch(e){toast(e.message,true)}}
function executeCommand(id){
  const returnFocus=state.overlayReturnFocus,recent=[id,...(state.settings.recentCommands||[]).filter(x=>x!==id)].slice(0,10);saveSettings({recentCommands:recent});closeOverlay(false);
  if(id==="new")return createTask();if(id==="quick")return setTimeout(()=>openQuickAdd(returnFocus),0);if(["table","timeline","history","completed","trash"].includes(id))return openView(id);
  if(id==="today"){openView("timeline");return setTimeout(()=>$("#today").click(),0)}
  if(id==="overdue"){saveSettings({view:"table",filters:{...state.settings.filters,status:"open",due:"overdue"}});applySettings();return render()}
  if(id==="reset")return resetFilters();if(id==="columns"){openView("table");const details=$("#columns").closest("details");details.open=true;return $("#columns input")?.focus()}
  if(id==="deps-on"||id==="deps-off"){saveSettings({view:"timeline",showDependencies:id==="deps-on"});applySettings();return render()}
  if(id==="backup")return downloadBackup();
}
function executePaletteItem(){
  const item=state.paletteItems[state.paletteIndex];if(!item)return;if(item.type==="command")return executeCommand(item.id);closeOverlay(false);
  if(item.type==="task"){openView("table");return setTimeout(()=>openEditor(item.id),0)}toast(item.label);
}

function bind(){
  $("#tasks").addEventListener("click",e=>{const restore=e.target.closest("[data-restore]");if(restore)return restoreTask(+restore.dataset.restore);const cell=e.target.closest("td");if(cell?.dataset.field){selectCell(cell);if(e.detail===2){if(cell.dataset.field==="id")openEditor(+cell.closest("tr").dataset.id);else editCell(cell)}}});$("#tasks").addEventListener("keydown",onTableKey);
  $("#tasks thead").onclick=e=>{const f=e.target.closest("th")?.dataset.field;if(!f)return;const old=state.settings.sort||defaults.sort;saveSettings({sort:{field:f,dir:old.field===f?-old.dir:1}});renderTable()};
  $$("nav button").forEach(b=>b.onclick=()=>{saveSettings({view:b.dataset.view});render()});
  for(const [key,ids] of Object.entries(filterControls))for(const id of ids){const el=$("#"+id);el.addEventListener(key==="search"?"input":"change",()=>{const filters={...state.settings.filters,[key]:el.value};saveSettings({filters});for(const peer of ids)if(peer!==id)$("#"+peer).value=el.value;if(state.settings.view==="timeline")renderTimeline();else renderTable()})}
  $("#columns").onchange=()=>{const visibleColumns=$$("#columns input:checked").map(x=>x.value);saveSettings({visibleColumns});renderTable()};
  $("#zoom").onchange=e=>{saveSettings({zoom:e.target.value});renderTimeline()};$("#show-deps").onchange=e=>{saveSettings({showDependencies:e.target.checked});renderTimeline()};
  $("#today").onclick=()=>{const px=DAY[state.settings.zoom],x=140+45*px;$("#timeline").scrollLeft=Math.max(0,x-$("#timeline").clientWidth/3)};$("#timeline").onscroll=()=>{clearTimeout($("#timeline")._timer);$("#timeline")._timer=setTimeout(()=>saveSettings({timelineScroll:$("#timeline").scrollLeft}),500)};
  $("#add").onclick=()=>createTask();$("#backup").onclick=downloadBackup;
  $("#help").onclick=()=>$("#shortcuts").showModal();$("#close-help").onclick=()=>$("#shortcuts").close();$("#edit-form").onsubmit=saveEditor;
  $("#quick-input").addEventListener("input",()=>{state.quickIndex=0;$("#quick-error").textContent="";renderQuickSuggestions()});
  $("#quick-input").addEventListener("keydown",e=>{const visible=!$("#quick-suggestions").classList.contains("hidden"),items=$("#quick-suggestions")._found?.items||[];if(["Escape","ArrowUp","ArrowDown","Enter","Tab"].includes(e.key))e.stopPropagation();if(e.key==="Escape"){e.preventDefault();if(visible)hideQuickSuggestions();else closeOverlay()}else if(visible&&(e.key==="ArrowUp"||e.key==="ArrowDown")){e.preventDefault();state.quickIndex=(state.quickIndex+(e.key==="ArrowDown"?1:-1)+items.length)%items.length;renderQuickSuggestions()}else if(visible&&!e.shiftKey&&(e.key==="Enter"||e.key==="Tab")){e.preventDefault();acceptQuickSuggestion()}else if(e.key==="Enter"){e.preventDefault();submitQuickAdd(e.shiftKey)}});
  $("#quick-suggestions").addEventListener("mousedown",e=>e.preventDefault());$("#quick-suggestions").addEventListener("click",e=>{const option=e.target.closest("[data-index]");if(option){state.quickIndex=+option.dataset.index;acceptQuickSuggestion()}});
  $("#palette-input").addEventListener("input",()=>{state.paletteIndex=0;renderPalette()});
  $("#palette-input").addEventListener("keydown",e=>{if(["Escape","ArrowUp","ArrowDown","Home","End","Enter"].includes(e.key))e.stopPropagation();if(e.key==="Escape"){e.preventDefault();closeOverlay()}else if(e.key==="ArrowUp"||e.key==="ArrowDown"){e.preventDefault();const n=state.paletteItems.length;if(n){state.paletteIndex=(state.paletteIndex+(e.key==="ArrowDown"?1:-1)+n)%n;renderPalette()}}else if(e.key==="Home"||e.key==="End"){e.preventDefault();state.paletteIndex=e.key==="Home"?0:Math.max(0,state.paletteItems.length-1);renderPalette()}else if(e.key==="Enter"){e.preventDefault();executePaletteItem()}});
  $("#palette-results").addEventListener("mousedown",e=>e.preventDefault());$("#palette-results").addEventListener("click",e=>{const option=e.target.closest("[data-index]");if(option){state.paletteIndex=+option.dataset.index;executePaletteItem()}});
  for(const id of ["quick-add","command-palette"]){const dialog=$("#"+id);dialog.addEventListener("cancel",e=>{e.preventDefault();closeOverlay()})}
  for(const id of ["h-search","h-from","h-to","h-action"])$("#"+id).addEventListener(id==="h-action"?"change":"input",()=>{if(state.settings.view==="history")renderHistory()});
  document.addEventListener("keydown",e=>{const mod=e.ctrlKey||e.metaKey,typing=/INPUT|SELECT|TEXTAREA/.test(e.target.tagName)||e.target.isContentEditable,key=e.key.toLowerCase(),isN=key==="n"||e.code==="KeyN",isP=key==="p"||e.code==="KeyP",quick=(e.altKey&&!mod&&isN)||(e.ctrlKey&&(e.shiftKey||e.altKey)&&isN),palette=e.ctrlKey&&(e.shiftKey||e.altKey)&&isP;if(e.key==="Escape"&&$("#shortcuts").open)$("#shortcuts").close();if(e.key==="Escape"&&$("#editor").open)$("#editor").close();if(quick){e.preventDefault();openQuickAdd()}else if(palette){e.preventDefault();openPalette()}else if(mod&&e.key==="Enter"){e.preventDefault();createTask()}else if(mod&&key==="f"){e.preventDefault();$("#search").focus()}else if(mod&&e.key===" "&&!state.editing){e.preventDefault();toggleCurrent()}else if(mod&&(e.key==="Delete"||e.key==="Backspace")&&!state.editing&&!typing){e.preventDefault();deleteCurrent()}else if(e.key==="?"&&!typing)$("#shortcuts").showModal()});
  setInterval(()=>{if(state.settings.view==="table"&&state.settings.filters.status==="active")renderTable()},60000);
}
const TodoLogic={parseDate,parseQuickAdd,normalizeSearch,fuzzyRank,rankSearchItems,dayOffset,isoLocal,paletteResults};
if(typeof module!=="undefined")module.exports=TodoLogic;
if(typeof window!=="undefined"){window.TodoLogic=TodoLogic;bind();load()}

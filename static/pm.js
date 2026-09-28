/* Project workspace. Existing monthly reporting and performance modules remain intact. */
(function(){
  'use strict';
  const PROJECT={planning:'未开始',active:'进行中',paused:'已暂停',done:'已完成'};
  const TASK={todo:'待处理',doing:'进行中',blocked:'受阻',done:'已完成'};
  const PRIORITY={low:'低',normal:'普通',high:'高',urgent:'紧急'};
  const modes=['pm-home','pm-projects','pm-tasks','pm-timeline'];
  const titles={'pm-home':['WORKSPACE','项目工作台','把计划变成进展，专注每一次交付。'],'pm-projects':['PROJECTS','项目中心','管理项目全周期，跟进每一个关键节点。'],'pm-tasks':['TASK BOARD','任务看板','明确负责人，让协作有序推进。'],'pm-timeline':['DELIVERY PLAN','交付计划','统筹交付日期与里程碑，提前关注风险。']};
  const state={mode:'pm-home',projects:[],tasks:[],milestones:[],allowed:{},detail:null,tab:'tasks',query:'',filter:'',code:'',category:'',epoch:0,delivery:{from:'',to:'',query:'',type:'',status:''}};
  const root=document.createElement('section');root.id='pm-workspace';root.className='module hidden';
  const dialog=document.createElement('dialog');dialog.className='pm-dialog';dialog.id='pm-dialog';document.body.append(dialog);
  dialog.addEventListener('click',e=>{if(e.target===dialog)dialog.close();});
  const el=id=>document.getElementById(id);
  const e=value=>esc(value);
  const today=()=>{const d=new Date();return d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0');};
  const late=(date,status)=>!!date&&date<today()&&status!=='done';
  const tag=(value,labels)=>`<span class="pm-tag ${e(value)}">${e(labels[value]||value)}</span>`;
  const empty=(title,body='')=>`<div class="pm-empty"><b>${e(title)}</b>${e(body)}</div>`;
  const button=(text,action,kind='')=>`<button type="button" class="pm-button ${kind}" onclick="${action}">${text}</button>`;
  const progress=n=>`<div class="pm-progress"><div><i style="width:${Math.max(0,Math.min(100,Number(n)||0))}%"></i></div><span>${Number(n)||0}%</span></div>`;
  const avatar=name=>`<span class="pm-avatar">${e((name||'未')[0])}</span>`;
  async function request(method,url,body){const r=await api(method,url,body);if(!r.ok){if(r.unauthorized)showLogin();throw Error(r.error||'请求失败，请稍后重试');}return r;}
  function header(title,sub,actions='',eyebrow='PROJECT MANAGEMENT'){return `<header class="pm-heading"><div><div class="pm-eyebrow">${eyebrow}</div><h1>${e(title)}</h1><p>${e(sub)}</p></div><div class="pm-actions">${actions}</div></header>`;}
  function panel(title,body,action=''){return `<div class="pm-panel"><div class="pm-panel-head"><h2>${title}</h2>${action}</div>${body}</div>`;}
  function options(map,value){return Object.entries(map).map(([k,v])=>`<option value="${e(k)}" ${String(value)===k?'selected':''}>${e(v)}</option>`).join('');}
  function field(label,name,value='',type='text',full=false,required=false){return `<label class="${full?'full':''}">${label}<input name="${name}" type="${type}" value="${e(value)}" ${required?'required':''} ${type==='number'?'min="0" max="100" step="1"':''}></label>`;}
  function select(label,name,map,value,full=false){return `<label class="${full?'full':''}">${label}<select name="${name}" aria-label="${e(label)}">${options(map,value)}</select></label>`;}
  function area(label,name,value=''){return `<label class="full">${label}<textarea name="${name}" rows="3" maxlength="2000">${e(value)}</textarea></label>`;}
  function form(title,fields,save){
    dialog.innerHTML=`<form><h2>${e(title)}</h2><div class="pm-form-grid">${fields}</div><p id="pm-form-error" class="pm-overdue mt-3" role="alert"></p><footer>${button('取消',"document.getElementById('pm-dialog').close()") }<button class="pm-button primary" type="submit">保存</button></footer></form>`;
    dialog.querySelector('form').onsubmit=async ev=>{ev.preventDefault();const f=ev.currentTarget;const submit=f.querySelector('[type=submit]');submit.disabled=true;el('pm-form-error').textContent='';try{await save(Object.fromEntries(new FormData(f)));dialog.close();toast('已保存');await refresh();}catch(err){el('pm-form-error').textContent=err.message;}finally{submit.disabled=false;}};
    dialog.showModal();
  }
  function scope(){return currentUser.is_admin?'全部项目':(currentUser.is_manager?'我的项目与所辖部门项目':'我创建的项目');}
  function syncLegacy(){allowedPeriod=state.allowed;if(currentUser.is_admin)adminProjectList=state.projects;else projectList=state.projects.filter(p=>p.can_manage);}
  async function load(mode){
    state.mode=mode;state.detail=null;state.query='';state.filter='';state.code='';state.category='';const epoch=++state.epoch;
    root.innerHTML='<div class="pm-body-loading" role="status">正在载入项目工作区…</div>';
    try{const [p,t,m]=await Promise.all([request('GET','/api/projects'),request('GET','/api/work/tasks'),request('GET','/api/work/milestones')]);if(epoch!==state.epoch)return;state.projects=p.projects;state.allowed=p.allowed;state.tasks=t.tasks;state.milestones=m.milestones;syncLegacy();render();}catch(err){if(epoch===state.epoch)root.innerHTML=`<div class="pm-error" role="alert">${e(err.message)} ${button('重新加载',`PM.load('${mode}')`)}</div>`;}
  }
  async function refresh(){if(state.detail){const id=state.detail.project.id,tab=state.tab;const [p,t,m]=await Promise.all([request('GET','/api/projects'),request('GET','/api/work/tasks'),request('GET','/api/work/milestones')]);state.projects=p.projects;state.tasks=t.tasks;state.milestones=m.milestones;state.allowed=p.allowed;syncLegacy();await detail(id,tab);}else{const q=state.query,f=state.filter,c=state.code,k=state.category;await load(state.mode);state.query=q;state.filter=f;state.code=(state.mode==='pm-tasks'?[...state.projects,...state.tasks]:state.projects).some(p=>p.project_code===c)?c:'';state.category=k;render();}}
  function projectRow(p){const n=p.task_total?Math.round(p.task_done/p.task_total*100):0;return `<tr><td><button class="pm-link" onclick="PM.detail(${p.id})"><strong>${e(p.project_name)}</strong><span class="pm-sub">${e(p.project_code)}</span></button></td><td>${avatar(p.owner_name)}${e(p.owner_name)}</td><td>${tag(p.status,PROJECT)}</td><td>${progress(n)}<span class="pm-sub">${p.task_done} / ${p.task_total} 项任务</span></td><td class="${late(p.delivery_date,p.status)?'pm-overdue':''}">${e(p.delivery_date)}</td></tr>`;}
  function projectTable(list){return list.length?`<div class="pm-table-wrap"><table class="pm-table"><thead><tr><th>项目名称</th><th>负责人</th><th>状态</th><th>任务完成率</th><th>交付日期</th></tr></thead><tbody>${list.map(projectRow).join('')}</tbody></table></div>`:empty('还没有项目','创建第一个项目，开始安排任务与交付。');}
  function agenda(ms){return ms.length?ms.map(m=>`<div class="pm-agenda"><div class="pm-agenda-date">${e(m.due_date?m.due_date.slice(5,7)+'月':'待定')}<b>${e(m.due_date?m.due_date.slice(8):'—')}</b></div><div><h3><button class="pm-link" onclick="PM.detail(${m.project_id},'milestones')">${e(m.name)}</button></h3><p>${e(m.project_name)}</p><span class="pm-date ${late(m.due_date,m.status)?'pm-overdue':''}">${late(m.due_date,m.status)?'已逾期':m.status==='done'?'已完成':'待交付'}</span></div></div>`).join(''):empty('暂无待交付里程碑','在项目详情中添加关键节点。');}
  function home(){
    const active=state.projects.filter(p=>p.status==='active').length;
    const pending=state.tasks.filter(t=>t.status!=='done').length;
    const overdue=state.tasks.filter(t=>late(t.due_date,t.status));
    const managed=state.projects.filter(p=>p.can_manage);
    const filled=managed.filter(p=>p.this_month).length;
    const personal=state.tasks.filter(t=>t.shared&&t.assignee_id===currentUser.id);
    const personalPending=personal.filter(t=>!t.monthly).length;
    const upcoming=state.milestones.filter(m=>m.status!=='done').slice(0,5);
    const stats=[[active,'进行中的项目',`共 ${state.projects.length} 个项目`],[pending,'待完成任务',`已完成 ${state.tasks.length-pending} 项`],[overdue.length,'逾期任务','优先处理交付风险'],[managed.length-filled,'待填报项目',state.allowed.label+'月度填报']];
    return `<div class="pm-stats">${stats.map(([n,l,s])=>`<article class="pm-stat"><small>${l}</small><strong>${n}</strong><footer>${e(s)}</footer></article>`).join('')}</div><div class="pm-grid"><div>${panel('项目进展',projectTable(state.projects.slice(0,6)),button('查看全部 →',"js_go('pm-projects')"))}${panel('需要关注的任务',overdue.length?`<div class="pm-panel-body">${overdue.slice(0,5).map(t=>`<div class="pm-agenda"><div><h3>${t.can_edit?`<button class="pm-link" onclick="PM.editTask(${t.id})">${e(t.title)}</button>`:e(t.title)}</h3><p>${e(t.project_name)} · ${e(t.assignee_name||'未分配')}</p></div><span class="pm-date pm-overdue ms-auto">${e(t.due_date)} 到期</span></div>`).join('')}</div>`:empty('当前没有逾期任务','保持节奏，按计划推进。'))}</div><div>${panel('近期里程碑',`<div class="pm-panel-body">${agenda(upcoming)}</div>`,button('全部 →',"js_go('pm-timeline')"))}<div class="pm-panel pm-month"><div class="pm-panel-body"><div class="pm-eyebrow">MONTHLY REPORT</div><h2 class="mt-3" style="font-size:18px">${e(state.allowed.label)} · 月度填报</h2><p>已填报 ${filled} / ${managed.length} 个负责项目。记录本期进度与完成事项，形成可追溯的月度绩效。</p>${progress(managed.length?Math.round(filled/managed.length*100):0)}<div class="pm-actions mt-4">${personal.length?button('个人任务月报（待填 '+personalPending+'）',"js_go('pm-tasks')",'primary'):''}${button('项目月报 →',`js_go('${currentUser.is_admin?'a-projects':'m-projects'}')`,'primary')}${currentUser.is_admin?button('月度绩效',"js_go('a-board')") :currentUser.is_manager?button('部门绩效',"js_go('dm-performance')") :''}</div></div></div></div></div>`;
  }
  function toolbar(kind){
    if(state.mode==='pm-projects'||state.mode==='pm-tasks'){
      const isTask=state.mode==='pm-tasks';
      const codes=[...new Set((isTask?[...state.projects,...state.tasks]:state.projects).map(p=>p.project_code).filter(Boolean))].sort((a,b)=>a.localeCompare(b,'zh-CN',{numeric:true}));
      return `<div class="pm-project-filters">
        <label>关键字<input class="pm-input" id="pm-query" aria-label="关键字" placeholder="${isTask?'任务名称、项目名称、执行人或任务说明':'项目名称、负责人或任务内容'}" value="${e(state.query)}" oninput="PM.projectFilter('query',this.value)"></label>
        <label>项目编码<select class="pm-input" id="pm-code" aria-label="项目编码" onchange="PM.projectFilter('code',this.value)"><option value="">全部编码</option>${codes.map(c=>`<option value="${e(c)}" ${c===state.code?'selected':''}>${e(c)}</option>`).join('')}</select></label>
        <label>项目类别<select class="pm-input" id="pm-category" aria-label="项目类别" onchange="PM.projectFilter('category',this.value)"><option value="">全部类别</option>${options({market:'市场项目',self:'自研项目'},state.category)}</select></label>
        <label>状态<select class="pm-input" id="pm-status" aria-label="状态" onchange="PM.projectFilter('filter',this.value)"><option value="">全部状态</option>${options(isTask?TASK:PROJECT,state.filter)}</select></label>
        ${button('重置筛选','PM.resetProjectFilters()')}
      </div><div class="pm-filter-count" id="pm-filter-count" role="status" aria-live="polite">${isTask?taskCount():projectCount()}</div>`;
    }
    return `<div class="pm-toolbar"><input class="pm-input" id="pm-query" aria-label="搜索${kind}" placeholder="搜索${kind}名称、编号或负责人…" value="${e(state.query)}" oninput="PM.filter(this.value)"><select class="pm-input" aria-label="筛选状态" onchange="PM.filter(undefined,this.value)"><option value="">全部状态</option>${options(TASK,state.filter)}</select><span class="pm-note">${e(scope())}</span></div>`;
  }
  function filteredProjects(){
    const q=state.query.trim().toLowerCase();
    return state.projects.filter(p=>(!state.code||p.project_code===state.code)&&(!state.category||p.category===state.category)&&(!state.filter||p.status===state.filter)&&[p.project_name,p.project_code,p.owner_name,p.tasks].join(' ').toLowerCase().includes(q));
  }
  function projectCount(){return `共 ${state.projects.length} 个项目，当前显示 ${filteredProjects().length} 个`;}
  function projectFilter(key,value){
    if(!['query','code','category','filter'].includes(key))return;
    state[key]=value;
    el('pm-results').innerHTML=state.mode==='pm-tasks'?taskWorkspace():projectCards();
    el('pm-filter-count').textContent=state.mode==='pm-tasks'?taskCount():projectCount();
  }
  function resetProjectFilters(){state.query='';state.code='';state.category='';state.filter='';render();}
  function projectCards(){const list=filteredProjects();return list.length?list.map(p=>`<article class="pm-project"><div class="pm-project-top"><span class="pm-sub">${e(p.project_code)}</span>${tag(p.status,PROJECT)}</div><h3><button class="pm-link" onclick="PM.detail(${p.id})">${e(p.project_name)}</button></h3><p>${e(p.tasks||'暂未填写项目目标和交付内容')}</p><div class="pm-actions">${tag(p.priority,PRIORITY)}<span class="pm-tag">${p.category==='market'?'市场项目':'自研项目'}</span></div><div><div class="pm-project-top mb-2"><span class="pm-note">任务完成率</span><span class="pm-note">${p.task_done} / ${p.task_total}</span></div>${progress(p.task_total?Math.round(p.task_done/p.task_total*100):0)}</div><footer><span>${avatar(p.owner_name)}${e(p.owner_name)}</span><span class="${late(p.delivery_date,p.status)?'pm-overdue':''}">${e(p.delivery_date)} 交付</span></footer><div>${button('进入项目 →',`PM.detail(${p.id})`)}</div></article>`).join(''):empty('没有符合条件的项目','调整筛选，或创建一个新项目。');}
  function taskCard(t){return `<article class="pm-task"><div class="pm-project-top">${tag(t.priority,PRIORITY)}<span class="pm-date ${late(t.due_date,t.status)?'pm-overdue':''}">${e(t.due_date||'未设日期')}</span></div><h3>${t.can_edit?`<button class="pm-link" onclick="PM.editTask(${t.id})">${e(t.title)}</button>`:e(t.title)}</h3><span class="pm-sub">${e(t.project_name||state.detail?.project.project_name||'')}</span><div class="mt-3">${progress(t.progress)}</div><footer><span>${avatar(t.assignee_name)}${e(t.assignee_name||'未分配')}</span><select ${t.can_edit?'':'disabled'} aria-label="${e(t.title)}的状态" onchange="PM.changeTask(${t.id},this.value,this)">${options(TASK,t.status)}</select></footer>${t.can_edit&&(t.shared||t.can_manage)?button('本月项目占比',`PM.editShare(${t.project_id})`):''}${t.shared&&t.can_edit?button('填报个人月报',`PM.reportTask(${t.id})`):''}</article>`;}
  function filteredTasks(tasks){const q=state.query.trim().toLowerCase();return tasks.filter(t=>(!state.code||t.project_code===state.code)&&(!state.category||t.project_category===state.category)&&(!state.filter||t.status===state.filter)&&[t.title,t.detail,t.project_name,t.project_code,t.assignee_name].join(' ').toLowerCase().includes(q));}
  function taskCount(){return `共 ${state.tasks.length} 项任务，当前显示 ${filteredTasks(state.tasks).length} 项`;}
  function board(tasks){tasks=filteredTasks(tasks);return Object.entries(TASK).filter(([s])=>!state.filter||state.filter===s).map(([s,l])=>{const list=tasks.filter(t=>t.status===s);return `<section class="pm-column"><h2>${l}<span>${list.length}</span></h2>${list.length?list.map(taskCard).join(''):empty('暂无任务')}</section>`;}).join('');}
  function taskProjects(){
    const q=state.query.trim().toLowerCase(),matching=new Set(filteredTasks(state.tasks).map(t=>t.project_id));
    return state.projects.filter(p=>(!state.code||p.project_code===state.code)&&(!state.category||p.category===state.category)&&(!state.filter||matching.has(p.id))&&(!q||[p.project_name,p.project_code,p.owner_name,p.tasks].join(' ').toLowerCase().includes(q)||matching.has(p.id)));
  }
  function taskWorkspace(){const projects=taskProjects();return panel('项目 · 与项目中心同步',projects.length?`<div class="pm-table-wrap"><table class="pm-table"><thead><tr><th>项目</th><th>类别</th><th>项目状态</th><th>任务数</th><th>操作</th></tr></thead><tbody>${projects.map(p=>`<tr><td><strong>${e(p.project_name)}</strong><span class="pm-sub">${e(p.project_code)}</span></td><td>${p.category==='market'?'市场项目':'自研项目'}</td><td>${tag(p.status,PROJECT)}</td><td>${p.task_total||0}</td><td><div class="pm-actions">${button('进入项目',`PM.detail(${p.id})`)}${p.can_manage?button('编辑项目',`PM.editProject(${p.id})`):''}</div></td></tr>`).join('')}</tbody></table></div>`:empty('没有符合条件的项目'))+`<div class="pm-note mb-3">项目只需创建一次，即同步显示在工作台、项目中心、任务看板和交付计划。下方为各项目的具体工作任务；状态筛选针对任务。</div><div class="pm-board">${board(state.tasks)}</div>`;}
  function deliveryRows(){
    const deliveries=state.projects.map(p=>({date:p.delivery_date,title:p.project_name,sub:p.project_code,type:'project',status:p.status,pid:p.id}));
    const milestones=state.milestones.map(m=>({date:m.due_date,title:m.name,sub:m.project_name,type:'milestone',status:m.status,pid:m.project_id}));
    return [...deliveries,...milestones].sort((a,b)=>(a.date||'9999').localeCompare(b.date||'9999'));
  }
  function filteredDeliveries(rows){const f=state.delivery,q=f.query.trim().toLowerCase();return rows.filter(r=>(!f.from||(r.date&&r.date>=f.from))&&(!f.to||(r.date&&r.date<=f.to))&&(!q||[r.title,r.sub].join(' ').toLowerCase().includes(q))&&(!f.type||r.type===f.type)&&(!f.status||(f.status==='overdue'?late(r.date,r.status):r.status===f.status)));}
  function deliveryBody(){const rows=filteredDeliveries(deliveryRows());return rows.length?rows.map(r=>`<tr><td class="${late(r.date,r.status)?'pm-overdue':''}">${e(r.date||'未设定')}</td><td><strong>${e(r.title)}</strong><span class="pm-sub">${e(r.sub)}</span></td><td>${r.type==='project'?'项目交付':'里程碑'}</td><td>${tag(r.status,{...PROJECT,pending:'待完成'})}${late(r.date,r.status)?' <span class="pm-tag urgent">逾期</span>':''}</td><td>${button('查看',`PM.detail(${r.pid},'milestones')`)}</td></tr>`).join(''):'<tr><td colspan="5">没有符合筛选条件的交付计划</td></tr>';}
  function deliveryCount(){return `共 ${deliveryRows().length} 项，当前显示 ${filteredDeliveries(deliveryRows()).length} 项`;}
  function timeline(){const f=state.delivery;return panel('交付日程 · 按日期排序',`<div class="pm-table-wrap"><table class="pm-table"><thead><tr><th>日期<div class="pm-actions"><input class="pm-input" type="date" aria-label="开始日期" value="${e(f.from)}" onchange="PM.deliveryFilter('from',this.value)"><span>至</span><input class="pm-input" type="date" aria-label="结束日期" value="${e(f.to)}" onchange="PM.deliveryFilter('to',this.value)"></div></th><th>交付内容<input class="pm-input" aria-label="交付内容关键字" placeholder="项目、编码或里程碑" value="${e(f.query)}" oninput="PM.deliveryFilter('query',this.value)"></th><th>类型<select class="pm-input" aria-label="交付类型" onchange="PM.deliveryFilter('type',this.value)">${options({'':'全部类型',project:'项目交付',milestone:'里程碑'},f.type)}</select></th><th>状态<select class="pm-input" aria-label="交付状态" onchange="PM.deliveryFilter('status',this.value)">${options({'':'全部状态',...PROJECT,pending:'待完成',overdue:'逾期'},f.status)}</select></th><th>${button('重置筛选','PM.resetDeliveryFilters()')}</th></tr></thead><tbody id="pm-delivery-results">${deliveryBody()}</tbody></table></div><p id="pm-delivery-count" class="pm-note" role="status">${deliveryCount()}</p><p id="pm-delivery-error" role="alert"></p>`);}
  function deliveryFilter(key,value){if(!['from','to','query','type','status'].includes(key))return;state.delivery[key]=value;el('pm-delivery-error').textContent=state.delivery.from&&state.delivery.to&&state.delivery.from>state.delivery.to?'开始日期不能晚于结束日期':'';el('pm-delivery-results').innerHTML=deliveryBody();el('pm-delivery-count').textContent=deliveryCount();}
  function resetDeliveryFilters(){state.delivery={from:'',to:'',query:'',type:'',status:''};render();}
  function render(){
    if(state.detail)return renderDetail();
    const [en,title,sub]=titles[state.mode];
    root.innerHTML=header(title,sub,button('刷新','PM.refresh()')+button('＋ 新建项目','PM.editProject()','primary'),en)+(state.mode==='pm-home'?home():state.mode==='pm-projects'?toolbar('项目')+`<div id="pm-results" class="pm-project-grid">${projectCards()}</div>`:state.mode==='pm-tasks'?toolbar('任务')+`<div class="pm-actions mb-3">${button('＋ 添加任务','PM.editTask()','primary')}<span class="pm-note">选择下方已有项目安排任务，无需重复新建项目。</span></div><div id="pm-results">${taskWorkspace()}</div>`:timeline());
  }
  async function detail(id,tab='tasks'){
    const epoch=++state.epoch;root.innerHTML='<div class="pm-body-loading">正在加载项目详情…</div>';
    try{const r=await request('GET','/api/projects/'+id);if(epoch!==state.epoch)return;state.detail=r;state.tab=tab;state.query='';state.filter='';state.code='';state.category='';state.allowed=r.allowed;const p=r.project;p.owner_name=p.owner.name;const idx=state.projects.findIndex(x=>x.id===id);if(idx>=0)state.projects[idx]=p;else state.projects.push(p);syncLegacy();renderDetail();}catch(err){root.innerHTML=`<div class="pm-error">${e(err.message)} ${button('返回项目中心',"js_go('pm-projects')")}</div>`;}
  }
  function renderDetail(){const d=state.detail,p=d.project;let body='';
    if(state.tab==='tasks')body=`<div class="pm-actions mb-3">${p.can_manage?button('＋ 添加任务','PM.editTask()','primary'):''}<span class="pm-note">项目创建人维护任务；部门负责人可查看所辖部门项目。</span></div><div class="pm-board">${board(d.tasks)}</div>`;
    if(state.tab==='milestones')body=panel('关键交付节点',d.milestones.length?`<div class="pm-table-wrap"><table class="pm-table"><thead><tr><th>里程碑</th><th>目标日期</th><th>状态</th><th>操作</th></tr></thead><tbody>${d.milestones.map(m=>`<tr><td>${e(m.name)}</td><td class="${late(m.due_date,m.status)?'pm-overdue':''}">${e(m.due_date||'未设定')}</td><td>${tag(m.status,{pending:'待完成',done:'已完成'})}</td><td><div class="pm-actions">${p.can_manage?button('编辑',`PM.editMilestone(${m.id})`)+button(m.status==='done'?'重新打开':'标记完成',`PM.toggleMilestone(${m.id})`)+button('删除',`PM.removeMilestone(${m.id})`,'danger'):''}</div></td></tr>`).join('')}</tbody></table></div>`:empty('尚未设置里程碑','添加关键交付节点，帮助团队跟进计划。'),(p.can_manage?button('＋ 添加里程碑','PM.editMilestone()','primary'):''));
    if(state.tab==='monthly')body=panel('月度进度记录',d.progress.length?`<div class="pm-table-wrap"><table class="pm-table"><thead><tr><th>月份</th><th>进度</th><th>本期完成事项</th><th>未完成事项</th></tr></thead><tbody>${[...d.progress].reverse().map(r=>`<tr><td>${r.year}-${String(r.month).padStart(2,'0')}</td><td>${r.progress}%</td><td style="white-space:pre-wrap">${e(r.done_items||'—')}</td><td style="white-space:pre-wrap">${e(r.undone_items||'—')}</td></tr>`).join('')}</tbody></table></div>`:empty('还没有月度记录','仅可填写上一个月，历史月份保持只读。'),(p.can_manage?button('填报 '+e(state.allowed.label),'PM.reportMonth()','primary'):''));
    if(state.tab==='monthly'&&p.shared){body='<p class="pm-note">项目整体月报仅记录项目交付；个人绩效请在任务看板中点击「填报个人月报」。同一人同一项目按已填报任务平均进度 × 本月项目占比 × 考核权重计算。</p>'+body+panel('个人任务月报',d.task_reports.length?`<div class="pm-table-wrap"><table class="pm-table"><thead><tr><th>月份</th><th>人员</th><th>任务</th><th>进度</th><th>完成事项</th></tr></thead><tbody>${d.task_reports.map(r=>`<tr><td>${r.year}-${r.month}</td><td>${e(r.user_name)}</td><td>${e(r.task_title)}</td><td>${r.progress}%</td><td>${e(r.done_items)}</td></tr>`).join('')}</tbody></table></div>`:empty('暂无个人任务月报'));}
    root.innerHTML=button('← 项目中心',"js_go('pm-projects')")+header(p.project_name,p.project_code+' · '+(p.category==='market'?'市场项目':'自研项目')+(p.shared?' · 所属部门：'+p.shared_department:' · 个人项目'),button('本月项目占比',`PM.editShare(${p.id})`)+(p.can_manage?button('编辑项目',`PM.editProject(${p.id})`)+button('删除项目',`PM.removeProject(${p.id})`,'danger'):''),'PROJECT DETAIL')+`<div class="pm-detail-meta"><div><small>负责人</small>${e(p.owner.name)}</div><div><small>状态</small>${tag(p.status,PROJECT)}</div><div><small>优先级</small>${tag(p.priority,PRIORITY)}</div><div><small>计划周期</small>${e(p.start_date)} → ${e(p.delivery_date)}</div><div><small>任务完成</small>${p.task_done} / ${p.task_total}</div><div><small>我的本月项目占比</small>${p.my_project_share??0}%</div><div><small>最新月度进度</small>${p.current_progress===null?'未填报':p.current_progress+'%'}</div></div><p class="pm-detail-description">${e(p.tasks||'暂无项目目标描述')}</p><div class="pm-tabs">${[['tasks','任务看板'],['milestones','里程碑'],['monthly','月度填报']].map(([k,v])=>`<button class="${state.tab===k?'active':''}" onclick="PM.tab('${k}')">${v}</button>`).join('')}</div>${body}`;
  }
  async function userOptions(){const r=await request('GET','/api/user-options');return Object.fromEntries(r.users.map(u=>[u.id,u.name+(u.department?' · '+u.department:'')]));}
  async function editProject(id){try{const p=id?(state.detail?.project.id===id?state.detail.project:state.projects.find(p=>p.id===id)):{};const users=currentUser.is_admin&&!id?await userOptions():{};form(id?'编辑项目':'新建项目',field('项目名称 *','project_name',p.project_name,'text',true,true)+field('项目编号 *','project_code',p.project_code,'text',false,true)+select('项目类别','category',{market:'市场项目',self:'自研项目'},p.category||'market')+field('开始日期 *','start_date',p.start_date||today(),'date',false,true)+field('交付日期 *','delivery_date',p.delivery_date,'date',false,true)+select('项目状态','status',PROJECT,p.status||'planning')+select('优先级','priority',PRIORITY,p.priority||'normal')+(currentUser.is_admin&&!id?select('项目负责人','user_id',users,currentUser.id,true):'')+(!id&&(currentUser.is_manager||currentUser.is_admin)?select('发布范围','shared',{'true':'项目任务月报模式（仅本人及负责人可见）','false':'个人项目'},currentUser.is_manager?'true':'false',true):'')+(!id?field('负责人本月项目占比（%）','project_share',0,'number',true,true).replace('step="1"','step="0.01"'):'')+area('项目目标 / 交付内容','tasks',p.tasks),async values=>{if('shared' in values)values.shared=values.shared==='true';if(values.delivery_date<values.start_date)throw Error('交付日期不能早于开始日期');await request(id?'PUT':'POST','/api/projects'+(id?'/'+id:''),values);});}catch(err){toast(err.message,'danger');}}
  function findTask(id){return state.detail?.tasks.find(t=>t.id===id)||state.tasks.find(t=>t.id===id);}
  async function editTask(id){try{
    const t=id?findTask(id):{}; if(id&&(!t||!t.can_edit))throw Error('只能维护自己的任务');
    const editableProjects=state.projects.filter(p=>p.can_manage);
    if(!id&&!editableProjects.length)throw Error('暂无可添加任务的项目');
    const selected=state.detail?.project||editableProjects[0];
    const manage=id?t.can_manage:!!selected?.can_manage;
    const full=!id||t.can_edit_details;
    const users=manage?{'':'未分配',...await userOptions()}:{[currentUser.id]:currentUser.name};
    const fields=(full?(!id&&!state.detail?select('所属项目','project_id',Object.fromEntries(editableProjects.map(p=>[p.id,p.project_name])),selected.id,true):'')+
      field('任务名称 *','title',t.title,'text',true,true)+select('执行人','assignee_id',users,manage?(t.assignee_id||''):currentUser.id)+field('截止日期','due_date',t.due_date,'date')+select('优先级','priority',PRIORITY,t.priority||'normal'):'')+
      select('任务状态','status',TASK,t.status||'todo')+field('完成进度（0–100）','progress',t.progress||0,'number')+(full?area('任务说明','detail',t.detail):'');
    form(id?'维护工作任务':'添加工作任务',fields,async values=>{
      const pid=state.detail?.project.id||values.project_id;delete values.project_id;
      values.progress=Number(values.progress);
      await request(id?'PUT':'POST',id?'/api/tasks/'+id:'/api/projects/'+pid+'/tasks',values);
    });
    if(id&&manage){const remove=document.createElement('button');remove.type='button';remove.className='pm-button danger';remove.textContent='删除任务';remove.onclick=()=>removeTask(id);dialog.querySelector('footer').prepend(remove);}
  }catch(err){toast(err.message,'danger');}}
  function reportTask(id){const t=findTask(id),r=t.monthly||{};form('个人任务月报 · '+state.allowed.label,
    '<div class="full pm-note">'+e(t.title)+'：按个人实际工作填报。同一项目的任务完成率取平均，再乘本月项目占比和考核权重。请先设置「本月项目占比」，未设置按0%计算。</div>'+field('本月完成进度','progress',r.progress??t.progress,'number',true,true)+area('本期完成事项','done_items',r.done_items)+area('未完成事项','undone_items',r.undone_items),
    values=>request('PUT','/api/tasks/'+id+'/monthly',{...values,progress:Number(values.progress),year:state.allowed.year,month:state.allowed.month}));}
  async function changeTask(id,status,selectEl){const t=findTask(id);selectEl.disabled=true;try{await request('PUT','/api/tasks/'+id,{status,progress:status==='done'?100:t.status==='done'?0:t.progress});await refresh();}catch(err){selectEl.value=t.status;toast(err.message,'danger');}finally{selectEl.disabled=false;}}
  async function removeTask(id){if(!confirm('确定删除这个任务？此操作无法撤销。'))return;try{await request('DELETE','/api/tasks/'+id);dialog.close();await refresh();toast('任务已删除');}catch(err){toast(err.message,'danger');}}
  function editMilestone(id){const m=id?state.detail.milestones.find(m=>m.id===id):{};form(id?'编辑里程碑':'添加里程碑',field('里程碑名称 *','name',m.name,'text',true,true)+field('目标日期','due_date',m.due_date,'date')+(id?select('状态','status',{pending:'待完成',done:'已完成'},m.status):''),values=>request(id?'PUT':'POST',id?'/api/milestones/'+id:'/api/projects/'+state.detail.project.id+'/milestones',values));}
  async function toggleMilestone(id){const m=state.detail.milestones.find(m=>m.id===id);try{await request('PUT','/api/milestones/'+id,{...m,status:m.status==='done'?'pending':'done'});await refresh();}catch(err){toast(err.message,'danger');}}
  async function removeMilestone(id){if(!confirm('确定删除这个里程碑？'))return;try{await request('DELETE','/api/milestones/'+id);await refresh();}catch(err){toast(err.message,'danger');}}
  async function removeProject(id){if(!confirm('确定删除项目及其全部任务、里程碑和月度记录？此操作无法撤销。'))return;try{await request('DELETE','/api/projects/'+id);state.detail=null;await load('pm-projects');}catch(err){toast(err.message,'danger');}}
  function reportMonth(){const p=state.detail.project,r=p.this_month||{};form('填报 '+state.allowed.label,`<div class="full pm-note">只记录该月实际进度，不会修改任务状态或其他月份。</div>`+field('当月进度（0–100） *','progress',r.progress??0,'number',true,true)+area('本期完成事项','done_items',r.done_items)+area('未完成事项','undone_items',r.undone_items),values=>request('PUT','/api/projects/'+p.id+'/progress',{...values,progress:Number(values.progress),year:state.allowed.year,month:state.allowed.month}));}

  async function editShare(pid){try{const r=await request('GET','/api/projects/'+pid+'/share');form('项目占比 · '+r.year+'年'+r.month+'月',
    '<p class="full">每人每月分别分配，市场、自研各自合计不超过100%。此项目最多可设置 '+r.available+'%。同一项目多个任务共用这一个占比。新月份不自动沿用上月占比。</p>'+field('我的项目占比（%）','share',r.share,'number',true,true).replace('step="1"','step="0.01"'),
    values=>request('PUT','/api/projects/'+pid+'/share',{share:Number(values.share),year:r.year,month:r.month}));}catch(err){toast(err.message,'danger');}}
  window.PM={deliveryFilter,resetDeliveryFilters,editShare,reportTask,projectFilter,resetProjectFilters,load,refresh,detail,editProject,editTask,changeTask,editMilestone,toggleMilestone,removeMilestone,removeProject,reportMonth,tab(t){state.tab=t;renderDetail();},filter(q,f){if(q!==undefined)state.query=q;if(f!==undefined)state.filter=f;el('pm-results').innerHTML=state.mode==='pm-projects'?projectCards():taskWorkspace();}};
  // Place the shared workspace inside the active role's layout.
  ENG_MODULES.unshift(...modes);ADMIN_MODULES.unshift(...modes);
  const oldShow=showModuleOnly,oldLoad=loadModuleData;
  showModuleOnly=function(mod){oldShow(mod);root.classList.add('hidden');if(modes.includes(mod)){const view=el(currentUser.is_admin?'adminView':'engineerView');view.querySelector('.content').prepend(root);root.classList.remove('hidden');}};
  loadModuleData=function(mod){if(modes.includes(mod)){stopUsersAutoRefresh();load(mod);}else{++state.epoch;oldLoad(mod);}};
  document.querySelectorAll('.sidebar').forEach(side=>{
    side.querySelectorAll('[data-mod="m-overview"],[data-mod="a-overview"]').forEach(b=>b.remove());
    const nav=document.createElement('div');nav.style.display='contents';nav.innerHTML='<div class="side-title">项目协作</div>'+[['pm-home','◫','工作台'],['pm-projects','▦','项目中心'],['pm-tasks','▤','任务看板'],['pm-timeline','◷','交付计划']].map(([m,i,l])=>`<button class="side-item" data-mod="${m}" onclick="js_go('${m}')"><span class="ico">${i}</span><span>${l}</span></button>`).join('');side.prepend(nav);
    side.querySelectorAll('[data-mod="m-projects"],[data-mod="a-projects"]').forEach(b=>{b.querySelector('span:last-child').textContent='月度填报';});
    side.querySelectorAll('[data-mod="a-board"]').forEach(b=>{b.querySelector('span:last-child').textContent='月度绩效';});
  });
  // Default to the new workbench while retaining explicit old reporting links.
  const oldLast=lastModule;lastModule=function(){const m=oldLast();return ['m-overview','a-overview'].includes(m)?'pm-home':m;};
})();

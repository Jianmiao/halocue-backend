const state={works:[],work:null,capabilities:null,stage:'overview',surface:'works',sceneId:null,context:null,inspector:'agent',mobileView:'writing',writingChapterId:'',libraryView:'overview',libraryEditorOpen:false,showGlobalSurfaces:true,editCardId:'',editCard:null,characterCardDraft:null,libraryCharacterFilter:'active',libraryQuery:'',librarySourceFilter:'all',libraryStatusFilter:'all',historyCardId:'',editCanonFactId:'',canonHistoryOpen:false,officialReferenceQuery:'',officialReferenceResults:[],officialReferenceSearched:false,officialReferenceLimit:6,worldQuery:'',worldKindFilter:'all',worldSourceFilter:'all',worldStatusFilter:'all',graphFocus:'',graphTypeFilter:'all',editWorldEntry:null,worldCardDraft:null,worldHistoryOpen:false,sceneContextEditorOpen:false,sceneContractOpen:false,manuscriptDirty:false,manuscriptSceneId:'',manuscriptBlockCounter:0,structureDraft:null,structureDirty:false,conversationThreadId:'',renamingThreadId:'',workAgentExpanded:false,mobileThreadOpen:false,composerAttachmentIds:[],threadRailFilter:'active',threadRailQuery:''};
document.addEventListener('click',event=>{
  const button=event.target.closest('button');
  if(!button)return;
  if(button.dataset.section==='works'||button.dataset.section==='references'||button.dataset.workSurface&&button.dataset.workSurface!=='writing')state.surface='works';
  if(button.dataset.section==='writing'||button.dataset.workSurface==='writing'||button.dataset.scene||button.dataset.sceneOpen||button.dataset.writingChapter)state.surface='writing';
},true);
document.addEventListener('submit',event=>{if(event.target.id==='workForm')state.surface='works'},true);
document.addEventListener('click',event=>{
  const button=event.target.closest('button[data-mobile]');
  if(!button)return;
  if(button.dataset.mobile==='works'){
    event.preventDefault();event.stopImmediatePropagation();
    state.mobileView='writing';state.surface='works';state.stage='overview';state.inspector='decision';render();
  }else if(button.dataset.mobile==='references'){
    event.preventDefault();event.stopImmediatePropagation();
    state.mobileView='writing';state.surface='works';state.stage='references';state.libraryView='overview';render();
  }else if(button.dataset.mobile==='writing'){
    event.preventDefault();event.stopImmediatePropagation();
    state.mobileView='writing';state.surface='writing';if(['overview','brief','blueprint','references'].includes(state.stage))state.stage='structure';render();
  }
},true);

// Agent composers use the common chat convention: Enter submits and
// Shift+Enter inserts a newline. IME composition must never submit early.
document.addEventListener('keydown',event=>{
  const textarea=event.target.closest('#workConversationForm textarea, #mobileWorkConversationForm textarea');
  if(!textarea||event.key!=='Enter'||event.shiftKey||event.isComposing||event.keyCode===229)return;
  event.preventDefault();
  const form=textarea.form;
  if(!textarea.value.trim()||form?.dataset.submitting==='true')return;
  form?.requestSubmit();
},true);

const renderStructureBeforeCompactGuidance=renderStructure;
renderStructure=function(el){
  renderStructureBeforeCompactGuidance(el);
  const inner=$('.workspace-inner',el);if(!inner)return;
  const title=inner.querySelector('h2'),lede=inner.querySelector('.lede');
  if(title)title.textContent='章节与场景';
  if(lede)lede.textContent='选择当前章节，再管理这一章的场景。全作方向、人物和世界观请回到“作品”。';
  inner.querySelector('.structure-scope-note')?.remove();
  const targets=[...inner.querySelectorAll('.writing-target-bar')];
  targets.slice(1).forEach(node=>node.remove());
  targets[0]?.classList.add('writing-target-compact');
  inner.querySelector('[data-structure-add-volume]')?.classList.replace('primary','quiet');
};

function decorateCurrentStepGuidance(){
  if(!state.work||['overview','brief','blueprint','references'].includes(state.stage))return;
  const hints={
    structure:'选择当前章节，并管理本章的场景',
    draft:'选择一个场景，写作并审查候选',
    release:'完成全篇审查，再冻结发布版本',
  };
  const current=$(`[data-stage="${state.stage}"]`);
  const small=current?.querySelector('small');
  if(small)small.textContent=hints[state.stage]||'处理当前任务';
}

const renderBeforeFeedbackAndGuidance=render;
render=function(){renderBeforeFeedbackAndGuidance();decorateCurrentStepGuidance()};

const renderMobileTasksBeforeFeedback=renderMobileTasks;
renderMobileTasks=function(el){
  renderMobileTasksBeforeFeedback(el);
  const actions=el.querySelector('.actions');
  if(actions)actions.insertAdjacentHTML('beforeend','<button class="quiet" type="button" data-action="feedback">反馈问题</button>');
};

function feedbackContext(){
  const chapter=writingChapter(),scene=selectedScene();
  return {
    path:location.pathname,
    stage:state.stage,
    mobile_view:state.mobileView,
    work_version:state.work?.version||null,
    chapter_id:chapter?.id||null,
    scene_id:scene?.id||null,
    viewport:{width:window.innerWidth,height:window.innerHeight},
  };
}

document.addEventListener('click',event=>{
  const open=event.target.closest('[data-action="feedback"]');
  const close=event.target.closest('[data-close-feedback]');
  if(open){event.preventDefault();event.stopImmediatePropagation();$('#feedbackDialog')?.showModal();setTimeout(()=>$('#feedbackForm input[name="summary"]')?.focus(),0);return;}
  if(close){event.preventDefault();event.stopImmediatePropagation();$('#feedbackDialog')?.close();}
},true);

document.addEventListener('submit',event=>{
  if(event.target.id!=='feedbackForm')return;
  event.preventDefault();event.stopImmediatePropagation();
  const form=event.target,fields=new FormData(form),submit=form.querySelector('[type="submit"]');
  if(submit)submit.disabled=true;
  (async()=>{try{
    const attach=fields.get('attach_context')==='on';
    const result=await api('/feedback',{method:'POST',body:JSON.stringify({
      work_id:state.work?.id||null,
      category:fields.get('category'),
      summary:fields.get('summary'),
      details:fields.get('details'),
      context:attach?feedbackContext():{},
    })});
    form.reset();$('#feedbackDialog')?.close();toast(`反馈已保存 · ${result.id}`);
  }catch(error){toast(error.message,true)}finally{if(submit)submit.disabled=false}})();
},true);

/* Final surface overrides. Keep these at EOF while the original vertical
   slice is still present above; this is the only render path the browser uses. */
conversationTaskContract=function(thread){
  const scope=agentTaskScope(),hasBrief=Boolean(brief()),hasBlueprint=blueprintIsConfirmed();
  let id='brief.build',task='理解这句想法，提出需要讨论的方向，不写入任何正式设定。';
  if(scope.surface==='work'){
    if(hasBrief){id='blueprint.generate';task='在作品栏目维护全作方向、人物关系和世界观边界；任何调整都先形成新的 StoryBlueprint Proposal。';}
  }else if(hasBlueprint){id='chapter.plan';task=`只规划《${writingChapter()?.title||'当前章节'}》内部的章节目标、承接点和场景节拍，不重写全作 StoryBlueprint。`;}
  else {id='blueprint.generate';task='全作方向尚未确认，请先回到作品栏目完成确认。';}
  const template=(state.capabilities?.writing_pack?.templates||[]).find(item=>item.id===id)||{};
  return {...template,id,task,task_scope:{surface:scope.surface,chapter_id:scope.surface==='chapter'?writingChapter()?.id:null,chapter_title:scope.surface==='chapter'?writingChapter()?.title:null},write_boundary:'正式方案、章节细纲和正文都必须经过对应 Proposal 或 Gate。'};
};
renderConversationTask=function(contract){
  const execution={user_confirmed:'等待确认',proposal_then_confirm:'先提案后确认',automatic_proposal_only:'仅生成候选',automatic_gate_then_user_freeze:'审查后冻结'}[contract?.execution]||'受阶段约束';
  const scope=contract?.task_scope?.surface==='chapter'?'章内写作':'作品规划';
  return `<section class="director-task-contract"><div><span>当前任务 · ${esc(scope)}${contract?.task_scope?.chapter_title?` · ${esc(contract.task_scope.chapter_title)}`:''}</span><b>${esc(contract?.id||'writing')}</b><small>${esc(contract?.task||'继续当前阶段的讨论；正式变更仍需经过 Proposal 和 Gate。')}</small></div><em>${esc(execution)}</em></section>`;
};
renderConversationAction=function(contract,proposal){
  if(proposal)return '';
  if(contract?.id==='chapter.plan')return '<button class="quiet" type="button" data-organize-conversation>整理章内细纲</button>';
  if(['brief.build','blueprint.generate'].includes(contract?.id))return '<button class="quiet" type="button" data-organize-conversation>形成全作方案</button>';
  return '';
};
renderConversationMessage=function(message){
  const assistant=message.role==='assistant',content=message.content||{},questions=content.questions||[],proposal=message.proposal_id?state.work?.proposals?.find(item=>item.id===message.proposal_id):null,target=proposal?.kind==='chapter_plan'?'structure':'brief';
  return `<article class="conversation-message ${assistant?'assistant':'user'}"><div class="message-role">${assistant?'创作导演':'你'}</div><div class="message-bubble"><p>${esc(messageText(message))}</p>${questions.length?`<ul>${questions.map(item=>`<li>${esc(item)}</li>`).join('')}</ul>`:''}${message.proposal_id?`<button class="message-proposal-link" type="button" data-stage-jump="${target}">查看待审方案</button>`:''}</div></article>`;
};
renderWorkConversationInspector=function(){
  const el=$('#inspectorContent'),thread=workConversationThread(),proposal=activeConversationProposal(),task=conversationTaskContract(thread),chapter=writingChapter(),workSurface=agentTaskScope().surface==='work';
  $$('[data-inspector]').forEach(button=>button.classList.toggle('active',button.dataset.inspector==='agent'));
  if(!thread){el.innerHTML='<div class="inspector-body"><p>当前作品还没有创作主对话。</p></div>';return;}
  const pending=proposal?`<div class="director-pending"><b>${workSurface?'全作故事方案':`《${esc(chapter?.title||'当前章节')}》章内细纲`}等待决定</b><span>正式产物尚未改变。先审查，再决定采纳或退回。</span><div class="director-pending-actions"><button class="primary" type="button" data-accept-director-proposal="${esc(proposal.id)}">采纳</button><button class="quiet" type="button" data-reject-director-proposal="${esc(proposal.id)}">退回继续讨论</button></div></div>`:'';
  el.innerHTML=`<div class="director-panel"><header class="director-header"><div><p class="eyebrow">${workSurface?'WORK DIRECTOR':'CHAPTER DIRECTOR'}</p><div class="agent-title-row"><h3>${workSurface?'全作创作导演':'章内写作助手'}</h3><span class="agent-provider-chip">${state.capabilities?.providers?.[0]?.is_simulation?'本地模拟':'已连接'}</span></div><small>${workSurface?'作品栏目 · 全局方向、人物和世界观':'写作栏目 · '+esc(chapter?.title||'尚未选择章节')} · 对话连续保留</small></div></header>${renderConversationTask(task)}<div class="conversation-scroll" data-conversation-scroll>${conversationHistoryMarkup(thread.messages)||'<p class="conversation-empty">开始一段关于当前作品的讨论。</p>'}</div>${pending}<form id="workConversationForm" class="conversation-composer"><label><span class="sr-only">给 Agent 发送消息</span><textarea name="text" required placeholder="输入消息…"></textarea></label><div class="composer-actions"><div class="composer-tools">${renderPermissionMenu(thread)}${renderConversationAction(task,proposal)}</div><button class="primary" type="submit" title="发送消息">发送</button></div></form></div>`;
  const scroll=$('[data-conversation-scroll]',el);if(scroll)scroll.scrollTop=scroll.scrollHeight;
};
renderMobileAgent=function(el){
  const thread=workConversationThread(),proposal=activeConversationProposal(),task=conversationTaskContract(thread),chapter=writingChapter(),workSurface=agentTaskScope().surface==='work';
  if(!thread){el.innerHTML=frame('CREATIVE DIRECTOR','创作对话','当前作品还没有创作主对话。','<div class="notice">重新打开作品后会自动恢复对话。</div>');return;}
  const pending=proposal?`<div class="director-pending"><b>${workSurface?'全作故事方案':`《${esc(chapter?.title||'当前章节')}》章内细纲`}等待决定</b><span>正式产物尚未改变。</span><div class="director-pending-actions"><button class="primary" type="button" data-accept-director-proposal="${esc(proposal.id)}">采纳</button><button class="quiet" type="button" data-reject-director-proposal="${esc(proposal.id)}">退回继续讨论</button></div></div>`:'';
  el.innerHTML=`<div class="mobile-agent-page"><header class="mobile-agent-head"><div><p class="eyebrow">${workSurface?'WORK DIRECTOR':'CHAPTER DIRECTOR'}</p><div class="agent-title-row"><h2>${workSurface?'全作创作导演':'章内写作助手'}</h2><span class="agent-provider-chip">${state.capabilities?.providers?.[0]?.is_simulation?'本地模拟':'已连接'}</span></div><p>${esc(state.work.title)} · ${workSurface?'作品规划':'写作 · '+(chapter?.title||'未选择章节')}</p></div></header>${renderConversationTask(task)}<div class="mobile-conversation-scroll" data-mobile-conversation-scroll>${conversationHistoryMarkup(thread.messages)||'<p class="conversation-empty">开始一段关于当前作品的讨论。</p>'}</div>${pending}<form id="mobileWorkConversationForm" class="conversation-composer mobile-composer"><label><span class="sr-only">给 Agent 发送消息</span><textarea name="text" required placeholder="输入消息…"></textarea></label><div class="composer-actions"><div class="composer-tools">${renderPermissionMenu(thread)}${renderConversationAction(task,proposal)}</div><button class="primary" type="submit" title="发送消息">发送</button></div></form></div>`;
  const scroll=$('[data-mobile-conversation-scroll]',el);if(scroll)scroll.scrollTop=scroll.scrollHeight;
};
var writingStructureBase=renderStructure;
renderStructure=function(el){
  writingStructureBase(el);
  const inner=$('.workspace-inner',el),chapter=writingChapter();if(!inner)return;
  const target=document.createElement('section');target.className='writing-target-bar';target.innerHTML=`<div><p class="eyebrow">CURRENT WRITING TARGET</p><h3>${chapter?esc(chapter.title):'还没有可写章节'}</h3><p>章内细纲、场景上下文和 Agent 讨论都会绑定这一章。全作方向请回到“作品”。</p></div><label>当前章节<select data-select-writing-chapter>${(state.work?.chapters||[]).map(item=>`<option value="${esc(item.id)}" ${item.id===chapter?.id?'selected':''}>${esc(item.title)}</option>`).join('')}</select></label>`;
  inner.querySelector('.structure-scope-note, .structure-command')?.before(target);
  const planArtifact=(state.work.artifacts||[]).find(item=>item.kind==='chapter_plan'&&item.scope_id===chapter?.id),plan=planArtifact?.current_revision?.content;
  if(plan){const note=document.createElement('section');note.className='chapter-plan-summary';note.innerHTML=`<div><p class="eyebrow">CHAPTER PLAN · 已采纳</p><h3>${esc(plan.title||`${chapter.title}细纲`)}</h3><p>${esc(plan.chapter_goal||'本章目标已保存。')}</p></div><button class="quiet" type="button" data-inspector="agent">继续讨论本章</button>`;inner.querySelector('.structure-scope-note, .structure-command')?.before(note)}
};
var renderSurfaceBase=render;
render=function(){renderSurfaceBase();decorateVolumeTree();decorateTopStatus();if(state.stage==='draft'&&state.sceneId)queueMicrotask(()=>ensureSceneContext(state.sceneId));};

// Product boundary: Works owns the whole-story director; Writing owns one
// persisted chapter at a time. The same thread remains continuous, but every
// turn receives the server-validated scope below.
function writingTarget(){return artifact('writing_target')||{chapter_id:state.writingChapterId||''}}
function writingChapter(){const id=writingTarget().chapter_id||state.writingChapterId;return (state.work?.chapters||[]).find(ch=>ch.id===id)||state.work?.chapters?.find(ch=>ch.status!=='placeholder')||state.work?.chapters?.[0]||null}
function agentTaskScope(){
  const chapter=writingChapter();
  const isWorkSurface=state.surface==='works';
  return isWorkSurface?{surface:'work'}:{surface:'chapter',chapter_id:chapter?.id||null,chapter_title:chapter?.title||null};
}
function chapterPlanProposal(){const chapter=writingChapter();return (state.work?.proposals||[]).find(item=>item.kind==='chapter_plan'&&item.status==='pending'&&(!chapter||item.scope_id===chapter.id))||null}
function activeConversationProposal(){return agentTaskScope().surface==='chapter'?chapterPlanProposal():workPlanProposal()}

renderConversationTask=function(contract){
  const execution={user_confirmed:'等待确认',proposal_then_confirm:'先提案后确认',automatic_proposal_only:'仅生成候选',automatic_gate_then_user_freeze:'审查后冻结'}[contract?.execution]||'受阶段约束';
  const scope=contract?.task_scope?.surface==='chapter'?'章内写作': '作品规划';
  const title=contract?.task_scope?.chapter_title?`${scope} · ${contract.task_scope.chapter_title}`:scope;
  return `<section class="director-task-contract"><div><span>当前任务 · ${esc(title)}</span><b>${esc(contract?.id||'writing')}</b><small>${esc(contract?.task||'继续当前阶段的讨论；正式变更仍需经过 Proposal 和 Gate。')}</small></div><em>${esc(execution)}</em></section>`;
};
renderConversationAction=function(contract,proposal){
  if(proposal)return '';
  if(contract?.id==='chapter.plan')return '<button class="quiet" type="button" data-organize-conversation>整理章内细纲</button>';
  if(['brief.build','blueprint.generate'].includes(contract?.id))return '<button class="quiet" type="button" data-organize-conversation>形成全作方案</button>';
  return '';
};
renderConversationMessage=function(message){
  const assistant=message.role==='assistant',content=message.content||{},questions=content.questions||[],proposal=message.proposal_id?state.work?.proposals?.find(item=>item.id===message.proposal_id):null;
  const target=proposal?.kind==='chapter_plan'?'structure':'brief';
  return `<article class="conversation-message ${assistant?'assistant':'user'}"><div class="message-role">${assistant?'创作导演':'你'}</div><div class="message-bubble"><p>${esc(messageText(message))}</p>${questions.length?`<ul>${questions.map(item=>`<li>${esc(item)}</li>`).join('')}</ul>`:''}${message.proposal_id?`<button class="message-proposal-link" type="button" data-stage-jump="${target}">查看待审方案</button>`:''}</div></article>`;
};

function directorPendingMarkup(proposal){
  if(!proposal)return '';
  const chapter=proposal.kind==='chapter_plan'?writingChapter():null;
  return `<div class="director-pending"><b>${chapter?`《${esc(chapter.title)}》章内细纲候选`:'全作故事方案'}等待决定</b><span>正式产物尚未改变。先审查，再决定采纳或退回。</span><div class="director-pending-actions"><button class="primary" type="button" data-accept-director-proposal="${esc(proposal.id)}">采纳</button><button class="quiet" type="button" data-reject-director-proposal="${esc(proposal.id)}">退回继续讨论</button></div></div>`;
}
renderWorkConversationInspector=function(){
  const el=$('#inspectorContent'),thread=workConversationThread(),proposal=activeConversationProposal(),task=conversationTaskContract(thread);
  $$('[data-inspector]').forEach(button=>button.classList.toggle('active',button.dataset.inspector==='agent'));
  if(!thread){el.innerHTML='<div class="inspector-body"><p>当前作品还没有创作主对话。</p></div>';return;}
  const chapter=writingChapter(),workSurface=agentTaskScope().surface==='work';
  el.innerHTML=`<div class="director-panel"><header class="director-header"><div><p class="eyebrow">${workSurface?'WORK DIRECTOR':'CHAPTER DIRECTOR'}</p><div class="agent-title-row"><h3>${workSurface?'全作创作导演':'章内写作助手'}</h3><span class="agent-provider-chip">${state.capabilities?.providers?.[0]?.is_simulation?'本地模拟':'已连接'}</span></div><small>${workSurface?'作品栏目 · 全局方向、人物和世界观':'写作栏目 · '+esc(chapter?.title||'尚未选择章节')} · 对话连续保留</small></div></header>${renderConversationTask(task)}<div class="conversation-scroll" data-conversation-scroll>${conversationHistoryMarkup(thread.messages)||'<p class="conversation-empty">开始一段关于当前作品的讨论。</p>'}</div>${directorPendingMarkup(proposal)}<form id="workConversationForm" class="conversation-composer"><label><span class="sr-only">给 Agent 发送消息</span><textarea name="text" required placeholder="输入消息…"></textarea></label><div class="composer-actions"><div class="composer-tools">${renderPermissionMenu(thread)}${renderConversationAction(task,proposal)}</div><button class="primary" type="submit" title="发送消息">发送</button></div></form></div>`;
  const scroll=$('[data-conversation-scroll]',el);if(scroll)scroll.scrollTop=scroll.scrollHeight;
};
renderMobileAgent=function(el){
  const thread=workConversationThread(),proposal=activeConversationProposal(),task=conversationTaskContract(thread),chapter=writingChapter();
  if(!thread){el.innerHTML=frame('CREATIVE DIRECTOR','创作对话','当前作品还没有创作主对话。','<div class="notice">重新打开作品后会自动恢复对话。</div>');return;}
  const workSurface=agentTaskScope().surface==='work';
  el.innerHTML=`<div class="mobile-agent-page"><header class="mobile-agent-head"><div><p class="eyebrow">${workSurface?'WORK DIRECTOR':'CHAPTER DIRECTOR'}</p><div class="agent-title-row"><h2>${workSurface?'全作创作导演':'章内写作助手'}</h2><span class="agent-provider-chip">${state.capabilities?.providers?.[0]?.is_simulation?'本地模拟':'已连接'}</span></div><p>${esc(state.work.title)} · ${workSurface?'作品规划':'写作 · '+(chapter?.title||'未选择章节')}</p></div></header>${renderConversationTask(task)}<div class="mobile-conversation-scroll" data-mobile-conversation-scroll>${conversationHistoryMarkup(thread.messages)||'<p class="conversation-empty">开始一段关于当前作品的讨论。</p>'}</div>${directorPendingMarkup(proposal)}<form id="mobileWorkConversationForm" class="conversation-composer mobile-composer"><label><span class="sr-only">给 Agent 发送消息</span><textarea name="text" required placeholder="输入消息…"></textarea></label><div class="composer-actions"><div class="composer-tools">${renderPermissionMenu(thread)}${renderConversationAction(task,proposal)}</div><button class="primary" type="submit" title="发送消息">发送</button></div></form></div>`;
  const scroll=$('[data-mobile-conversation-scroll]',el);if(scroll)scroll.scrollTop=scroll.scrollHeight;
};

const renderStructureBeforeWritingTarget=renderStructure;
renderStructure=function(el){
  renderStructureBeforeWritingTarget(el);
  const inner=$('.workspace-inner',el),chapter=writingChapter();
  if(!inner)return;
  const plan=chapter?artifact('chapter_plan')&&((state.work.artifacts||[]).find(item=>item.kind==='chapter_plan'&&item.scope_id===chapter.id)?.current_revision?.content):null;
  const target=document.createElement('section');target.className='writing-target-bar';target.innerHTML=`<div><p class="eyebrow">CURRENT WRITING TARGET</p><h3>${chapter?`第 ${esc(chapter.title)}`:'还没有可写章节'}</h3><p>后续章内细纲、场景上下文和 Agent 讨论都会绑定这一章。全作方向请回到“作品”。</p></div><label>当前章节<select data-select-writing-chapter>${(state.work?.chapters||[]).map(item=>`<option value="${esc(item.id)}" ${item.id===chapter?.id?'selected':''}>${esc(item.title)}</option>`).join('')}</select></label>`;
  inner.querySelector('.structure-command')?.before(target);
  if(plan){const note=document.createElement('section');note.className='chapter-plan-summary';note.innerHTML=`<div><p class="eyebrow">CHAPTER PLAN · 已采纳</p><h3>${esc(plan.title||`${chapter.title}细纲`)}</h3><p>${esc(plan.chapter_goal||'本章目标已保存。')}</p></div><button class="quiet" type="button" data-inspector="agent">继续讨论本章</button>`;inner.querySelector('.structure-command')?.before(note)}
};

document.addEventListener('change',event=>{
  const select=event.target.closest('select[data-select-writing-chapter]');
  if(!select||!state.work)return;
  event.preventDefault();event.stopImmediatePropagation();
  (async()=>{try{const result=await api(`/works/${state.work.id}/writing-target`,{method:'POST',body:JSON.stringify({expected_version:state.work.version,chapter_id:select.value})});state.work=result.work;state.writingChapterId=select.value;state.sceneId=state.work.chapters.find(ch=>ch.id===select.value)?.scenes?.[0]?.id||state.sceneId;state.context=null;toast('当前写作章节已保存');render()}catch(error){toast(error.message,true);render()}})();
},true);
document.addEventListener('click',event=>{
  const button=event.target.closest('button[data-writing-chapter]');
  if(!button||!state.work)return;
  event.preventDefault();event.stopImmediatePropagation();
  const gate=stageGate('structure');if(!gate.allowed){toast(`尚未解锁「章节细纲」：${gate.reason}`,true);return;}
  (async()=>{try{const result=await api(`/works/${state.work.id}/writing-target`,{method:'POST',body:JSON.stringify({expected_version:state.work.version,chapter_id:button.dataset.writingChapter})});state.work=result.work;state.writingChapterId=button.dataset.writingChapter;state.stage='structure';state.mobileView='writing';toast('已切换到本轮写作章节');render()}catch(error){toast(error.message,true)}})();
},true);

document.addEventListener('click',event=>{
  const accept=event.target.closest('[data-accept-director-proposal]'),reject=event.target.closest('[data-reject-director-proposal]');
  if(!accept&&!reject)return;
  event.preventDefault();event.stopImmediatePropagation();
  const proposalId=(accept||reject).dataset.acceptDirectorProposal||(accept||reject).dataset.rejectDirectorProposal;
  (async()=>{try{const result=await api(`/works/${state.work.id}/proposals/${proposalId}/${accept?'accept':'reject'}`,{method:'POST',body:JSON.stringify({expected_version:state.work.version,note:accept?'在当前工作面审查后采纳':'退回当前 Agent 继续讨论'})});state.work=result.work;toast(accept?'候选已采纳为正式修订':'候选已退回，讨论仍保留');render()}catch(error){toast(error.message,true)}})();
},true);

// Context assembly is automatic when a scene becomes the active writing task.
function ensureSceneContext(sceneId){
  if(!sceneId||!state.work||state._contextLoadingScene===sceneId||state.context?.scene_id===sceneId)return;
  state._contextLoadingScene=sceneId;state._contextError='';setBusy('正在准备本场上下文');
  api(`/works/${state.work.id}/scenes/${sceneId}/context:assemble`,{method:'POST',body:'{}'}).then(context=>{if(state.sceneId===sceneId){state.context=context;setBusy('本场上下文已准备');render()}}).catch(error=>{if(state.sceneId===sceneId){state._contextError=error.message;setBusy('本场上下文准备失败');render()}}).finally(()=>{if(state._contextLoadingScene===sceneId)state._contextLoadingScene='';});
}
const renderAfterScope=render;
render=function(){renderAfterScope();if(state.stage==='draft'&&state.sceneId)queueMicrotask(()=>ensureSceneContext(state.sceneId));};

// Mobile uses one full task surface at a time. The desktop inspector remains
// available in the DOM, but the conversation below has distinct form IDs so
// the two layouts never compete for focus or submission.
function renderMobileConversationMessage(message){
  const assistant=message.role==='assistant',content=message.content||{},questions=content.questions||[];
  return `<article class="conversation-message ${assistant?'assistant':'user'}"><div class="message-role">${assistant?'创作导演':'你'}</div><div class="message-bubble"><p>${esc(messageText(message))}</p>${questions.length?`<ul>${questions.map(item=>`<li>${esc(item)}</li>`).join('')}</ul>`:''}${content.simulation_notice?`<small>${esc(content.simulation_notice)}</small>`:''}${message.proposal_id?'<button class="message-proposal-link" type="button" data-stage-jump="brief">查看待审方案</button>':''}</div></article>`;
}

function renderPermissionMenu(thread){
  const managed=thread.permission_mode==='managed';
  return `<details class="permission-menu ${managed?'managed':''}"><summary title="设置 Agent 权限"><span class="permission-mark" aria-hidden="true">!</span><span>${managed?'托管创作':'审核协作'}</span></summary><div class="permission-popover" role="menu" aria-label="Agent 权限"><header><b>Agent 权限</b><small>决定正式修改如何落地</small></header><button type="button" role="menuitemradio" aria-checked="${managed?'false':'true'}" class="${managed?'':'active'}" data-permission-mode="review"><span class="permission-option-mark" aria-hidden="true">!</span><span><b>审核协作</b><small>所有正式修改先进入 Proposal/Diff，由你决定是否采纳</small></span><i aria-hidden="true">&#10003;</i></button><button type="button" role="menuitemradio" aria-checked="${managed?'true':'false'}" class="${managed?'active':''}" data-permission-mode="managed"><span class="permission-option-mark" aria-hidden="true">A</span><span><b>托管创作</b><small>只在限定范围内自动采纳普通、低风险的写作修改</small></span><i aria-hidden="true">&#10003;</i></button><p>核心设定、大规模删除、冻结发布和 AA 交接始终需要确认。</p></div></details>`;
}

function renderMobileAgent(el){
  const thread=workConversationThread(),proposal=workPlanProposal();
  if(!thread){el.innerHTML=frame('CREATIVE DIRECTOR','创作导演','当前作品还没有创作主对话。','<div class="notice">旧作品需要重新打开一次，系统会补建主对话。</div>');return}
  const discuss=thread.phase==='discuss';
  el.innerHTML=`<div class="mobile-agent-page"><header class="mobile-agent-head"><div><p class="eyebrow">CREATIVE DIRECTOR</p><h2>全作 · 创作主对话</h2><p>${esc(state.work.title)} · 对话 v${thread.version}</p></div></header><div class="director-modes" role="group" aria-label="创作导演状态"><button type="button" data-thread-phase="discuss" class="${discuss?'active':''}">讨论创作</button><button type="button" data-thread-phase="execute" class="${!discuss?'active':''}">执行修改</button></div><div class="mobile-conversation-scroll" data-mobile-conversation-scroll>${thread.messages.map(renderMobileConversationMessage).join('')||'<p class="conversation-empty">先说一句你想看的故事。</p>'}</div>${proposal?'<div class="director-pending"><b>故事方案等待决定</b><span>正式 Brief 和故事方向尚未改变。</span><button type="button" data-stage-jump="brief">查看方案</button></div>':''}<form id="mobileWorkConversationForm" class="conversation-composer mobile-composer"><label><span class="sr-only">给创作导演发送消息</span><textarea name="text" required placeholder="补充、反悔、比较方向，或直接说明哪里不对……"></textarea></label><div class="composer-actions"><div class="composer-tools">${renderPermissionMenu(thread)}<button class="quiet" type="button" data-organize-conversation ${proposal?'disabled':''}>整理为方案</button></div><button class="primary" type="submit" title="发送消息">发送</button></div></form></div>`;
  const scroll=$('[data-mobile-conversation-scroll]',el);if(scroll)scroll.scrollTop=scroll.scrollHeight;
}

const renderWorkspaceBeforeMobileAgent=renderWorkspace;
renderWorkspace=function(){
  if(state.work&&window.matchMedia('(max-width: 640px)').matches&&state.mobileView==='agent'){
    const el=$('#workspace');renderMobileAgent(el);el.scrollTop=0;return;
  }
  return renderWorkspaceBeforeMobileAgent();
};

mobileLabel=function(view){return({works:'作品结构',agent:'创作导演',context:'上下文',tasks:'任务'})[view]||'写作'};

document.addEventListener('click',event=>{
  const button=event.target.closest('button[data-inspector="agent"]');
  if(!button||!window.matchMedia('(max-width: 640px)').matches)return;
  event.preventDefault();event.stopImmediatePropagation();state.mobileView='agent';render();
},true);

document.addEventListener('submit',async event=>{
  if(event.target.id!=='mobileWorkConversationForm')return;
  event.preventDefault();event.stopImmediatePropagation();
  const thread=workConversationThread(),fields=new FormData(event.target);
  try{
    setBusy('创作导演正在回应');
    const result=await api(`/works/${state.work.id}/threads/${thread.id}/messages`,{method:'POST',body:JSON.stringify({expected_thread_version:thread.version,text:fields.get('text'),attachment_ids:state.composerAttachmentIds||[],task_scope:agentTaskScope()})});
    state.work=result.work;state.composerAttachmentIds=[];setBusy('对话已保存');toast(result.simulation?'模拟回应已保存，可继续讨论':'回应已保存');render();
  }catch(error){setBusy('对话发送失败');toast(error.message,true)}
},true);

// The overview is a projection of the same Work/Volume/Chapter/Scene data,
// not a second copy of the outline. It keeps the current writing scope visible
// and gives the user one decision without hiding the underlying structure.
function renderOverviewV3(el){
  const work=state.work,volumes=work.volumes||[],sceneList=scenes(),total=sceneList.length,drafted=sceneList.filter(scene=>scene.current_revision_id).length;
  const planProposal=workPlanProposal(),savedBrief=brief(),formal=blueprintIsConfirmed(),pending=work.proposals.filter(item=>item.status==='pending').length;
  const blocker=(work.review_findings||[]).filter(item=>item.status==='open'&&item.severity==='blocking').length;
  const activeScene=selectedScene(),activeChapter=(work.chapters||[]).find(chapter=>chapter.scenes.some(scene=>scene.id===activeScene?.id))||(work.chapters||[])[0];
  const activeVolume=volumes.find(volume=>volume.chapters.some(chapter=>chapter.id===activeChapter?.id))||volumes[0];
  let next={stage:'brief',title:'继续和创作导演讨论',detail:'可以补充、反悔或比较方向；聊清楚后再把共识整理成正式方案。',label:'查看讨论与方案',agent:true};
  if(planProposal)next={stage:'brief',title:'审查刚整理的故事方案',detail:'方案仍是 Proposal。采纳后才会建立正式 Brief 与 StoryBlueprint 修订。',label:'审查方案'};
  else if(savedBrief&&!formal)next={stage:'brief',title:'继续确认故事方向',detail:'当前想法已经保存，但整体故事方向仍需确认。',label:'查看故事方向'};
  else if(formal&&!total)next={stage:'structure',title:'规划第一章的第一场',detail:'第一卷和第一章已经存在。现在只需要说明第一场发生什么变化。',label:'安排第一个场景'};
  else if(pending)next={stage:'draft',title:'先处理待决定的候选',detail:`有 ${pending} 份 Proposal 等待采纳、局部修改或退回。`,label:'查看候选'};
  else if(blocker)next={stage:'draft',title:'处理审查阻塞项',detail:`有 ${blocker} 个阻塞项需要处理，完成后才可冻结版本。`,label:'处理审查'};
  else if(total&&drafted<total)next={stage:'draft',title:'继续下一场写作',detail:`还有 ${total-drafted} 个场景没有已采纳正文。Agent 结果会先进入 Diff 审查。`,label:'打开逐场写作'};
  else if(total)next={stage:'release',title:'运行全篇审查',detail:'确认连续性、人物约束和正文修订后，再冻结交给制作的定稿。',label:'检查并发布'};
  const progress=total?Math.round(drafted/total*100):0,cards=libraryCards(),worldEntities=(worldBible().entities||[]).filter(item=>item.status!=='archived');
  const scope=[activeVolume?.title,activeChapter?.title,activeScene?.title].filter(Boolean);
  const volumeMarkup=volumes.map((volume,volumeIndex)=>`<section class="overview-volume"><header><span>卷 ${String(volumeIndex+1).padStart(2,'0')}</span><b>${esc(volume.title)}</b><small>${volume.chapters.length} 章</small></header>${volume.chapters.map(chapter=>`<button type="button" class="overview-chapter-line" data-writing-chapter="${esc(chapter.id)}"><span>${esc(chapter.title)}</span><b>${chapter.scenes.length} 场</b><small>${chapter.scenes.filter(scene=>scene.current_revision_id).length} 场已有正文</small></button>`).join('')}</section>`).join('');
  el.innerHTML=`<div class="overview-workbench overview-v3"><header class="overview-header"><div><p class="eyebrow">WORK OVERVIEW</p><h2>${esc(work.title)}</h2><p>${scope.length?`当前范围：${scope.map(esc).join(' / ')}`:'作品骨架已经建立，尚未选择场景。'}</p></div><button class="quiet" data-stage-jump="references">管理资料库</button></header><section class="overview-next overview-next-calm"><div><p class="eyebrow">RECOMMENDED DECISION</p><h3>${next.title}</h3><p>${next.detail}</p></div><button class="primary" ${next.agent?'data-focus-discussion':`data-stage-jump="${next.stage}"`}>${next.agent?'进入全作讨论':next.label}</button></section><div class="overview-progress-line"><b>正文进度 ${progress}%</b><span>${drafted} / ${total} 个场景已有正式正文</span>${pending||blocker?`<button class="text-link ${blocker?'has-attention':''}" data-stage-jump="draft">${pending+blocker} 项等待决定</button>`:''}</div><section class="overview-foundation-strip"><button data-stage-jump="references" data-library-target="characters"><span>人物卡</span><b>${cards.length} 张</b><small>${cards.filter(card=>card.source_type==='custom').length} 张自定义</small></button><button data-stage-jump="references" data-library-target="world"><span>世界设定</span><b>${worldEntities.length} 项</b><small>${worldEntities.filter(item=>item.confidence_status==='confirmed').length} 项已确认</small></button><div><span>创作对话</span><b>${workConversationThread()?.messages?.length||0} 条</b><small>${planProposal?'有方案等待决定':'讨论与正式产物分开保存'}</small></div></section><section class="overview-structure-v3"><div class="overview-section-head"><div><p class="eyebrow">STORY BINDER</p><h3>卷、章与场景</h3></div><button class="quiet" data-stage-jump="structure">管理结构</button></div>${volumeMarkup||'<p class="overview-empty">尚未建立卷结构。</p>'}</section></div>`;
}

// Keep the guided path visible in the stage list, but do not repeat the same
// instruction in a second, competing side-panel card.
function renderWorkflowGuide(){
  const guide=$('#workflowGuide');
  if(guide)guide.replaceChildren();
  if(!state.work)return;
  const progress=workflowProgress();
  const nextStage=FLOW_STAGES.find(stage=>!progress.done[stage])||'release';
  $$('[data-stage]').forEach(button=>{
    const stage=button.dataset.stage;
    const gate=stageGate(stage);
    const small=button.querySelector('small');
    const complete=Boolean(progress.done[stage]);
    const current=stage===state.stage;
    const next=stage===nextStage&&!complete;
    button.disabled=!gate.allowed;
    button.classList.toggle('is-complete',complete);
    button.classList.toggle('is-current',current);
    button.classList.toggle('is-next',next&&!current);
    button.setAttribute('aria-current',current?'step':'false');
    button.setAttribute('aria-disabled',String(!gate.allowed));
    button.title=gate.allowed?(complete?'已完成，可随时查看':'可进入此阶段'):gate.reason;
    if(small){
      small.textContent=complete?'已完成':current?'正在进行':gate.allowed?'可随时查看':'完成前一步后可继续';
    }
  });
  const production=$('[data-section="production"]');
  if(production){
    const releaseGate=stageGate('release');
    production.classList.toggle('locked-nav',!releaseGate.allowed);
    production.title=releaseGate.allowed?'打开检查与发布':'检查与发布将在完成逐场写作后开放';
  }
}

function renderBrief(el){
  const b=brief()||{};
  const isSaved=Boolean(brief());
  el.innerHTML=frame(
    '第 1 步 / 5',
    '先把故事开头说清楚',
    '这张写作想法只记录你此刻的创作意图。人物卡、世界观和正文仍在各自的资料与写作页面管理。',
    `<section class="brief-clarity-band">
      <div><p class="eyebrow">THIS STEP</p><h3>先回答三个问题，其他设定以后再补。</h3><p>故事要写什么、用什么写法、谁是主要角色。保存后才会解锁故事方向。</p></div>
      <span class="brief-step-state ${isSaved?'is-saved':''}">${isSaved?'已保存，可继续':'等待填写'}</span>
    </section>
    <form id="briefForm" class="brief-form">
      <label class="brief-idea">一句想法<textarea name="idea" required placeholder="例如：凯伊发现游戏开发部的旧机器在深夜自行启动">${esc(b.idea)}</textarea><small>用一句话说清这部作品最想发生什么。</small></label>
      <div class="brief-core-grid">
        <label>写作模式<select name="mode"><option value="bond_short" ${b.mode==='bond_short'?'selected':''}>羁绊短场景</option><option value="main_battle" ${b.mode==='main_battle'?'selected':''}>主线与战斗</option><option value="long_comedy" ${b.mode==='long_comedy'?'selected':''}>长篇喜剧</option><option value="text_reading" ${b.mode==='text_reading'?'selected':''}>小说化阅读</option></select></label>
        <label>主要角色<input name="characters" value="${esc((b.characters||[]).join('、'))}" placeholder="爱丽丝、凯伊"><small>用顿号分隔；之后可到人物库完善卡片。</small></label>
      </div>
      <details class="brief-optional" ${b.target_length||b.constraints||b.has_sensei?'open':''}>
        <summary>补充设定（可选）</summary>
        <div class="brief-optional-fields">
          <label>目标长度<select name="target_length"><option value="short" ${b.target_length==='short'?'selected':''}>短场景</option><option value="chapter" ${b.target_length==='chapter'?'selected':''}>单章</option><option value="long" ${b.target_length==='long'?'selected':''}>长篇</option></select></label>
          <label class="brief-constraint">额外约束<textarea name="constraints" placeholder="不可提前揭示的事实、希望保留的关系距离……">${esc(b.constraints)}</textarea></label>
          <label class="check brief-check"><span><input type="checkbox" name="has_sensei" ${b.has_sensei?'checked':''}> 老师在场</span></label>
        </div>
      </details>
      <div class="brief-actions">
        <div><b>${isSaved?'修改会新建一份写作想法修订':'保存后不会自动生成正文或改写资料库'}</b><small>${isSaved?'故事方向、章节和场景会继续引用这份简报。':'你仍可随时回到这里修改。'}</small></div>
        <div class="actions"><button class="primary" type="submit">${isSaved?'保存修改':'保存写作想法'}</button>${isSaved?'<button class="quiet" type="button" data-stage-jump="blueprint">下一步：确认故事方向</button>':''}</div>
      </div>
    </form>`
  );
}

function renderOverview(el){
  const work=state.work;
  const sceneList=scenes();
  const total=sceneList.length;
  const drafted=sceneList.filter(scene=>scene.current_revision_id).length;
  const cards=libraryCards();
  const world=worldBible();
  const worldEntities=(world.entities||[]).filter(item=>item.status!=='archived');
  const pending=work.proposals.filter(item=>item.status==='pending').length;
  const blocker=(work.review_findings||[]).filter(item=>item.status==='open'&&item.severity==='blocking').length;
  let next={stage:'brief',title:'先提供一句创作想法',detail:'只要说出想看什么；系统会在下一步提出角色、世界观依据和写作组成候选。',label:'开始写作想法'};
  if(brief()&&!blueprintIsConfirmed())next={stage:'blueprint',title:'审查故事方向候选',detail:'系统先提出角色、写作组成与世界观依据；确认后才会建立章节。',label:'审查故事方向'};
  else if(blueprintIsConfirmed()&&!work.chapters.length)next={stage:'structure',title:'建立章节与场景',detail:'先建立第一章，再把故事拆成有稳定身份的场景。',label:'建立章节与场景'};
  else if(pending)next={stage:'draft',title:'先审查待处理候选',detail:`有 ${pending} 份候选等待你采纳、局部修改或退回。`,label:'查看候选'};
  else if(blocker)next={stage:'draft',title:'处理审查阻塞项',detail:`有 ${blocker} 个阻塞项。处理完成后才可以冻结发布版本。`,label:'处理审查'};
  else if(total&&drafted<total)next={stage:'draft',title:'开始下一场写作',detail:`还有 ${total-drafted} 个场景没有已采纳正文，生成结果会先进入候选审查。`,label:'打开逐场写作'};
  else if(total)next={stage:'release',title:'运行全篇审查',detail:'确认连续性、人物约束和正文修订后，再冻结交给制作的定稿。',label:'检查并发布'};
  const progress=total?Math.round(drafted/total*100):0;
  el.innerHTML=`<div class="overview-workbench overview-workbench-calm">
    <header class="overview-header"><div><p class="eyebrow">WORK OVERVIEW</p><h2>${esc(work.title)}</h2><p>从这里看清作品当前走到哪里，以及接下来只需要做哪一个决定。</p></div><button class="quiet" data-stage-jump="references">管理资料库</button></header>
    <section class="overview-next overview-next-calm"><div><p class="eyebrow">NEXT STEP</p><h3>${next.title}</h3><p>${next.detail}</p></div><button class="primary" data-stage-jump="${next.stage}">${next.label}</button></section>
    <section class="overview-signal-strip" aria-label="作品概况">
      <div class="overview-signal"><b>${progress}%</b><span>正文进度</span><small>${drafted}/${total||0} 个场景已采纳</small></div>
      <button class="overview-signal overview-signal-link" data-stage-jump="references" data-library-target="characters"><b>${cards.length}</b><span>人物卡</span><small>${cards.filter(card=>card.source_type==='custom').length} 张自定义</small></button>
      <button class="overview-signal overview-signal-link" data-stage-jump="references" data-library-target="world"><b>${worldEntities.length}</b><span>世界观卡</span><small>${worldEntities.filter(item=>item.confidence_status==='confirmed').length} 张已确认</small></button>
      <button class="overview-signal overview-signal-link ${pending||blocker?'has-attention':''}" data-stage-jump="draft"><b>${pending+blocker}</b><span>等待决定</span><small>${blocker?'有审查阻塞项':'候选与审查事项'}</small></button>
    </section>
    <section class="overview-columns overview-columns-calm"><div><div class="overview-section-head"><h3>作品结构</h3><button class="quiet" data-stage-jump="structure">管理章节</button></div>${work.chapters.length?work.chapters.map(ch=>`<div class="overview-chapter"><b>${esc(ch.title)}</b><span>${ch.scenes.length} 个场景</span><small>${ch.scenes.filter(scene=>scene.current_revision_id).length} 已完成</small></div>`).join(''):'<p class="overview-empty">尚未建立章节。完成故事方向后，再把它拆成第一章和场景。</p>'}</div><div><div class="overview-section-head"><h3>写作基础</h3><span class="status-chip ${pending||blocker?'amber':''}">${pending||blocker?'等待你的决定':'暂无待处理项'}</span></div><ul class="overview-checks"><li><i class="check-dot"></i>作品与修订已保存到本地</li><li><i class="check-dot ${cards.length?'':'muted'}"></i>${cards.length?'人物卡已登记':'尚未建立人物卡'}</li><li><i class="check-dot ${worldEntities.length?'':'muted'}"></i>${worldEntities.length?'世界观卡已登记':'尚未建立 BA 或自定义世界观卡'}</li></ul></div></section>
  </div>`;
}

function stageDecisionModel(){
  const scene=selectedScene();
  const proposal=pendingProposal();
  const latest=state.work?.releases?.[0];
  const definitions={
    overview:{kicker:'WORKFLOW',title:'按推荐下一步推进即可',body:'作品总览只保留一个推荐行动，其他入口仍在左侧导航。',impact:'不会自动生成、修改或发布任何内容。'},
    brief:{kicker:'STEP 1',title:brief()?'写作想法已保存':'先建立写作想法',body:brief()?'可以修改这份创作意图，或继续确认故事方向。':'填写一句想法、写作模式和主要角色；其他设定可以稍后补充。',impact:'保存后会建立 Brief 修订，并解锁故事方向。'},
    blueprint:{kicker:'STEP 2',title:blueprint()?'检查故事方向':'先生成故事方向',body:blueprint()?'确认故事范围、冲突与收束方式，再开始建立章节。':'这一步只生成结构化方向，不会改动正文。',impact:'生成结果会保存为独立 StoryBlueprint。'},
    structure:{kicker:'STEP 3',title:'建立稳定的章节与场景',body:'场景会拥有稳定 ID；改标题或调整顺序不会丢失正文和资料引用。',impact:'保存结构会使旧的全篇审查失效，需要重新检查。'},
    draft:{kicker:'STEP 4',title:proposal?'审查本场候选':scene?`推进「${scene.title}」`:'先建立一个场景',body:proposal?'候选可以局部修改；采纳前不会进入正文。':scene?'先装配上下文，再生成候选或检查已有正文。':'回到章节安排，先建立场景。',impact:proposal?'采纳时才会建立新的正文修订。':'Agent 只能提交候选，不能静默修改正文或资料。'},
    references:{kicker:'WORK LIBRARY',title:'确认可进入 Agent 的资料',body:'人物、世界观、事实与来源证据分别管理。待核对条目不会自动作为写作事实。',impact:'只有确认采用的资料会出现在下一场可选择的上下文中。'},
    release:{kicker:'STEP 5',title:latest?'确认交给制作的定稿':'先完成全篇审查',body:latest?'冻结版本不会随正文修改而改变；新稿需要创建新的发布版本。':'所有场景都有已采纳正文后，才能运行全篇审查与冻结。',impact:latest?'提交制作只交付固定的 ScriptRelease。':'全篇审查通过前，发布操作保持锁定。'}
  };
  return definitions[state.stage]||definitions.overview;
}

function renderInspector(){
  const el=$('#inspectorContent');
  if(!el)return;
  const scene=selectedScene();
  const proposal=pendingProposal();
  $$('[data-inspector]').forEach(button=>button.classList.toggle('active',button.dataset.inspector===state.inspector));
  if(!state.work){el.innerHTML='';return;}
  if(state.inspector==='decision'){
    const decision=stageDecisionModel();
    el.innerHTML=`<div class="inspector-body inspector-decision"><p class="eyebrow">${decision.kicker}</p><h3>${esc(decision.title)}</h3><p class="inspector-copy">${esc(decision.body)}</p><section class="inspector-impact"><span>保存或确认后</span><b>${esc(decision.impact)}</b></section><ul class="inspector-checklist"><li><i class="status-dot"></i>作品与版本已持久化</li><li><i class="status-dot ${proposal?'amber':''}"></i>${proposal?'当前有候选等待审查':'没有会被静默写入的内容'}</li><li><i class="status-dot"></i>当前操作由中央工作区完成</li></ul></div>`;
    return;
  }
  if(state.inspector==='context'){
    const c=state.context;
    el.innerHTML=`<div class="inspector-body"><p class="eyebrow">SCENE CONTEXT</p><h3>当前作用域</h3><p>${scene?`${esc(scene.chapterTitle)} / ${esc(scene.title)}`:'尚未选择场景'}</p><ul class="context-list">${c?`<li>规则包<br><b>${esc(c.rules.pack_version)}</b></li><li>单一模式<br><b>${esc(c.rules.mode)}</b></li><li>固定输入修订<br><b>${c.source_revision_ids.length} 个</b></li><li>真实 BA 写作<br><b>${esc(c.readiness.real_ba_writing)}</b></li>`:'<li>进入“逐场写作”并装配上下文后，这里会列出实际读取的版本。</li>'}</ul></div>`;
    return;
  }
  if(state.stage!=='draft'){
    el.innerHTML=`<div class="inspector-body"><p class="eyebrow">CREATIVE DIRECTOR</p><h3>Agent 在逐场写作时才出现</h3><p>先在中央工作区完成当前阶段。Agent 只依附一个场景和明确任务，不会取代作品结构或资料库。</p><button class="quiet" type="button" data-stage-jump="draft" ${stageGate('draft').allowed?'':'disabled'}>打开逐场写作</button></div>`;
    return;
  }
  const existing=scene?.current_revision_id;
  const latestRun=(state.work?.agent_runs||[]).find(run=>run.scope_id===scene?.id);
  el.innerHTML=`<div class="inspector-body"><p class="eyebrow">CREATIVE DIRECTOR</p><h3>只为当前场景提供候选</h3><p>它读取固定场景合同、BA 规则和已确认的人物卡；结果必须先以候选形式交给你审查。</p>${latestRun?`<section class="agent-run"><b>${esc(latestRun.status)}</b><p>工具记录 ${latestRun.tool_calls.length} 项${latestRun.proposal_id?` · Proposal ${esc(latestRun.proposal_id)}`:''}</p></section>`:''}<form id="agentRunForm"><label>本场指令<textarea name="instruction" placeholder="例如：以爱丽丝先观察、凯伊后补充的节奏起草本场" ${scene&&!existing?'':'disabled'}></textarea></label><button class="primary" type="submit" ${scene&&!existing&&!proposal?'':'disabled'}>运行 BA 场景 Agent</button></form><p class="form-note">${existing?'当前已有正文：受控复写会以新候选返回。':'当前使用明确标注的 Fake Provider；真实模型尚未接入。'}</p></div>`;
}
document.addEventListener('click',event=>{
  const button=event.target.closest('button[data-library-target]');
  if(button)state.libraryView=button.dataset.libraryTarget;
},true);
const $=(q,root=document)=>root.querySelector(q);const $$=(q,root=document)=>[...root.querySelectorAll(q)];
const esc=v=>String(v??'').replace(/[&<>'"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
async function api(path,options={}){const response=await fetch('/api/v1'+path,{...options,headers:{'Content-Type':'application/json',...(options.headers||{})}});const result=await response.json();if(!response.ok||result.ok===false){const error=new Error(result.error?.message||'请求失败');error.code=result.error?.code;error.details=result.error?.details||{};error.status=response.status;throw error}return result.data??result}
async function officialReferenceSearch(query){return api(`/official-references/search?q=${encodeURIComponent(query)}&limit=18`)}
function toast(message,bad=false){const el=$('#toast');el.textContent=message;el.style.background=bad?'#8e382e':'';el.classList.add('show');clearTimeout(toast.timer);toast.timer=setTimeout(()=>el.classList.remove('show'),2600)}
function dismissToast(){const el=$('#toast');if(!el)return;clearTimeout(toast.timer);el.textContent='';el.classList.remove('show');el.style.background=''}
function setBusy(message){const el=$('#saveStatus');if(!el)return;el.textContent=message;el.dataset.state=/失败|未保存|未完成/.test(message)?'error':/正在|联系/.test(message)?'busy':'saved'}
function brief(){return state.work?.artifacts.find(x=>x.kind==='brief')?.current_revision?.content}
function blueprint(){return state.work?.artifacts.find(x=>x.kind==='story_blueprint')?.current_revision?.content}
function scenes(){return (state.work?.chapters||[]).flatMap(ch=>ch.scenes.map(s=>({...s,chapterTitle:ch.title})))}
function selectedScene(){return scenes().find(s=>s.id===state.sceneId)||scenes()[0]}
function pendingProposal(){const scene=selectedScene();return state.work?.proposals.find(p=>p.scope_id===scene?.id&&p.status==='pending')}
function recommendedStage(){if(state.work?.releases?.length)return'release';if(scenes().some(s=>s.current_revision_id))return'draft';if(scenes().length||state.work?.chapters?.length)return'structure';if(blueprint())return'blueprint';return'brief'}
async function loadWork(id,{resume=true}={}){state.work=await api('/works/'+id);const target=artifact('writing_target')||{};state.writingChapterId=target.chapter_id||state.writingChapterId||state.work.chapters?.find(ch=>ch.status!=='placeholder')?.id||state.work.chapters?.[0]?.id||'';if(!state.sceneId)state.sceneId=scenes()[0]?.id||null;if(resume){state.stage='overview';state.surface='works'}render()}
async function boot(){try{const caps=await api('/capabilities');state.capabilities=caps;const provider=caps.providers?.[0];$('#providerBadge').textContent=provider?.is_simulation?'本地模拟 · 未调用模型':`${provider?.display_name||'模型'} · 已配置`;state.works=await api('/works');if(state.works[0])await loadWork(state.works[0].id);else render()}catch(error){render();toast(error.message,true)}}
function isCompactViewport(){return window.matchMedia('(max-width: 640px)').matches}
let previousCompactViewport=isCompactViewport();
window.addEventListener('resize',()=>{
  const compact=isCompactViewport();
  if(compact===previousCompactViewport)return;
  previousCompactViewport=compact;
  if(['agent','context'].includes(state.mobileView))render();
});
function render(){
  // Phone-only views must not survive a resize into the desktop workbench.
  if(!isCompactViewport()&&['agent','context'].includes(state.mobileView))state.mobileView='writing';
  const app=$('#app');
  app?.classList.toggle('overview-stage',Boolean(state.work&&state.mobileView==='writing'&&state.stage==='overview'));
  renderChrome();renderWorkspace();decorateLibrary();decorateSceneContext();renderInspector()
}
function renderChrome(){const work=state.work;$('#workTitle').textContent=work?.title||'尚未建立作品';$('#crumb').textContent=work?`${work.title} / ${state.mobileView==='writing'?stageLabel(state.stage):mobileLabel(state.mobileView)}`:'HaloCue / 写作工作台';$('#versionStatus').textContent=`作品版本 ${work?.version??'-'}`;const items=work?.runs?.flatMap(r=>r.work_items)||[];$('#taskStatus').textContent=`后台任务 ${items.filter(x=>['running','ready','waiting_user'].includes(x.status)).length}`;$('#saveStatus').textContent=work?'已保存到本地':'等待建立作品';$$('[data-stage]').forEach(b=>b.classList.toggle('active',b.dataset.stage===state.stage));const primarySection=state.mobileView==='tasks'?'tasks':state.stage==='references'?'works':(['overview','brief','blueprint'].includes(state.stage)?'works':'writing');$$('[data-section]').forEach(b=>b.classList.toggle('active',b.dataset.section===primarySection));const mobileActive=state.stage==='references'&&state.mobileView==='writing'?'works':state.mobileView;$$('[data-mobile]').forEach(b=>b.classList.toggle('active',b.dataset.mobile===mobileActive));const tree=$('#sceneTree');const treeChapters=state.stage==='structure'?(work?.chapters||[]):(work?.chapters||[]).filter(chapter=>chapter.scenes.length);tree.innerHTML=treeChapters.map(ch=>`<p class="tree-chapter">${esc(ch.title)}</p>${ch.scenes.map(s=>`<button class="scene-link ${s.id===state.sceneId?'active':''}" data-scene="${s.id}">${esc(s.title)} <small>· ${esc(s.status)}</small></button>`).join('')}`).join('');const switchList=$('#workSwitchList');if(switchList)switchList.innerHTML=state.works.length?state.works.map(item=>`<button type="button" class="work-switch-row ${item.id===work?.id?'active':''}" data-select-work="${esc(item.id)}"><span><b>${esc(item.title)}</b><small>${item.id===work?.id?'当前打开':'作品数据独立保存'}</small></span><em>${item.id===work?.id?'当前':''}</em></button>`).join(''):'<p class="form-note">尚未建立作品。</p>'}
function stageLabel(stage){return({overview:'作品总览',brief:'全作创作方向',blueprint:'全作故事方向',structure:'章节细纲',draft:'逐场写作',references:'创作资料',release:'检查并发布'})[stage]}
function mobileLabel(view){return({works:'作品结构',context:'上下文',tasks:'任务'})[view]||'写作'}
function frame(kicker,title,lede,body){return `<div class="workspace-inner"><p class="eyebrow">${kicker}</p><h2>${title}</h2><p class="lede">${lede}</p>${body}</div>`}
function renderWorkspace(){const el=$('#workspace');if(!state.work){el.innerHTML=frame('WRITING WORKSPACE','从一句想法开始','这里保存作品结构、正文版本和审查决定。模型只能提出候选，不会直接覆盖正式内容。',`<div class="empty-state"><div class="number">01</div><h3>先建立一部作品</h3><p class="lede">作品是长期创作的边界。建立后，你会先确认写作想法，再逐步得到故事方向、场景与可审查候选。</p><button class="primary" data-action="new-work">开始一个新故事</button></div>`);return}if(state.mobileView==='works')renderMobileWorks(el);else if(state.mobileView==='context')renderMobileContext(el);else if(state.mobileView==='tasks')renderMobileTasks(el);else if(state.stage==='overview')renderOverview(el);else if(state.stage==='references')renderReferences(el);else if(state.stage==='brief')renderBrief(el);else if(state.stage==='blueprint')renderBlueprint(el);else if(state.stage==='structure')renderStructure(el);else if(state.stage==='draft')renderDraft(el);else if(state.stage==='release')renderRelease(el);el.scrollTop=0}
function renderOverview(el){
  const work=state.work, sceneList=scenes(), total=sceneList.length, drafted=sceneList.filter(scene=>scene.current_revision_id).length;
  const cards=libraryCards(), world=worldBible(), pending=work.proposals.filter(item=>item.status==='pending').length;
  const blocker=(work.review_findings||[]).filter(item=>item.status==='open'&&item.severity==='blocking').length;
  let next={stage:'brief',title:'先保存写作想法',detail:'把一句想法、主要角色和写作范围存成 Brief。',label:'开始写作想法'};
  if(brief()&&!blueprint())next={stage:'blueprint',title:'确认故事方向',detail:'系统会生成一份可检查的 StoryBlueprint，你决定是否采用。',label:'确认故事方向'};
  else if(blueprint()&&!work.chapters.length)next={stage:'structure',title:'建立章节与场景',detail:'先给作品一个章节，再把它拆成稳定 ID 的场景。',label:'建立结构'};
  else if(total&&drafted<total)next={stage:'draft',title:'开始下一场写作',detail:`${total-drafted} 个场景还没有正文，候选会先进入 Proposal。`,label:'打开逐场写作'};
  else if(pending)next={stage:'draft',title:'审查待处理候选',detail:`有 ${pending} 份候选等待采纳、局部修改或退回。`,label:'查看候选'};
  else if(blocker)next={stage:'draft',title:'处理审查阻塞项',detail:`有 ${blocker} 个阻塞项，解决后才能继续冻结发布。`,label:'处理审查'};
  else if(total)next={stage:'release',title:'运行全篇审查',detail:'确认连续性、人物约束和正文修订后，再冻结 ScriptRelease。',label:'检查并发布'};
  const progress=total?Math.round(drafted/total*100):0;
  const worldEntities=(world.entities||[]).filter(item=>item.status!=='archived');
  el.innerHTML=`<div class="overview-workbench"><header class="overview-header"><div><p class="eyebrow">WORK OVERVIEW</p><h2>${esc(work.title)}</h2><p>这是这部作品的控制台。系统保存了什么、现在卡在哪里、下一步由你决定，都从这里开始。</p></div><button class="quiet" data-stage-jump="references">打开资料库</button></header><section class="overview-next"><div><span class="overview-label">推荐下一步</span><h3>${next.title}</h3><p>${next.detail}</p></div><button class="primary" data-stage-jump="${next.stage}">${next.label}</button></section><section class="overview-grid"><div class="overview-stat"><b>${progress}%</b><span>正文进度</span><small>${drafted}/${total||0} 个场景已采纳正文</small><div class="progress-track"><i style="width:${progress}%"></i></div></div><button class="overview-stat overview-link" data-stage-jump="references" data-library-target="characters"><b>${cards.length}</b><span>人物卡</span><small>${cards.filter(card=>card.source_type==='official_reference').length} 原作参考 · ${cards.filter(card=>card.source_type==='custom').length} 自定义</small></button><button class="overview-stat overview-link" data-stage-jump="references" data-library-target="world"><b>${worldEntities.length}</b><span>世界观卡</span><small>${worldEntities.filter(item=>item.confidence_status==='confirmed').length} 已确认 · ${world.rules.length} 条规则</small></button><button class="overview-stat overview-link" data-stage-jump="draft"><b>${pending}</b><span>待处理候选</span><small>${blocker} 个开放阻塞项</small></button></section><section class="overview-columns"><div><div class="overview-section-head"><h3>作品结构</h3><button class="quiet" data-stage-jump="structure">管理章节</button></div>${work.chapters.length?work.chapters.map(ch=>`<div class="overview-chapter"><b>${esc(ch.title)}</b><span>${ch.scenes.length} 个场景</span><small>${ch.scenes.filter(scene=>scene.current_revision_id).length} 已完成</small></div>`).join(''):'<p class="overview-empty">还没有章节。下一步会引导你建立第一章。</p>'}</div><div><div class="overview-section-head"><h3>系统状态</h3><span class="status-chip ${pending?'amber':''}">${pending?'等待你的决定':'暂无待处理项'}</span></div><ul class="overview-checks"><li><i class="check-dot"></i>作品与版本已持久化</li><li><i class="check-dot"></i>${cards.length?'人物卡已登记':'尚未建立人物卡'}</li><li><i class="check-dot"></i>${worldEntities.length?'世界观卡已登记':'尚未建立 BA/自定义世界观卡'}</li></ul></div></section></div>`;
}
function renderMobileWorks(el){el.innerHTML=frame('WORK TREE','作品结构','从章节进入一场写作。场景 ID 不会因标题变化而改变。',`${state.work.chapters.map(ch=>`<section class="artifact"><h3>${esc(ch.title)}</h3>${ch.scenes.map(s=>`<div class="structure-row"><div><h3>${esc(s.title)}</h3><p>${esc(s.contract.goal||'场景目标待定')} · ${esc(s.status)}</p></div><button class="quiet" data-scene-open="${s.id}">打开</button></div>`).join('')||'<p>本章还没有场景。</p>'}</section>`).join('')||'<div class="notice">作品还没有章节。</div>'}<div class="actions"><button class="primary" data-mobile="writing">返回写作</button><button class="quiet" data-stage-jump="structure">编辑结构</button></div>`)}
function renderMobileContext(el){const scene=selectedScene(),c=state.context;el.innerHTML=frame('SCENE CONTEXT','当前场景上下文',scene?`${esc(scene.chapterTitle)} / ${esc(scene.title)}`:'尚未选择场景',`${c?`<div class="notice ${c.readiness.real_ba_writing==='ready'?'good':''}">真实 BA 写作：${esc(c.readiness.real_ba_writing)}${c.readiness.reason?` · ${esc(c.readiness.reason)}`:''}</div><section class="artifact"><h3>固定输入</h3><p>规则包：${esc(c.rules.pack_version)}</p><p>单一模式：${esc(c.rules.mode)}</p><p>来源修订：${c.source_revision_ids.length} 个</p><p>资料证据：${c.reference_files?.length||0} 个（不会自动变成事实）</p><p>缺少运行时人物卡：${esc(c.readiness.missing_runtime_character_cards.join('、')||'无')}</p></section>`:'<div class="notice">尚未装配本场上下文。回到写作页执行“装配上下文”。</div>'}<div class="actions"><button class="primary" data-mobile="writing">返回写作</button>${scene?'<button class="quiet" data-action="assemble-context">重新装配</button>':''}</div>`)}
function renderMobileTasks(el){const items=state.work.runs.flatMap(r=>r.work_items.map(item=>({...item,runId:r.id})));el.innerHTML=frame('WORK ITEMS','后台任务','任务状态来自持久化 WorkItem，应用重启后仍可恢复。',`${items.length?items.map(item=>`<section class="artifact"><p class="eyebrow">${esc(item.type)}</p><h3>${esc(item.status)}</h3><p>${esc(item.scope_type)} · ${esc(item.scope_id)}</p><p class="code-meta">${esc(item.id)}<br>Run ${esc(item.runId)}</p></section>`).join(''):'<div class="notice good">当前没有后台任务。</div>'}<div class="actions"><button class="primary" data-mobile="writing">返回写作</button></div>`)}
function artifact(kind){return state.work.artifacts.find(x=>x.kind===kind)?.current_revision?.content}
function renderReferences(el){const canon=artifact('work_canon')||{facts:[]},cards=state.work.artifacts.filter(x=>x.kind==='character_card').map(x=>x.current_revision?.content).filter(Boolean),files=state.work.reference_files||[];el.innerHTML=frame('REFERENCES','确认这部作品的事实','事实、人物卡和资料分别保存，有来源才能成为场景上下文。',`<div class="step-band"><strong>现在需要你决定</strong><span>确认哪些事实与人物声音可以被下一场读取</span></div><div class="reference-grid"><section class="artifact"><p class="eyebrow">WORK CANON</p><h3>已确定的事实</h3>${canon.facts.length?`<ul class="fact-list">${canon.facts.map(f=>`<li><b>${esc(f.text)}</b><small>${esc(f.source)} · ${esc(f.confidence_status)}</small></li>`).join('')}</ul>`:'<p class="lede">还没有已确认事实。</p>'}<form id="canonForm"><label>新增事实<textarea name="text" placeholder="例如：旧机器没有接通电源"></textarea></label><label>来源<input name="source" placeholder="用户确认 / 场景修订 / 已登记资料"></label><div class="actions"><button class="primary">确认事实</button></div></form></section><section class="artifact"><p class="eyebrow">CHARACTER CARDS</p><h3>人物声音与边界</h3>${cards.length?cards.map(c=>`<div class="card-row"><b>${esc(c.name)}</b><small>${esc((c.voice_anchors||[]).join(' / ')||'无声音锚点')}<br>${esc((c.source_refs||[]).join('、'))}</small></div>`).join(''):'<p class="lede">还没有人物卡。</p>'}<form id="characterForm"><label>角色名称<input name="name" placeholder="爱丽丝"></label><label>声音锚点<input name="voice" placeholder="短句、直接、把判断落到当前操作"></label><label>OOC 红线<input name="ooc" placeholder="不替他人说出隐藏动机"></label><label>来源<input name="source" placeholder="官方剧情索引 / 用户确认"></label><div class="actions"><button class="primary">保存人物卡</button></div></form></section><section class="artifact"><p class="eyebrow">REFERENCE FILES</p><h3>已登记资料</h3>${files.length?files.map(f=>`<div class="card-row"><b>${esc(f.title)}</b><small>${esc(f.source_label)} · ${esc(f.trust_status)}</small></div>`).join(''):'<p class="lede">还没有资料文件。</p>'}<form id="referenceForm"><label>资料名称<input name="title" placeholder="场景前提笔记"></label><label>来源标签<input name="source_label" placeholder="用户导入"></label><label>资料内容<textarea name="content" placeholder="资料正文会以版本化文件保存"></textarea></label><div class="actions"><button class="quiet">登记资料</button></div></form></section></div>`)}
function renderBrief(el){const b=brief()||{};el.innerHTML=frame('01 / BRIEF','你想写一个怎样的故事？','先固定创作意图、角色与范围。保存后它会成为可追溯的创意简报修订。',`<div class="step-band"><strong>现在需要你决定</strong><span>一句想法、写作模式和主要角色</span></div><form id="briefForm" class="field-grid"><label class="wide">一句想法<textarea name="idea" required placeholder="例如：凯伊发现游戏开发部的旧机器在深夜自行启动">${esc(b.idea)}</textarea></label><label>写作模式<select name="mode"><option value="bond_short" ${b.mode==='bond_short'?'selected':''}>羁绊短场景</option><option value="main_battle" ${b.mode==='main_battle'?'selected':''}>主线与战斗</option><option value="long_comedy" ${b.mode==='long_comedy'?'selected':''}>长篇喜剧</option><option value="text_reading" ${b.mode==='text_reading'?'selected':''}>小说化阅读</option></select></label><label>目标长度<select name="target_length"><option value="short">短场景</option><option value="chapter">单章</option><option value="long">长篇</option></select></label><label class="wide">主要角色<input name="characters" value="${esc((b.characters||[]).join('、'))}" placeholder="爱丽丝、凯伊"></label><label class="wide">额外约束<textarea name="constraints" placeholder="不可提前揭示的事实、希望保留的关系距离……">${esc(b.constraints)}</textarea></label><label class="check"><span><input type="checkbox" name="has_sensei" ${b.has_sensei?'checked':''}> 老师在场</span></label><div class="actions wide"><button class="primary" type="submit">保存写作想法</button><button class="quiet" type="button" data-stage-jump="blueprint" ${brief()?'':'disabled'}>继续确认故事方向</button></div></form>`)}
function renderBlueprint(el){const b=blueprint();el.innerHTML=frame('02 / STORY BLUEPRINT','确认故事方向','系统把写作想法整理成结构化方向。当前纵切使用明确标注的本地模拟 Provider。',`<div class="step-band"><strong>${b?'系统已整理故事方向':'系统尚未生成方向'}</strong><span>${b?'检查冲突、范围与停止方式':'先保存写作想法'}</span></div>${!brief()?'<div class="notice bad">请先回到“写作想法”保存创意简报。</div>':b?`<div class="notice">模拟结果 · 未调用真实模型，也未自动写入正文。</div><section class="artifact"><h3>${esc(b.title)}</h3><p>${esc(b.premise)}</p><p><b>核心冲突：</b>${esc(b.central_conflict)}</p><p><b>主题方向：</b>${esc(b.theme)}</p><ol class="direction-list">${b.direction.map(x=>`<li>${esc(x)}</li>`).join('')}</ol></section><div class="actions"><button class="primary" data-stage-jump="structure">确认并建立章节</button><button class="quiet" data-action="generate-blueprint">重新生成方向</button></div>`:`<div class="empty-state"><div class="number">02</div><h3>把一句想法整理成可检查的方向</h3><p class="lede">结果会单独保存为 StoryBlueprint，不与聊天或正文混在一起。</p><button class="primary" data-action="generate-blueprint">生成故事方向</button></div>`}`)}
function structureSeed(){return {chapter_ids:(state.work?.chapters||[]).map(chapter=>chapter.id),scene_placements:(state.work?.chapters||[]).flatMap(chapter=>chapter.scenes.map(scene=>({scene_id:scene.id,chapter_id:chapter.id})))} }
function structureDraft(){const canonical=structureSeed(),draft=state.structureDraft;const needsRefresh=!draft||draft.workId!==state.work?.id||(!state.structureDirty&&(draft.chapter_ids.length!==canonical.chapter_ids.length||draft.scene_placements.length!==canonical.scene_placements.length||canonical.chapter_ids.some(id=>!draft.chapter_ids.includes(id))||canonical.scene_placements.some(item=>!draft.scene_placements.some(entry=>entry.scene_id===item.scene_id))));if(needsRefresh){state.structureDraft={workId:state.work?.id,...canonical};state.structureDirty=false}return state.structureDraft}
function draftScenesForChapter(chapterId){const lookup=new Map(scenes().map(scene=>[scene.id,scene]));return structureDraft().scene_placements.filter(item=>item.chapter_id===chapterId).map(item=>lookup.get(item.scene_id)).filter(Boolean)}
function resetStructureDraft(){state.structureDraft={workId:state.work?.id,...structureSeed()};state.structureDirty=false}
function moveListEntry(items,from,to){if(to<0||to>=items.length)return false;const [entry]=items.splice(from,1);items.splice(to,0,entry);return true}
function moveDraftChapter(chapterId,direction){const draft=structureDraft(),from=draft.chapter_ids.indexOf(chapterId);if(from<0||!moveListEntry(draft.chapter_ids,from,from+(direction==='up'?-1:1)))return;state.structureDirty=true;render()}
function moveDraftScene(sceneId,direction){const draft=structureDraft(),entry=draft.scene_placements.find(item=>item.scene_id===sceneId);if(!entry)return;const siblings=draft.scene_placements.filter(item=>item.chapter_id===entry.chapter_id),from=siblings.findIndex(item=>item.scene_id===sceneId),to=from+(direction==='up'?-1:1);if(to<0||to>=siblings.length)return;const other=siblings[to],fromIndex=draft.scene_placements.indexOf(entry),toIndex=draft.scene_placements.indexOf(other);[draft.scene_placements[fromIndex],draft.scene_placements[toIndex]]=[draft.scene_placements[toIndex],draft.scene_placements[fromIndex]];state.structureDirty=true;render()}
function placeDraftScene(sceneId,chapterId){const draft=structureDraft(),from=draft.scene_placements.findIndex(item=>item.scene_id===sceneId);if(from<0||!draft.chapter_ids.includes(chapterId)||draft.scene_placements[from].chapter_id===chapterId)return;const [entry]=draft.scene_placements.splice(from,1);entry.chapter_id=chapterId;const lastTarget=[...draft.scene_placements].map((item,index)=>item.chapter_id===chapterId?index:-1).filter(index=>index>=0).pop();draft.scene_placements.splice(lastTarget===undefined?draft.scene_placements.length:lastTarget+1,0,entry);state.structureDirty=true;render()}
function renderStructure(el){const chapters=state.work.chapters,draft=structureDraft(),hasChanges=state.structureDirty;el.innerHTML=frame('03 / STRUCTURE','章节安排','章节和场景是作品的骨架。调整位置不会改变场景 ID、正文修订或资料关联；保存后需要重新运行全篇审查。',`<section class="structure-command"><div><p class="eyebrow">STORY ORDER</p><h3>${chapters.length?'整理章节与场景':'现在需要你决定'}</h3><p>${chapters.length?(hasChanges?'结构有未保存调整。保存后，旧全篇审查会过期。':'拖动前先用方向按钮调整；每次保存都可追溯。'):'先建立第一章，再把故事拆成稳定场景。'}</p></div>${chapters.length?`<div class="structure-command-actions"><span class="structure-save-state ${hasChanges?'dirty':'saved'}">${hasChanges?'未保存调整':'当前顺序已保存'}</span><button class="quiet" type="button" data-structure-reset ${hasChanges?'':'disabled'}>撤销调整</button><button class="primary" type="button" data-structure-save ${hasChanges?'':'disabled'}>保存章节安排</button></div>`:''}</section><div class="structure-board">${chapters.map(ch=>{const chapterPosition=draft.chapter_ids.indexOf(ch.id),orderedScenes=draftScenesForChapter(ch.id);return `<section class="chapter-lane" data-chapter-lane="${esc(ch.id)}"><header class="chapter-lane-head"><div><p>第 ${String(chapterPosition+1).padStart(2,'0')} 章</p><h3>${esc(ch.title)}</h3><small>${orderedScenes.length} 个场景 · ${orderedScenes.filter(scene=>scene.current_revision_id).length} 个已有正文</small></div><div class="lane-actions"><button class="icon-button" type="button" title="章节上移" aria-label="章节上移" data-structure-chapter-move="up" data-chapter-id="${esc(ch.id)}" ${chapterPosition===0?'disabled':''}>↑</button><button class="icon-button" type="button" title="章节下移" aria-label="章节下移" data-structure-chapter-move="down" data-chapter-id="${esc(ch.id)}" ${chapterPosition===draft.chapter_ids.length-1?'disabled':''}>↓</button><button class="quiet" type="button" data-structure-add-scene="${esc(ch.id)}">添加场景</button></div></header><div class="scene-arrangement-list">${orderedScenes.length?orderedScenes.map((scene,index)=>`<article class="scene-arrangement"><div class="scene-order">${String(index+1).padStart(2,'0')}</div><div class="scene-arrangement-copy"><b>${esc(scene.title)}</b><p>${esc(scene.contract.goal||'尚未填写本场目标')}</p><small>${scene.current_revision_id?'已有正文':'尚未起草'} · ${esc(scene.id)}</small></div><div class="scene-arrangement-actions"><button class="icon-button" type="button" title="场景上移" aria-label="场景上移" data-structure-scene-move="up" data-scene-id="${esc(scene.id)}" ${index===0?'disabled':''}>↑</button><button class="icon-button" type="button" title="场景下移" aria-label="场景下移" data-structure-scene-move="down" data-scene-id="${esc(scene.id)}" ${index===orderedScenes.length-1?'disabled':''}>↓</button><label class="scene-chapter-select"><span>放入</span><select data-structure-scene-target="${esc(scene.id)}">${draft.chapter_ids.map((targetId,targetIndex)=>{const target=chapters.find(item=>item.id===targetId);return `<option value="${esc(targetId)}" ${targetId===ch.id?'selected':''}>第${targetIndex+1}章 · ${esc(target?.title||'')}</option>`}).join('')}</select></label><button class="quiet" type="button" data-scene-open="${esc(scene.id)}">写本场</button></div></article>`).join(''):'<div class="scene-arrangement-empty">本章还没有场景。可以先添加一场，再安排到合适的位置。</div>'}</div></section>`}).join('')||'<div class="empty-state"><div class="number">03</div><h3>先建立第一章</h3><p class="lede">章节与场景是正文的稳定结构，不依赖标题或数组顺序作为身份。</p></div>'}</div><div class="actions structure-footer-actions"><button class="primary" data-structure-add-chapter>${chapters.length?'添加章节':'建立第一章'}</button>${chapters.length?`<button class="quiet" data-structure-add-scene="${chapters[0].id}">添加场景</button>`:''}</div>`)}
function renderDraft(el){const scene=selectedScene(),proposal=pendingProposal(),findings=(state.work.review_findings||[]).filter(f=>f.scene_id===scene?.id&&f.status==='open');if(!scene){el.innerHTML=frame('04 / SCENE DRAFT','还没有可写的场景','先建立章节和场景，再为一个稳定 Scene ID 装配上下文。','<button class="primary" data-stage-jump="structure">建立场景</button>');return}const current=state.work.artifacts.find(a=>a.kind==='scene_script'&&a.scope_id===scene.id)?.current_revision?.content?.text||'';el.innerHTML=frame('04 / SCENE DRAFT',esc(scene.title),`${esc(scene.chapterTitle)} · ${esc(scene.contract.location||'地点待定')} · ${esc(scene.contract.goal||'目标待定')}`,`<div class="step-band"><strong>${proposal?'现在需要你审查候选':current?'正文已采纳，可继续生成新候选':'系统可以装配本场上下文'}</strong><span>Agent 作用域：仅当前场景与固定输入修订</span></div>${findings.length?`<section class="review-findings ${findings.some(x=>x.severity==='blocking')?'has-blocker':''}"><p class="eyebrow">REVIEW FINDINGS</p><h3>本场需要你的决定</h3>${findings.map(x=>`<div class="finding-row"><div><b>${esc(x.severity)}</b><p>${esc(x.message)}</p></div><button class="quiet" data-resolve-finding="${x.id}">处理</button></div>`).join('')}</section>`:''}${proposal?`<div class="notice">模拟候选 · 可修改后部分采纳。采纳才会建立新的正文修订。</div><div class="proposal-layout"><label>候选正文<textarea class="editor" id="candidateText">${esc(proposal.candidate)}</textarea></label><div><label>与当前稿件的差异</label><pre class="diff-view">${proposal.diff.map(line=>`<span class="${line.startsWith('+')?'diff-add':line.startsWith('-')?'diff-del':''}">${esc(line)}</span>`).join('\n')}</pre><p class="code-meta">Proposal ${proposal.id}<br>Base ${proposal.base_revision_id||'空白正文'}</p></div></div><div class="actions"><button class="primary" data-accept="${proposal.id}">采纳当前内容</button><button class="danger" data-reject="${proposal.id}">退回候选</button></div>`:`${current?`<label>当前正文<textarea class="editor" readonly>${esc(current)}</textarea></label>`:'<div class="notice">本场还没有正文。生成会先创建 WorkItem 和 JobAttempt，结果只进入 Proposal。</div>'}<div class="actions"><button class="primary" data-action="assemble-context">装配上下文</button><button class="quiet" data-action="review-scene" ${current?'':'disabled'}>检查本场</button><button class="quiet" data-action="generate-candidate">生成模拟候选</button></div>`}`)}
function renderRelease(el){const releases=state.work.releases||[],ready=scenes().length&&scenes().every(s=>s.current_revision_id),sourceIds=scenes().map(s=>s.current_revision_id),latestSources=releases[0]?JSON.parse(releases[0].source_revision_ids_json):[],alreadyFrozen=ready&&sourceIds.length===latestSources.length&&sourceIds.every((id,i)=>id===latestSources[i]),gates=state.work.gates||[],reviewGate=gates.find(g=>g.kind==='release.review'),snapshot=reviewGate?.snapshot,reviewCurrent=snapshot&&JSON.stringify(snapshot.scene_revision_ids)===JSON.stringify(sourceIds),canFreeze=ready&&!alreadyFrozen&&reviewGate?.status==='passed'&&reviewCurrent;el.innerHTML=frame('05 / RELEASE','检查并发布定稿','全篇审查会固定本次检查覆盖的场景修订。任何正文变更后，都必须重新审查才可冻结。',`<div class="step-band"><strong>${alreadyFrozen?'当前正文已冻结':canFreeze?'全篇审查已通过':'等待全篇审查'}</strong><span>${alreadyFrozen?'正文产生新修订后可发布下一版':canFreeze?'现在由你决定是否冻结':ready?'先运行全篇审查，确认当前正文':'每个场景都必须有已采纳正文'}</span></div><div class="notice ${canFreeze||alreadyFrozen?'good':'bad'}">${!ready?`还缺少 ${scenes().filter(s=>!s.current_revision_id).length} 个场景正文。`:reviewGate?`${reviewGate.status==='passed'&&reviewCurrent?'审查覆盖当前正文，未发现阻塞项':`审查 ${reviewGate.status==='passed'?'已过期':'未通过'}`} · 已检查 ${snapshot?.checked_scene_count||0} 个场景 · 阻塞项 ${snapshot?.blocking_finding_ids?.length||0}`:'尚未运行全篇审查。'}</div><div class="actions"><button class="quiet" data-action="review-release" ${ready?'':'disabled'}>运行全篇审查</button><button class="primary" data-action="freeze-release" ${canFreeze?'':'disabled'}>${alreadyFrozen?'当前正文已冻结':'冻结新的发布版本'}</button></div>${releases.map(r=>`<section class="artifact"><h3>交给制作的定稿 ${esc(r.display_version)}</h3><p class="code-meta">${esc(r.id)}<br>${esc(r.content_hash)}</p><p>${r.production_run_id?`已交给制作 · ${esc(r.production_run_id)}`:'尚未交给 AA 制作后端'}</p><div class="actions"><button class="quiet" data-handoff="${r.id}" ${r.production_run_id?'disabled':''}>${r.production_run_id?'已提交制作':'交给 AA 制作'}</button></div></section>`).join('')}`)}
function renderInspector(){const el=$('#inspectorContent'),scene=selectedScene(),proposal=pendingProposal(),latest=state.work?.releases?.[0];$$('[data-inspector]').forEach(b=>b.classList.toggle('active',b.dataset.inspector===state.inspector));if(state.inspector==='decision'){el.innerHTML=`<div class="inspector-body"><h3>现在需要你决定</h3><div class="notice ${proposal?'':'good'}">${proposal?'检查候选正文，并采纳、局部修改或退回。':state.stage==='release'&&latest&&!latest.production_run_id?'确认是否把冻结版本交给制作。':state.stage==='release'&&latest?'当前发布版本已完成交接。':'完成当前阶段的推荐动作。'}</div><h3>系统已经做了什么</h3><ul class="context-list"><li><span class="status-dot"></span>作品与版本已持久化</li><li><span class="status-dot ${proposal?'amber':''}"></span>${proposal?'候选等待审查':'没有待处理候选'}</li><li><span class="status-dot"></span>Agent 不可直接写回正文</li></ul></div>`}else if(state.inspector==='context'){const c=state.context;el.innerHTML=`<div class="inspector-body"><h3>当前作用域</h3><p>${scene?`${esc(scene.chapterTitle)} / ${esc(scene.title)}`:'未选择场景'}</p><ul class="context-list">${c?`<li>规则包<br><b>${esc(c.rules.pack_version)}</b></li><li>单一模式<br><b>${esc(c.rules.mode)}</b></li><li>固定输入修订<br><b>${c.source_revision_ids.length} 个</b></li><li>真实 BA 写作<br><b>${esc(c.readiness.real_ba_writing)}</b></li>`:'<li>点击“装配上下文”查看本场固定输入。</li>'}</ul></div>`}else{const existing=scene?.current_revision_id,latestRun=(state.work?.agent_runs||[]).find(run=>run.scope_id===scene?.id);el.innerHTML=`<div class="inspector-body"><h3>创作导演</h3><p>本次运行只读取固定场景合同、单一 BA 模式和运行时人物卡。它只提交一次 Proposal，不能改正文或长期事实。</p>${latestRun?`<section class="agent-run"><b>${esc(latestRun.status)}</b><p>工具记录 ${latestRun.tool_calls.length} 项${latestRun.proposal_id?` · Proposal ${esc(latestRun.proposal_id)}`:''}</p></section>`:''}<form id="agentRunForm"><label>本场指令<textarea name="instruction" placeholder="例如：以爱丽丝先观察、凯伊后补充的节奏起草本场" ${scene&&!existing?'':'disabled'}></textarea></label><button class="primary" type="submit" ${scene&&!existing&&!proposal?'':'disabled'}>运行 BA 场景 Agent</button></form><p class="form-note">${existing?'当前已有正文：首次 BA Agent 不读取旧稿，受控复写将在后续工作流开放。':'当前仅有明确标注的 Fake Provider；真实模型尚未接入。'}</p></div>`}}
document.addEventListener('click',async event=>{const b=event.target.closest('button');if(!b)return;try{if(b.dataset.action==='new-work')return $('#workDialog').showModal();if(b.dataset.mobile){state.mobileView=b.dataset.mobile;render();return}if(b.dataset.stage){navigateToStage(b.dataset.stage);return}if(b.dataset.stageJump){navigateToStage(b.dataset.stageJump);return}if(b.dataset.scene){state.sceneId=b.dataset.scene;navigateToStage('draft');state.context=null;state.sceneContextEditorOpen=false;render();return}if(b.dataset.sceneOpen){state.sceneId=b.dataset.sceneOpen;navigateToStage('draft');state.sceneContextEditorOpen=false;render();return}if(b.dataset.inspector){state.inspector=b.dataset.inspector;renderInspector();return}if(b.dataset.action==='generate-blueprint'){setBusy('正在建立故事方向');const x=await api(`/works/${state.work.id}/blueprint:generate`,{method:'POST',body:JSON.stringify({expected_version:state.work.version})});state.work=x.work;toast('故事方向已保存');render();return}if(b.dataset.action==='add-chapter'){const title=prompt('章节名称','第一章');if(!title)return;const x=await api(`/works/${state.work.id}/chapters`,{method:'POST',body:JSON.stringify({expected_version:state.work.version,title})});state.work=x.work;toast('章节已建立');render();return}if(b.dataset.addScene){const title=prompt('场景名称','场景 01');if(!title)return;const goal=prompt('本场需要发生什么变化？','确认异常提示灯的来源')||'';const location=prompt('场景地点','游戏开发部活动室')||'';const x=await api(`/works/${state.work.id}/chapters/${b.dataset.addScene}/scenes`,{method:'POST',body:JSON.stringify({expected_version:state.work.version,title,goal,location})});state.work=x.work;state.sceneId=x.scene_id;toast('场景已建立');render();return}if(b.dataset.action==='assemble-context'){state.context=await api(`/works/${state.work.id}/scenes/${selectedScene().id}/context:assemble`,{method:'POST',body:'{}'});state.inspector='context';toast('本场上下文已装配');render();return}if(b.dataset.action==='generate-candidate'){setBusy('正在运行 scene.draft.generate');const x=await api(`/works/${state.work.id}/scenes/${selectedScene().id}/candidate:generate`,{method:'POST',body:JSON.stringify({expected_version:state.work.version})});state.work=x.work;toast('模拟候选已生成，等待你的决定');render();return}if(b.dataset.accept){const x=await api(`/works/${state.work.id}/proposals/${b.dataset.accept}/accept`,{method:'POST',body:JSON.stringify({expected_version:state.work.version,text:$('#candidateText').value})});state.work=x.work;toast('候选已采纳为新正文修订');render();return}if(b.dataset.reject){const x=await api(`/works/${state.work.id}/proposals/${b.dataset.reject}/reject`,{method:'POST',body:JSON.stringify({expected_version:state.work.version,note:'用户在工作台退回'})});state.work=x.work;toast('候选已退回');render();return}if(b.dataset.action==='freeze-release'){const x=await api(`/works/${state.work.id}/releases:freeze`,{method:'POST',body:JSON.stringify({expected_version:state.work.version})});state.work=x.work;toast(`已冻结 ${x.manifest.display_version}`);render();return}if(b.dataset.handoff){setBusy('正在联系 AA 制作后端');const x=await api(`/releases/${b.dataset.handoff}/handoff`,{method:'POST',body:'{}'});toast(`已建立制作任务 ${x.production_run_id}`);await loadWork(state.work.id);return}if(b.dataset.section==='works'){state.stage='overview';state.mobileView='writing';render();return}if(b.dataset.section==='writing'){navigateToStage(blueprintIsConfirmed()?'structure':'brief');return}if(b.dataset.section==='production'){const gate=stageGate('release');if(!gate.allowed){toast(`检查并发布尚未开放：${gate.reason}`,true);return}state.stage='release';state.mobileView='writing';render();return}if(b.dataset.section==='references'){state.stage='references';state.mobileView='writing';state.libraryView='overview';render();return}if(b.dataset.section==='tasks'){state.mobileView='tasks';render();return}}catch(error){setBusy('操作失败，作品数据未丢失');toast(error.message,true)}});
$('#workForm').addEventListener('submit',async event=>{event.preventDefault();const submitter=event.submitter;if(submitter?.dataset.submit!=='work')return;try{const form=new FormData(event.target);const work=await api('/works',{method:'POST',body:JSON.stringify(Object.fromEntries(form))});state.works.unshift(work);state.work=work;state.stage='brief';state.inspector='agent';$('#workDialog').close();event.target.reset();toast('作品骨架与创作主对话已保存');render()}catch(error){toast(error.message,true)}});
document.addEventListener('submit',async event=>{if(event.target.id!=='briefForm')return;event.preventDefault();try{const f=new FormData(event.target);const payload={idea:String(f.get('idea')||'').trim(),intent_only:true,expected_version:state.work.version};setBusy('正在保存想法');const intent=await api(`/works/${state.work.id}/brief`,{method:'POST',body:JSON.stringify(payload)});state.work=intent.work;setBusy('正在分析故事方向');const analysis=await api(`/works/${state.work.id}/blueprint:generate`,{method:'POST',body:JSON.stringify({expected_version:state.work.version})});state.work=analysis.work;state.stage='blueprint';toast(analysis.simulation?'已生成模拟方向候选，等待你的确认':'已生成故事方向候选，等待你的确认');render()}catch(error){setBusy('分析未完成，想法已安全保存');toast(error.message,true)}});
const officialCatalogObserver=new MutationObserver(()=>{
  if(state.capabilities?.official_references?.available!==false)return;
  const library=$('#workspace .library-workbench');
  if(!library)return;
  const main=$('.library-main',library);
  if(main&&!$('.catalog-availability',main)){
    const notice=document.createElement('div');
    notice.className='catalog-availability';
    notice.style.cssText='margin:0 0 16px;padding:10px 12px;border:1px solid #ddc17f;background:#fff9eb;color:#72551e;font-size:12px;line-height:1.6';
    notice.setAttribute('role','status');
    notice.textContent='BA 原作语料库当前不可读取。你仍可编辑 BA 起始结构、自定义角色和世界观；原作检索会在语料库恢复可读后开放。';
    main.prepend(notice);
  }
  $$('[data-library-view="official"]',library).forEach(button=>{
    if(!button.disabled){
      button.disabled=true;
      button.setAttribute('aria-disabled','true');
      button.title='官方演出语料库目录当前不可读取';
      button.textContent='BA 原作语料库当前不可读取';
    }
  });
});
officialCatalogObserver.observe($('#workspace'),{childList:true,subtree:true});
boot();

// The workflow guard is deliberately a view-layer policy. It never mutates a
// Work: server-side commands still validate their own domain preconditions.
const FLOW_STAGES=['structure','draft','release'];
const FLOW_REQUIREMENTS={
  structure:'先在作品栏目确认全作故事方向，并选择当前章节',
  draft:'先建立至少一个章节和场景',
  release:'先为每个场景采纳正文，并处理待决定的候选'
};
function usesGuidedWorkflow(){
  const creation=state.work?.runs?.find(run=>run.kind==='creation');
  return creation?.automation_level!=='milestone';
}
function workflowProgress(){
  const sceneList=scenes();
  const hasStructure=Boolean(state.work?.chapters?.length&&sceneList.length);
  const pending=(state.work?.proposals||[]).some(proposal=>proposal.status==='pending');
  const allManuscripts=Boolean(sceneList.length)&&sceneList.every(scene=>Boolean(scene.current_revision_id));
  const sourceRevisionIds=sceneList.map(scene=>scene.current_revision_id).filter(Boolean);
  const latestRelease=state.work?.releases?.[0];
  let frozenRevisionIds=[];
  try{frozenRevisionIds=latestRelease?JSON.parse(latestRelease.source_revision_ids_json||'[]'):[]}catch(_){frozenRevisionIds=[]}
  const releaseIsCurrent=Boolean(latestRelease&&allManuscripts&&sourceRevisionIds.length===frozenRevisionIds.length&&sourceRevisionIds.every((id,index)=>id===frozenRevisionIds[index]));
  return {
    done:{
      brief:Boolean(brief()),
      blueprint:blueprintIsConfirmed(),
      structure:hasStructure,
      draft:allManuscripts&&!pending,
      release:releaseIsCurrent
    },
    sceneList,
    pending,
    allManuscripts
  };
}
function stageGate(stage){
  if(!FLOW_STAGES.includes(stage))return{allowed:true,reason:''};
  if(!state.work)return{allowed:stage==='brief',reason:'请先建立作品。'};
  const progress=workflowProgress();
  if(!usesGuidedWorkflow())return{allowed:true,reason:'里程碑模式允许浏览各阶段；写入动作仍会检查前置条件。',progress};
  const allowed={
    structure:progress.done.blueprint||progress.done.structure,
    draft:progress.done.structure,
    release:progress.done.draft||Boolean(state.work?.releases?.length)
  }[stage];
  let reason=FLOW_REQUIREMENTS[stage];
  if(stage==='release'&&progress.sceneList.length&&!progress.allManuscripts){
    reason=`还有 ${progress.sceneList.filter(scene=>!scene.current_revision_id).length} 个场景没有已采纳正文`;
  }else if(stage==='release'&&progress.pending){
    reason='先审查并采纳或退回待决定的候选';
  }
  return{allowed,reason,progress};
}
function navigateToStage(stage,{quiet=false}={}){
  const gate=stageGate(stage);
  if(!gate.allowed){
    if(!quiet)toast(`尚未解锁「${stageLabel(stage)}」：${gate.reason}`,true);
    return false;
  }
  state.stage=stage;
  state.mobileView='writing';
  render();
  return true;
}
function renderWorkflowGuide(){
  const guide=$('#workflowGuide');
  if(!guide)return;
  if(!state.work){guide.innerHTML='';return;}
  const progress=workflowProgress();
  const nextStage=FLOW_STAGES.find(stage=>!progress.done[stage])||'release';
  const nextGate=stageGate(nextStage);
  const completed=FLOW_STAGES.filter(stage=>progress.done[stage]).length;
  const modeText=usesGuidedWorkflow()?'引导模式':'里程碑模式';
  const detail=progress.done.release
    ?'当前正文已经形成冻结发布版本。正文出现新修订时，会重新回到全篇审查。'
    :`${nextGate.reason}。完成后，系统会解锁下一步。`;
  guide.innerHTML=`<p>${modeText} · ${completed}/5</p><h2>${progress.done.release?'发布版本已冻结':`现在完成：${stageLabel(nextStage)}`}</h2><small>${detail}</small>${progress.done.release?'':`<button class="guide-action" type="button" data-guide-next="${nextStage}">去完成这一步</button>`}`;
  $$('[data-stage]').forEach(button=>{
    const stage=button.dataset.stage;
    const gate=stageGate(stage);
    const small=button.querySelector('small');
    if(small&&!button.dataset.stageDescription)button.dataset.stageDescription=small.textContent;
    const complete=Boolean(progress.done[stage]);
    const isNext=stage===nextStage&&!complete;
    button.disabled=!gate.allowed;
    button.classList.toggle('is-complete',complete);
    button.classList.toggle('is-next',isNext);
    button.setAttribute('aria-disabled',String(!gate.allowed));
    button.title=gate.allowed?(complete?'已完成，可随时返回查看':'可进入此阶段'):gate.reason;
    if(small){
      small.textContent=complete?`已完成 · ${button.dataset.stageDescription}`:gate.allowed?`可进行 · ${button.dataset.stageDescription}`:`待解锁 · ${gate.reason}`;
    }
  });
  const production=$('[data-section="production"]');
  if(production){
    const releaseGate=stageGate('release');
    production.classList.toggle('locked-nav',!releaseGate.allowed);
    production.title=releaseGate.allowed?'打开检查与发布':'检查与发布将在完成逐场写作后开放';
  }
}
const renderBeforeWorkflowGuard=render;
render=function(){renderBeforeWorkflowGuard();renderWorkflowGuide();syncWorkbenchGuards()};
function syncWorkbenchGuards(){
  const blueprintForm=$('#blueprintReviewForm');
  if(blueprintForm){
    const confirmButton=blueprintForm.querySelector('button[name="review_action"][value="confirm"]');
    if(confirmButton)confirmButton.disabled=!blueprintForm.querySelector('input[name="character_card_ids"]:checked');
  }
  if(state.stage==='draft'){
    const contextNote=$('.scene-context-head p:last-child'),legacyMode=$('.context-mode.legacy');
    if(contextNote&&legacyMode){
      contextNote.textContent='系统会按已确认方向中的角色、全部已确认世界设定和资料自动装配。保存选择后，可固定为本场的明确范围。';
      legacyMode.textContent='自动范围';
    }
  }
  if(state.context&&state.inspector==='context'){
    const modeItem=$$('.context-list li').find(item=>item.textContent.trim().startsWith('单一模式'));
    const modeValue=modeItem?.querySelector('b');
    if(modeValue)modeValue.textContent=sceneModeLabel(state.context.rules.mode_key||selectedScene()?.contract?.writing_mode||brief()?.mode);
  }
}
document.addEventListener('change',event=>{
  if(event.target.matches('#blueprintReviewForm input[name="character_card_ids"]'))syncWorkbenchGuards();
},true);
document.addEventListener('click',event=>{
  const button=event.target.closest('button');
  if(!button)return;
  const stage=button.dataset.guideNext||button.dataset.stage||button.dataset.stageJump||(button.dataset.scene||button.dataset.sceneOpen?'draft':'');
  if(!stage)return;
  const gate=stageGate(stage);
  if(!gate.allowed){
    event.preventDefault();
    event.stopImmediatePropagation();
    toast(`尚未解锁「${stageLabel(stage)}」：${gate.reason}`,true);
    return;
  }
  if(button.dataset.guideNext){
    event.preventDefault();
    event.stopImmediatePropagation();
    state.stage=stage;
    state.mobileView='writing';
    render();
  }
},true);

// These product surfaces are deliberately isolated from the older, monolithic
// event handler above while the workbench is being expanded incrementally.
function stageLabel(stage){return({overview:'作品总览',brief:'写作想法',blueprint:'故事方向',structure:'章节与场景',draft:'逐场写作',references:'创作资料',release:'检查并发布'})[stage]||'写作'}
document.addEventListener('click',async event=>{
  const button=event.target.closest('button');
  if(!button)return;
  if(['works','writing','production','tasks'].includes(button.dataset.section)){
    event.preventDefault();event.stopImmediatePropagation();
    if(button.dataset.section==='works'){state.mobileView='writing';state.stage='overview';state.inspector='agent'}
    else if(button.dataset.section==='tasks'){state.mobileView='tasks'}
    else {state.mobileView='writing';state.stage=button.dataset.section==='production'?'release':'structure'}
    $$('.primary-nav .nav-item').forEach(item=>item.classList.toggle('active',item===button));
    render();return;
  }
  if(button.dataset.section==='references'){
    event.preventDefault();event.stopImmediatePropagation();
    state.stage='references';state.mobileView='writing';
    $$('.primary-nav .nav-item').forEach(item=>item.classList.toggle('active',item===button));
    render();return;
  }
  if(button.dataset.action==='review-release'){
    event.preventDefault();event.stopImmediatePropagation();
    try{
      const result=await api(`/works/${state.work.id}/release:review`,{method:'POST',body:JSON.stringify({expected_version:state.work.version})});
      state.work=result.work;toast(result.status==='passed'?'全篇审查通过，可以冻结':'全篇审查发现待处理项');render();
    }catch(error){toast(error.message,true)}
    return;
  }
  if(button.dataset.resolveFinding){
    event.preventDefault();event.stopImmediatePropagation();
    const note=prompt('请说明为什么处理这条审查发现：','已修改正文，将在下一次场景审查中复核');
    if(!note)return;
    try{
      const result=await api(`/works/${state.work.id}/findings/${button.dataset.resolveFinding}/resolve`,{method:'POST',body:JSON.stringify({expected_version:state.work.version,note})});
      state.work=result.work;toast('审查发现已记录为已处理；正文变更后请重新审查');render();
    }catch(error){toast(error.message,true)}
    return;
  }
  if(button.dataset.action!=='review-scene')return;
  event.preventDefault();event.stopImmediatePropagation();
  try{
    const scene=selectedScene();
    const result=await api(`/works/${state.work.id}/scenes/${scene.id}/review`,{method:'POST',body:JSON.stringify({expected_version:state.work.version})});
    state.work=result.work;state.inspector='decision';
    toast(result.findings.length?`审查完成：${result.findings.length} 项发现`:'审查通过，未发现问题');render();
  }catch(error){toast(error.message,true)}
},true);
document.addEventListener('submit',async event=>{
  const form=event.target;
  if(!['canonForm','characterForm','referenceForm','agentRunForm'].includes(form.id))return;
  event.preventDefault();event.stopImmediatePropagation();
  const fields=new FormData(form);
  try{
    if(form.id==='agentRunForm'){
      const scene=selectedScene();
      const endpoint=form.dataset.agentMode==='rewrite'?'agent:rewrite':'agent:run';
      const result=await api(`/works/${state.work.id}/scenes/${scene.id}/${endpoint}`,{method:'POST',body:JSON.stringify({expected_version:state.work.version,instruction:fields.get('instruction')})});
      state.work=result.work;state.inspector='decision';toast('BA 场景 Agent 已提交一次候选，等待你的决定');render();return;
    }
    let path,payload,success;
    if(form.id==='canonForm'){
      path=`/works/${state.work.id}/canon`;
      payload={expected_version:state.work.version,facts:[{text:fields.get('text'),source:fields.get('source'),confidence_status:'confirmed',scope:'work'}]};success='事实已确认并保存';
    }else if(form.id==='characterForm'){
      path=`/works/${state.work.id}/character-cards`;
      payload={expected_version:state.work.version,name:fields.get('name'),voice_anchors:[fields.get('voice')],ooc_constraints:[fields.get('ooc')],source_refs:[fields.get('source')],trust_status:'confirmed'};success='人物卡已保存';
    }else{
      path=`/works/${state.work.id}/reference-files`;
      payload={expected_version:state.work.version,title:fields.get('title'),source_label:fields.get('source_label'),content:fields.get('content'),trust_status:'unverified'};success='资料已登记';
    }
    const result=await api(path,{method:'POST',body:JSON.stringify(payload)});
    state.work=result.work;form.reset();toast(success);render();
  }catch(error){toast(error.message,true)}
},true);

function openStructureDialog(kind,chapterId='',volumeId=''){
  const dialog=$('#structureDialog'),form=$('#structureForm');
  if(!dialog||!form)return;
  form.reset();form.elements.kind.value=kind;form.elements.chapter_id.value=chapterId;form.elements.volume_id.value=volumeId;
  if(form.elements.writing_mode)form.elements.writing_mode.value=blueprint()?.decision?.mode||brief()?.mode||'bond_short';
  const scene=kind==='scene',volume=kind==='volume';
  $('#structureDialogKicker').textContent=scene?'SCENE':volume?'VOLUME':'CHAPTER';
  $('#structureDialogTitle').textContent=scene?'建立一个场景':volume?'建立一个卷':'建立一个章节';
  $('#structureDialogNote').textContent=scene?'填写地点和本场变化，系统会保存稳定 Scene ID。':volume?'卷是长篇结构的正式层级，建立时会同时创建第一章占位。':'章节是场景的容器，建立后可以继续添加场景。';
  $('#sceneStructureFields').classList.toggle('dialog-fields-hidden',!scene);
  dialog.showModal();
  setTimeout(()=>form.elements.title.focus(),0);
}

document.addEventListener('click',event=>{
  const button=event.target.closest('button');if(!button)return;
  if(button.dataset.structureChapterMove){event.preventDefault();event.stopImmediatePropagation();moveDraftChapter(button.dataset.chapterId,button.dataset.structureChapterMove);return}
  if(button.dataset.structureSceneMove){event.preventDefault();event.stopImmediatePropagation();moveDraftScene(button.dataset.sceneId,button.dataset.structureSceneMove);return}
  if(button.dataset.structureReset!==undefined){event.preventDefault();event.stopImmediatePropagation();resetStructureDraft();render();return}
  if(button.dataset.structureSave!==undefined){event.preventDefault();event.stopImmediatePropagation();(async()=>{try{const draft=structureDraft(),result=await api(`/works/${state.work.id}/structure:reorder`,{method:'POST',body:JSON.stringify({expected_version:state.work.version,chapter_ids:draft.chapter_ids,scene_placements:draft.scene_placements})});state.work=result.work;resetStructureDraft();toast(result.changed?'章节安排已保存；全篇审查需要重新运行。':'章节安排没有变化。');render()}catch(error){toast(error.message,true)}})();return}
  if(button.dataset.structureAddChapter!==undefined){event.preventDefault();event.stopImmediatePropagation();openStructureDialog('chapter','',button.dataset.structureAddChapter);return}
  if(button.dataset.structureAddVolume!==undefined){event.preventDefault();event.stopImmediatePropagation();openStructureDialog('volume');return}
  if(button.dataset.structureAddScene){event.preventDefault();event.stopImmediatePropagation();openStructureDialog('scene',button.dataset.structureAddScene);return}
  if(button.dataset.libraryTarget){state.libraryView=button.dataset.libraryTarget}
  if(button.dataset.openContextCharacters!==undefined){event.preventDefault();event.stopImmediatePropagation();state.stage='references';state.mobileView='writing';state.libraryView='characters';state.editCardId='';state.editCard=null;state.prefillCharacter=(brief()?.characters||[])[0]||'';state.sceneContextEditorOpen=false;render();setTimeout(()=>$('#libraryCharacterForm input[name="name"]')?.focus(),0);return}
  if(button.dataset.toggleSceneContext!==undefined){event.preventDefault();event.stopImmediatePropagation();state.sceneContextEditorOpen=!state.sceneContextEditorOpen;render();return}
  if(button.dataset.toggleSceneContract!==undefined){event.preventDefault();event.stopImmediatePropagation();state.sceneContractOpen=!state.sceneContractOpen;render();return}
  if(button.dataset.closeStructureDialog!==undefined){event.preventDefault();event.stopImmediatePropagation();$('#structureDialog')?.close();return}
  if(button.dataset.action==='add-chapter'){event.preventDefault();event.stopImmediatePropagation();openStructureDialog('chapter');return}
  if(button.dataset.addScene){event.preventDefault();event.stopImmediatePropagation();openStructureDialog('scene',button.dataset.addScene);return}
},true);

document.addEventListener('change',event=>{
  const select=event.target.closest('select[data-structure-scene-target]');if(!select)return;
  event.preventDefault();event.stopImmediatePropagation();placeDraftScene(select.dataset.structureSceneTarget,select.value);
},true);

function decorateLibrary(){
  if(state.stage!=='references')return;
  const host=$('.library-page-head');
  if(!host)return;
  if(state.libraryView==='overview'){
    const cards=libraryCards(),officialCount=cards.filter(card=>card.source_type==='official_reference').length,customCount=cards.filter(card=>card.source_type==='custom').length,legacyCount=cards.length-officialCount-customCount;
    const summary=$('.library-summary[data-library-view="characters"] b');
    if(summary&&legacyCount)summary.textContent=`${officialCount} 张原作参考 · ${customCount} 张自定义 · ${legacyCount} 张旧版未标注`;
  }
  if(state.libraryView==='characters'&&state.editCardId){
    const form=$('#libraryCharacterForm'),card=libraryCards().find(item=>item.id===state.editCardId);
    if(form&&card){
      const actions=document.createElement('div');actions.className='library-inline-actions';actions.innerHTML=`<button class="danger" type="button" data-archive-card="${esc(card.id)}" ${card.status==='archived'?'disabled':''}>${card.status==='archived'?'已归档':'归档人物卡'}</button><button class="quiet" type="button" data-card-history="${esc(card.id)}">${state.historyCardId===card.id?'收起历史':'查看历史修订'}</button>`;form.querySelector('.actions')?.append(actions);
      if(state.historyCardId===card.id){const history=document.createElement('div');history.className='revision-history';history.innerHTML=card.revisions.map(rev=>`<div><b>修订 ${rev.ordinal}</b><small>${esc(rev.created_at)} · ${esc(rev.created_by)} · ${esc(rev.content_hash)}</small></div>`).join('')||'<p>暂无历史修订。</p>';form.append(history)}
    }
  }
  if(state.libraryView==='characters'&&state.characterCardDraft&&!state.editCardId){
    const form=$('#libraryCharacterForm'),draft=state.characterCardDraft;
    if(form){
      form.elements.source_type.value=draft.source_type||'official_reference';
      form.elements.trust_status.value='open';
      form.elements.name.value=draft.name||'';
      form.elements.canonical_name.value=draft.canonical_name||'';
      form.elements.role.value=draft.role||'';
      form.elements.voice.value=(draft.voice_anchors||[]).join('\n');
      form.elements.boundary.value=draft.knowledge_boundary||'';
      form.elements.ooc.value=(draft.ooc_constraints||[]).join('\n');
      form.elements.relationships.value=(draft.relationships||[]).map(item=>`${item.target} | ${item.kind} | ${item.summary}`).join('\n');
      form.elements.source.value=(draft.source_refs||[]).join('；');
      form.elements.name.focus();
      state.characterCardDraft=null;
      toast('已建立待核对人物卡草稿；确认身份和设定后，才能进入 Agent。');
    }
  }
  if(state.libraryView==='canon'){
    const form=$('#workCanonForm'),fact=(workCanon().facts||[]).find(item=>item.id===state.editCanonFactId);
    if(form&&fact){
      form.elements.text.value=fact.text||'';
      form.elements.source.value=fact.source||'';
      form.elements.confidence_status.value=fact.confidence_status||'open';
      form.elements.scope.value=fact.scope||'work';
      const archive=document.createElement('button');
      archive.className='danger';
      archive.type='button';
      archive.dataset.archiveCanonFact=fact.id;
      archive.textContent='归档事实';
      form.querySelector('.actions')?.append(archive);
    }
    if(form&&state.canonHistoryOpen){
      const artifact=workCanonArtifact(),history=document.createElement('div');
      history.className='revision-history';
      history.innerHTML=(artifact?.revisions||[]).map(rev=>`<div><b>事实集修订 ${rev.ordinal}</b><small>${esc(rev.created_at)} · ${esc(rev.created_by)} · ${esc(rev.content_hash)}</small></div>`).join('')||'<p>暂无历史修订。</p>';
      form.append(history);
    }
  }
  if(state.libraryView==='world'&&state.worldCardDraft&&!state.editWorldEntry){
    const form=$('#worldEntityForm'),draft=state.worldCardDraft;
    if(form){form.elements.kind.value=draft.kind||'custom';form.elements.source_type.value=draft.source_type||'custom';form.elements.name.value=draft.name||'';form.elements.summary.value=draft.summary||'';form.elements.aliases.value=(draft.aliases||[]).join('、');form.elements.source.value=draft.source||'';form.elements.confidence_status.value=draft.confidence_status||'open';form.elements.participants.value=(draft.participants||[]).join('、');form.elements.name.focus();state.worldCardDraft=null;if(draft.source)toast('已带入原作资料；请核对后决定本作采用的定义。')}
  }
  if(state.libraryView==='world'){
    const form=$('#worldEntityForm'),currentId=state.editWorldEntry?.type==='entity'?state.editWorldEntry.id:'';
    if(form){
      const selected=new Set(worldBible().entities?.find(item=>item.id===currentId)?.related_world_ids||[]),available=(worldBible().entities||[]).filter(item=>item.status!=='archived'&&item.id!==currentId),picker=document.createElement('fieldset');
      picker.className='world-link-picker';picker.innerHTML=`<legend>关联的世界观卡 <small>保存后会在知识图中形成真实连线</small></legend>${available.length?available.map(item=>`<label><input type="checkbox" name="related_world_ids" value="${esc(item.id)}" ${selected.has(item.id)?'checked':''}><span>${esc(item.name)}<small>${worldKindLabel(item.kind)}</small></span></label>`).join(''):'<p>还没有其他世界观卡。</p>'}`;
      form.querySelector('.actions')?.before(picker);
      const linkedCharacters=new Set((worldBible().entities?.find(item=>item.id===currentId)?.participants||[])),characterCards=libraryCards().filter(card=>card.status!=='archived'),characterPicker=document.createElement('fieldset');
      characterPicker.className='world-link-picker character-link-picker';characterPicker.innerHTML=`<legend>关联的人物卡 <small>保存后显示在知识图，可被场景上下文按需选择</small></legend>${characterCards.length?characterCards.map(card=>`<label><input type="checkbox" name="world_character_card_ids" value="${esc(card.id)}" ${linkedCharacters.has(card.name)?'checked':''}><span>${esc(card.name)}<small>${libraryKindLabel(card.source_type)} · ${trustLabel(card.trust_status)}</small></span></label>`).join(''):'<p>先建立人物卡，再将角色与这张设定卡连起来。</p>'}`;
      form.querySelector('.actions')?.before(characterPicker);
    }
  }
  if(state.libraryView==='world'&&state.editWorldEntry?.type==='entity'&&state.worldHistoryOpen){
    const artifact=state.work.artifacts.find(item=>item.kind==='world_bible'),form=$('#worldEntityForm');
    if(artifact&&form){const history=document.createElement('div');history.className='revision-history';history.innerHTML=(artifact.revisions||[]).map(rev=>`<div><b>世界观修订 ${rev.ordinal}</b><small>${esc(rev.created_at)} · ${esc(rev.created_by)} · ${esc(rev.content_hash)}</small></div>`).join('')||'<p>暂无世界观修订。</p>';form.append(history)}
  }
  if(state.libraryView==='rules'){$$('.world-rule').forEach((row,index)=>{const entry=worldBible().rules?.filter(item=>item.status!=='archived')?.[index];if(!entry)return;const actions=document.createElement('div');actions.className='entry-actions';actions.innerHTML=`<button class="quiet" type="button" data-edit-world-entry="rule:${esc(entry.id)}">编辑</button>`;row.append(actions)})}
  if(state.libraryView==='timeline'){$$('.timeline-event').forEach((row,index)=>{const entry=worldBible().timeline?.filter(item=>item.status!=='archived')?.[index];if(!entry)return;const actions=document.createElement('div');actions.className='entry-actions';actions.innerHTML=`<button class="quiet" type="button" data-edit-world-entry="event:${esc(entry.id)}">编辑</button>`;row.append(actions)})}
  if(state.libraryView==='official'){
    const rows=$$('.official-record');
    rows.forEach((row,index)=>{row.hidden=index>=state.officialReferenceLimit});
    if(rows.length>state.officialReferenceLimit){const more=document.createElement('button');more.className='quiet official-more';more.type='button';more.dataset.officialMore='';more.textContent=`再显示 ${Math.min(6,rows.length-state.officialReferenceLimit)} 条（共 ${rows.length} 条）`;$('.official-reference-workbench')?.append(more)}
  }
}

document.addEventListener('submit',async event=>{
  if(event.target.id!=='structureForm')return;
  event.preventDefault();event.stopImmediatePropagation();
  const form=event.target,fields=new FormData(form),kind=String(fields.get('kind'));
  try{
    let result;
    if(kind==='volume'){
      result=await api(`/works/${state.work.id}/volumes`,{method:'POST',body:JSON.stringify({expected_version:state.work.version,title:fields.get('title')})});
      toast('卷与第一章占位已建立');
    }else if(kind==='chapter'){
      result=await api(`/works/${state.work.id}/chapters`,{method:'POST',body:JSON.stringify({expected_version:state.work.version,title:fields.get('title'),volume_id:fields.get('volume_id')})});
      toast('章节已建立');
    }else{
      result=await api(`/works/${state.work.id}/chapters/${fields.get('chapter_id')}/scenes`,{method:'POST',body:JSON.stringify({expected_version:state.work.version,title:fields.get('title'),goal:fields.get('goal'),location:fields.get('location'),writing_mode:fields.get('writing_mode'),forbidden_reveals:splitLines(fields.get('forbidden_reveals'))})});
      state.sceneId=result.scene_id;toast('场景已建立');
    }
    state.work=result.work;$('#structureDialog').close();state.stage=kind==='scene'?'draft':'structure';render();
  }catch(error){toast(error.message,true)}
},true);

function sceneModeOptions(selected){return ['bond_short','main_battle','long_comedy','text_reading'].map(mode=>`<option value="${mode}" ${mode===selected?'selected':''}>${esc(sceneModeLabel(mode))}</option>`).join('')}
function sceneContractForm(scene){const contract=scene.contract||{},writingMode=contract.writing_mode||brief()?.mode||'bond_short';return `<section class="scene-contract-editor"><div><p class="eyebrow">SCENE CONTRACT</p><h3>编辑本场契约</h3><p>这里定义接下来生成时的地点、目标、已知事实和揭示边界，不会改动已有正文。</p></div><form id="sceneContractForm"><label>场景标题<input name="title" required value="${esc(scene.title)}"></label><label>发生地点<input name="location" value="${esc(contract.location||'')}" placeholder="例如：游戏开发部活动室"></label><label>本场目标<textarea name="goal" required placeholder="本场结束时，具体什么发生了变化？">${esc(contract.goal||'')}</textarea></label><label class="scene-mode-field">本场起草重心<select name="writing_mode">${sceneModeOptions(writingMode)}</select><small>作品可以混合推进；本场的 Agent 和候选生成只读取这一套规则包。</small></label><label>已知事实（每行一条）<textarea name="known_facts" placeholder="只写本场开始前已经成立的事实。">${esc((contract.known_facts||[]).join('\n'))}</textarea></label><label>禁止提前揭示（每行一条）<textarea name="forbidden_reveals" placeholder="例如：匿名发件人的身份。">${esc((contract.forbidden_reveals||[]).join('\n'))}</textarea></label><label>停止边界<textarea name="stop_boundary" required placeholder="达到什么状态就必须收束？">${esc(contract.stop_boundary||'')}</textarea></label><div class="contract-warning">${pendingProposal()?'保存后，当前待处理候选会标为“已替代”，不能再采纳。':'保存后会用于之后的上下文装配、Agent 和候选生成。'}</div><div class="actions"><button class="primary" type="submit">保存场景契约</button><button class="quiet" type="button" data-toggle-scene-contract>取消</button></div></form></section>`}
function sceneScriptArtifact(scene){return state.work?.artifacts.find(item=>item.kind==='scene_script'&&item.scope_id===scene?.id)}
function makeClientBlockId(){state.manuscriptBlockCounter+=1;return `block-${Date.now().toString(36)}-${state.manuscriptBlockCounter.toString(36)}`}
function blockRowMarkup(block,index){const type=block.type==='dialogue'?'dialogue':'action',speaker=block.speaker||'';return `<article class="manuscript-block ${type==='action'?'is-action':''}" data-manuscript-block data-block-id="${esc(block.id)}" data-block-type="${type}"><div class="block-gutter"><span>${String(index+1).padStart(2,'0')}</span><div class="block-move"><button type="button" class="icon-button" title="上移此块" aria-label="上移此块" data-manuscript-move="up">↑</button><button type="button" class="icon-button" title="下移此块" aria-label="下移此块" data-manuscript-move="down">↓</button></div></div><div class="block-fields"><div class="block-meta"><select name="type" aria-label="正文块类型"><option value="action" ${type==='action'?'selected':''}>动作</option><option value="dialogue" ${type==='dialogue'?'selected':''}>对白</option></select><input name="speaker" value="${esc(speaker)}" placeholder="说话人" aria-label="说话人" ${type==='action'?'disabled':''}></div><textarea name="text" aria-label="正文内容" placeholder="${type==='dialogue'?'角色说的话':'动作、环境或叙述'}">${esc(block.text||'')}</textarea></div><button type="button" class="icon-button block-remove" title="删除此块" aria-label="删除此块" data-manuscript-remove>×</button></article>`}
function manuscriptBlocks(content){return Array.isArray(content?.blocks)?content.blocks:[]}
function manuscriptMarkup(scene,artifact){const revision=artifact?.current_revision,blocks=manuscriptBlocks(revision?.content),baseRevision=revision?.id||'';return `<section class="manuscript-desk"><div class="desk-head manuscript-head"><div><p class="eyebrow">MANUSCRIPT / ${revision?`修订 ${revision.ordinal}`:'尚未建立修订'}</p><h3>正文</h3><p class="manuscript-meta">${revision?`基准 ${esc(revision.id)}`:'可以先手工建立第一份正文；保存后才会产生修订。'}</p></div><div class="desk-tools"><span id="manuscriptSaveState" class="manuscript-state ${state.manuscriptDirty?'dirty':'saved'}">${state.manuscriptDirty?'未保存修改':'已保存'}</span><button class="quiet" type="button" data-action="assemble-context">上下文</button><button class="quiet" type="button" data-action="generate-candidate">生成候选</button></div></div><form id="sceneManuscriptForm" data-base-revision="${esc(baseRevision)}"><div class="manuscript-toolbar"><div><b>结构化正文</b><small>每个动作或对白都有稳定 ID。手工保存会建立新修订，不会改写历史。</small></div><div class="manuscript-add"><button class="quiet" type="button" data-manuscript-add="dialogue">新增对白</button><button class="quiet" type="button" data-manuscript-add="action">新增动作</button></div></div><div class="script-sheet block-editor-list" data-manuscript-list>${blocks.length?blocks.map(blockRowMarkup).join(''):'<div class="manuscript-empty" data-manuscript-empty><b>这场还没有正文</b><span>先新增一段动作或对白，再保存第一份修订。</span></div>'}</div><div class="desk-actions manuscript-actions"><p>手工修改不会调用 Agent；若存在基于旧正文的候选，保存后会明确标为已替代。</p><button class="primary" type="submit">保存为新正文修订</button></div></form></section>`}
function renderDraft(el){const scene=selectedScene(),proposal=pendingProposal(),findings=(state.work.review_findings||[]).filter(f=>f.scene_id===scene?.id&&f.status==='open');if(!scene){el.innerHTML=frame('04 / SCENE DRAFT','还没有可写的场景','先建立章节和场景，再开始本场工作。','<button class="primary" data-stage-jump="structure">建立场景</button>');return}if(state.manuscriptSceneId!==scene.id){state.manuscriptSceneId=scene.id;state.manuscriptDirty=false}const manuscript=sceneScriptArtifact(scene),current=manuscript?.current_revision?.content?.text||'',blocker=findings.find(f=>f.severity==='blocking'),warning=findings.find(f=>f.severity==='warning'),headline=proposal?'有一份候选等待决定':blocker?'先处理本场阻塞项':warning?'先补齐本场依据':current?'正文已就绪，可开始下一步':'先装配上下文，准备本场';const action=proposal?`<button class="primary" data-focus-candidate>查看候选与 Diff</button>`:blocker?`<button class="primary" data-resolve-finding="${blocker.id}">处理阻塞项</button>`:warning?`<button class="primary" data-action="open-character-card">补齐人物卡</button>`:current?`<button class="primary" data-action="generate-candidate">生成下一份模拟候选</button>`:`<button class="primary" data-action="assemble-context">装配本场上下文</button>`;el.innerHTML=`<div class="scene-workbench"><header class="scene-head"><div><p class="eyebrow">SCENE / ${esc(scene.id)}</p><h2>${esc(scene.title)}</h2><p>${esc(scene.chapterTitle)} <span>·</span> ${esc(scene.contract.location||'地点待定')} <span>·</span> ${esc(scene.contract.goal||'场景目标待定')}</p></div><button class="scene-contract ${state.sceneContractOpen?'active':''}" data-toggle-scene-contract>${state.sceneContractOpen?'收起契约':'场景契约'}</button></header>${state.sceneContractOpen?sceneContractForm(scene):''}<section class="next-command ${blocker?'blocked':warning?'attention':''}"><div><small>当前下一步</small><strong>${headline}</strong><p>${blocker?esc(blocker.message):warning?esc(warning.message):proposal?'候选不会自动写入正文。请检查 Diff 后采纳、局部修改或退回。':current?'可先检查连续性，也可以让系统提出一份新的候选。':'将固定本场合同、单一 BA 模式和人物卡修订。'}</p></div><div class="command-actions">${action}${current&&!proposal?'<button class="quiet" data-action="review-scene">检查本场</button>':''}</div></section>${proposal?`<section class="candidate-desk"><div class="desk-head"><div><p class="eyebrow">PROPOSAL / 未写入</p><h3>候选与 Diff</h3></div><span class="status-chip amber">等待决定</span></div><div class="proposal-layout"><label>候选正文<textarea class="editor" id="candidateText">${esc(proposal.candidate)}</textarea></label><div><p class="diff-label">与当前稿件的差异</p><pre class="diff-view">${proposal.diff.map(line=>`<span class="${line.startsWith('+')?'diff-add':line.startsWith('-')?'diff-del':''}">${esc(line)}</span>`).join('\n')}</pre></div></div><div class="desk-actions"><button class="primary" data-accept="${proposal.id}">采纳为新正文</button><button class="quiet" data-reject="${proposal.id}">退回候选</button></div></section>`:manuscriptMarkup(scene,manuscript)}</div>`}
function sceneWorldItems(){const world=worldBible();return [...(world.entities||[]).map(item=>({...item,_collection:'entities',label:item.name||''})),...(world.rules||[]).map(item=>({...item,_collection:'rules',label:item.text||''})),...(world.timeline||[]).map(item=>({...item,_collection:'timeline',label:item.text||''}))].filter(item=>item.status!=='archived')}
function sceneContextSelection(scene){return scene?.contract?.context_selection||{mode:'legacy',character_card_ids:[],world_item_ids:[],reference_file_ids:[]}}
function contextPill(label,items){return `<div class="context-mini-group"><b>${label}</b><span>${items.length?items.map(esc).join('、'):'未选择'}</span></div>`}
function decorateSceneContext(){
  if(state.stage!=='draft'||state.mobileView!=='writing')return;
  const activeScene=selectedScene(),sceneHeadMeta=$('.scene-head > div > p:last-child');
  if(activeScene&&sceneHeadMeta&&!sceneHeadMeta.querySelector('[data-scene-mode]')){
    const mode=document.createElement('span');
    mode.dataset.sceneMode='';mode.className='scene-mode-inline';
    mode.textContent=` · ${sceneModeLabel(activeScene.contract?.writing_mode||brief()?.mode||'bond_short')}`;
    sceneHeadMeta.append(mode);
  }
  const scene=selectedScene(),host=$('.scene-workbench');if(!scene||!host)return;
  const selection=sceneContextSelection(scene),explicit=selection.mode==='explicit',cards=libraryCards().filter(card=>card.status!=='archived'),worldItems=sceneWorldItems(),files=state.work.reference_files||[];
  const cardById=new Map(cards.map(card=>[card.id,card])),worldById=new Map(worldItems.map(item=>[item.id,item])),fileById=new Map(files.map(file=>[file.id,file]));
  const legacyCharacters=(brief()?.characters||[]),summaryCards=explicit?selection.character_card_ids.map(id=>cardById.get(id)?.name||id):legacyCharacters,summaryWorld=explicit?selection.world_item_ids.map(id=>worldById.get(id)?.label||id):worldItems.filter(item=>item.confidence_status==='confirmed').map(item=>item.label),summaryFiles=explicit?selection.reference_file_ids.map(id=>fileById.get(id)?.title||id):files.map(file=>file.title);
  const confirmedCards=cards.filter(card=>card.trust_status==='confirmed');
  const editor=state.sceneContextEditorOpen?(confirmedCards.length?`<form id="sceneContextForm" class="scene-context-form"><p class="context-form-note">选择后，场景只读取这些条目。人物卡必须是“已确认”，世界设定必须是“已确认且未归档”；证据资料可以为空。</p><fieldset><legend>人物卡 <small>至少选择一张</small></legend>${cards.map(card=>`<label class="context-check ${card.trust_status==='confirmed'?'':'disabled'}"><input type="checkbox" name="character_card_ids" value="${esc(card.id)}" ${(explicit?selection.character_card_ids:cards.filter(item=>legacyCharacters.includes(item.name)&&item.trust_status==='confirmed').map(item=>item.id)).includes(card.id)?'checked':''} ${card.trust_status==='confirmed'?'':'disabled'}><span><b>${esc(card.name)}</b><small>${esc(libraryKindLabel(card.source_type))} · ${esc(trustLabel(card.trust_status))}</small></span></label>`).join('')}</fieldset><fieldset><legend>世界设定 <small>可留空</small></legend>${worldItems.length?worldItems.map(item=>`<label class="context-check ${item.confidence_status==='confirmed'?'':'disabled'}"><input type="checkbox" name="world_item_ids" value="${esc(item.id)}" ${(explicit?selection.world_item_ids:worldItems.filter(entry=>entry.confidence_status==='confirmed').map(entry=>entry.id)).includes(item.id)?'checked':''} ${item.confidence_status==='confirmed'?'':'disabled'}><span><b>${esc(item.label)}</b><small>${esc(worldKindLabel(item.kind)||item._collection)} · ${esc(confidenceLabel(item.confidence_status))}</small></span></label>`).join(''):'<p class="context-empty">暂无世界观条目；可以在资料库中添加。</p>'}</fieldset><fieldset><legend>证据资料 <small>可留空</small></legend>${files.length?files.map(file=>`<label class="context-check"><input type="checkbox" name="reference_file_ids" value="${esc(file.id)}" ${(explicit?selection.reference_file_ids:files.map(entry=>entry.id)).includes(file.id)?'checked':''}><span><b>${esc(file.title)}</b><small>${esc(referenceTrustLabel(file.trust_status))} · ${esc(file.source_label)}</small></span></label>`).join(''):'<p class="context-empty">暂无资料文件；可以在资料库中登记或导入原作摘录。</p>'}</fieldset><div class="actions"><button class="primary" type="submit">保存本场上下文</button><button class="quiet" type="button" data-toggle-scene-context>取消</button></div></form>`:`<div class="scene-context-blocked"><b>先建立本场人物卡</b><p>这场的 Brief 提到了 ${esc(legacyCharacters.join('、')||'角色')}，但资料库还没有已确认的人物卡。先补齐角色声音和知情边界，才能固定本场读取范围。</p><button class="primary" type="button" data-open-context-characters>去建立人物卡</button></div>`):'';
  const section=document.createElement('section');section.className='scene-context-panel';section.innerHTML=`<div class="scene-context-head"><div><p class="eyebrow">SCENE CONTEXT</p><h3>本场上下文</h3><p>${explicit?'以下选择已保存，只会影响下一次装配和生成。':'当前仍使用旧作品的兼容规则：按 Brief 角色、全部已确认世界观和全部资料装配。保存后可固定本场范围。'}</p></div><button class="quiet" data-toggle-scene-context>${state.sceneContextEditorOpen?'收起编辑':'编辑本场上下文'}</button></div><div class="scene-context-summary"><span class="context-mode ${explicit?'explicit':'legacy'}">${explicit?'已固定范围':'兼容范围'}</span>${contextPill('人物卡',summaryCards)}${contextPill('世界设定',summaryWorld)}${contextPill('证据资料',summaryFiles)}</div>${editor}`;
  const anchor=$('.next-command',host);anchor?.after(section);
}
document.addEventListener('click',event=>{const button=event.target.closest('button');if(!button)return;if(button.dataset.action==='open-character-card'){event.preventDefault();event.stopImmediatePropagation();const scene=selectedScene(),finding=(state.work.review_findings||[]).find(item=>item.scene_id===scene?.id&&item.kind==='character_card_missing'&&item.status==='open');state.prefillCharacter=finding?.evidence?.speakers?.[0]||'';state.stage='references';state.mobileView='writing';state.libraryView='characters';render();setTimeout(()=>$('#libraryCharacterForm input[name="name"]')?.focus(),0)}if(button.dataset.focusCandidate){event.preventDefault();event.stopImmediatePropagation();$('#candidateText')?.focus()}if(button.dataset.agentInstruction){event.preventDefault();event.stopImmediatePropagation();const input=$('#agentRunForm textarea[name="instruction"]');if(input){input.value=button.dataset.agentInstruction;input.focus()}}},true);
function markManuscriptDirty(){state.manuscriptDirty=true;const badge=$('#manuscriptSaveState');if(badge){badge.textContent='未保存修改';badge.className='manuscript-state dirty'}}
function normalizeManuscriptRows(){const rows=$$('[data-manuscript-block]');rows.forEach((row,index)=>{const indexLabel=$('.block-gutter>span',row);if(indexLabel)indexLabel.textContent=String(index+1).padStart(2,'0');const type=$('select[name="type"]',row)?.value||'action';row.dataset.blockType=type;row.classList.toggle('is-action',type==='action');const speaker=$('input[name="speaker"]',row);if(speaker){speaker.disabled=type==='action';if(type==='action')speaker.value=''}});const empty=$('[data-manuscript-empty]');if(empty)empty.remove()}
function addManuscriptBlock(type){const list=$('[data-manuscript-list]');if(!list)return;const temp=document.createElement('div');temp.innerHTML=blockRowMarkup({id:makeClientBlockId(),type,text:'',speaker:''},$$('[data-manuscript-block]',list).length);const row=temp.firstElementChild;list.append(row);normalizeManuscriptRows();markManuscriptDirty();$('textarea[name="text"]',row)?.focus()}
function readManuscriptBlocks(){return $$('[data-manuscript-block]').map(row=>{const type=$('select[name="type"]',row)?.value||'action',text=$('textarea[name="text"]',row)?.value||'',block={id:row.dataset.blockId,type,text};if(type==='dialogue')block.speaker=$('input[name="speaker"]',row)?.value||'';return block})}
document.addEventListener('click',event=>{const button=event.target.closest('button');if(!button)return;if(button.dataset.manuscriptAdd){event.preventDefault();event.stopImmediatePropagation();addManuscriptBlock(button.dataset.manuscriptAdd);return}if(button.dataset.manuscriptRemove!==undefined){event.preventDefault();event.stopImmediatePropagation();const row=button.closest('[data-manuscript-block]');if(!row)return;row.remove();normalizeManuscriptRows();if(!$$('[data-manuscript-block]').length){const list=$('[data-manuscript-list]');if(list)list.innerHTML='<div class="manuscript-empty" data-manuscript-empty><b>正文已清空</b><span>至少新增一个动作或对白，才能保存为新修订。</span></div>'}markManuscriptDirty();return}if(button.dataset.manuscriptMove){event.preventDefault();event.stopImmediatePropagation();const row=button.closest('[data-manuscript-block]');if(!row)return;const sibling=button.dataset.manuscriptMove==='up'?row.previousElementSibling:row.nextElementSibling;if(sibling?.matches('[data-manuscript-block]')){button.dataset.manuscriptMove==='up'?sibling.before(row):sibling.after(row);normalizeManuscriptRows();markManuscriptDirty()}return}},true);
document.addEventListener('change',event=>{const field=event.target.closest('[data-manuscript-block] select[name="type"]');if(!field)return;normalizeManuscriptRows();markManuscriptDirty()},true);
document.addEventListener('input',event=>{if(event.target.closest('[data-manuscript-block]'))markManuscriptDirty()},true);
document.addEventListener('submit',async event=>{const form=event.target;if(form.id!=='sceneManuscriptForm')return;event.preventDefault();event.stopImmediatePropagation();try{const scene=selectedScene(),blocks=readManuscriptBlocks();if(!blocks.length)throw new Error('请先新增至少一个动作或对白块。');setBusy('正在保存新的正文修订');const result=await api(`/works/${state.work.id}/scenes/${scene.id}/manuscript`,{method:'POST',body:JSON.stringify({expected_version:state.work.version,expected_base_revision_id:form.dataset.baseRevision||null,blocks})});state.work=result.work;state.manuscriptDirty=false;toast(result.superseded_proposal_ids?.length?'正文已保存为新修订；旧候选已替代。':'正文已保存为新修订');render()}catch(error){setBusy('正文未保存');toast(error.message,true)}},true);
function renderInspector(){const el=$('#inspectorContent'),scene=selectedScene(),proposal=pendingProposal(),latest=state.work?.releases?.[0],findings=(state.work?.review_findings||[]).filter(item=>item.scene_id===scene?.id&&item.status==='open'),blocker=findings.find(item=>item.severity==='blocking'),warning=findings.find(item=>item.severity==='warning');$$('[data-inspector]').forEach(button=>button.classList.toggle('active',button.dataset.inspector===state.inspector));if(state.inspector==='decision'){const message=proposal?'候选已经生成，正文尚未改变。请检查 Diff 后决定。':blocker?blocker.message:warning?warning.message:state.stage==='release'&&latest?'当前发布版本已完成交接。':'当前场景没有待处理阻塞项。';const action=proposal?'候选等待决定':blocker?'处理阻塞项':warning?'补齐人物卡后重新审查':'可以生成候选或检查本场';el.innerHTML=`<div class="inspector-body"><p class="eyebrow">SCENE DECISION</p><h3>${esc(action)}</h3><div class="notice ${blocker?'bad':warning?'':'good'}">${esc(message)}</div><ul class="context-list"><li><b>当前场景</b><br>${esc(scene?.title||'未选择')}</li><li><b>审查状态</b><br>${blocker?'存在阻塞项':warning?'存在提示项':'没有开放发现'}</li><li><b>写入规则</b><br>Agent 只能提交 Proposal，用户采纳后才建立修订。</li></ul></div>`}else if(state.inspector==='context'){const c=state.context;el.innerHTML=`<div class="inspector-body"><p class="eyebrow">PINNED CONTEXT</p><h3>本场固定输入</h3><p>${scene?`${esc(scene.chapterTitle)} / ${esc(scene.title)}`:'未选择场景'}</p><ul class="context-list">${c?`<li>规则包<br><b>${esc(c.rules.pack_version)}</b></li><li>单一模式<br><b>${esc(c.rules.mode)}</b></li><li>固定输入修订<br><b>${c.source_revision_ids.length} 个</b></li><li>运行时人物卡<br><b>${c.runtime_character_cards.length} 张</b></li>`:'<li>执行“装配上下文”后查看本场固定输入。</li>'}</ul></div>`}else{const existing=scene?.current_revision_id,latestRun=(state.work?.agent_runs||[]).find(run=>run.scope_id===scene?.id),mode=existing?'rewrite':'draft',missingCharacters=(warning?.kind==='character_card_missing'?warning.evidence?.speakers||[]:[]),agentReady=!proposal&&!missingCharacters.length;const chips=existing?`<div class="agent-chips"><button type="button" class="quiet" data-agent-instruction="调整本场节奏：压缩解释，让动作和停顿先出现。">调整节奏</button><button type="button" class="quiet" data-agent-instruction="检查人物是否 OOC，并把需要调整的对白改写为更符合人物卡的表达。">检查 OOC</button><button type="button" class="quiet" data-agent-instruction="重写选中对白：保留本场事实、角色关系和停止边界。">重写选中对白</button></div>`:'';const blocked=missingCharacters.length?`<div class="notice bad">还不能运行：${esc(missingCharacters.join('、'))} 尚无已确认人物卡。补齐后才能把正文与人物约束一起交给 Agent。</div><button type="button" class="primary" data-agent-complete-cards>补齐人物卡</button>`:'';el.innerHTML=`<div class="inspector-body"><p class="eyebrow">BA WRITING AGENT</p><h3>${existing?'改写当前场景':'起草当前场景'}</h3><p>${existing?'当前正文会作为固定输入，Agent 只返回完整场景候选和 Diff，不会直接改动任何一句。':'只读取本场合同、单一 BA 模式和运行时人物卡；每次只提交一份 Proposal。'}</p>${latestRun?`<section class="agent-run"><b>${esc(latestRun.status)}</b><p>工具记录 ${latestRun.tool_calls.length} 项${latestRun.proposal_id?` · Proposal ${esc(latestRun.proposal_id)}`:''}</p></section>`:''}${blocked}<form id="agentRunForm" data-agent-mode="${mode}"><label>本场指令<textarea name="instruction" placeholder="${existing?'例如：压缩解释，保留爱丽丝先观察、凯伊后补充的节奏':'例如：以爱丽丝先观察、凯伊后补充的节奏起草本场'}" ${agentReady?'':'disabled'}></textarea></label>${agentReady?chips:''}<button class="primary" type="submit" ${agentReady?'':'disabled'}>${existing?'生成完整改写候选':'运行 BA 场景 Agent'}</button></form><p class="form-note">${existing?'完整候选不会写回正文，采纳后才建立新的正文修订。':'当前仅有明确标注的 Fake Provider；真实模型尚未接入。'}</p></div>`}}

// The library is a source-of-truth work surface, not a loose notes page.  It
// intentionally keeps original references distinct from work-local invention.
function libraryCards(){return state.work?.artifacts.filter(item=>item.kind==='character_card'&&item.current_revision?.content).map(item=>({id:item.scope_id,artifactId:item.id,...item.current_revision.content,revision:item.current_revision.ordinal,revisions:item.revisions||[]}))||[]}
function workCanon(){return artifact('work_canon')||{facts:[]}}
function workCanonArtifact(){return state.work?.artifacts.find(item=>item.kind==='work_canon')}
function worldBible(){return artifact('world_bible')||{title:'作品世界观',source_type:'custom',entities:[],rules:[],timeline:[]}}
function libraryKindLabel(type){return({official_reference:'原作参考',custom:'自定义设定',mixed:'原作参考 + 自定义',ba_starter:'BA 起始架构'})[type]||'旧版未标注'}
function trustLabel(status){return({confirmed:'可用于写作',open:'待核对',inferred:'推断待确认',unverified:'未核验',conflict:'存在冲突'})[status]||'待核对'}
function confidenceLabel(status){return({confirmed:'已确认',open:'待决定',inferred:'推断',unverified:'未核验',conflict:'存在冲突',retired:'已废弃'})[status]||'待决定'}
function referenceTrustLabel(status){return({official_reference:'原作摘录',confirmed:'已确认',open:'待核对',inferred:'推断待确认',unverified:'未核验',conflict:'存在冲突'})[status]||'待核对'}
function worldKindLabel(kind){return({place:'地点',academy:'学院',organization:'组织',object:'物件',technology:'技术',custom:'本作原创'})[kind]||'设定'}
function normalizedSearch(value){return String(value||'').trim().toLocaleLowerCase('zh-CN')}
function includesSearch(values,query){const needle=normalizedSearch(query);return !needle||values.some(value=>normalizedSearch(value).includes(needle))}
function matchesTrust(status,filter){if(filter==='all')return true;if(filter==='confirmed')return status==='confirmed';return status!=='confirmed'}
function libraryToolbar({query,placeholder,queryName,filters=[]}){return `<div class="asset-toolbar"><label class="asset-search"><span>搜索</span><input name="${queryName}" value="${esc(query)}" placeholder="${esc(placeholder)}"></label><div class="asset-filters">${filters.map(filter=>`<label><span>${esc(filter.label)}</span><select data-library-filter-key="${esc(filter.key)}">${filter.options.map(option=>`<option value="${esc(option.value)}" ${filter.value===option.value?'selected':''}>${esc(option.label)}</option>`).join('')}</select></label>`).join('')}</div></div>`}
function relationRows(cards){return cards.flatMap(card=>(card.relationships||[]).map(link=>({...link,from:card.name,from_id:card.id,to_id:cards.find(item=>item.name===link.target)?.id||''})).filter(link=>link.to_id))}
function graphRecords(cards,world){return [...cards.map(card=>({id:`character:${card.id}`,type:'character',label:card.name,meta:`${libraryKindLabel(card.source_type)} · ${trustLabel(card.trust_status)}`,target:card.id})),...(world.entities||[]).filter(item=>item.status!=='archived').map(item=>({id:`entity:${item.id}`,type:'entity',label:item.name,meta:`${worldKindLabel(item.kind)} · ${confidenceLabel(item.confidence_status)}`,target:item.id})),...(world.rules||[]).filter(item=>item.status!=='archived').map(item=>({id:`rule:${item.id}`,type:'rule',label:item.text,meta:`${item.category} · ${confidenceLabel(item.confidence_status)}`,target:item.id})),...(world.timeline||[]).filter(item=>item.status!=='archived').map(item=>({id:`event:${item.id}`,type:'event',label:item.text,meta:`${item.category} · ${confidenceLabel(item.confidence_status)}`,target:item.id}))]}
function graphLinks(cards,world){const byName=new Map(cards.map(card=>[card.name,card])),worldById=new Map((world.entities||[]).map(item=>[item.id,item])),worldPairs=new Set();const links=relationRows(cards).map(link=>({from:`character:${link.from_id}`,to:`character:${link.to_id}`,kind:link.kind,summary:link.summary||'关系说明待补充'}));for(const [prefix,items] of [['entity',world.entities||[]],['rule',world.rules||[]],['event',world.timeline||[]]])for(const item of items)for(const participant of item.participants||[]){const card=byName.get(participant);if(card)links.push({from:`character:${card.id}`,to:`${prefix}:${item.id}`,kind:'关联',summary:item.summary||item.text||item.name})}for(const item of world.entities||[])for(const targetId of item.related_world_ids||[]){const target=worldById.get(targetId),pair=[item.id,targetId].sort().join('|');if(target&&!worldPairs.has(pair)){worldPairs.add(pair);links.push({from:`entity:${item.id}`,to:`entity:${targetId}`,kind:'设定关联',summary:`${item.name} 与 ${target.name} 的显式关联`})}}return links}
function unconfirmedWorldCards(world){return (world.entities||[]).filter(item=>item.status!=='archived'&&item.confidence_status!=='confirmed')}
function worldCardPayload(current,card){return {title:current.title,source_type:current.source_type,entities:(current.entities||[]).map(item=>item.id===card.id?card:item),rules:current.rules||[],timeline:current.timeline||[]}}
function cardLinkedWorldIds(card,world){return (world.entities||[]).filter(item=>(item.participants||[]).includes(card.name)&&item.status!=='archived').map(item=>item.id)}
function renderReferences(el){
  const view=state.libraryView||'overview',allCards=libraryCards(),cards=allCards.filter(card=>state.libraryCharacterFilter==='all'||card.status!=='archived'),archived=allCards.filter(card=>card.status==='archived'),canon=workCanon(),canonFacts=(canon.facts||[]).filter(item=>item.status!=='archived'),world=worldBible(),files=state.work.reference_files||[],relations=relationRows(cards),official=cards.filter(card=>card.source_type==='official_reference').length,custom=cards.filter(card=>card.source_type==='custom').length,legacy=cards.filter(card=>!['official_reference','custom'].includes(card.source_type)).length,officialFiles=files.filter(file=>file.trust_status==='official_reference'),worldCards=(world.entities||[]).filter(item=>item.status!=='archived'),worldRules=(world.rules||[]).filter(item=>item.status!=='archived'),worldTimeline=(world.timeline||[]).filter(item=>item.status!=='archived'),graphNodes=graphRecords(cards,world),graphEdges=graphLinks(cards,world);
  const nav=[['overview','资料总览',['overview']],['characters','角色卡',['characters']],['world','世界观',['world','rules','timeline']],['canon','作品事实',['canon']],['relations','关系图',['relations']],['files','证据资料',['files','official']]].map(([id,label,views])=>`<button class="library-nav-item ${views.includes(view)?'active':''}" data-library-view="${id}">${label}</button>`).join('');
  const worldSubnav=`<nav class="library-subnav" aria-label="世界库分类"><button class="${view==='world'?'active':''}" data-library-view="world">设定卡</button><button class="${view==='rules'?'active':''}" data-library-view="rules">世界规则</button><button class="${view==='timeline'?'active':''}" data-library-view="timeline">时间线</button></nav>`;
  const sourceSubnav=`<nav class="library-subnav" aria-label="证据资料分类"><button class="${view==='files'?'active':''}" data-library-view="files">已存资料</button><button class="${view==='official'?'active':''}" data-library-view="official">检索 BA 原作</button></nav>`;
  let body='';
  if(view==='overview'){const pendingWorld=unconfirmedWorldCards(world),nextWorld=pendingWorld[0],nextCharacter=cards.find(card=>card.trust_status!=='confirmed');body=`<section class="library-brief"><div><p class="eyebrow">CREATIVE BIBLE</p><h3>这里是作品的设定控制台</h3><p>人物、BA 世界观、本作私设、长期事实和证据分开管理。每项都有来源、确认状态和修订历史；只有已确认条目会进入下一场的受控 Agent。</p></div><div class="library-metrics"><b>${cards.length}<small>人物卡</small></b><b>${worldCards.length}<small>世界设定</small></b><b>${canonFacts.length}<small>作品事实</small></b><b>${graphEdges.length}<small>已登记关系</small></b></div></section><section class="library-control-deck"><div class="library-control-copy"><p class="eyebrow">NEXT DECISION</p><h3>${nextWorld?`先确认「${esc(nextWorld.name)}」在本作中的定义`:nextCharacter?`补齐「${esc(nextCharacter.name)}」的人物边界`:'资料库已具备可写基础'}</h3><p>${nextWorld?'BA 起始卡只是可编辑目录，不会冒充官方设定。打开后补充本作定义、证据和角色关联，再决定是否让它进入 Agent。':nextCharacter?'人物卡必须明确声音、知情范围和 OOC 红线；确认后才会成为可选的场景上下文。':'现在可以检查知识图和场景上下文，决定哪些已确认资料给下一场使用。'}</p><div class="actions">${nextWorld?`<button class="primary" data-edit-world-entry="entity:${esc(nextWorld.id)}">打开待核对世界卡</button><button class="quiet" data-library-view="official">检索 BA 原作证据</button>`:nextCharacter?`<button class="primary" data-library-view="characters">管理人物卡</button><button class="quiet" data-library-view="relations">检查关系图</button>`:`<button class="primary" data-library-view="relations">打开关系图</button><button class="quiet" data-stage-jump="draft">配置场景上下文</button>`}</div></div><ol class="library-decision-queue"><li><span>${pendingWorld.length}</span><div><b>待核对世界卡</b><small>确认来源与本作采用范围</small></div><button class="quiet" data-library-view="world">处理</button></li><li><span>${cards.filter(card=>card.trust_status!=='confirmed').length}</span><div><b>待核对人物卡</b><small>补齐声音、边界与关系</small></div><button class="quiet" data-library-view="characters">处理</button></li><li><span>${graphNodes.filter(node=>!graphEdges.some(edge=>edge.from===node.id||edge.to===node.id)).length}</span><div><b>尚未连线的条目</b><small>在关系图检查孤立设定</small></div><button class="quiet" data-library-view="relations">查看</button></li></ol></section><section class="library-summary-grid"><button class="library-summary" data-library-view="characters"><span>人物库</span><b>${official} 张原作参考 · ${custom} 张自定义</b><small>${cards.filter(card=>card.trust_status!=='confirmed').length} 张尚未确认；可管理人格、声音、边界与关系。</small></button><button class="library-summary" data-library-view="world"><span>世界库</span><b>${worldCards.length} 张设定卡 · ${worldRules.length} 条规则</b><small>${pendingWorld.length} 张待核对；BA 底稿与本作私设可以并存。</small></button><button class="library-summary" data-library-view="canon"><span>作品事实</span><b>${canonFacts.filter(fact=>fact.confidence_status==='confirmed').length} 条可用于写作 · ${canonFacts.filter(fact=>fact.confidence_status!=='confirmed').length} 条待确认</b><small>记录本作已经发生或明确成立的长期事实，不与世界设定混在一起。</small></button><button class="library-summary" data-library-view="relations"><span>关系图</span><b>${graphNodes.length} 个节点 · ${graphEdges.length} 条明确关系</b><small>查看人物如何连接到世界设定、规则和事件；所有连线都可回到来源编辑。</small></button></section>`;}
  if(view==='canon')body=`<section class="library-page-head"><div><h3>作品事实</h3><p>只保存这部作品已经明确成立、推断中或待决定的长期事实。保存新修订不会覆盖历史；只有“已确认”条目会进入场景上下文。</p></div><span class="source-pill">${canonFacts.length} 条当前事实 · ${canonFacts.filter(fact=>fact.confidence_status==='confirmed').length} 条可用于写作</span></section><div class="world-layout"><section class="world-rules">${canonFacts.length?canonFacts.map(fact=>`<div class="world-rule canon-fact"><span class="confidence ${esc(fact.confidence_status)}">${confidenceLabel(fact.confidence_status)}</span><div><b>${esc(fact.text)}</b><small>${esc(fact.source)} · ${fact.scope==='scene'?'场景':fact.scope==='chapter'?'章节':'作品'}范围</small></div><div class="entry-actions"><button class="quiet" type="button" data-edit-canon-fact="${esc(fact.id)}">编辑</button></div></div>`).join(''):'<div class="library-empty">还没有作品事实。可以登记已经明确成立的事件、身份、状态或不可变约束。</div>'}</section><section class="library-editor"><p class="eyebrow">${state.editCanonFactId?'EDIT FACT':'WORK CANON'}</p><h3>${state.editCanonFactId?'修订作品事实':'新增作品事实'}</h3><form id="workCanonForm"><label>事实内容<textarea name="text" required placeholder="例如：旧机器当前没有接通外部电源。"></textarea></label><label>来源或证据<input name="source" required placeholder="用户确认 / 正文修订 / official-corpus://..."></label><label>可信状态<select name="confidence_status"><option value="confirmed">已确认，可用于写作</option><option value="inferred">推断，等待确认</option><option value="open">尚未决定</option><option value="conflict">存在冲突</option></select></label><label>作用范围<select name="scope"><option value="work">整部作品</option><option value="chapter">当前章节</option><option value="scene">当前场景</option></select></label><div class="actions"><button class="primary" type="submit">${state.editCanonFactId?'保存新修订':'保存作品事实'}</button><button class="quiet" type="button" data-canon-history>${state.canonHistoryOpen?'收起修订历史':'查看修订历史'}</button></div></form></section></div>`;
  if(view==='characters'){
    const visibleCards=cards.filter(card=>(state.librarySourceFilter==='all'||card.source_type===state.librarySourceFilter)&&matchesTrust(card.trust_status,state.libraryStatusFilter)&&includesSearch([card.name,card.canonical_name,card.role,...(card.voice_anchors||[]),...(card.source_refs||[])],state.libraryQuery));
    body=`<section class="library-page-head"><div><h3>人物库</h3><p>原作人物和自定义人物分别管理。先搜索或筛选已有卡；右侧可以新建、修订，原作卡也可复制为本作自定义版本。</p></div><div class="source-count"><span>全部 ${allCards.filter(card=>card.status!=='archived').length}</span><span>原作 ${official}</span><span>自定义 ${custom}</span><span>待核对 ${cards.filter(card=>card.trust_status!=='confirmed').length}</span></div></section>${libraryToolbar({query:state.libraryQuery,queryName:'character_query',placeholder:'搜索名称、别名、职责或来源',filters:[{label:'来源',key:'librarySourceFilter',value:state.librarySourceFilter,options:[{value:'all',label:'全部来源'},{value:'official_reference',label:'原作参考'},{value:'custom',label:'自定义'}]},{label:'状态',key:'libraryStatusFilter',value:state.libraryStatusFilter,options:[{value:'all',label:'全部状态'},{value:'confirmed',label:'可用于写作'},{value:'pending',label:'待核对'}]},{label:'范围',key:'libraryCharacterFilter',value:state.libraryCharacterFilter,options:[{value:'active',label:'当前使用'},{value:'all',label:'含已归档'}]}]})}<div class="asset-primary-actions"><span>找到 ${visibleCards.length} 张人物卡</span><div><button class="quiet" data-library-view="official">从 BA 原作建立</button><button class="primary" data-library-new-card>新建自定义人物</button></div></div><div class="character-library"><section class="character-list">${visibleCards.length?visibleCards.map(card=>`<button class="character-record ${card.status==='archived'?'archived':''} ${card.id===state.editCardId?'active':''}" data-edit-card="${esc(card.id)}"><span class="avatar-token">${esc(card.name.slice(0,1))}</span><span><b>${esc(card.name)}</b><small>${libraryKindLabel(card.source_type)} · r${card.revision} · ${trustLabel(card.trust_status)}</small></span><em>${esc((card.voice_anchors||[])[0]||'待补充声音')}</em></button>`).join(''):'<div class="library-empty"><b>没有符合条件的人物卡</b><span>调整搜索或筛选，或者建立一张新的自定义人物卡。</span></div>'}</section><section class="library-editor"><p class="eyebrow">${state.editCardId?'EDIT CARD':'NEW CARD'}</p><h3>${state.editCardId?`修订「${esc(state.editCard?.name||'人物')}」`:'新建自定义人物'}</h3><p class="editor-guidance">${state.editCardId?'保存会创建新修订；场景上下文仍固定原来的版本，直到下次重新装配。':'先写清角色在本作中的职责、说话方式、知情边界和不能做的事。'}</p><form id="libraryCharacterForm"><input type="hidden" name="card_id" value="${esc(state.editCardId||'')}"><label>来源类型<select name="source_type"><option value="custom" ${state.editCard?.source_type!=='official_reference'?'selected':''}>自定义设定</option><option value="official_reference" ${state.editCard?.source_type==='official_reference'?'selected':''}>原作参考</option></select></label><label>采用状态<select name="trust_status"><option value="confirmed" ${state.editCard?.trust_status==='confirmed'?'selected':''}>已确认，可用于写作</option><option value="open" ${state.editCard?.trust_status!=='confirmed'?'selected':''}>待核对，不进入 Agent</option></select></label><label>显示名称<input name="name" value="${esc(state.editCard?.name||state.prefillCharacter||'')}" required placeholder="例如：爱丽丝 / 原创角色名"></label><label>标准名称或别名<input name="canonical_name" value="${esc(state.editCard?.canonical_name||'')}" placeholder="用于检索和别名统一"></label><label>故事职责<textarea name="role" placeholder="她在这部作品中要推动什么？">${esc(state.editCard?.role||'')}</textarea></label><label>声音锚点<textarea name="voice" placeholder="短句、行动优先；遇到谜题会游戏化命名。">${esc((state.editCard?.voice_anchors||[]).join('\n'))}</textarea></label><label>知情边界<textarea name="boundary" placeholder="此时知道什么，绝对不知道什么。">${esc(state.editCard?.knowledge_boundary||'')}</textarea></label><label>OOC 红线<textarea name="ooc" placeholder="每行一条，例如：不替别人解释隐藏动机。">${esc((state.editCard?.ooc_constraints||[]).join('\n'))}</textarea></label><label>关系（每行：对象 | 关系 | 当前说明）<textarea name="relationships" placeholder="凯伊 | 队友 | 本场互相试探，但仍共同调查。">${esc((state.editCard?.relationships||[]).map(item=>`${item.target} | ${item.kind} | ${item.summary}`).join('\n'))}</textarea></label><label>来源或证据<input name="source" value="${esc((state.editCard?.source_refs||[]).join('；'))}" required placeholder="官方剧情索引 / 用户确认 / 本作设定文档"></label><div class="actions"><button class="primary" type="submit">${state.editCardId?'保存新修订':'建立人物卡'}</button>${state.editCardId&&state.editCard?.source_type==='official_reference'?'<button class="quiet" type="button" data-duplicate-card>复制为自定义</button>':''}<button class="quiet" type="button" data-library-new-card>${state.editCardId?'取消编辑':'清空表单'}</button></div></form></section></div>`;
  }
  if(view==='world'){
    const editing=state.editWorldEntry?.type==='entity',editCard=editing?worldCards.find(card=>card.id===state.editWorldEntry.id):null,starterPresent=worldCards.some(card=>card.id==='ba-starter-kivotos'),starterCount=worldCards.filter(card=>card.source_type==='ba_starter').length;
    const visibleWorldCards=worldCards.filter(card=>(state.worldKindFilter==='all'||card.kind===state.worldKindFilter)&&(state.worldSourceFilter==='all'||card.source_type===state.worldSourceFilter)&&matchesTrust(card.confidence_status,state.worldStatusFilter)&&includesSearch([card.name,card.summary,...(card.aliases||[]),card.source,...(card.participants||[])],state.worldQuery));
    body=`<section class="library-page-head"><div><h3>世界库</h3><p>管理 BA 世界底稿和本作自定义地点、学院、组织、物件与技术。搜索已有设定，确认采用范围，或创建新的世界观卡。</p></div><div class="source-count"><span>全部 ${worldCards.length}</span><span>BA 底稿 ${starterCount}</span><span>自定义 ${worldCards.filter(card=>card.source_type==='custom').length}</span><span>可用于写作 ${worldCards.filter(card=>card.confidence_status==='confirmed').length}</span></div></section>${libraryToolbar({query:state.worldQuery,queryName:'world_query',placeholder:'搜索名称、别名、定义、来源或关联角色',filters:[{label:'类型',key:'worldKindFilter',value:state.worldKindFilter,options:[{value:'all',label:'全部类型'},{value:'academy',label:'学院'},{value:'place',label:'地点'},{value:'organization',label:'组织'},{value:'object',label:'物件'},{value:'technology',label:'技术'},{value:'custom',label:'本作原创'}]},{label:'来源',key:'worldSourceFilter',value:state.worldSourceFilter,options:[{value:'all',label:'全部来源'},{value:'ba_starter',label:'BA 起始架构'},{value:'official_reference',label:'原作参考'},{value:'custom',label:'自定义'},{value:'mixed',label:'混合'}]},{label:'状态',key:'worldStatusFilter',value:state.worldStatusFilter,options:[{value:'all',label:'全部状态'},{value:'confirmed',label:'可用于写作'},{value:'pending',label:'待核对'}]}]})}<section class="world-onboarding"><div><p class="eyebrow">BA WORLD STARTER</p><h4>${starterPresent?'BA 世界观底稿已加入':'以 BA 世界观作为底稿'}</h4><p>${starterPresent?`当前有 ${starterCount} 张 BA 起始卡。它们是待核对的编辑入口，不是已确认的官方事实；逐项打开后补齐本作定义与来源。`:'一次复制一组可编辑的 BA 设定入口：基沃托斯、夏莱、联邦学生会、光环、社团与主要学院。'}</p>${starterPresent?'':`<div class="actions"><button class="primary" data-apply-ba-starter>加入 BA 世界观底稿</button><button class="quiet" data-library-view="official">先检索原作资料</button></div>`}</div><div><p class="eyebrow">CUSTOM WORLD</p><h4>自定义世界与 BA 可以并存</h4><p>私设学院、原创组织、改写地点和技术规则都能单独保存来源与确认状态，不会覆盖 BA 底稿。</p><div class="actions"><button class="primary" data-new-world-card>新建自定义设定</button></div></div></section><div class="asset-primary-actions"><span>找到 ${visibleWorldCards.length} 张世界观卡</span><div><button class="quiet" data-library-view="official">从 BA 原作建立</button><button class="primary" data-new-world-card>新建世界观卡</button></div></div><div class="world-layout"><section class="world-rules">${visibleWorldCards.length?visibleWorldCards.map(card=>`<div class="world-rule world-entity ${card.confidence_status==='open'?'pending':''} ${card.id===state.editWorldEntry?.id?'active':''}"><span class="confidence ${esc(card.confidence_status)}">${worldKindLabel(card.kind)}</span><div><b>${esc(card.name)}</b><p>${esc(card.summary||'尚未补充本作定义')}</p><small>${libraryKindLabel(card.source_type)} · ${confidenceLabel(card.confidence_status)}${card.participants?.length?` · 关联：${esc(card.participants.join('、'))}`:''}</small></div><div class="entry-actions">${card.confidence_status!=='confirmed'?`<button class="quiet" type="button" data-confirm-world-card="${esc(card.id)}">确认采用</button>`:''}<button class="quiet" type="button" data-edit-world-entry="entity:${esc(card.id)}">打开</button></div></div>`).join(''):'<div class="library-empty"><b>没有符合条件的世界观卡</b><span>调整搜索或筛选，也可以直接建立一张自定义设定卡。</span></div>'}</section><section class="library-editor"><p class="eyebrow">${editing?'EDIT WORLD CARD':'WORLD CARD'}</p><h3>${editing?`修订「${esc(editCard?.name||'世界观卡')}」`:'新增世界观卡'}</h3><p class="editor-guidance">${editing?'保存会创建整个 WorldBible 的新修订，并保留这张卡的稳定 ID。':'写清它在本作里是什么、有什么限制、依据来自哪里。'}</p><form id="worldEntityForm"><label>类型<select name="kind"><option value="place" ${editCard?.kind==='place'?'selected':''}>地点</option><option value="academy" ${editCard?.kind==='academy'?'selected':''}>学院</option><option value="organization" ${editCard?.kind==='organization'?'selected':''}>组织</option><option value="object" ${editCard?.kind==='object'?'selected':''}>物件</option><option value="technology" ${editCard?.kind==='technology'?'selected':''}>技术</option><option value="custom" ${editCard?.kind==='custom'?'selected':''}>本作原创</option></select></label><label>来源类型<select name="source_type"><option value="custom" ${editCard?.source_type==='custom'||!editCard?'selected':''}>自定义设定</option><option value="official_reference" ${editCard?.source_type==='official_reference'?'selected':''}>原作参考</option><option value="mixed" ${editCard?.source_type==='mixed'?'selected':''}>两者混合</option><option value="ba_starter" ${editCard?.source_type==='ba_starter'?'selected':''}>BA 起始架构</option></select></label><label>名称<input name="name" required value="${esc(editCard?.name||state.worldCardDraft?.name||'')}" placeholder="例如：夏莱 / 游戏开发部活动室"></label><label>本作定义与限制<textarea name="summary" placeholder="它在本作里是什么，能做什么，限制是什么？">${esc(editCard?.summary||state.worldCardDraft?.summary||'')}</textarea></label><label>别名<input name="aliases" value="${esc((editCard?.aliases||state.worldCardDraft?.aliases||[]).join('、'))}" placeholder="别名用顿号或逗号分隔"></label><label>来源或证据<input name="source" required value="${esc(editCard?.source||state.worldCardDraft?.source||'')}" placeholder="official-corpus://... / 用户确认"></label><label>可信状态<select name="confidence_status"><option value="confirmed" ${editCard?.confidence_status==='confirmed'?'selected':''}>已确认，可用于写作</option><option value="inferred" ${editCard?.confidence_status==='inferred'?'selected':''}>推断</option><option value="open" ${(editCard?.confidence_status||state.worldCardDraft?.confidence_status||'open')==='open'?'selected':''}>待决定，不进入 Agent</option></select></label><label>关联角色<input name="participants" value="${esc((editCard?.participants||state.worldCardDraft?.participants||[]).join('、'))}" placeholder="未建人物卡时可手动填写；已建人物在下方勾选"></label><div class="actions"><button class="primary" type="submit">${editing?'保存新修订':'保存世界观卡'}</button>${editing?`<button class="quiet" type="button" data-world-history>查看历史</button><button class="quiet" type="button" data-new-world-card>取消编辑</button><button class="danger" type="button" data-archive-world-entry="entity:${esc(editCard.id)}" ${editCard.status==='archived'?'disabled':''}>归档条目</button>`:''}</div></form></section></div>`
  }
  if(view==='rules')body=`<section class="library-page-head"><div><h3>世界规则</h3><p>规则表达“在这部作品里什么成立”，而世界观卡表达“有哪些人、地点、组织与物件”。只有已确认规则会进入场景上下文。</p></div><span class="source-pill">${worldRules.length} 条当前规则</span></section><div class="world-layout"><section class="world-rules">${worldRules.length?worldRules.map(rule=>`<div class="world-rule"><span class="confidence ${esc(rule.confidence_status)}">${confidenceLabel(rule.confidence_status)}</span><div><b>${esc(rule.text)}</b><small>${esc(rule.category)} · ${esc(rule.source)}${rule.participants?.length?` · 关联：${esc(rule.participants.join('、'))}`:''}</small></div></div>`).join(''):'<div class="library-empty">还没有规则。建立空间、技术、组织或本作改写边界。</div>'}</section><section class="library-editor"><p class="eyebrow">WORLD RULE</p><h3>新增或修订规则</h3><form id="worldBibleForm"><label>世界观来源<select name="source_type"><option value="custom" ${world.source_type==='custom'?'selected':''}>自定义设定</option><option value="official_reference" ${world.source_type==='official_reference'?'selected':''}>原作参考</option><option value="mixed" ${world.source_type==='mixed'?'selected':''}>两者混合</option></select></label><label>世界观标题<input name="title" value="${esc(world.title)}"></label><label>新增规则<textarea name="rule_text" placeholder="例如：本作的旧游戏机只能在零点后收到匿名指令。"></textarea></label><label>规则分类<input name="rule_category" placeholder="技术 / 组织 / 地点 / 本作改写"></label><label>规则来源<input name="rule_source" placeholder="用户确认 / 官方剧情索引 / 已登记资料"></label><label>可信状态<select name="rule_status"><option value="confirmed">已确认</option><option value="inferred">推断</option><option value="open">待决定</option></select></label><label>关联角色<input name="rule_participants" placeholder="爱丽丝、凯伊；会显示在关系图"></label><div class="actions"><button class="primary" type="submit">保存规则修订</button></div></form></section></div>`;
  if(view==='relations'){
    const typeNodes=state.graphTypeFilter==='all'?graphNodes:graphNodes.filter(node=>node.type===state.graphTypeFilter),focusId=typeNodes.some(node=>node.id===state.graphFocus)?state.graphFocus:'',visibleNodes=focusId?new Set([focusId,...graphEdges.filter(link=>link.from===focusId||link.to===focusId).flatMap(link=>[link.from,link.to])]):new Set(typeNodes.map(node=>node.id)),focusedNode=graphNodes.find(node=>node.id===focusId),focusedEdges=focusId?graphEdges.filter(link=>link.from===focusId||link.to===focusId):graphEdges.filter(link=>visibleNodes.has(link.from)&&visibleNodes.has(link.to)),visibleGraphNodes=graphNodes.filter(node=>visibleNodes.has(node.id));
    const openSource=focusedNode?`<button class="primary" data-open-graph-source="${esc(focusedNode.id)}">打开来源编辑</button>`:'';
    body=`<section class="library-page-head"><div><h3>作品知识图</h3><p>用来检查人物、世界设定、规则和事件有没有真实连接。这里只显示你明确保存过的关系，不让模型猜测；点击节点聚焦，随后可直接打开来源编辑。</p></div><span class="source-pill">${graphNodes.length} 个节点 · ${graphEdges.length} 条明确关系</span></section><div class="graph-toolbar"><div class="segmented-control" aria-label="图谱类型"><button class="${state.graphTypeFilter==='all'?'active':''}" data-graph-type="all">全部</button><button class="${state.graphTypeFilter==='character'?'active':''}" data-graph-type="character">人物</button><button class="${state.graphTypeFilter==='entity'?'active':''}" data-graph-type="entity">世界观</button><button class="${state.graphTypeFilter==='rule'?'active':''}" data-graph-type="rule">规则</button><button class="${state.graphTypeFilter==='event'?'active':''}" data-graph-type="event">时间线</button></div>${focusId?`<div class="graph-focus-actions"><span>已选：<b>${esc(focusedNode.label)}</b></span>${openSource}<button class="quiet" data-clear-graph-focus>返回全图</button></div>`:''}</div><section class="knowledge-map">${graphNodes.length?`<div class="knowledge-map-key"><span class="key-character">人物</span><span class="key-entity">世界观卡</span><span class="key-rule">世界规则</span><span class="key-event">时间线</span></div>${focusId?`<div class="graph-focus-note"><b>正在查看：${esc(focusedNode.label)}</b><span>${focusedEdges.length?`关联 ${focusedEdges.length} 条已保存关系`:'该节点还没有与其他资料建立关系'}</span></div>`:''}<div class="knowledge-canvas"><div class="knowledge-branch character-branch"><p>人物</p>${visibleGraphNodes.filter(node=>node.type==='character').map(node=>`<button class="knowledge-node ${node.type} ${focusId===node.id?'active':''}" data-graph-node="${esc(node.id)}"><span>${esc(node.label.slice(0,1))}</span><b>${esc(node.label)}</b><small>${esc(node.meta)}</small></button>`).join('')||'<span class="branch-empty">当前没有人物节点</span>'}</div><div class="knowledge-hub"><span>CURRENT WORK</span><b>${esc(state.work.title)}</b><small>${graphEdges.length} 条已保存关系</small></div><div class="knowledge-branch world-branch"><p>世界与剧情</p>${visibleGraphNodes.filter(node=>node.type!=='character').map(node=>`<button class="knowledge-node ${node.type} ${focusId===node.id?'active':''}" data-graph-node="${esc(node.id)}"><span>${node.type==='entity'?'设':node.type==='rule'?'规':'事'}</span><b>${esc(node.label)}</b><small>${esc(node.meta)}</small></button>`).join('')||'<span class="branch-empty">当前没有世界或剧情节点</span>'}</div></div><div class="knowledge-links"><h4>${focusId?'关联明细':'当前筛选下的明确关系'}</h4>${focusedEdges.length?focusedEdges.map(link=>`<button class="knowledge-link" data-graph-node="${esc(link.from)}"><b>${esc(graphNodes.find(node=>node.id===link.from)?.label||'')}</b><span>${esc(link.kind)}</span><b>${esc(graphNodes.find(node=>node.id===link.to)?.label||'')}</b><small>${esc(link.summary)}</small></button>`).join(''):'<div class="library-empty">当前没有明确关系。可以在下方登记人物关系，也可以编辑世界观卡，在“关联角色”里选择人物。</div>'}</div>`:'<div class="library-empty">先建立人物卡或世界观卡，知识图会从真实资料自动形成。</div>'}</section><section class="relation-compose"><div><p class="eyebrow">ADD A LINK</p><h4>登记人物关系</h4><p>关系会写入起始人物的人物卡新修订，并立刻成为可追溯连线。</p></div>${allCards.filter(card=>card.status!=='archived').length>1?`<form id="relationForm"><label>起始人物<select name="from_card_id">${allCards.filter(card=>card.status!=='archived').map(card=>`<option value="${esc(card.id)}">${esc(card.name)}</option>`).join('')}</select></label><label>关联人物<select name="to_card_id">${allCards.filter(card=>card.status!=='archived').map(card=>`<option value="${esc(card.id)}">${esc(card.name)}</option>`).join('')}</select></label><label>关系类型<input name="kind" required placeholder="例如：队友 / 对手 / 同社团"></label><label>当前说明<input name="summary" placeholder="这部作品当前阶段的关系状态"></label><div class="actions"><button class="primary" type="submit">保存关系</button></div></form>`:'<div class="relation-compose-empty">至少需要两张未归档人物卡，才能登记人物关系。</div>'}</section>`
  }
  if(view==='timeline')body=`<section class="library-page-head"><div><h3>时间线</h3><p>时间线事件与世界规则保存在同一份世界观修订中，供连续性审查和场景上下文引用。</p></div><span class="source-pill">${worldTimeline.length} 个当前事件</span></section><div class="timeline-layout"><section class="timeline-list">${worldTimeline.length?worldTimeline.map((item,index)=>`<div class="timeline-event"><span>${String(index+1).padStart(2,'0')}</span><div><b>${esc(item.text)}</b><small>${esc(item.category)} · ${esc(item.source)} · ${confidenceLabel(item.confidence_status)}${item.participants?.length?` · 关联：${esc(item.participants.join('、'))}`:''}</small></div></div>`).join(''):'<div class="library-empty">没有已登记事件。可以在这里添加过去事件、当前剧情或未来伏笔。</div>'}</section><section class="library-editor"><p class="eyebrow">TIMELINE EVENT</p><h3>添加事件</h3><form id="timelineForm"><label>事件内容<textarea name="event_text" placeholder="例如：零点后，旧游戏机第一次向爱丽丝发出提示。" required></textarea></label><label>事件类型<input name="event_category" placeholder="过去事件 / 当前剧情 / 未来伏笔"></label><label>来源<input name="event_source" required placeholder="用户确认 / 原作剧情索引 / 已登记资料"></label><label>可信状态<select name="event_status"><option value="confirmed">已确认</option><option value="inferred">推断</option><option value="open">待决定</option></select></label><label>关联角色<input name="event_participants" placeholder="爱丽丝、凯伊；会显示在关系图"></label><div class="actions"><button class="primary" type="submit">加入时间线</button></div></form></section></div>`;
  if(view==='files')body=`<section class="library-page-head"><div><h3>证据资料</h3><p>资料文件是证据或创作依据，不会自动变成世界观事实。核对后再从人物库、世界库或作品事实中明确采用。</p></div><span class="source-pill">${files.length} 个文件 · ${officialFiles.length} 个原作摘录</span></section><div class="world-layout"><section class="world-rules">${files.length?files.map(file=>`<div class="world-rule"><span class="confidence ${esc(file.trust_status)}">${referenceTrustLabel(file.trust_status)}</span><div><b>${esc(file.title)}</b><small>${esc(file.kind)} · ${esc(file.source_label)} · v${file.version}</small></div></div>`).join(''):'<div class="library-empty">还没有资料文件。可以手动登记，也可以检索 BA 原作资料并导入摘录。</div>'}</section><section class="library-editor"><p class="eyebrow">REFERENCE FILE</p><h3>登记资料</h3><form id="libraryReferenceForm"><label>资料名称<input name="title" required placeholder="例如：游戏开发部设定摘录"></label><label>来源标签<input name="source_label" required placeholder="官方剧情索引 / 用户导入"></label><label>资料内容<textarea name="content" required placeholder="粘贴可追溯的摘录、作者设定或参考摘要。"></textarea></label><div class="actions"><button class="primary" type="submit">保存资料文件</button></div></form></section></div>`;
  if(view==='official')body=`<section class="library-page-head"><div><h3>BA 原作资料导入</h3><p>在本机只读官方演出语料库中检索。结果可以保留为资料证据，或带来源建立待确认的人物卡、世界观卡；不会直接改写作品事实。</p></div><span class="source-pill">只读语料 · ${officialFiles.length} 个已导入</span></section><section class="official-reference-workbench"><form id="officialReferenceSearchForm" class="official-search"><label>检索原作资料<input name="query" value="${esc(state.officialReferenceQuery)}" required minlength="2" placeholder="例如：爱丽丝、凯伊、基沃托斯、夏莱"></label><button class="primary" type="submit">检索</button></form>${state.officialReferenceSearched?`<p class="search-summary">${state.officialReferenceResults.length?`找到 ${state.officialReferenceResults.length} 条可用资料。先检查故事归属、说话者和中文摘录，再决定要建立哪种资料卡。`:'没有找到匹配资料。可以尝试角色名、故事标题、说话者或地点关键词。'}</p>${state.officialReferenceResults.map(item=>`<article class="official-record"><div><p class="eyebrow">${esc(item.record_uid)}</p><h4>${esc(item.character_name||'未标注角色')} <span>/${esc(item.story_title||'未标注故事')}</span></h4><p class="record-meta">${esc(item.story_category||'未标注类别')} · ${esc((item.speakers||[]).join('、')||'未标注说话者')} · ${esc(item.source_file||item.record_file)}</p><p class="record-excerpt">${esc(item.zh_cn||'该记录未提供官方中文文本；可先导入索引信息，再人工核对。')}</p></div><div class="official-record-actions"><button class="quiet" type="button" data-official-to-character="${esc(item.record_uid)}">建立人物卡草稿</button><button class="quiet" type="button" data-official-to-world="${esc(item.record_uid)}">建立世界观卡草稿</button><button class="quiet" type="button" data-import-official="${esc(item.record_uid)}">导入为资料</button></div></article>`).join('')}`:'<div class="library-empty">输入关键词开始检索。资料导入只在本作品目录创建副本，不会修改原作语料库。</div>'}</section>`;
  if(['world','rules','timeline'].includes(view))body=worldSubnav+body;
  if(['files','official'].includes(view))body=sourceSubnav+body;
  el.innerHTML=`<div class="library-workbench"><header class="library-header"><div><p class="eyebrow">WORK / CREATIVE BIBLE</p><h2>当前作品 · 创作资料</h2><p>角色卡、世界观、作品事实、关系和证据都绑定当前作品。AI 会从讨论与正文中提出维护候选；采纳前不会改写正式资料。</p></div><button class="quiet" data-library-view="overview">资料总览</button></header><div class="library-scope-banner"><div><span>当前作品</span><b>${esc(state.work?.title||'未选择作品')}</b></div><div><span>AI 维护状态</span><small>候选进入 Proposal · 你只在需要时审核</small></div><span class="status-chip">作品范围</span></div><div class="library-layout"><nav class="library-nav" aria-label="当前作品资料分类">${nav}</nav><main class="library-main">${body}</main></div></div>`;
}

function splitLines(value){return String(value||'').split(/\r?\n/).map(item=>item.trim()).filter(Boolean)}
function parseRelationships(value){return splitLines(value).map(line=>{const [target,kind,summary]=line.split('|').map(item=>item.trim());return target?{target,kind:kind||'关系待定',summary:summary||'',status:'confirmed'}:null}).filter(Boolean)}
function splitParticipants(value){return String(value||'').split(/[、,，]/).map(item=>item.trim()).filter(Boolean)}
function mergeSourceTypes(types){const values=new Set(types.filter(type=>['official_reference','custom','mixed','ba_starter'].includes(type)));if(values.has('mixed')||(values.has('official_reference')&&values.has('custom'))||(values.has('ba_starter')&&values.size>1))return'mixed';if(values.has('ba_starter'))return'ba_starter';return values.has('official_reference')?'official_reference':'custom'}
function nextWorldSourceType(current,entity,editing){const remaining=(current.entities||[]).filter(item=>!editing||item.id!==editing.id);const types=remaining.map(item=>item.source_type);if(current.rules?.length||current.timeline?.length||remaining.length)types.push(current.source_type);types.push(entity.source_type);return mergeSourceTypes(types)}
function storeWorldMutation({entity,rule,event,replaceEntity,replaceRule,replaceEvent}){const current=worldBible();let entities=[...(current.entities||[])],rules=[...(current.rules||[])],timeline=[...(current.timeline||[])];if(replaceEntity)entities=entities.map(item=>item.id===replaceEntity.id?replaceEntity:item);else if(entity)entities.push(entity);if(replaceRule)rules=rules.map(item=>item.id===replaceRule.id?replaceRule:item);else if(rule)rules.push(rule);if(replaceEvent)timeline=timeline.map(item=>item.id===replaceEvent.id?replaceEvent:item);else if(event)timeline.push(event);return {title:current.title||'作品世界观',source_type:current.source_type||'custom',entities,rules,timeline}}

document.addEventListener('click',event=>{
  const button=event.target.closest('button');if(!button)return;
  if(button.dataset.openWorkSwitch!==undefined){event.preventDefault();event.stopImmediatePropagation();$('#workSwitchDialog')?.showModal();return}
  if(button.dataset.selectWork){event.preventDefault();event.stopImmediatePropagation();(async()=>{try{const selected=button.dataset.selectWork;state.work=await api('/works/'+selected);state.sceneId=scenes()[0]?.id||null;state.context=null;state.stage='overview';state.surface='works';state.mobileView='writing';$('#workSwitchDialog')?.close();toast(`已切换到《${state.work.title}》`);render()}catch(error){toast(error.message,true)}})();return}
  if(button.dataset.agentCompleteCards!==undefined){event.preventDefault();event.stopImmediatePropagation();state.stage='references';state.mobileView='writing';state.libraryView='characters';state.editCardId='';state.editCard=null;state.prefillCharacter=button.closest('.inspector-body')?.querySelector('.notice.bad')?.textContent.match(/：(.+?) 尚无/)?.[1]?.split('、')[0]||'';render();setTimeout(()=>$('#libraryCharacterForm input[name="name"]')?.focus(),0);return}
  if(button.dataset.characterFilter){event.preventDefault();event.stopImmediatePropagation();state.libraryCharacterFilter=button.dataset.characterFilter;render();return}
  if(button.dataset.duplicateCard!==undefined){event.preventDefault();event.stopImmediatePropagation();const card=state.editCard;if(!card)return;state.characterCardDraft={...card,name:card.name,canonical_name:card.canonical_name,source_type:'custom',trust_status:'open',source_refs:[...(card.source_refs||[]),`复制自人物卡 ${card.id} 修订 ${card.revision}`]};state.editCardId='';state.editCard=null;state.historyCardId='';render();toast('已复制为自定义人物草稿；确认本作改写后再保存。');return}
  if(button.dataset.archiveCard){event.preventDefault();event.stopImmediatePropagation();if(!confirm('归档后，这张人物卡不会进入新的场景上下文，但历史仍会保留。确定继续吗？'))return;(async()=>{try{const result=await api(`/works/${state.work.id}/character-cards/${button.dataset.archiveCard}/archive`,{method:'POST',body:JSON.stringify({expected_version:state.work.version})});state.work=result.work;state.libraryCharacterFilter='all';toast('人物卡已归档');render()}catch(error){toast(error.message,true)}})();return}
  if(button.dataset.cardHistory){event.preventDefault();event.stopImmediatePropagation();state.historyCardId=state.historyCardId===button.dataset.cardHistory?'':button.dataset.cardHistory;render();return}
  if(button.dataset.worldHistory!==undefined){event.preventDefault();event.stopImmediatePropagation();state.worldHistoryOpen=!state.worldHistoryOpen;render();return}
  if(button.dataset.officialMore!==undefined){event.preventDefault();event.stopImmediatePropagation();state.officialReferenceLimit+=6;render();return}
  if(button.dataset.canonHistory!==undefined){event.preventDefault();event.stopImmediatePropagation();state.canonHistoryOpen=!state.canonHistoryOpen;render();return}
  if(button.dataset.editCanonFact){event.preventDefault();event.stopImmediatePropagation();state.editCanonFactId=button.dataset.editCanonFact;state.libraryEditorOpen=true;state.canonHistoryOpen=false;render();return}
  if(button.dataset.archiveCanonFact){event.preventDefault();event.stopImmediatePropagation();if(!confirm('归档后，这条事实不会进入新的场景上下文，但历史修订仍会保留。确定继续吗？'))return;(async()=>{try{const current=workCanon(),facts=(current.facts||[]).map(item=>item.id===button.dataset.archiveCanonFact?{...item,status:'archived'}:item);const result=await api(`/works/${state.work.id}/canon`,{method:'POST',body:JSON.stringify({expected_version:state.work.version,facts})});state.work=result.work;state.editCanonFactId='';toast('作品事实已归档');render()}catch(error){toast(error.message,true)}})();return}
  if(button.dataset.applyBaStarter!==undefined){event.preventDefault();event.stopImmediatePropagation();(async()=>{try{const result=await api(`/works/${state.work.id}/world-bible:starter`,{method:'POST',body:JSON.stringify({expected_version:state.work.version})});state.work=result.work;state.libraryView='world';toast(result.disclosure);render()}catch(error){toast(error.message,true)}})();return}
  if(button.dataset.confirmWorldCard){event.preventDefault();event.stopImmediatePropagation();const current=worldBible(),existing=(current.entities||[]).find(item=>item.id===button.dataset.confirmWorldCard);if(!existing)return;(async()=>{try{const confirmed={...existing,confidence_status:'confirmed'};const result=await api(`/works/${state.work.id}/world-bible`,{method:'POST',body:JSON.stringify({expected_version:state.work.version,...worldCardPayload(current,confirmed)})});state.work=result.work;toast(`已确认「${existing.name}」可用于本作写作。`);render()}catch(error){toast(error.message,true)}})();return}
  if(button.dataset.editWorldEntry){event.preventDefault();event.stopImmediatePropagation();const [type,id]=button.dataset.editWorldEntry.split(':');const current=worldBible(),items=type==='entity'?current.entities:type==='rule'?current.rules:current.timeline,entry=items?.find(item=>item.id===id);if(!entry)return;state.editWorldEntry={type,id};state.libraryEditorOpen=true;state.worldHistoryOpen=false;if(type==='entity'){render();const workspace=$('#workspace'),form=$('#worldEntityForm');if(workspace&&form){const top=form.closest('.library-editor')?.offsetTop||0;workspace.scrollTo({top:Math.max(0,top-24),behavior:'smooth'})}return}const form=type==='rule'?$('#worldBibleForm'):$('#timelineForm');if(!form)return;const prefix=type==='rule'?'rule':'event';form.elements[`${prefix}_text`].value=entry.text||'';form.elements[`${prefix}_category`].value=entry.category||'';form.elements[`${prefix}_source`].value=entry.source||'';form.elements[`${prefix}_status`].value=entry.confidence_status||'confirmed';let participants=form.querySelector(`[name="${prefix}_participants"]`);if(!participants){const label=document.createElement('label');label.className='entry-participants';label.textContent='参与角色（用顿号或逗号分隔）';participants=document.createElement('input');participants.name=`${prefix}_participants`;participants.placeholder='例如：爱丽丝、凯伊';label.append(participants);form.querySelector('.actions')?.before(label)}participants.value=(entry.participants||[]).join('、');let archive=form.querySelector('[data-archive-world-entry]');if(!archive){archive=document.createElement('button');archive.className='danger';archive.type='button';form.querySelector('.actions')?.append(archive)}archive.dataset.archiveWorldEntry=`${type}:${id}`;archive.textContent=entry.status==='archived'?'已归档':'归档条目';archive.disabled=entry.status==='archived';const workspace=$('#workspace');if(workspace){const top=form.closest('.library-editor')?.offsetTop||0;workspace.scrollTo({top:Math.max(0,top-24),behavior:'smooth'})}return}
  if(button.dataset.archiveWorldEntry){event.preventDefault();event.stopImmediatePropagation();const [type,id]=button.dataset.archiveWorldEntry.split(':');const current=worldBible(),items=type==='entity'?[...current.entities]:type==='rule'?[...current.rules]:[...current.timeline],entry=items.find(item=>item.id===id);if(!entry)return;(async()=>{try{const next={...entry,status:'archived'};const payload={expected_version:state.work.version,title:current.title,source_type:current.source_type,entities:type==='entity'?items.map(item=>item.id===id?next:item):current.entities||[],rules:type==='rule'?items.map(item=>item.id===id?next:item):current.rules,timeline:type==='event'?items.map(item=>item.id===id?next:item):current.timeline};const result=await api(`/works/${state.work.id}/world-bible`,{method:'POST',body:JSON.stringify(payload)});state.work=result.work;state.editWorldEntry=null;toast('条目已归档，历史仍会保留');render()}catch(error){toast(error.message,true)}})();return}
  if(button.dataset.officialToWorld){event.preventDefault();event.stopImmediatePropagation();const item=state.officialReferenceResults.find(entry=>entry.record_uid===button.dataset.officialToWorld);if(!item)return;state.worldCardDraft={kind:'custom',source_type:'official_reference',name:item.story_title||item.character_name||'待命名 BA 设定',summary:`基于原作资料 ${item.record_uid} 的待确认世界观卡草稿。请核对摘录上下文，并写明本作中采用的定义、限制或改写边界。原始说话者：${(item.speakers||[]).join('、')||'未标注'}。`,aliases:[],source:item.evidence_uri||`official-corpus:${item.record_uid}`,confidence_status:'open',participants:[]};state.libraryView='world';state.editWorldEntry=null;render();return}
  if(button.dataset.officialToCharacter){event.preventDefault();event.stopImmediatePropagation();const item=state.officialReferenceResults.find(entry=>entry.record_uid===button.dataset.officialToCharacter);if(!item)return;state.characterCardDraft={name:item.character_name||'',canonical_name:item.character_name||'',source_type:'official_reference',trust_status:'open',role:'',voice_anchors:[],knowledge_boundary:'',ooc_constraints:[],relationships:[],source_refs:[item.evidence_uri||`official-corpus:${item.record_uid}`]};state.libraryView='characters';state.editCardId='';state.editCard=null;state.historyCardId='';render();return}
  if(button.dataset.clearGraphFocus!==undefined){event.preventDefault();event.stopImmediatePropagation();state.graphFocus='';render();return}
  if(button.dataset.graphType){event.preventDefault();event.stopImmediatePropagation();state.graphTypeFilter=button.dataset.graphType;state.graphFocus='';render();return}
  if(button.dataset.graphNode){event.preventDefault();event.stopImmediatePropagation();state.graphFocus=button.dataset.graphNode;render();return}
  if(button.dataset.openGraphSource){event.preventDefault();event.stopImmediatePropagation();const [type,id]=button.dataset.openGraphSource.split(':');state.graphFocus='';if(type==='character'){state.libraryView='characters';state.editCardId=id;state.editCard=libraryCards().find(card=>card.id===id)||null;render();return}state.libraryView=type==='entity'?'world':type==='rule'?'rules':'timeline';state.editWorldEntry={type:type==='event'?'event':type,id};render();if(type!=='entity')setTimeout(()=>document.querySelector(`[data-edit-world-entry="${type==='event'?'event':type}:${CSS.escape(id)}"]`)?.click(),0);return}
  if(button.dataset.importOfficial){event.preventDefault();event.stopImmediatePropagation();(async()=>{try{const result=await api(`/works/${state.work.id}/official-references:import`,{method:'POST',body:JSON.stringify({expected_version:state.work.version,record_uid:button.dataset.importOfficial})});state.work=result.work;toast('原作摘录已导入本作品资料库');render()}catch(error){toast(error.message,true)}})();return}
  if(button.dataset.libraryOpenEditor){event.preventDefault();event.stopImmediatePropagation();state.libraryEditorOpen=true;if(button.dataset.libraryOpenEditor==='canon'){state.editCanonFactId='';}if(button.dataset.libraryOpenEditor==='files'){state.libraryView='files';}if(button.dataset.libraryOpenEditor==='timeline'){state.libraryView='timeline';}render();setTimeout(()=>document.querySelector('.library-editor textarea, .library-editor input')?.focus(),0);return}
  if(button.dataset.libraryView){event.preventDefault();event.stopImmediatePropagation();state.libraryView=button.dataset.libraryView;state.libraryEditorOpen=false;state.editCardId='';state.editCard=null;state.characterCardDraft=null;state.editCanonFactId='';state.canonHistoryOpen=false;state.editWorldEntry=null;state.worldCardDraft=null;state.worldHistoryOpen=false;dismissToast();render();return}
  if(button.dataset.newWorldCard!==undefined){event.preventDefault();event.stopImmediatePropagation();const closing=Boolean(state.editWorldEntry||state.worldCardDraft||state.libraryEditorOpen);state.libraryView='world';state.editWorldEntry=null;state.worldCardDraft=closing?null:{};state.libraryEditorOpen=!closing;state.worldHistoryOpen=false;dismissToast();render();if(!closing)setTimeout(()=>$('#worldEntityForm input[name="name"]')?.focus(),0);return}
  if(button.dataset.libraryNewCard!==undefined){event.preventDefault();event.stopImmediatePropagation();const closing=Boolean(state.editCardId||state.characterCardDraft||state.libraryEditorOpen);state.editCardId='';state.editCard=null;state.characterCardDraft=closing?null:{};state.libraryEditorOpen=!closing;state.prefillCharacter='';dismissToast();render();if(!closing)setTimeout(()=>$('#libraryCharacterForm input[name="name"]')?.focus(),0);return}
  if(button.dataset.editCard){event.preventDefault();event.stopImmediatePropagation();state.libraryView='characters';state.characterCardDraft=null;state.editCardId=button.dataset.editCard;state.editCard=libraryCards().find(card=>card.id===button.dataset.editCard)||null;render();return}
},true);
document.addEventListener('change',event=>{
  const select=event.target.closest('select[data-library-filter-key]');if(!select)return;
  const key=select.dataset.libraryFilterKey;
  if(!['librarySourceFilter','libraryStatusFilter','libraryCharacterFilter','worldKindFilter','worldSourceFilter','worldStatusFilter'].includes(key))return;
  state[key]=select.value;render();
},true);
document.addEventListener('input',event=>{
  const input=event.target;if(!input.matches('input[name="character_query"],input[name="world_query"]'))return;
  const key=input.name==='character_query'?'libraryQuery':'worldQuery';state[key]=input.value;
  clearTimeout(state.librarySearchTimer);state.librarySearchTimer=setTimeout(()=>{const cursor=state[key].length;render();const next=document.querySelector(`input[name="${input.name}"]`);next?.focus();next?.setSelectionRange(cursor,cursor)},140);
},true);
document.addEventListener('submit',async event=>{
  if(event.target.id==='sceneContractForm'){
    event.preventDefault();event.stopImmediatePropagation();
    const fields=new FormData(event.target),scene=selectedScene();
    try{
      const result=await api(`/works/${state.work.id}/scenes/${scene.id}/contract`,{method:'POST',body:JSON.stringify({expected_version:state.work.version,title:fields.get('title'),location:fields.get('location'),goal:fields.get('goal'),writing_mode:fields.get('writing_mode'),known_facts:splitLines(fields.get('known_facts')),forbidden_reveals:splitLines(fields.get('forbidden_reveals')),stop_boundary:fields.get('stop_boundary')})});
      state.work=result.work;state.context=null;state.sceneContractOpen=false;toast(result.superseded_proposal_ids?.length?'场景契约已更新，旧候选已替代。':'场景契约已更新，下一次生成会使用新边界。');render();
    }catch(error){toast(error.message,true)}
    return;
  }
  if(event.target.id==='sceneContextForm'){
    event.preventDefault();event.stopImmediatePropagation();
    const fields=new FormData(event.target),ids=name=>fields.getAll(name).map(value=>String(value));
    try{
      const scene=selectedScene();
      const result=await api(`/works/${state.work.id}/scenes/${scene.id}/context:configure`,{method:'POST',body:JSON.stringify({expected_version:state.work.version,character_card_ids:ids('character_card_ids'),world_item_ids:ids('world_item_ids'),reference_file_ids:ids('reference_file_ids')})});
      state.work=result.work;state.context=null;state.sceneContextEditorOpen=false;state.inspector='context';toast('本场上下文已保存；下一次装配和生成将使用这个范围。');render();
    }catch(error){toast(error.message,true)}
    return;
  }
  const form=event.target;if(form.id==='relationForm'){
    event.preventDefault();event.stopImmediatePropagation();const fields=new FormData(form),fromId=String(fields.get('from_card_id')||''),toId=String(fields.get('to_card_id')||'');
    try{if(!fromId||!toId)throw new Error('请选择两张人物卡。');if(fromId===toId)throw new Error('关系两端需要是不同人物。');const from=libraryCards().find(card=>card.id===fromId),to=libraryCards().find(card=>card.id===toId);if(!from||!to)throw new Error('所选人物卡已不存在。');const kind=String(fields.get('kind')||'').trim(),summary=String(fields.get('summary')||'').trim();if(!kind)throw new Error('请填写关系类型。');const existing=(from.relationships||[]).filter(link=>link.target!==to.name);const result=await api(`/works/${state.work.id}/character-cards`,{method:'POST',body:JSON.stringify({expected_version:state.work.version,card_id:from.id,name:from.name,canonical_name:from.canonical_name,source_type:from.source_type,role:from.role,voice_anchors:from.voice_anchors||[],knowledge_boundary:from.knowledge_boundary,ooc_constraints:from.ooc_constraints||[],relationships:[...existing,{target:to.name,kind,summary,status:'confirmed'}],source_refs:from.source_refs||[],trust_status:from.trust_status})});state.work=result.work;toast(`${from.name} 与 ${to.name} 的关系已保存为人物卡新修订。`);render()}catch(error){toast(error.message,true)}
    return;
  }
  if(!['workCanonForm','libraryCharacterForm','worldEntityForm','worldBibleForm','timelineForm','libraryReferenceForm','officialReferenceSearchForm'].includes(form.id))return;
  event.preventDefault();event.stopImmediatePropagation();const fields=new FormData(form);
  try{
    if(form.id==='officialReferenceSearchForm'){const query=String(fields.get('query')||'').trim();const result=await officialReferenceSearch(query);state.officialReferenceQuery=query;state.officialReferenceResults=result.items||[];state.officialReferenceSearched=true;state.officialReferenceLimit=6;render();return}
    let path,payload,success;
    if(form.id==='workCanonForm'){
      const current=workCanon(),existing=(current.facts||[]).find(item=>item.id===state.editCanonFactId);const fact={...(existing||{}),id:existing?.id,text:String(fields.get('text')||'').trim(),source:String(fields.get('source')||'').trim(),confidence_status:fields.get('confidence_status'),scope:fields.get('scope'),status:existing?.status||'active'};const facts=existing?current.facts.map(item=>item.id===existing.id?fact:item):[...(current.facts||[]),fact];path=`/works/${state.work.id}/canon`;payload={expected_version:state.work.version,facts};success=existing?'作品事实已保存为新修订':'作品事实已登记';
    }else if(form.id==='libraryCharacterForm'){
      path=`/works/${state.work.id}/character-cards`;payload={expected_version:state.work.version,card_id:fields.get('card_id'),name:fields.get('name'),canonical_name:fields.get('canonical_name'),source_type:fields.get('source_type'),role:fields.get('role'),voice_anchors:splitLines(fields.get('voice')),knowledge_boundary:fields.get('boundary'),ooc_constraints:splitLines(fields.get('ooc')),relationships:parseRelationships(fields.get('relationships')),source_refs:String(fields.get('source')).split(/[；;]/).map(item=>item.trim()).filter(Boolean),trust_status:fields.get('trust_status')};success=fields.get('trust_status')==='confirmed'?'人物卡已确认并保存为新修订':'人物卡草稿已保存，尚不会进入 Agent';
    }else if(form.id==='worldEntityForm'){
      const edit=state.editWorldEntry?.type==='entity'?state.editWorldEntry:null,current=worldBible(),existing=edit?(current.entities||[]).find(item=>item.id===edit.id):null;
      const selectedCharacterNames=fields.getAll('world_character_card_ids').map(id=>libraryCards().find(card=>card.id===String(id))?.name).filter(Boolean),manualParticipantNames=splitParticipants(fields.get('participants')),participants=[...new Set([...manualParticipantNames,...selectedCharacterNames])];
      const entity={...(existing||{}),id:existing?.id,name:String(fields.get('name')||'').trim(),kind:fields.get('kind'),summary:String(fields.get('summary')||'').trim(),aliases:splitParticipants(fields.get('aliases')),source:String(fields.get('source')||'').trim(),source_type:fields.get('source_type'),confidence_status:fields.get('confidence_status'),participants,related_world_ids:fields.getAll('related_world_ids').map(value=>String(value)),scope:existing?.scope||'work',status:existing?.status||'active'};
      path=`/works/${state.work.id}/world-bible`;payload={expected_version:state.work.version,...storeWorldMutation(edit?{replaceEntity:entity}:{entity}),title:current.title,source_type:nextWorldSourceType(current,entity,edit)};success=edit?'世界观卡已保存为新修订':'世界观卡已保存为新修订';
    }else if(form.id==='worldBibleForm'){
      const text=String(fields.get('rule_text')).trim(),source=String(fields.get('rule_source')).trim();if((text&&!source)||(!text&&source))throw new Error('新增世界规则时，需要同时填写内容和来源。');
      const edit=state.editWorldEntry?.type==='rule'?state.editWorldEntry:null,current=worldBible(),existing=edit?(current.rules||[]).find(item=>item.id===edit.id):null;const rule=text?{...(existing||{}),id:existing?.id,text,category:fields.get('rule_category')||'general',source,confidence_status:fields.get('rule_status'),scope:existing?.scope||'work',participants:splitParticipants(fields.get('rule_participants')),status:existing?.status||'active'}:null;
      path=`/works/${state.work.id}/world-bible`;payload={expected_version:state.work.version,...storeWorldMutation(edit?{replaceRule:rule}:{rule}),title:fields.get('title'),source_type:fields.get('source_type')};success=edit?'世界规则已保存为新修订':'世界观已保存为新修订';
    }else if(form.id==='timelineForm'){
      const edit=state.editWorldEntry?.type==='event'?state.editWorldEntry:null,current=worldBible(),existing=edit?(current.timeline||[]).find(item=>item.id===edit.id):null;const entry={...(existing||{}),id:existing?.id,text:fields.get('event_text'),category:fields.get('event_category')||'当前剧情',source:fields.get('event_source'),confidence_status:fields.get('event_status'),scope:existing?.scope||'work',participants:splitParticipants(fields.get('event_participants')),status:existing?.status||'active'};
      path=`/works/${state.work.id}/world-bible`;payload={expected_version:state.work.version,...storeWorldMutation(edit?{replaceEvent:entry}:{event:entry})};success=edit?'时间线事件已保存为新修订':'事件已加入时间线';
    }else{
      path=`/works/${state.work.id}/reference-files`;payload={expected_version:state.work.version,title:fields.get('title'),source_label:fields.get('source_label'),content:fields.get('content'),trust_status:'unverified'};success='资料文件已登记';
    }
     const result=await api(path,{method:'POST',body:JSON.stringify(payload)});state.work=result.work;state.libraryEditorOpen=false;state.editCardId='';state.editCard=null;state.characterCardDraft=null;state.editCanonFactId='';state.canonHistoryOpen=false;state.editWorldEntry=null;state.worldCardDraft=null;state.worldHistoryOpen=false;state.prefillCharacter='';toast(success);render();
  }catch(error){toast(error.message,true)}
},true);

// Final UI overrides: later declarations in this legacy single-file client are
// intentionally superseded here so the view used by the browser stays coherent.
renderWorkflowGuide=function(){
  const guide=$('#workflowGuide');
  if(guide)guide.replaceChildren();
  if(!state.work)return;
  const progress=workflowProgress();
  const nextStage=FLOW_STAGES.find(stage=>!progress.done[stage])||'release';
  $$('[data-stage]').forEach(button=>{
    const stage=button.dataset.stage,gate=stageGate(stage),small=button.querySelector('small');
    const complete=Boolean(progress.done[stage]),current=stage===state.stage,next=stage===nextStage&&!complete;
    button.disabled=!gate.allowed;
    button.classList.toggle('is-complete',complete);
    button.classList.toggle('is-current',current);
    button.classList.toggle('is-next',next&&!current);
    button.setAttribute('aria-current',current?'step':'false');
    button.setAttribute('aria-disabled',String(!gate.allowed));
    button.title=gate.allowed?(complete?'已完成，可随时查看':'可进入此阶段'):gate.reason;
    if(small)small.textContent=complete?'已完成':current?'正在进行':gate.allowed?'可随时查看':'完成前一步后可继续';
  });
  const releaseGate=stageGate('release');
  $$('[data-section="production"]').forEach(production=>{
    production.classList.toggle('locked-nav',!releaseGate.allowed);
    production.setAttribute('aria-disabled',String(!releaseGate.allowed));
    production.title=releaseGate.allowed?'打开 AA 制作':`AA 制作尚未开放：${releaseGate.reason}`;
  });
};

renderBrief=function(el){
  const b=brief()||{},isSaved=Boolean(brief());
  el.innerHTML=frame('第 1 步 / 5','先把故事开头说清楚','这张写作想法只记录你此刻的创作意图。人物卡、世界观和正文仍在各自的资料与写作页面管理。',`<section class="brief-clarity-band"><div><p class="eyebrow">THIS STEP</p><h3>先回答三个问题，其他设定以后再补。</h3><p>故事要写什么、用什么写法、谁是主要角色。保存后才会解锁故事方向。</p></div><span class="brief-step-state ${isSaved?'is-saved':''}">${isSaved?'已保存，可继续':'等待填写'}</span></section><form id="briefForm" class="brief-form"><label class="brief-idea">一句想法<textarea name="idea" required placeholder="例如：凯伊发现游戏开发部的旧机器在深夜自行启动">${esc(b.idea)}</textarea><small>用一句话说清这部作品最想发生什么。</small></label><div class="brief-core-grid"><label>写作模式<select name="mode"><option value="bond_short" ${b.mode==='bond_short'?'selected':''}>羁绊短场景</option><option value="main_battle" ${b.mode==='main_battle'?'selected':''}>主线与战斗</option><option value="long_comedy" ${b.mode==='long_comedy'?'selected':''}>长篇喜剧</option><option value="text_reading" ${b.mode==='text_reading'?'selected':''}>小说化阅读</option></select></label><label>主要角色<input name="characters" value="${esc((b.characters||[]).join('、'))}" placeholder="爱丽丝、凯伊"><small>用顿号分隔；之后可到人物库完善卡片。</small></label></div><details class="brief-optional" ${b.target_length||b.constraints||b.has_sensei?'open':''}><summary>补充设定（可选）</summary><div class="brief-optional-fields"><label>目标长度<select name="target_length"><option value="short" ${b.target_length==='short'?'selected':''}>短场景</option><option value="chapter" ${b.target_length==='chapter'?'selected':''}>单章</option><option value="long" ${b.target_length==='long'?'selected':''}>长篇</option></select></label><label class="brief-constraint">额外约束<textarea name="constraints" placeholder="不可提前揭示的事实、希望保留的关系距离……">${esc(b.constraints)}</textarea></label><label class="check brief-check"><span><input type="checkbox" name="has_sensei" ${b.has_sensei?'checked':''}> 老师在场</span></label></div></details><div class="brief-actions"><div><b>${isSaved?'修改会新建一份写作想法修订':'保存后不会自动生成正文或改写资料库'}</b><small>${isSaved?'故事方向、章节和场景会继续引用这份简报。':'你仍可随时回到这里修改。'}</small></div><div class="actions"><button class="primary" type="submit">${isSaved?'保存修改':'保存写作想法'}</button>${isSaved?'<button class="quiet" type="button" data-stage-jump="blueprint">下一步：确认故事方向</button>':''}</div></div></form>`);
};

renderOverview=function(el){
  const work=state.work,sceneList=scenes(),total=sceneList.length,drafted=sceneList.filter(scene=>scene.current_revision_id).length,cards=libraryCards(),world=worldBible(),worldEntities=(world.entities||[]).filter(item=>item.status!=='archived'),pending=work.proposals.filter(item=>item.status==='pending').length,blocker=(work.review_findings||[]).filter(item=>item.status==='open'&&item.severity==='blocking').length;
  let next={stage:'brief',title:'先提供一句创作想法',detail:'只要说出想看什么；系统会在下一步提出角色、世界观依据和写作组成候选。',label:'开始写作想法'};
  if(brief()&&!blueprintIsConfirmed())next={stage:'blueprint',title:'审查故事方向候选',detail:'系统先提出角色、写作组成与世界观依据；确认后才会建立章节。',label:'审查故事方向'};
  else if(blueprintIsConfirmed()&&!work.chapters.length)next={stage:'structure',title:'建立章节与场景',detail:'先建立第一章，再把故事拆成有稳定身份的场景。',label:'建立章节与场景'};
  else if(pending)next={stage:'draft',title:'先审查待处理候选',detail:`有 ${pending} 份候选等待你采纳、局部修改或退回。`,label:'查看候选'};
  else if(blocker)next={stage:'draft',title:'处理审查阻塞项',detail:`有 ${blocker} 个阻塞项。处理完成后才可以冻结发布版本。`,label:'处理审查'};
  else if(total&&drafted<total)next={stage:'draft',title:'开始下一场写作',detail:`还有 ${total-drafted} 个场景没有已采纳正文，生成结果会先进入候选审查。`,label:'打开逐场写作'};
  else if(total)next={stage:'release',title:'运行全篇审查',detail:'确认连续性、人物约束和正文修订后，再冻结交给制作的定稿。',label:'检查并发布'};
  const customCards=cards.filter(card=>card.source_type==='custom').length;
  const confirmedWorld=worldEntities.filter(item=>item.confidence_status==='confirmed').length;
  const visibleChapters=work.chapters.filter(chapter=>chapter.scenes.length);
  const libraryReadiness=`<section class="overview-readiness" aria-label="创作资料库">
    <div class="overview-readiness-copy"><p class="eyebrow">CREATIVE LIBRARY</p><h3>创作资料已经独立保存</h3><p>人物、世界设定、作品事实和证据不会混进对话或正文。需要时再进入资料库补齐。</p></div>
    <div class="overview-readiness-actions">
      <button class="readiness-link" data-stage-jump="references" data-library-target="characters"><span>人物卡</span><b>${cards.length} 张${customCards?` · ${customCards} 张自定义`:''}</b><small>${cards.length?'查看角色声音、边界与关系':'添加第一张人物卡'}</small></button>
      <button class="readiness-link" data-stage-jump="references" data-library-target="world"><span>世界设定</span><b>${worldEntities.length} 项${confirmedWorld?` · ${confirmedWorld} 项已确认`:''}</b><small>${worldEntities.length?'查看 BA 底稿与本作私设':'建立世界设定'}</small></button>
    </div>
  </section>`;
  if(!total){
    el.innerHTML=`<div class="overview-workbench overview-start-workbench">
      <header class="overview-header overview-start-header"><div><p class="eyebrow">WORK / START HERE</p><h2>${esc(work.title)}</h2><p>先把这部作品要写什么说清楚。章节、正文和审查会在你确认方向后逐步出现。</p></div></header>
      <section class="overview-start-command"><div><p class="eyebrow">CURRENT DECISION</p><h3>${next.title}</h3><p>${next.detail}</p><button class="primary overview-primary" data-stage-jump="${next.stage}">${next.label}</button></div><ol class="overview-flow-preview" aria-label="写作流程预览"><li class="active"><span>01</span><b>写作想法</b><small>现在开始</small></li><li><span>02</span><b>故事方向</b><small>确认后解锁</small></li><li><span>03</span><b>章节与场景</b><small>方向确定后建立</small></li><li><span>04</span><b>逐场写作与发布</b><small>按场审查，再冻结定稿</small></li></ol></section>
      ${libraryReadiness}
    </div>`;
    return;
  }
  const progress=Math.round(drafted/total*100);
  el.innerHTML=`<div class="overview-workbench overview-progress-workbench">
     <header class="overview-header"><div><p class="eyebrow">WORK OVERVIEW</p><h2>${esc(work.title)}</h2><p>按一个明确的下一步推进；正文、候选和审查决定始终分开保存。</p></div><button class="quiet" data-stage-jump="references">打开创作资料</button></header>
    <section class="overview-next overview-next-calm"><div><p class="eyebrow">NEXT STEP</p><h3>${next.title}</h3><p>${next.detail}</p></div><button class="primary" data-stage-jump="${next.stage}">${next.label}</button></section>
    <div class="overview-progress-line"><b>正文进度 ${progress}%</b><span>${drafted} / ${total} 个场景已有已采纳正文</span>${pending||blocker?`<button class="text-link ${blocker?'has-attention':''}" data-stage-jump="draft">${pending+blocker} 项等待决定</button>`:''}</div>
    ${libraryReadiness}
    ${visibleChapters.length?`<section class="overview-structure-summary"><div class="overview-section-head"><h3>已有场景</h3><button class="quiet" data-stage-jump="structure">管理章节</button></div>${visibleChapters.map(chapter=>`<button class="overview-chapter" data-stage-jump="structure"><b>${esc(chapter.title)}</b><span>${chapter.scenes.length} 个场景</span><small>${chapter.scenes.filter(scene=>scene.current_revision_id).length} 已完成</small></button>`).join('')}</section>`:''}
  </div>`;
};

stageDecisionModel=function(){
  const scene=selectedScene(),proposal=pendingProposal(),latest=state.work?.releases?.[0];
  const definitions={overview:{kicker:'WORKFLOW',title:'按推荐下一步推进即可',body:'作品总览只保留一个推荐行动，其他入口仍在左侧导航。',impact:'不会自动生成、修改或发布任何内容。'},brief:{kicker:'STEP 1',title:brief()?'想法已保存，等待分析':'先提供一句想法',body:'先让系统理解你想看什么；角色、写作组成和老师是否出场会以候选呈现，不要求你预先猜测。',impact:'系统会保存意图修订并生成 StoryBlueprint Proposal。'},blueprint:{kicker:'STEP 2',title:blueprintIsConfirmed()?'故事方向已确认':blueprint()?'审查故事方向候选':'等待系统分析',body:blueprintIsConfirmed()?'确认过的角色卡、世界观依据和第一场规则包会成为后续结构的边界。':'检查系统建议，点选人物卡、保留或覆盖老师出场建议，并决定是否采用。',impact:blueprintIsConfirmed()?'现在可以建立章节与场景。':'未确认前不会建立章节或写入正文。'},structure:{kicker:'STEP 3',title:'建立稳定的章节与场景',body:'场景拥有稳定 ID；改标题或调整顺序不会丢失正文和资料引用。',impact:'保存结构会使旧的全篇审查失效，需要重新检查。'},draft:{kicker:'STEP 4',title:proposal?'审查本场候选':scene?`推进「${scene.title}」`:'先建立一个场景',body:proposal?'候选可以局部修改；采纳前不会进入正文。':scene?'先装配上下文，再生成候选或检查已有正文。':'回到章节安排，先建立场景。',impact:proposal?'采纳时才会建立新的正文修订。':'Agent 只能提交候选，不能静默修改正文或资料。'},references:{kicker:'WORK LIBRARY',title:'当前作品的创作资料',body:'角色卡、世界观、事实、关系和证据都绑定当前作品。AI 会持续提出维护候选，只有采纳后才进入 Agent 上下文。',impact:'手动编辑只在你打开小工作面时出现；待审核内容不会冒充事实。'},release:{kicker:'STEP 5',title:latest?'确认交给制作的定稿':'先完成全篇审查',body:latest?'冻结版本不会随正文修改而改变；新稿需要创建新的发布版本。':'所有场景都有已采纳正文后，才能运行全篇审查与冻结。',impact:latest?'提交制作只交付固定的 ScriptRelease。':'全篇审查通过前，发布操作保持锁定。'}};
  return definitions[state.stage]||definitions.overview;
};

function sceneModeLabel(mode){return({bond_short:'羁绊与日常互动',main_battle:'主线冲突与行动',long_comedy:'轻松喜剧推进',text_reading:'小说化叙事阅读'})[mode]||'待决定'}
function briefWorldFoundation(){
  const world=worldBible(),items=['entities','rules','timeline'].flatMap(key=>(world[key]||[]).filter(item=>item.status!=='archived'));
  const label=world.source_type==='ba_starter'?'BA 起始架构':world.source_type==='mixed'?'BA 起始架构 + 本作自定义设定':items.length?'本作自定义世界观':'尚未建立世界观基础';
  return {label,detail:items.length?`当前资料库有 ${items.length} 项设定，${items.filter(item=>item.confidence_status==='confirmed').length} 项已确认；未确认条目不会当作既定事实。`:'当前作品还没有世界观条目。可以先分析想法，确认方向后再补充原创设定。'};
}
function blueprintIsConfirmed(){const value=blueprint();return Boolean(value&&value.status!=='proposed')}

renderBrief=function(el){
  const b=brief()||{},foundation=briefWorldFoundation(),hasIdea=Boolean(b.idea);
  el.innerHTML=frame('第 1 步 / 5','先告诉系统你想看什么','这一页只收集创作意图。角色、写作重心、老师是否出场和世界观采用范围，都由系统先提出候选，再由你确认。',`<section class="brief-intent-band"><div><p class="eyebrow">YOUR INPUT</p><h3>一句想法就够了</h3><p>可以写一个画面、人物关系、事件或情绪。系统会把它整理成可审查的故事方向，不会直接开始写正文。</p></div><span class="brief-step-state ${hasIdea?'is-saved':''}">${hasIdea?'已有想法，可重新分析':'等待你的想法'}</span></section><form id="briefForm" class="brief-intent-form"><label class="brief-idea">我想看<textarea name="idea" required placeholder="例如：游戏开发部的旧机器在深夜自行启动，爱丽丝和凯伊必须在天亮前确认它留下的线索。">${esc(b.idea||'')}</textarea><small>不必填写角色译名、写作类型或技术约束；这些会在下一步以候选和卡片选择呈现。</small></label><section class="brief-world-foundation"><div><p class="eyebrow">CURRENT WORLD BASIS</p><h3>${esc(foundation.label)}</h3><p>${esc(foundation.detail)}</p></div><button type="button" class="quiet" data-stage-jump="references" data-library-target="world">查看世界设定</button></section><div class="brief-intent-actions"><div><b>下一步：系统分析故事方向</b><small>会提出建议的角色卡、全作的混合走向、第一场的起草重心和老师出场建议。结果先是 Proposal。</small></div><button class="primary" type="submit">${hasIdea?'重新分析这句想法':'分析这句想法'}</button></div></form>`);
};

renderBlueprint=function(el){
  const b=blueprint(),cards=libraryCards().filter(card=>card.status!=='archived'),foundation=briefWorldFoundation();
  if(!brief()){
    el.innerHTML=frame('第 2 步 / 5','等待故事方向','先提供一句想法，系统才有可分析的输入。','<button class="primary" data-stage-jump="brief">返回写作想法</button>');
    return;
  }
  if(!b){
    el.innerHTML=frame('第 2 步 / 5','等待系统分析','想法已经保存。现在生成一份可修改、可退回的方向候选。',`<div class="empty-state"><div class="number">02</div><h3>还没有故事方向候选</h3><p class="lede">分析只会创建 StoryBlueprint Proposal，不会写正文或修改人物卡。</p><button class="primary" data-action="generate-blueprint">生成故事方向候选</button></div>`);
    return;
  }
  const proposal=b.status==='proposed',recommendations=b.recommendations||{},suggestedMode=recommendations.primary_scene_mode||b.mode||'bond_short',secondary=(recommendations.secondary_scene_modes||[]).filter(mode=>mode!==suggestedMode),selectedIds=new Set(b.decision?.character_card_ids||recommendations.character_card_ids||[]),sensei=recommendations.sensei_presence||'absent',worldBasis=recommendations.world_basis||foundation;
  const story=`<section class="blueprint-story"><div><p class="eyebrow">STORY DIRECTION</p><h3>${esc(b.title)}</h3><p>${esc(b.premise)}</p></div><div class="blueprint-conflict"><span>核心冲突</span><b>${esc(b.central_conflict)}</b><span>主题方向</span><b>${esc(b.theme)}</b></div><ol class="direction-list">${(b.direction||[]).map(item=>`<li>${esc(item)}</li>`).join('')}</ol></section>`;
  if(!proposal){
    el.innerHTML=frame('第 2 步 / 5','故事方向已确认','这份确认过的方向会作为章节与场景的共同边界。要调整时，重新让系统分析并确认新的候选。',`<div class="notice good">已确认 · ${b.simulation_notice?esc(b.simulation_notice):'方向已保存为独立 StoryBlueprint 修订。'}</div>${story}<section class="blueprint-confirmed-summary"><span>已确认角色</span><b>${esc((b.decision?.character_card_ids||b.characters||[]).length?cards.filter(card=>(b.decision?.character_card_ids||[]).includes(card.id)).map(card=>card.name).join('、')||(b.characters||[]).join('、'):'尚未登记')}</b><span>第一场起草重心</span><b>${esc(sceneModeLabel(b.decision?.mode||suggestedMode))}</b></section><form id="blueprintReviewForm" class="blueprint-revisit"><label>这版方向需要怎样调整？<textarea name="feedback" placeholder="例如：保留调查线，但希望先用日常互动建立角色关系。"></textarea></label><button class="quiet" name="review_action" value="regenerate" type="submit">按这些意见重新分析</button></form><div class="actions"><button class="primary" data-stage-jump="structure">开始安排章节与场景</button></div>`);
    return;
  }
  const cardChoices=cards.length?cards.map(card=>`<label class="blueprint-character-choice ${selectedIds.has(card.id)?'suggested':''}"><input type="checkbox" name="character_card_ids" value="${esc(card.id)}" ${selectedIds.has(card.id)?'checked':''}><span class="avatar-token">${esc(card.name.slice(0,1))}</span><span><b>${esc(card.name)}</b><small>${esc(libraryKindLabel(card.source_type))} · ${esc(trustLabel(card.trust_status))}</small></span>${selectedIds.has(card.id)?'<em>系统建议</em>':''}</label>`).join(''):'<div class="blueprint-empty-choice"><b>还没有可选择的人物卡</b><span>先在人物库建立或导入角色；这里不会要求你手输译名。</span><button type="button" class="quiet" data-stage-jump="references" data-library-target="characters">去人物库</button></div>';
  el.innerHTML=frame('第 2 步 / 5','审查系统给出的故事方向','系统已经根据你的想法和当前作品资料库提出候选。现在由你调整并确认；未确认前不能建立章节。',`<section class="blueprint-proposal-status"><div><p class="eyebrow">PROPOSAL / NOT YET APPLIED</p><h3>这是一份${b.simulation_notice?'模拟':''}方向候选</h3><p>${b.simulation_notice?esc(b.simulation_notice):'结果来自已配置的写作 Provider。'} 它不会改正文、人物卡或世界观。</p></div><span class="status-chip amber">等待你的确认</span></section>${story}<form id="blueprintReviewForm" class="blueprint-review-form"><section class="blueprint-world-basis"><div><p class="eyebrow">WORLD BASIS USED FOR ANALYSIS</p><h3>${esc(worldBasis.label||foundation.label)}</h3><p>${esc(worldBasis.detail||foundation.detail)}</p></div><button type="button" class="quiet" data-stage-jump="references" data-library-target="world">查看或调整世界设定</button></section><fieldset class="blueprint-modes"><legend>系统建议的写作组成 <small>作品可以混合推进；每次场景生成只会固定使用一种规则包。</small></legend><p>当前建议以「${esc(sceneModeLabel(suggestedMode))}」作为第一场起草重心${secondary.length?`，并保留「${secondary.map(sceneModeLabel).map(esc).join('、')}」作为后续场景方向。`:''}</p><div class="mode-choice-grid">${['bond_short','main_battle','long_comedy','text_reading'].map(mode=>`<label class="mode-choice ${mode===suggestedMode?'suggested':''}"><input type="radio" name="mode" value="${mode}" ${mode===suggestedMode?'checked':''}><span><b>${esc(sceneModeLabel(mode))}</b><small>${mode===suggestedMode?'系统建议的第一场重心':'可在确认时改为此重心'}</small></span></label>`).join('')}</div></fieldset><fieldset class="blueprint-characters"><legend>确认主要角色 <small>从已登记人物卡中点击选择，避免译名和手输错误。</small></legend><div class="blueprint-character-grid">${cardChoices}</div></fieldset><fieldset class="blueprint-sensei"><legend>老师是否出场 <small>系统建议：${sensei==='present'?'本次出场':'本次不必出场'}。你可以保留自动判断或明确覆盖。</small></legend><div class="segmented-control"><label><input type="radio" name="sensei_presence" value="auto" checked><span>采用系统建议</span></label><label><input type="radio" name="sensei_presence" value="present"><span>明确出场</span></label><label><input type="radio" name="sensei_presence" value="absent"><span>明确不出场</span></label></div></fieldset><label class="blueprint-feedback">看完方向后再补充（可选）<textarea name="feedback" placeholder="例如：不希望把旧机器解释成反派阴谋；希望两人的关系先保持克制。">${esc(b.feedback||'')}</textarea><small>这里是对系统候选的反馈，不是让你预先猜模型需要什么。</small></label><div class="brief-actions"><div><b>确认后才会保存可执行 Brief</b><small>角色选择、第一场规则包和老师出场决定都会形成新的修订；角色卡和世界观本身不会被静默修改。</small></div><div class="actions"><button class="primary" name="review_action" value="confirm" type="submit" ${cards.length?'':'disabled'}>确认方向，进入章节安排</button><button class="quiet" name="review_action" value="regenerate" type="submit">按反馈重新分析</button></div></div></form>`);
};

document.addEventListener('submit',async event=>{
  if(event.target.id!=='blueprintReviewForm')return;
  event.preventDefault();event.stopImmediatePropagation();
  const form=event.target,fields=new FormData(form),action=String(fields.get('review_action')||'confirm');
  try{
    if(action==='regenerate'){
      const feedback=String(fields.get('feedback')||'').trim();
      if(!feedback){toast('请先说明希望系统调整什么。',true);return;}
      setBusy('正在按反馈重新分析');
      const result=await api(`/works/${state.work.id}/blueprint:generate`,{method:'POST',body:JSON.stringify({expected_version:state.work.version,feedback})});
      state.work=result.work;toast(result.simulation?'已生成新的模拟方向候选':'已生成新的方向候选');render();return;
    }
    const result=await api(`/works/${state.work.id}/blueprint:confirm`,{method:'POST',body:JSON.stringify({expected_version:state.work.version,mode:fields.get('mode'),character_card_ids:fields.getAll('character_card_ids'),sensei_presence:fields.get('sensei_presence'),feedback:String(fields.get('feedback')||'').trim()})});
    state.work=result.work;state.stage='structure';toast('故事方向已确认，现在可以安排章节与场景');render();
  }catch(error){setBusy('确认未完成，候选仍安全保留');toast(error.message,true)}
},true);

renderInspector=function(){
  const el=$('#inspectorContent');
  if(!el)return;
  const scene=selectedScene(),proposal=pendingProposal(),latest=state.work?.releases?.[0];
  $$('[data-inspector]').forEach(button=>button.classList.toggle('active',button.dataset.inspector===state.inspector));
  if(!state.work){el.innerHTML='';return;}
  if(state.inspector==='decision'){const decision=stageDecisionModel();el.innerHTML=`<div class="inspector-body inspector-decision"><p class="eyebrow">${decision.kicker}</p><h3>${esc(decision.title)}</h3><p class="inspector-copy">${esc(decision.body)}</p><section class="inspector-impact"><span>保存或确认后</span><b>${esc(decision.impact)}</b></section><ul class="inspector-checklist"><li><i class="status-dot"></i>作品与版本已持久化</li><li><i class="status-dot ${proposal?'amber':''}"></i>${proposal?'当前有候选等待审查':'没有会被静默写入的内容'}</li><li><i class="status-dot"></i>当前操作由中央工作区完成</li></ul></div>`;return;}
  if(state.inspector==='context'){const c=state.context;el.innerHTML=`<div class="inspector-body"><p class="eyebrow">SCENE CONTEXT</p><h3>当前作用域</h3><p>${scene?`${esc(scene.chapterTitle)} / ${esc(scene.title)}`:'尚未选择场景'}</p><ul class="context-list">${c?`<li>规则包<br><b>${esc(c.rules.pack_version)}</b></li><li>本场起草重心<br><b>${esc(sceneModeLabel(c.rules.mode))}</b></li><li>固定输入修订<br><b>${c.source_revision_ids.length} 个</b></li><li>运行时人物卡<br><b>${c.runtime_character_cards.length} 张</b></li><li>BA 写作就绪状态<br><b>${esc(c.readiness.real_ba_writing)}</b></li>`:'<li>进入“逐场写作”并装配上下文后，这里会列出实际读取的版本。</li>'}</ul></div>`;return;}
  if(state.stage!=='draft'){el.innerHTML=`<div class="inspector-body"><p class="eyebrow">CREATIVE DIRECTOR</p><h3>Agent 在逐场写作时才出现</h3><p>先在中央工作区完成当前阶段。Agent 只依附一个场景和明确任务，不会取代作品结构或资料库。</p><button class="quiet" type="button" data-stage-jump="draft" ${stageGate('draft').allowed?'':'disabled'}>打开逐场写作</button></div>`;return;}
  if(!scene){el.innerHTML='<div class="inspector-body"><p class="eyebrow">BA WRITING AGENT</p><h3>先选择一个场景</h3><p>Agent 必须依附稳定 Scene ID，不能在空白聊天页中修改作品。</p></div>';return;}
  const existing=scene.current_revision_id,latestRun=(state.work.agent_runs||[]).find(run=>run.scope_id===scene.id),contextReady=Boolean(state.context),contextMissing=state.context?.readiness?.missing_runtime_character_cards||[],findings=(state.work.review_findings||[]).filter(item=>item.scene_id===scene.id&&item.status==='open'),warning=findings.find(item=>item.kind==='character_card_missing'),missingCharacters=contextMissing.length?contextMissing:(warning?.evidence?.speakers||[]),mode=existing?'rewrite':'draft',agentReady=contextReady&&!proposal&&!missingCharacters.length;
  const blocked=!contextReady?'<div class="notice">先在中央工作区装配本场上下文，Agent 才能读取固定的场景合同、规则包和人物卡修订。</div><button type="button" class="primary" data-action="assemble-context">装配本场上下文</button>':missingCharacters.length?`<div class="notice bad">还不能运行：${esc(missingCharacters.join('、'))} 尚无已确认人物卡。补齐后才能把正文与人物约束一起交给 Agent。</div><button type="button" class="primary" data-agent-complete-cards>补齐人物卡</button>`:proposal?'<div class="notice">当前已有候选等待决定。采纳或退回后，才能开始下一次 Agent 运行。</div>':'';
  const chips=existing&&agentReady?`<div class="agent-chips"><button type="button" class="quiet" data-agent-instruction="调整本场节奏：压缩解释，让动作和停顿先出现。">调整节奏</button><button type="button" class="quiet" data-agent-instruction="检查人物是否 OOC，并把需要调整的对白改写为更符合人物卡的表达。">检查 OOC</button><button type="button" class="quiet" data-agent-instruction="重写选中对白：保留本场事实、角色关系和停止边界。">重写对白</button></div>`:'';
  el.innerHTML=`<div class="inspector-body"><p class="eyebrow">BA WRITING AGENT</p><h3>${existing?'改写当前场景':'起草当前场景'}</h3><p>${existing?'当前正文会作为固定输入。Agent 只返回完整候选和 Diff，不会直接改动任何一句。':'只读取本场合同、单一 BA 模式和运行时人物卡；每次只提交一份 Proposal。'}</p>${latestRun?`<section class="agent-run"><b>${esc(latestRun.status)}</b><p>工具记录 ${latestRun.tool_calls.length} 项${latestRun.proposal_id?` · Proposal ${esc(latestRun.proposal_id)}`:''}</p></section>`:''}${blocked}<form id="agentRunForm" data-agent-mode="${mode}"><label>本场指令<textarea name="instruction" placeholder="${existing?'例如：压缩解释，保留爱丽丝先观察、凯伊后补充的节奏':'例如：让爱丽丝先观察异常，再把决定落到本场行动上'}" ${agentReady?'':'disabled'}></textarea></label>${chips}<button class="primary" type="submit" ${agentReady?'':'disabled'}>${existing?'生成完整改写候选':'运行 BA 场景 Agent'}</button></form><p class="form-note">${existing?'完整候选不会写回正文，采纳后才建立新的正文修订。':'当前仅有明确标注的 Fake Provider；真实模型尚未接入。'}</p></div>`;
};

function sceneContextIsReady(){return Boolean(state.stage==='draft'&&selectedScene()&&state.context)}
function sceneAgentIsReady(){return sceneContextIsReady()&&state.context?.readiness?.real_ba_writing==='ready'}

function syncSceneActionGuidance(){
  if(state.stage!=='draft'||state.mobileView!=='writing')return;
  const scene=selectedScene(),proposal=pendingProposal();
  if(!scene||proposal)return;
  const current=Boolean(scene.current_revision_id),contextReady=sceneContextIsReady(),agentReady=sceneAgentIsReady(),command=$('.next-command');
  if(command){
    const headline=$('strong',command),copy=$('p',command),actions=$('.command-actions',command);
    if(!contextReady){
      if(headline)headline.textContent=current?'重新装配上下文，再继续改写':'先装配上下文，准备本场';
      if(copy)copy.textContent='将固定本场合同、单一 BA 模式、人物卡和所选世界设定修订。';
      if(actions)actions.innerHTML=`<button class="primary" data-action="assemble-context">${current?'重新装配上下文':'装配本场上下文'}</button>${current?'<button class="quiet" data-action="review-scene">检查本场</button>':''}`;
    }else if(!agentReady){
      const reason=state.context?.readiness?.reason||'需要至少一张已确认、可用于运行时的人物卡。';
      if(headline)headline.textContent='上下文已固定，但 BA Agent 尚未就绪';
      if(copy)copy.textContent=reason;
      if(actions)actions.innerHTML='<button class="primary" data-stage-jump="references" data-library-target="characters">补齐人物卡</button><button class="quiet" data-action="assemble-context">重新装配</button>';
    }else if(!current){
      if(headline)headline.textContent='上下文已固定，可以生成候选';
      if(copy)copy.textContent='生成结果只会进入 Proposal；你确认采纳前，正文仍保持空白。';
      if(actions)actions.innerHTML='<button class="primary" data-action="generate-candidate">生成本场候选</button>';
    }else{
      if(headline)headline.textContent='正文已就绪，决定下一次操作';
      if(copy)copy.textContent='可以检查连续性，也可以让 Agent 基于当前修订提出完整改写候选。';
      if(actions)actions.innerHTML='<button class="primary" data-action="generate-candidate">生成下一份候选</button><button class="quiet" data-action="review-scene">检查本场</button>';
    }
  }
  $$('[data-action="generate-candidate"]').forEach(button=>{
    button.disabled=!agentReady;
    button.title=agentReady?'结果只会进入 Proposal':contextReady?'缺少 BA Agent 所需的已确认人物卡':'请先装配本场上下文';
  });
  const contextButton=$('.manuscript-head [data-action="assemble-context"]');
  if(contextButton)contextButton.textContent=contextReady?'重新装配':'上下文';
}

stageDecisionModel=function(){
  const scene=selectedScene(),proposal=pendingProposal(),latest=state.work?.releases?.[0],current=Boolean(scene?.current_revision_id),contextReady=sceneContextIsReady(),sourceIds=scenes().map(item=>item.current_revision_id).filter(Boolean),releaseGate=(state.work?.gates||[]).find(gate=>gate.kind==='release.review'),releaseReviewCurrent=Boolean(releaseGate?.status==='passed'&&releaseGate.snapshot&&JSON.stringify(releaseGate.snapshot.scene_revision_ids)===JSON.stringify(sourceIds));
  const definitions={
    overview:{kicker:'WORKFLOW',title:'按推荐下一步推进即可',body:'作品总览只保留一个推荐行动，其他入口仍在左侧导航。',impact:'不会自动生成、修改或发布任何内容。'},
    brief:{kicker:'STEP 1',title:brief()?'想法已保存，等待分析':'先提供一句想法',body:'先让系统理解你想看什么；角色、写作组成和老师是否出场会以候选呈现，不要求你预先猜测。',impact:'系统会保存意图修订并生成 StoryBlueprint Proposal。'},
    blueprint:{kicker:'STEP 2',title:blueprintIsConfirmed()?'故事方向已确认':blueprint()?'审查故事方向候选':'等待系统分析',body:blueprintIsConfirmed()?'确认过的角色卡、世界观依据和第一场规则包会成为后续结构的边界。':'检查系统建议，点选人物卡、保留或覆盖老师出场建议，并决定是否采用。',impact:blueprintIsConfirmed()?'现在可以建立章节与场景。':'未确认前不会建立章节或写入正文。'},
    structure:{kicker:'STEP 3',title:'建立稳定的章节与场景',body:'场景拥有稳定 ID；改标题或调整顺序不会丢失正文和资料引用。',impact:'保存结构会使旧的全篇审查失效，需要重新检查。'},
    draft:{kicker:'STEP 4',title:proposal?'审查本场候选':!scene?'先建立一个场景':!contextReady?'先固定本场上下文':current?'检查或改写当前正文':'生成第一份场景候选',body:proposal?'候选可以直接编辑；采纳前不会进入正文。':!scene?'回到章节安排，先建立场景。':!contextReady?'先固定场景合同、单一 BA 模式和人物卡修订，再允许 Agent 运行。':current?'本场已有正式修订，可以检查连续性或通过 Agent 生成完整改写候选。':'上下文已就绪，下一次生成只会创建 Proposal。',impact:proposal?'采纳时才会建立新的正文修订。':'Agent 只能提交候选，不能静默修改正文或资料。'},
    references:{kicker:'WORK LIBRARY',title:'确认可进入 Agent 的资料',body:'人物、世界观、事实与来源证据分别管理。待核对条目不会自动作为写作事实。',impact:'只有确认采用的资料会出现在下一场可选择的上下文中。'},
    release:{kicker:'STEP 5',title:latest?'确认是否交给 AA 制作':releaseReviewCurrent?'审查已通过，等待冻结':'先完成全篇审查',body:latest?'这份 ScriptRelease 已固定正文修订与内容哈希；后续改稿不会改变它。':releaseReviewCurrent?'当前审查覆盖全部正式正文，冻结后才会出现制作交接入口。':'所有场景都有已采纳正文后，才能运行全篇审查与冻结。',impact:latest?'只有点击交接后，才会在 AA 制作后端建立 ProductionRun。':releaseReviewCurrent?'冻结会创建新的不可变 ScriptRelease。':'全篇审查通过前，发布操作保持锁定。'}
  };
  return definitions[state.stage]||definitions.overview;
};

const renderBeforeSceneGuidance=render;
render=function(){renderBeforeSceneGuidance();syncSceneActionGuidance()};

// Durable creation conversation and Volume-aware structure. These overrides
// are intentionally last because this client still carries the first vertical
// slice in one file while the product surfaces are being separated.
usesGuidedWorkflow=function(){return true};

function workConversationThread(){
  const threads=state.work?.conversation_threads||[];
  const selected=threads.find(thread=>thread.id===state.conversationThreadId&&thread.status==='active');
  const workThread=threads.find(thread=>thread.scope_type==='work'&&thread.scope_id===state.work.id&&thread.status==='active');
  const fallback=threads.find(thread=>thread.status==='active')||threads[0];
  const thread=selected||workThread||fallback;
  if(thread)state.conversationThreadId=thread.id;
  return thread;
}
function workPlanProposal(){return state.work?.proposals?.find(item=>item.kind==='brief_blueprint'&&item.status==='pending')}
function messageText(message){return message?.content?.text||''}
function renderConversationMessage(message){
  const assistant=message.role==='assistant',content=message.content||{},questions=content.questions||[];
  return `<article class="conversation-message ${assistant?'assistant':'user'}"><div class="message-role">${assistant?'创作导演':'你'}</div><div class="message-bubble"><p>${esc(messageText(message))}</p>${questions.length?`<ul>${questions.map(item=>`<li>${esc(item)}</li>`).join('')}</ul>`:''}${content.simulation_notice?`<small>${esc(content.simulation_notice)}</small>`:''}${message.proposal_id?`<button class="message-proposal-link" type="button" data-stage-jump="brief">查看待审方案</button>`:''}</div></article>`;
}
function renderWorkConversationInspector(){
  const el=$('#inspectorContent'),thread=workConversationThread(),proposal=workPlanProposal();
  $$('[data-inspector]').forEach(button=>button.classList.toggle('active',button.dataset.inspector==='agent'));
  if(!thread){el.innerHTML='<div class="inspector-body"><p>当前作品还没有创作主对话。</p></div>';return}
  const discuss=thread.phase==='discuss';
  el.innerHTML=`<div class="director-panel"><header class="director-header"><div><p class="eyebrow">CREATIVE DIRECTOR</p><h3>全作 · 创作主对话</h3><small>对话 v${thread.version} · ${state.capabilities?.providers?.[0]?.is_simulation?'模拟 Provider':'已配置模型'}</small></div></header><div class="director-modes" role="group" aria-label="创作导演状态"><button type="button" data-thread-phase="discuss" class="${discuss?'active':''}">讨论创作</button><button type="button" data-thread-phase="execute" class="${!discuss?'active':''}">执行修改</button></div><div class="conversation-scroll" data-conversation-scroll>${thread.messages.map(renderConversationMessage).join('')||'<p class="conversation-empty">先说一句你想看的故事。</p>'}</div>${proposal?`<div class="director-pending"><b>故事方案等待决定</b><span>正式 Brief 和故事方向尚未改变。</span><button type="button" data-stage-jump="brief">查看方案</button></div>`:''}<form id="workConversationForm" class="conversation-composer"><label><span class="sr-only">给创作导演发送消息</span><textarea name="text" required placeholder="补充、反悔、比较方向，或直接说明哪里不对……"></textarea></label><div class="composer-actions"><div class="composer-tools">${renderPermissionMenu(thread)}<button class="quiet" type="button" data-organize-conversation ${proposal?'disabled':''}>整理为方案</button></div><button class="primary" type="submit" title="发送消息">发送</button></div></form></div>`;
  const scroll=$('[data-conversation-scroll]',el);if(scroll)scroll.scrollTop=scroll.scrollHeight;
}

const inspectorBeforeDurableConversation=renderInspector;
renderInspector=function(){
  if(state.work&&state.inspector==='agent'&&state.stage!=='draft')return renderWorkConversationInspector();
  return inspectorBeforeDurableConversation();
};

renderBrief=function(el){
  const proposal=workPlanProposal(),savedBrief=brief(),thread=workConversationThread();
  if(proposal){
    const candidate=proposal.candidate,plan=candidate.story_blueprint,changes=proposal.diff?.changes||[];
    el.innerHTML=frame('讨论整理 / 等待采纳','检查创作导演整理的故事方案','这份内容来自当前创作主对话。采纳前，正式 Brief、故事方向、人物卡和世界观都不会改变。',`<section class="work-plan-status"><div><p class="eyebrow">PROPOSAL / NOT APPLIED</p><h3>${esc(plan.title||'故事方向候选')}</h3><p>${esc(plan.premise||candidate.brief.idea)}</p></div><span class="status-chip amber">等待你的决定</span></section><div class="work-plan-grid"><section><p class="eyebrow">CENTRAL CONFLICT</p><h3>故事的核心变化</h3><p>${esc(plan.central_conflict||'待继续讨论')}</p><ol class="direction-list">${(plan.direction||[]).map(item=>`<li>${esc(item)}</li>`).join('')}</ol></section><section class="work-plan-diff"><p class="eyebrow">FROM DISCUSSION</p>${changes.map(item=>`<div><span>${esc(item.field)}</span><p>${esc(item.after||'未填写')}</p></div>`).join('')}</section></div><div class="plan-decision-bar"><div><b>采纳会建立两份正式修订</b><small>Brief 与 StoryBlueprint 会保留来源对话和 Proposal ID；仍不会生成正文。</small></div><div class="actions"><button class="primary" type="button" data-accept-work-plan="${esc(proposal.id)}">采纳方案</button><button class="quiet" type="button" data-reject-work-plan="${esc(proposal.id)}">退回继续讨论</button></div></div>`);
    return;
  }
  if(savedBrief){
    const plan=blueprint();
    el.innerHTML=frame('创作基础 / 已采纳','故事方向已经成为正式版本','你仍可在右侧继续讨论并提出新方案；旧修订不会被覆盖。',`<section class="accepted-plan"><p class="eyebrow">CURRENT FORMAL PLAN</p><h3>${esc(plan?.title||savedBrief.idea)}</h3><p>${esc(plan?.central_conflict||savedBrief.constraints||'可以继续补充本作约束。')}</p><div class="accepted-plan-meta"><span>Brief · 已确认</span><span>StoryBlueprint · 已接受</span><span>来源 · 创作主对话</span></div></section><div class="actions"><button class="primary" type="button" data-stage-jump="structure">查看卷、章与场景</button><button class="quiet" type="button" data-inspector="agent">继续讨论新方向</button></div>`);
    return;
  }
  el.innerHTML=frame('创作讨论 / 尚未成案','先和创作导演把想法聊清楚','作品骨架已经建立，但对话不是正式设定。你可以反悔、补充或比较方向，觉得足够后再整理为方案。',`<section class="discussion-start"><div><p class="eyebrow">CURRENT SCOPE</p><h3>${esc(state.work.title)} · 全作</h3><p>默认卷和第一章已经创建。当前 ${thread?.messages?.length||0} 条消息只属于创作讨论，不会写入 WorkCanon。</p></div><div class="discussion-actions"><button class="primary" type="button" data-inspector="agent">打开创作导演</button><button class="quiet" type="button" data-organize-conversation>整理为方案</button></div></section><section class="discussion-boundary"><b>创作导演可以做什么</b><p>复述理解、提出关键不确定项、比较方向，并把共识整理成 Brief/StoryBlueprint Proposal。</p><b>它不会做什么</b><p>不会把聊天静默写成人物卡、世界观、作品事实或正文。</p></section>`);
};

renderStructure=function(el){
  const volumes=state.work.volumes||[],formal=blueprintIsConfirmed();
  const volumeMarkup=volumes.map((volume,volumeIndex)=>`<section class="volume-section"><header class="volume-head"><div><p class="eyebrow">VOLUME ${String(volumeIndex+1).padStart(2,'0')}</p><h3>${esc(volume.title)}</h3><small>${volume.chapters.length} 章 · ${volume.chapters.reduce((sum,chapter)=>sum+chapter.scenes.length,0)} 个场景</small></div><button class="quiet" type="button" data-structure-add-chapter="${esc(volume.id)}" ${formal?'':'disabled'} title="${formal?'在本卷增加章节':'确认整体故事方向后可增加更多章节'}">新增章节</button></header><div class="volume-chapters">${volume.chapters.map((chapter,chapterIndex)=>`<section class="volume-chapter ${chapter.status==='placeholder'?'placeholder':''}"><header><div><span>第 ${String(chapterIndex+1).padStart(2,'0')} 章</span><h4>${esc(chapter.title)}</h4><small>${chapter.status==='placeholder'?'作品建立时创建，可直接规划第一场':`${chapter.scenes.length} 个场景`}</small></div><button class="quiet" type="button" data-structure-add-scene="${esc(chapter.id)}">新增场景</button></header><div class="volume-scenes">${chapter.scenes.length?chapter.scenes.map((scene,index)=>`<button type="button" class="volume-scene" data-scene-open="${esc(scene.id)}"><span>${String(index+1).padStart(2,'0')}</span><div><b>${esc(scene.title)}</b><small>${esc(scene.contract.goal||'尚未填写本场变化')} · ${scene.current_revision_id?'已有正文':'尚未起草'}</small></div></button>`).join(''):'<div class="volume-scene-empty">还没有场景。可以先建立第一场，再和创作导演讨论它应该发生什么。</div>'}</div></section>`).join('')}</div></section>`).join('');
  el.innerHTML=frame('STORY STRUCTURE','卷、章与场景','这些层级在建立作品时就存在，不需要等 AI 决定后才能查看。标题可以变化，稳定 ID 和正文修订不会变化。',`<section class="structure-scope-note"><div><b>${formal?'整体方向已确认':'整体方向仍在讨论'}</b><p>${formal?'可以继续增加卷章和场景。':'第一卷与第一章已经可用；新增更多章节前先确认整体方向，避免过早铺开结构。'}</p></div><button class="quiet" type="button" data-inspector="agent">和创作导演讨论结构</button></section><div class="volume-board">${volumeMarkup}</div><div class="structure-footer"><button class="primary" type="button" data-structure-add-volume>新增卷</button><span>新增卷会自动建立第一章占位。</span></div>`);
};

function decorateVolumeTree(){
  const tree=$('#sceneTree');if(!tree||!state.work)return;
  const volumes=state.work.volumes||[];
  tree.innerHTML=volumes.map((volume,index)=>`<div class="tree-volume"><p><span>卷 ${String(index+1).padStart(2,'0')}</span><b>${esc(volume.title)}</b></p>${volume.chapters.map(chapter=>`<div class="tree-chapter-group"><p class="tree-chapter">${esc(chapter.title)}</p>${chapter.scenes.map(scene=>`<button class="scene-link ${scene.id===state.sceneId?'active':''}" data-scene="${esc(scene.id)}">${esc(scene.title)} <small>· ${scene.current_revision_id?'正文':'计划'}</small></button>`).join('')}</div>`).join('')}</div>`).join('');
}

function decorateTopStatus(){
  const el=$('#saveStatus');if(!el)return;
  const items=state.work?.runs?.flatMap(run=>run.work_items)||[];
  const active=items.filter(item=>['running','ready','waiting_user'].includes(item.status)).length;
  el.dataset.state=state.work?'saved':'idle';
  el.title=state.work?`作品版本 ${state.work.version} · 后台任务 ${active}`:'尚未建立作品';
}

const renderBeforeDurableConversation=render;
render=function(){renderBeforeDurableConversation();decorateVolumeTree();decorateTopStatus()};

document.addEventListener('submit',async event=>{
  if(event.target.id==='workConversationForm'){
    event.preventDefault();event.stopImmediatePropagation();const thread=workConversationThread(),fields=new FormData(event.target);
    try{setBusy('创作导演正在回应');const result=await api(`/works/${state.work.id}/threads/${thread.id}/messages`,{method:'POST',body:JSON.stringify({expected_thread_version:thread.version,text:fields.get('text'),attachment_ids:state.composerAttachmentIds||[],task_scope:agentTaskScope()})});state.work=result.work;state.composerAttachmentIds=[];setBusy('对话已保存');toast(result.simulation?'模拟回应已保存，可继续讨论':'回应已保存');render()}catch(error){setBusy('对话发送失败');toast(error.message,true)}
    return;
  }
},true);

document.addEventListener('click',event=>{
  const button=event.target.closest('button');if(!button||!state.work)return;
  if(button.dataset.permissionMode){event.preventDefault();event.stopImmediatePropagation();(async()=>{const thread=workConversationThread(),mode=button.dataset.permissionMode;try{const result=await api(`/works/${state.work.id}/threads/${thread.id}/settings`,{method:'POST',body:JSON.stringify({expected_thread_version:thread.version,permission_mode:mode,phase:thread.phase})});state.work=result.work;toast(mode==='managed'?'已开启限定范围的托管创作':'已切换为所有修改均需审核');render()}catch(error){toast(error.message,true)}})();return}
  if(button.dataset.threadPhase){event.preventDefault();event.stopImmediatePropagation();(async()=>{const thread=workConversationThread();try{const result=await api(`/works/${state.work.id}/threads/${thread.id}/settings`,{method:'POST',body:JSON.stringify({expected_thread_version:thread.version,permission_mode:thread.permission_mode,phase:button.dataset.threadPhase})});state.work=result.work;render()}catch(error){toast(error.message,true)}})();return}
    if(button.dataset.organizeConversation!==undefined){event.preventDefault();event.stopImmediatePropagation();(async()=>{const thread=workConversationThread();try{setBusy('正在整理讨论');const scope=agentTaskScope();const result=await api(`/works/${state.work.id}/threads/${thread.id}/proposal:organize`,{method:'POST',body:JSON.stringify({expected_version:state.work.version,expected_thread_version:thread.version,task_scope:scope})});state.work=result.work;state.stage=scope.surface==='chapter'?'structure':'brief';state.inspector='agent';toast(scope.surface==='chapter'?'章内细纲候选已生成，等待你的决定':(result.simulation?'已生成模拟故事方案，等待你的决定':'故事方案已整理，等待你的决定'));render()}catch(error){setBusy('未能整理方案');toast(error.message,true)}})();return}
  if(button.dataset.acceptWorkPlan){event.preventDefault();event.stopImmediatePropagation();(async()=>{try{const result=await api(`/works/${state.work.id}/proposals/${button.dataset.acceptWorkPlan}/accept`,{method:'POST',body:JSON.stringify({expected_version:state.work.version})});state.work=result.work;state.stage='structure';toast('故事方案已采纳为正式修订');render()}catch(error){toast(error.message,true)}})();return}
  if(button.dataset.rejectWorkPlan){event.preventDefault();event.stopImmediatePropagation();(async()=>{try{const result=await api(`/works/${state.work.id}/proposals/${button.dataset.rejectWorkPlan}/reject`,{method:'POST',body:JSON.stringify({expected_version:state.work.version,note:'退回创作主对话继续讨论'})});state.work=result.work;state.inspector='agent';toast('方案已退回，对话和历史仍保留');render()}catch(error){toast(error.message,true)}})();return}
},true);

document.addEventListener('click',event=>{
  if(event.target.closest('.permission-menu'))return;
  $$('details.permission-menu[open]').forEach(menu=>menu.removeAttribute('open'));
});

document.addEventListener('keydown',event=>{
  if(event.key!=='Escape')return;
  $$('details.permission-menu[open]').forEach(menu=>menu.removeAttribute('open'));
});

renderOverview=renderOverviewV3;

/* The director is one conversation. Discussion, planning and a requested
   rewrite are different intents in the same thread, not tabs that duplicate
   the visible history. Keep the complete history for the provider, but keep
   the workbench readable by collapsing older messages. */
function conversationHistoryMarkup(messages){
  const all=Array.isArray(messages)?messages:[],visibleCount=10;
  const older=all.length>visibleCount?all.slice(0,-visibleCount):[],visible=all.slice(-visibleCount);
  return `${older.length?`<details class="conversation-history"><summary>查看较早对话 · ${older.length} 条</summary>${older.map(renderConversationMessage).join('')}</details>`:''}${visible.map(renderConversationMessage).join('')}`;
}

function conversationTaskContract(thread){
  const progress=workflowProgress();
  const expected=!progress.done.brief?'brief.build':!progress.done.blueprint?'blueprint.generate':!progress.done.structure?'structure.plan':!progress.done.draft?'scene.draft.generate':'release.review';
  const assistant=[...(thread?.messages||[])].reverse().find(message=>message.role==='assistant'&&message.content?.task_contract);
  if(assistant&&assistant.content.task_contract.id===expected)return assistant.content.task_contract;
  const template=(state.capabilities?.writing_pack?.templates||[]).find(item=>item.id===expected)||{};
  const fallbackTasks={
    'brief.build':'理解这句想法，提出需要讨论的方向，不写入任何正式设定。',
    'blueprint.generate':'围绕当前想法讨论、比较并形成可审查的故事方向 Proposal。',
    'structure.plan':'基于已确认的故事方向，讨论卷、章与场景的稳定结构；结构变更需经用户确认。',
    'scene.draft.generate':'协助确定下一场的目标与修改约束；具体正文只能通过该场的 Proposal / Diff 提交。',
    'release.review':'协助全篇审查、确认未决事项，并在 Gate 通过后准备冻结 ScriptRelease。'
  };
  return {...template,id:expected,task:fallbackTasks[expected]};
}
function renderConversationTask(contract){
  const execution={user_confirmed:'等待确认',proposal_then_confirm:'先提案后确认',automatic_proposal_only:'仅生成候选',automatic_gate_then_user_freeze:'审查后冻结'}[contract?.execution]||'受阶段约束';
  const stageName=state.stage==='overview'?'下一阶段':stageLabel(state.stage);
  return `<section class="director-task-contract"><div><span>当前任务</span><b>${esc(stageName)} · ${esc(contract?.id||'writing')}</b><small>${esc(contract?.task||'继续当前阶段的讨论；正式变更仍需经过 Proposal 和 Gate。')}</small></div><em>${esc(execution)}</em></section>`;
}
function renderConversationAction(contract,proposal){
  if(!['brief.build','blueprint.generate'].includes(contract?.id))return'';
  return `<button class="quiet" type="button" data-organize-conversation ${proposal?'disabled':''}>形成故事方向方案</button>`;
}

renderConversationMessage=function(message){
  const assistant=message.role==='assistant',content=message.content||{},questions=content.questions||[];
  return `<article class="conversation-message ${assistant?'assistant':'user'}"><div class="message-role">${assistant?'创作导演':'你'}</div><div class="message-bubble"><p>${esc(messageText(message))}</p>${questions.length?`<ul>${questions.map(item=>`<li>${esc(item)}</li>`).join('')}`:''}${message.proposal_id?`<button class="message-proposal-link" type="button" data-stage-jump="brief">查看待审方案</button>`:''}</div></article>`;
};

renderMobileAgent=function(el){
  const thread=workConversationThread(),proposal=workPlanProposal(),task=conversationTaskContract(thread);
  if(!thread){el.innerHTML=frame('CREATIVE DIRECTOR','创作对话','当前作品还没有创作主对话。','<div class="notice">重新打开作品后会自动恢复对话。</div>');return;}
  el.innerHTML=`<div class="mobile-agent-page"><header class="mobile-agent-head"><div><p class="eyebrow">CREATIVE DIRECTOR</p><div class="agent-title-row"><h2>创作导演</h2><span class="agent-provider-chip">${state.capabilities?.providers?.[0]?.is_simulation?'本地模拟':'已连接'}</span></div><p>${esc(state.work.title)} · 对话跨越全作连续保留</p></div></header>${renderConversationTask(task)}<div class="mobile-conversation-scroll" data-mobile-conversation-scroll>${conversationHistoryMarkup(thread.messages)||'<p class="conversation-empty">开始一段关于这部作品的对话。</p>'}</div>${proposal?'<div class="director-pending"><b>有一份故事方案等待决定</b><span>对话仍可继续，但正式产物尚未改变。</span><button type="button" data-stage-jump="brief">查看方案</button></div>':''}<form id="mobileWorkConversationForm" class="conversation-composer mobile-composer"><label><span class="sr-only">给创作导演发送消息</span><textarea name="text" required placeholder="输入消息…"></textarea></label><div class="composer-actions"><div class="composer-tools">${renderPermissionMenu(thread)}${renderConversationAction(task,proposal)}</div><button class="primary" type="submit" title="发送消息">发送</button></div></form></div>`;
  const scroll=$('[data-mobile-conversation-scroll]',el);if(scroll)scroll.scrollTop=scroll.scrollHeight;
};

renderWorkConversationInspector=function(){
  const el=$('#inspectorContent'),thread=workConversationThread(),proposal=workPlanProposal(),task=conversationTaskContract(thread);
  $$('[data-inspector]').forEach(button=>button.classList.toggle('active',button.dataset.inspector==='agent'));
  if(!thread){el.innerHTML='<div class="inspector-body"><p>当前作品还没有创作主对话。</p></div>';return;}
  el.innerHTML=`<div class="director-panel"><header class="director-header"><div><p class="eyebrow">CREATIVE DIRECTOR</p><div class="agent-title-row"><h3>创作导演</h3><span class="agent-provider-chip">${state.capabilities?.providers?.[0]?.is_simulation?'本地模拟':'已连接'}</span></div><small>全作 · 对话保留在当前作品</small></div></header>${renderConversationTask(task)}<div class="conversation-scroll" data-conversation-scroll>${conversationHistoryMarkup(thread.messages)||'<p class="conversation-empty">开始一段关于这部作品的对话。</p>'}</div>${proposal?`<div class="director-pending"><b>有一份故事方案等待决定</b><span>对话仍可继续，但正式产物尚未改变。</span><button type="button" data-stage-jump="brief">查看方案</button></div>`:''}<form id="workConversationForm" class="conversation-composer"><label><span class="sr-only">给创作导演发送消息</span><textarea name="text" required placeholder="输入消息…"></textarea></label><div class="composer-actions"><div class="composer-tools">${renderPermissionMenu(thread)}${renderConversationAction(task,proposal)}</div><button class="primary" type="submit" title="发送消息">发送</button></div></form></div>`;
  const scroll=$('[data-conversation-scroll]',el);if(scroll)scroll.scrollTop=scroll.scrollHeight;
};

function renderSceneAgentInspector(){
  const el=$('#inspectorContent'),scene=selectedScene(),proposal=pendingProposal(),findings=(state.work?.review_findings||[]).filter(item=>item.scene_id===scene?.id&&item.status==='open'),warning=findings.find(item=>item.kind==='character_card_missing'),missingCharacters=warning?.evidence?.speakers||[],existing=Boolean(scene?.current_revision_id),latestRun=(state.work?.agent_runs||[]).find(run=>run.scope_id===scene?.id),ready=Boolean(state.context)&&!proposal&&!missingCharacters.length,contextLabel=state.context?(state.context.readiness?.real_ba_writing==='ready'?'已准备，可开始对话':'已准备，但还缺人物卡'):state._contextError?'准备失败，可重试':state._contextLoadingScene===scene?.id?'正在准备本场上下文':'正在准备本场上下文';
  if(!scene){el.innerHTML='<div class="inspector-body"><p>先打开一个场景，Agent 才知道当前要处理哪一段。</p></div>';return;}
  const chips=`<div class="agent-chips"><button type="button" class="quiet" data-agent-instruction="先讨论这场应该发生什么变化，不要直接写正文。">先讨论本场</button><button type="button" class="quiet" data-agent-instruction="检查本场人物是否 OOC，并说明依据。">检查 OOC</button>${existing?'<button type="button" class="quiet" data-agent-instruction="重写当前场景，保留事实和人物关系，先返回候选与 Diff。">提出改写</button>':''}</div>`;
  const blocked=missingCharacters.length?`<div class="notice bad">还缺少已确认的人物卡：${esc(missingCharacters.join('、'))}。</div><button type="button" class="quiet" data-agent-complete-cards>去补齐人物卡</button>`:proposal?'<div class="notice">当前候选正在等待决定。先采纳或退回，再开始下一次写作任务。</div>':'';
  el.innerHTML=`<div class="scene-agent-panel"><header><p class="eyebrow">SCENE AGENT</p><h3>与本场 Agent 对话</h3><p>${esc(scene.chapterTitle)} / ${esc(scene.title)}</p></header><section class="agent-context-brief"><div><span>本场上下文</span><b>${contextLabel}</b><small>进入本场后系统自动按章节方向、前文承接和确认资料准备，不需要先理解内部装配动作。</small></div>${state.context?'<button class="quiet" type="button" data-inspector="context">查看输入</button>':state._contextError?'<button class="quiet" type="button" data-action="assemble-context">重试准备</button>':''}</section>${latestRun?`<section class="agent-run"><b>上次任务：${esc(latestRun.status)}</b><small>${latestRun.tool_calls.length} 项工具记录${latestRun.proposal_id?' · 已有候选':''}</small></section>`:''}${blocked}<form id="agentRunForm" data-agent-mode="${existing?'rewrite':'draft'}"><label class="sr-only" for="sceneAgentInstruction">告诉 Agent 你希望本场怎么处理</label><textarea id="sceneAgentInstruction" name="instruction" placeholder="告诉 Agent 你希望本场怎么处理…" ${ready?'':'disabled'}></textarea>${ready?chips:''}<div class="scene-agent-submit"><span>结果先进入 Proposal / Diff</span><button class="primary" type="submit" ${ready?'':'disabled'}>${existing?'生成改写候选':'生成场景候选'}</button></div></form></div>`;
}

const renderInspectorBeforeSceneAgent=renderInspector;
renderInspector=function(){
  if(state.work&&state.inspector==='agent'&&state.stage==='draft')return renderSceneAgentInspector();
  return renderInspectorBeforeSceneAgent();
};

// Selecting a scene is a read-only context preparation step. The user should
// not have to understand an internal "assemble" operation before chatting.
document.addEventListener('click',event=>{
  const button=event.target.closest('button[data-scene],button[data-scene-open]');
  if(!button||!state.work)return;
  const sceneId=button.dataset.scene||button.dataset.sceneOpen;
  if(!sceneId||state._contextLoadingScene===sceneId)return;
  state._contextLoadingScene=sceneId;
  setBusy('正在准备本场上下文');
  api(`/works/${state.work.id}/scenes/${sceneId}/context:assemble`,{method:'POST',body:'{}'}).then(context=>{
    if(state.sceneId===sceneId){state.context=context;state.inspector='agent';render();setBusy('本场上下文已准备');}
  }).catch(error=>{if(state.sceneId===sceneId)toast(error.message,true)}).finally(()=>{if(state._contextLoadingScene===sceneId)state._contextLoadingScene='';});
},true);

// EOF product overrides: these must run after the legacy slice declarations.
conversationTaskContract=function(){
  const scope=agentTaskScope(),hasBrief=Boolean(brief()),hasBlueprint=blueprintIsConfirmed();
  let id='brief.build',task='理解这句想法，提出需要讨论的方向，不写入任何正式设定。';
  if(scope.surface==='work'&&hasBrief){id='blueprint.generate';task='在作品栏目维护全作方向、人物关系和世界观边界；任何调整都先形成新的 StoryBlueprint Proposal。';}
  else if(scope.surface==='chapter'&&hasBlueprint){id='chapter.plan';task=`只规划《${writingChapter()?.title||'当前章节'}》内部的章节目标、承接点和场景节拍，不重写全作 StoryBlueprint。`;}
  else if(scope.surface==='chapter'){id='blueprint.generate';task='全作方向尚未确认，请先回到作品栏目完成确认。';}
  const template=(state.capabilities?.writing_pack?.templates||[]).find(item=>item.id===id)||{};
  return {...template,id,task,task_scope:{surface:scope.surface,chapter_id:scope.surface==='chapter'?writingChapter()?.id:null,chapter_title:scope.surface==='chapter'?writingChapter()?.title:null}};
};
renderConversationTask=function(contract){const execution={user_confirmed:'等待确认',proposal_then_confirm:'先提案后确认',automatic_proposal_only:'仅生成候选',automatic_gate_then_user_freeze:'审查后冻结'}[contract?.execution]||'受阶段约束';const scope=contract?.task_scope?.surface==='chapter'?'章内写作':'作品规划';return `<section class="director-task-contract"><div><span>当前任务 · ${esc(scope)}${contract?.task_scope?.chapter_title?` · ${esc(contract.task_scope.chapter_title)}`:''}</span><b>${esc(contract?.id||'writing')}</b><small>${esc(contract?.task||'继续当前阶段的讨论；正式变更仍需经过 Proposal 和 Gate。')}</small></div><em>${esc(execution)}</em></section>`};
renderConversationAction=function(contract,proposal){if(proposal)return'';if(contract?.id==='chapter.plan')return'<button class="quiet" type="button" data-organize-conversation>整理章内细纲</button>';if(['brief.build','blueprint.generate'].includes(contract?.id))return'<button class="quiet" type="button" data-organize-conversation>形成全作方案</button>';return''};
renderConversationMessage=function(message){const assistant=message.role==='assistant',content=message.content||{},questions=content.questions||[],proposal=message.proposal_id?state.work?.proposals?.find(item=>item.id===message.proposal_id):null,target=proposal?.kind==='chapter_plan'?'structure':'brief';return`<article class="conversation-message ${assistant?'assistant':'user'}"><div class="message-role">${assistant?'创作导演':'你'}</div><div class="message-bubble"><p>${esc(messageText(message))}</p>${questions.length?`<ul>${questions.map(item=>`<li>${esc(item)}</li>`).join('')}</ul>`:''}${message.proposal_id?`<button class="message-proposal-link" type="button" data-stage-jump="${target}">查看待审方案</button>`:''}</div></article>`};
renderWorkConversationInspector=function(){const el=$('#inspectorContent'),thread=workConversationThread(),proposal=activeConversationProposal(),task=conversationTaskContract(thread),chapter=writingChapter(),workSurface=agentTaskScope().surface==='work';$$('[data-inspector]').forEach(button=>button.classList.toggle('active',button.dataset.inspector==='agent'));if(!thread){el.innerHTML='<div class="inspector-body"><p>当前作品还没有创作主对话。</p></div>';return}const pending=proposal?`<div class="director-pending"><b>${workSurface?'全作故事方案':`《${esc(chapter?.title||'当前章节')}》章内细纲`}等待决定</b><span>正式产物尚未改变。先审查，再决定采纳或退回。</span><div class="director-pending-actions"><button class="primary" type="button" data-accept-director-proposal="${esc(proposal.id)}">采纳</button><button class="quiet" type="button" data-reject-director-proposal="${esc(proposal.id)}">退回继续讨论</button></div></div>`:'';el.innerHTML=`<div class="director-panel"><header class="director-header"><div><p class="eyebrow">${workSurface?'WORK DIRECTOR':'CHAPTER DIRECTOR'}</p><div class="agent-title-row"><h3>${workSurface?'全作创作导演':'章内写作助手'}</h3><span class="agent-provider-chip">${state.capabilities?.providers?.[0]?.is_simulation?'本地模拟':'已连接'}</span></div><small>${workSurface?'作品栏目 · 全局方向、人物和世界观':'写作栏目 · '+esc(chapter?.title||'尚未选择章节')} · 对话连续保留</small></div></header>${renderConversationTask(task)}<div class="conversation-scroll" data-conversation-scroll>${conversationHistoryMarkup(thread.messages)||'<p class="conversation-empty">开始一段关于当前作品的讨论。</p>'}</div>${pending}<form id="workConversationForm" class="conversation-composer"><label><span class="sr-only">给 Agent 发送消息</span><textarea name="text" required placeholder="输入消息…"></textarea></label><div class="composer-actions"><div class="composer-tools">${renderPermissionMenu(thread)}${renderConversationAction(task,proposal)}</div><button class="primary" type="submit" title="发送消息">发送</button></div></form></div>`;const scroll=$('[data-conversation-scroll]',el);if(scroll)scroll.scrollTop=scroll.scrollHeight};
var finalStructureBase=renderStructure;
renderStructure=function(el){finalStructureBase(el);const inner=$('.workspace-inner',el),chapter=writingChapter();if(!inner)return;const target=document.createElement('section');target.className='writing-target-bar';target.innerHTML=`<div><p class="eyebrow">CURRENT WRITING TARGET</p><h3>${chapter?esc(chapter.title):'还没有可写章节'}</h3><p>章内细纲、场景上下文和 Agent 讨论都会绑定这一章。全作方向请回到“作品”。</p></div><label>当前章节<select data-select-writing-chapter>${(state.work?.chapters||[]).map(item=>`<option value="${esc(item.id)}" ${item.id===chapter?.id?'selected':''}>${esc(item.title)}</option>`).join('')}</select></label>`;inner.querySelector('.structure-scope-note, .structure-command')?.before(target);const plan=(state.work.artifacts||[]).find(item=>item.kind==='chapter_plan'&&item.scope_id===chapter?.id)?.current_revision?.content;if(plan){const note=document.createElement('section');note.className='chapter-plan-summary';note.innerHTML=`<div><p class="eyebrow">CHAPTER PLAN · 已采纳</p><h3>${esc(plan.title||`${chapter.title}细纲`)}</h3><p>${esc(plan.chapter_goal||'本章目标已保存。')}</p></div><button class="quiet" type="button" data-inspector="agent">继续讨论本章</button>`;inner.querySelector('.structure-scope-note, .structure-command')?.before(note)}};
var finalRenderBase=render;
renderMobileAgent=function(el){const thread=workConversationThread(),proposal=activeConversationProposal(),task=conversationTaskContract(thread),chapter=writingChapter(),workSurface=agentTaskScope().surface==='work';if(!thread){el.innerHTML=frame('CREATIVE DIRECTOR','创作对话','当前作品还没有创作主对话。','<div class="notice">重新打开作品后会自动恢复对话。</div>');return}const pending=proposal?`<div class="director-pending"><b>${workSurface?'全作故事方案':`《${esc(chapter?.title||'当前章节')}》章内细纲`}等待决定</b><span>正式产物尚未改变。</span><div class="director-pending-actions"><button class="primary" type="button" data-accept-director-proposal="${esc(proposal.id)}">采纳</button><button class="quiet" type="button" data-reject-director-proposal="${esc(proposal.id)}">退回继续讨论</button></div></div>`:'';el.innerHTML=`<div class="mobile-agent-page"><header class="mobile-agent-head"><div><p class="eyebrow">${workSurface?'WORK DIRECTOR':'CHAPTER DIRECTOR'}</p><div class="agent-title-row"><h2>${workSurface?'全作创作导演':'章内写作助手'}</h2><span class="agent-provider-chip">${state.capabilities?.providers?.[0]?.is_simulation?'本地模拟':'已连接'}</span></div><p>${esc(state.work.title)} · ${workSurface?'作品规划':'写作 · '+(chapter?.title||'未选择章节')}</p></div></header>${renderConversationTask(task)}<div class="mobile-conversation-scroll" data-mobile-conversation-scroll>${conversationHistoryMarkup(thread.messages)||'<p class="conversation-empty">开始一段关于当前作品的讨论。</p>'}</div>${pending}<form id="mobileWorkConversationForm" class="conversation-composer mobile-composer"><label><span class="sr-only">给 Agent 发送消息</span><textarea name="text" required placeholder="输入消息…"></textarea></label><div class="composer-actions"><div class="composer-tools">${renderPermissionMenu(thread)}${renderConversationAction(task,proposal)}</div><button class="primary" type="submit" title="发送消息">发送</button></div></form></div>`;const scroll=$('[data-mobile-conversation-scroll]',el);if(scroll)scroll.scrollTop=scroll.scrollHeight};
function cleanAutomaticContextControls(){
  $$('[data-action="assemble-context"]').forEach(button=>{if(button.closest('.manuscript-head')){button.textContent='查看上下文';button.removeAttribute('data-action');button.dataset.inspector='context'}else if(!state._contextError)button.remove();else button.textContent='重试准备'});
  $$('.next-command strong').forEach(node=>{if(node.textContent.includes('装配'))node.textContent='正在准备本场上下文'});
  $$('.next-command .command-actions').forEach(actions=>{if(!actions.children.length)actions.innerHTML='<span class="context-auto-status">系统会自动准备本场上下文</span>'});
}
render=function(){finalRenderBase();decorateVolumeTree();decorateTopStatus();cleanAutomaticContextControls();if(state.stage==='draft'&&state.sceneId)queueMicrotask(()=>ensureSceneContext(state.sceneId));};

/* Works is a whole-story surface. Keep chapter workflow out of this rail and
   make the real discussion visible in the center of the workbench. */
function renderWorkRail(){
  const rail=$('#stageList'),tree=$('#sceneTree');
  if(!rail||!state.work)return;
  const worksSurface=state.surface==='works';
  const note=$('.work-surface-note');
  if(!worksSurface){
    if(!rail.classList.contains('stage-list')||rail.classList.contains('work-agent-rail')||rail.classList.contains('work-nav-list')){
      rail.className='stage-list';
      rail.setAttribute('aria-label','章节写作流程');
      rail.innerHTML='<li><button data-stage="structure"><span>01</span><b>章节细纲</b><small>只规划当前章节</small></button></li><li><button data-stage="draft"><span>02</span><b>逐场写作</b><small>候选、Diff 与正文</small></button></li><li><button data-stage="release"><span>03</span><b>检查并发布</b><small>冻结 ScriptRelease</small></button></li>';
    }
    if(note)note.hidden=false;
    if(note)note.innerHTML='<p>写作栏目</p><b>当前章节与正文</b><small>章内细纲、场景写作和发布检查都绑定当前写作目标。</small><button type="button" class="quiet" data-work-surface="discussion">返回全作讨论</button>';
    return;
  }
  const active=key=>key==='discussion'&&state.stage==='overview'||key==='direction'&&['brief','blueprint'].includes(state.stage)||key==='library'&&state.stage==='references'||key==='structure'&&state.stage==='structure';
  const formal=blueprintIsConfirmed();
  rail.className='work-nav-list';
  rail.setAttribute('aria-label','作品工作面');
  rail.innerHTML=`<li><button type="button" class="work-nav-item ${active('discussion')?'active':''}" data-work-surface="discussion"><span>01</span><b>全作讨论</b><small>和创作导演讨论整篇作品</small></button></li><li><button type="button" class="work-nav-item ${active('direction')?'active':''}" data-work-surface="direction"><span>02</span><b>全作方向</b><small>把讨论整理成正式方案</small></button></li><li><button type="button" class="work-nav-item ${active('structure')?'active':''}" data-work-surface="structure" ${formal?'':'disabled'}><span>03</span><b>作品结构</b><small>${formal?'管理卷、章与场景':'确认全作方向后开放'}</small></button></li><li class="work-nav-utility"><span>作品工具</span><button type="button" class="work-resource-entry ${active('library')?'active':''}" data-work-surface="library"><b>创作资料</b><small>人物、世界观、事实与证据 · AI 协作维护</small></button></li>`;
  if(tree)tree.replaceChildren();
  if(note)note.innerHTML='<p>作品栏目</p><b>先讨论整篇作品</b><small>这里处理全作方向、人物和世界观；章节正文在“写作”里进行。</small>';
}

function renderWorkDiscussion(){
  const thread=workConversationThread(),proposal=workPlanProposal(),task=conversationTaskContract(thread);
  const messages=thread?.messages||[];
  const panel=document.createElement('section');
  panel.className='work-discussion-panel';
  panel.id='workDiscussion';
  panel.innerHTML=`<header class="work-discussion-header"><div><p class="eyebrow">WORK / DISCUSSION</p><h3>全作讨论</h3><p>这里讨论整篇作品：故事方向、人物关系、世界观边界和整体节奏。聊清楚后，再整理为正式方案。</p></div><div class="work-discussion-header-actions"><span class="scope-chip">作品级</span><span class="agent-provider-chip">${state.capabilities?.providers?.[0]?.is_simulation?'本地模拟':'已连接'}</span></div></header><div class="work-discussion-scope"><div><b>当前讨论范围</b><span>整篇作品 · 不写入章节正文</span></div><button class="quiet" type="button" data-work-surface="direction">查看正式方向</button></div><div class="work-discussion-history" data-work-discussion-scroll>${conversationHistoryMarkup(messages)||'<p class="conversation-empty">从这里开始说。你可以先讲一个想法，也可以反悔、补充或比较多个方向。</p>'}</div>${directorPendingMarkup(proposal)}${thread?`<form id="workConversationForm" class="conversation-composer work-discussion-composer"><label><span class="sr-only">给创作导演发送消息</span><textarea name="text" required placeholder="补充想法、推翻刚才的方向，或告诉 AI 哪些地方需要注意……"></textarea></label><div class="composer-actions"><div class="composer-tools">${renderPermissionMenu(thread)}${renderConversationAction(task,proposal)}</div><button class="primary" type="submit" title="发送消息">发送</button></div></form>`:'<div class="notice">当前作品还没有创作主对话，重新打开作品后会自动恢复。</div>'}</section>`;
  const scroll=panel.querySelector('[data-work-discussion-scroll]');
  if(scroll)scroll.scrollTop=scroll.scrollHeight;
  return panel;
}

const renderChromeBeforeWorkRail=renderChrome;
renderChrome=function(){
  renderChromeBeforeWorkRail();renderWorkRail();
  if(state.work&&state.surface==='works'&&state.stage==='structure')$('#crumb').textContent=`${state.work.title} / 作品结构`;
  const primarySection=state.mobileView==='tasks'?'tasks':state.stage==='references'?'references':state.surface;
  $$('[data-section]').forEach(button=>button.classList.toggle('active',button.dataset.section===primarySection));
  if(state.surface==='works'&&state.mobileView==='writing'){
    const mobileSection=state.stage==='references'?'references':'works';
    $$('[data-mobile]').forEach(button=>button.classList.toggle('active',button.dataset.mobile===mobileSection));
  }
};

const renderBeforeWorkDiscussion=render;
render=function(){
  if(state.stage==='overview'&&state.mobileView==='writing')state.inspector='decision';
  renderBeforeWorkDiscussion();
  const workspace=$('#workspace');
  if(workspace&&state.stage==='overview'&&state.mobileView==='writing'){
    $('#sceneTree')?.replaceChildren();
    workspace.querySelector('#workDiscussion')?.remove();
    const overview=workspace.querySelector('.overview-workbench');
    const header=overview?.querySelector('.overview-header');
    if(header)header.insertAdjacentElement('afterend',renderWorkDiscussion());
  }
};

document.addEventListener('click',event=>{
  const toggle=event.target.closest('[data-work-surfaces-toggle]');
  if(toggle&&state.work){event.preventDefault();event.stopImmediatePropagation();state.showGlobalSurfaces=true;render();return;}
  const surface=event.target.closest('[data-work-surface]');
  if(surface&&state.work){
    event.preventDefault();event.stopImmediatePropagation();
    const key=surface.dataset.workSurface;
    if(key==='discussion'){state.stage='overview';state.mobileView='writing';}
    else if(key==='direction'){state.stage=brief()?'blueprint':'brief';state.mobileView='writing';}
    else if(key==='library'){state.stage='references';state.mobileView='writing';state.libraryView='overview';}
    else if(key==='structure'){
      const gate=stageGate('structure');
      if(!gate.allowed){toast(`尚未开放作品结构：${gate.reason}`,true);return;}
      state.stage='structure';state.mobileView='writing';
    }else if(key==='writing'){
      state.stage=blueprintIsConfirmed()?'structure':'brief';state.mobileView='writing';
    }
    render();return;
  }
  const focus=event.target.closest('[data-focus-discussion]');
  if(focus&&state.work){
    event.preventDefault();event.stopImmediatePropagation();
    state.stage='overview';state.mobileView='writing';state.inspector='decision';render();
    setTimeout(()=>document.querySelector('#workDiscussion textarea')?.focus(),0);
  }
},true);

// Final presentation pass. These wrappers must remain after the legacy
// vertical-slice overrides above.
const renderStructureBeforeFinalCompact=renderStructure;
renderStructure=function(el){
  renderStructureBeforeFinalCompact(el);
  const inner=$('.workspace-inner',el);if(!inner)return;
  const title=inner.querySelector('h2'),lede=inner.querySelector('.lede');
  if(title)title.textContent=state.surface==='works'?'作品结构':'章节与场景';
  if(lede)lede.textContent=state.surface==='works'?'在这里管理整部作品的卷、章与场景顺序。进入“写作”后，再围绕当前章节细化和起草正文。':'选择当前章节，再管理这一章的场景。全作方向、人物和世界观请回到“作品”。';
  inner.querySelectorAll('.structure-scope-note').forEach(node=>node.remove());
  const targets=[...inner.querySelectorAll('.writing-target-bar')];
  targets.slice(1).forEach(node=>node.remove());
  if(state.surface==='works')targets.forEach(node=>node.remove());
  else targets[0]?.classList.add('writing-target-compact');
  if(state.surface==='works')inner.querySelectorAll('.chapter-plan-summary').forEach(node=>node.remove());
  inner.querySelector('[data-structure-add-volume]')?.classList.replace('primary','quiet');
};

const renderMobileTasksBeforeFinalFeedback=renderMobileTasks;
renderMobileTasks=function(el){
  renderMobileTasksBeforeFinalFeedback(el);
  const actions=el.querySelector('.actions');
  if(actions&&!actions.querySelector('[data-action="feedback"]'))actions.insertAdjacentHTML('beforeend','<button class="quiet" type="button" data-action="feedback">反馈问题</button>');
};

const renderBeforeFinalGuidance=render;
render=function(){renderBeforeFinalGuidance();decorateCurrentStepGuidance()};

// The creative library is a work surface, not a second global workspace.
// Keep its maintenance forms closed until the user explicitly opens one.
function compactCreativeLibrary(){
  const library=$('#workspace .library-workbench');
  if(!library)return;
  const header=library.querySelector('.library-header');
  if(header){
    const title=header.querySelector('h2'),lede=header.querySelector('p:not(.eyebrow)');
    if(title)title.textContent='当前作品 · 创作资料';
    if(lede)lede.textContent='角色卡、世界观、事实、关系和证据都绑定当前作品。AI 会从讨论与正文中提出维护候选，采纳前不会改写正式资料。';
    if(!header.nextElementSibling?.classList.contains('library-scope-banner'))header.insertAdjacentHTML('afterend',`<div class="library-scope-banner"><div><span>当前作品</span><b>${esc(state.work?.title||'未选择作品')}</b></div><div><span>AI 维护状态</span><small>候选进入 Proposal · 你只在需要时审核</small></div><span class="status-chip">作品范围</span></div>`);
  }
  const view=state.libraryView;
  const editor=library.querySelector('.library-editor');
  const open=Boolean(state.libraryEditorOpen||state.editCardId||state.characterCardDraft||state.editWorldEntry||state.editCanonFactId);
  editor?.classList.toggle('library-editor-collapsed',!open);
  const pageHead=library.querySelector('.library-page-head');
  if(pageHead&&!pageHead.querySelector('[data-library-open-editor]')){
    const type=view==='canon'?'canon':view==='files'?'files':view==='timeline'?'timeline':view==='rules'?'rules':'';
    if(type)pageHead.insertAdjacentHTML('beforeend',`<button class="quiet library-open-editor" type="button" data-library-open-editor="${type}">${type==='canon'?'新增作品事实':type==='files'?'登记证据资料':type==='timeline'?'添加时间线事件':'新增世界规则'}</button>`);
  }
}

const renderBeforeCompactCreativeLibrary=render;
render=function(){renderBeforeCompactCreativeLibrary();compactCreativeLibrary();};

// In the integrated shell, a handed-off release remains a useful navigation
// target instead of becoming a permanently disabled button.
const renderReleaseBeforeProductionNavigation=renderRelease;
renderRelease=function(el){
  renderReleaseBeforeProductionNavigation(el);
  for(const release of state.work?.releases||[]){
    const button=[...el.querySelectorAll('[data-handoff]')].find(item=>item.dataset.handoff===release.id);
    if(!button)continue;
    button.dataset.workId=state.work.id;
    button.dataset.releaseId=release.id;
    if(!release.production_run_id)continue;
    button.disabled=false;
    delete button.dataset.handoff;
    button.dataset.openProduction=release.production_run_id;
    button.dataset.workId=state.work.id;
    button.dataset.releaseId=release.id;
    button.textContent='打开 AA 制作任务';
  }
};

function currentFlowStages(){
  if(state.surface==='works'){
    return ['overview',blueprint()?'blueprint':'brief','structure'];
  }
  return FLOW_STAGES;
}
function syncFlowNavigation(){
  const stages=currentFlowStages(),index=stages.indexOf(state.stage);
  const previous=$('[data-flow-nav="previous"]'),next=$('[data-flow-nav="next"]');
  const configure=(button,target,direction)=>{
    if(!button)return;
    const gate=target?stageGate(target):{allowed:false,reason:''};
    button.disabled=!target||!gate.allowed;
    button.dataset.flowTarget=target||'';
    button.setAttribute('aria-disabled',String(button.disabled));
    button.title=!target?(direction==='previous'?'已经是第一步':'已经是最后一步'):gate.allowed?`${direction==='previous'?'返回':'进入'}：${stageLabel(target)}`:`尚未解锁：${gate.reason}`;
  };
  configure(previous,index>0?stages[index-1]:'','previous');
  configure(next,index>=0&&index<stages.length-1?stages[index+1]:'','next');
}

const renderBeforeFlowNavigation=render;
render=function(){renderBeforeFlowNavigation();syncFlowNavigation();};

document.addEventListener('click',event=>{
  const button=event.target.closest('button[data-flow-nav]');
  if(!button)return;
  event.preventDefault();
  event.stopImmediatePropagation();
  const target=button.dataset.flowTarget;
  if(target)navigateToStage(target);
},true);

/* Unified work Agent surface. Whole-work discovery, direction and structure
   are outputs in one durable conversation instead of three competing pages. */
function workAgentToolMarkup(content={}){
  const contract=content.task_contract||{};
  const trace=content.agent_trace||{};
  const activity=Array.isArray(trace.steps)?trace.steps:(Array.isArray(content.tool_activity)?content.tool_activity:[]);
  const rows=[...activity];
  if(!rows.length&&contract.id)rows.push({tool:'load_workflow_template',label:'加载 BA 写作工作流',status:'succeeded',output:contract.id});
  if(!rows.length)return'';
  const status=trace.status||'completed';
  const completeCount=rows.filter(item=>item.status==='succeeded').length;
  const statusLabel={completed:'已完成',running:'执行中',failed:'有步骤失败',waiting_user:'等待决定'}[status]||'已记录';
  const scope=trace.scope||contract.task_scope||{};
  const scopeLabel=scope.surface==='chapter'?(scope.chapter_title?`章节 · ${scope.chapter_title}`:'当前章节'):'作品全局';
  const summary=trace.summary||'读取当前任务契约和作品上下文，再决定这一轮的安全输出。';
  const reasoning=trace.reasoning||{};
  const reasoningLabel=reasoning.source==='provider'?(reasoning.is_simulation?'模拟思考摘要':'模型思考摘要'):'执行摘要';
  const reasoningContent=reasoning.mode==='chain'&&reasoning.content?`<div class="agent-reasoning-chain"><span>模型思考链</span><pre>${esc(reasoning.content)}</pre></div>`:'';
  const outcome=trace.outcome||'本轮没有静默修改正式产物。';
  return `<details class="agent-process" data-status="${esc(status)}"><summary><span class="agent-process-mark" aria-hidden="true">${status==='failed'?'!':status==='running'?'…':'✓'}</span><div><b>思考与执行</b><small>${esc(scopeLabel)} · ${completeCount}/${rows.length} 步完成${reasoningContent?' · 含模型思考链':''}</small></div><span class="agent-process-state">${esc(statusLabel)}</span></summary><div class="agent-process-body"><div class="agent-process-summary"><span>${esc(reasoningLabel)}</span><p>${esc(summary)}</p></div>${reasoningContent}<ol class="agent-process-steps">${rows.map(item=>{const itemStatus=item.status||'succeeded';const itemLabel={succeeded:'完成',running:'执行中',failed:'失败',waiting_user:'待确认',queued:'排队'}[itemStatus]||'完成';return`<li data-status="${esc(itemStatus)}"><span class="agent-step-dot" aria-hidden="true"></span><div><b>${esc(item.label||item.tool||'执行步骤')}</b><code>${esc(item.tool||'agent_step')}</code>${item.output?`<p>${esc(item.output)}</p>`:''}</div><em>${esc(itemLabel)}</em></li>`}).join('')}</ol><div class="agent-process-outcome"><span>结果边界</span><p>${esc(outcome)}</p></div></div></details>`;
}

function workAgentDraftMarkup(content={},message={}){
  const draft=content.artifact_preview;
  if(!draft)return'';
  const character=draft.kind==='character_card';
  const expectedProposalKind=character?'character_card':'world_entity';
  const linkedProposal=(state.work?.proposals||[]).find(item=>item.kind===expectedProposalKind&&item.candidate?.source_message_ids?.includes(message.id));
  const proposalId=content.proposal_id||message.proposal_id||linkedProposal?.id||'';
  const proposal=proposalId?(state.work?.proposals||[]).find(item=>item.id===proposalId):null;
  const discussionAlreadyOrganized=draft.status==='discussion_draft'&&Boolean(linkedProposal);
  const status=discussionAlreadyOrganized?'organized':(proposal?.status||draft.status||'discussion_draft');
  const labels={discussion_draft:'对话草稿 · 尚未写入正式资料',organized:'讨论草稿 · 已整理为下方候选',proposal:'资料候选 · 等待你决定',pending:'资料候选 · 等待你决定',accepted:'已写入正式资料',rejected:'已退回 · 正式资料未改变',superseded:'已过期 · 需要重新整理'};
  let actions='';
  if(status==='discussion_draft'){
    actions=`<div class="artifact-decision-actions"><button type="button" class="primary" data-agent-propose-knowledge="${character?'character_card':'world_card'}">${character?'整理为人物卡候选':'整理为世界观候选'}</button><button type="button" class="quiet" data-agent-continue-draft="${character?'请继续和我讨论这张人物卡：':'请继续和我讨论这条世界观：'}${esc(draft.title||'')}">继续讨论</button></div>`;
  }else if(status==='organized'){
    const decision={pending:'等待你决定',accepted:'已采用',rejected:'已退回',superseded:'已过期'}[linkedProposal?.status]||'已整理';
    actions=`<div class="artifact-result compact"><div><b>已整理为资料候选</b><small>${decision}，请查看下方候选卡。</small></div></div>`;
  }else if(status==='proposal'||status==='pending'){
    actions=`<div class="artifact-decision-actions"><button class="primary" type="button" data-accept-director-proposal="${esc(proposalId)}">采用并写入正式资料</button><button class="quiet" type="button" data-reject-director-proposal="${esc(proposalId)}">退回继续讨论</button></div>`;
  }else if(status==='accepted'){
    actions=`<div class="artifact-result accepted"><span aria-hidden="true">✓</span><div><b>已建立正式修订</b><small>后续修改仍会保留版本与来源</small></div><button type="button" class="quiet" data-agent-open-library="${character?'characters':'world'}">在资料栏查看</button></div>`;
  }else{
    actions=`<div class="artifact-result"><div><b>${status==='rejected'?'这份候选已退回':'这份候选已失效'}</b><small>你可以继续对话，让 Agent 重新整理。</small></div></div>`;
  }
  const question=status==='discussion_draft'?`<div class="artifact-question"><span>Agent 下一步需要确认</span><b>${esc(draft.next_question||'继续补充这项设定。')}</b></div>`:'';
  return `<details class="agent-inline-artifact draft ${esc(status)}" ${status==='organized'?'':'open'}><summary><span class="artifact-kind">${character?'人物卡':'世界观卡'}</span><div><b>${esc(draft.title||'讨论草稿')}</b><small>${esc(labels[status]||labels.discussion_draft)}</small></div><span class="artifact-open-label">展开</span></summary><div class="agent-inline-artifact-body"><p>${esc(draft.summary||'')}</p>${question}${actions}</div></details>`;
}

function messageAttachmentsMarkup(message){
  const items=Array.isArray(message?.content?.attachments)?message.content.attachments:[];
  if(!items.length)return'';
  return `<div class="message-attachments">${items.map(item=>`<a href="${esc(item.content_url||`/api/v1/works/${state.work.id}/attachments/${item.id}/content`)}" target="_blank" rel="noreferrer" title="打开原图"><img src="${esc(item.content_url||`/api/v1/works/${state.work.id}/attachments/${item.id}/content`)}" alt="${esc(item.filename||'对话图片')}"><span>${esc(item.filename||'图片')}</span></a>`).join('')}</div>`;
}

renderConversationMessage=function(message){
  const assistant=message.role==='assistant',content=message.content||{},questions=Array.isArray(content.questions)?content.questions:[];
  return `<article class="conversation-message ${assistant?'assistant':'user'}"><div class="message-avatar" aria-hidden="true">${assistant?'HC':'你'}</div><div class="message-column"><div class="message-role">${assistant?'HaloCue 创作导演':'你'}${assistant&&message.provider?.is_simulation?'<span>模拟</span>':''}</div><div class="message-bubble">${messageAttachmentsMarkup(message)}${assistant?workAgentToolMarkup(content):''}<p>${esc(messageText(message))}</p>${questions.length?`<div class="agent-questions">${questions.map(item=>`<button type="button" data-agent-continue-draft="${esc(item)}">${esc(item)}</button>`).join('')}</div>`:''}${workAgentDraftMarkup(content,message)}${content.simulation_notice?`<small class="simulation-note">${esc(content.simulation_notice)}</small>`:''}</div></div></article>`;
};

function currentWorkArtifactMarkup(){
  const artifacts=state.work?.artifacts||[];
  const briefArtifact=artifacts.find(item=>item.kind==='brief'),briefContent=briefArtifact?.current_revision?.content;
  const blueprintArtifact=artifacts.find(item=>item.kind==='story_blueprint'),plan=blueprintArtifact?.current_revision?.content;
  const characterCards=artifacts.filter(item=>item.kind==='character_card').map(item=>item.current_revision?.content).filter(Boolean);
  const world=artifacts.find(item=>item.kind==='world_bible')?.current_revision?.content||{};
  const confirmedWorld=(world.entities||[]).filter(item=>item.confidence_status==='confirmed'&&item.status!=='archived');
  const canon=artifacts.find(item=>item.kind==='work_canon')?.current_revision?.content||{};
  const volumes=state.work?.volumes||[],chapters=volumes.flatMap(volume=>volume.chapters||[]),sceneList=chapters.flatMap(chapter=>chapter.scenes||[]);
  if(!briefContent&&!plan&&!characterCards.length&&!confirmedWorld.length&&!chapters.length)return'';
  const idea=briefContent?.idea||plan?.premise||'尚未形成正式创作想法';
  const ideaCard=briefContent||plan?`<details class="agent-inline-artifact idea" open><summary><span class="artifact-kind">创作想法</span><div><b>${esc(plan?.title||state.work.title)}</b><small>Brief + StoryBlueprint · 已采用</small></div><span class="artifact-open-label">展开</span></summary><div class="agent-inline-artifact-body"><p class="artifact-premise">${esc(idea)}</p>${plan?.central_conflict?`<div class="artifact-field"><span>核心变化</span><b>${esc(plan.central_conflict)}</b></div>`:''}${plan?.direction?.length?`<ol>${plan.direction.map(item=>`<li>${esc(item)}</li>`).join('')}</ol>`:''}</div></details>`:'';
  return `<article class="conversation-message assistant artifact-message"><div class="message-avatar" aria-hidden="true">HC</div><div class="message-column"><div class="message-role">HaloCue 创作导演<span>正式上下文</span></div><div class="message-bubble"><p>我已经读取这部作品现有的正式产物和稳定结构。它们分别保存、可以追溯，不会混进聊天文本。</p><div class="agent-tool-stack"><details class="agent-tool-run"><summary><span class="agent-tool-icon">✓</span><b>读取作品正式上下文</b><em>完成</em></summary><div><code>read_work_context</code><span>${artifacts.length} 项正式产物</span></div></details></div><div class="agent-artifact-grid">${ideaCard}<details class="agent-inline-artifact"><summary><span class="artifact-kind">人物</span><div><b>${characterCards.length?characterCards.map(card=>esc(card.name)).join('、'):'待讨论'}</b><small>${characterCards.length} 张正式人物卡</small></div><span class="artifact-open-label">展开</span></summary><div class="agent-inline-artifact-body">${characterCards.length?characterCards.map(card=>`<div class="artifact-person"><span>${esc((card.name||'?').slice(0,1))}</span><div><b>${esc(card.name||'未命名')}</b><small>${esc(card.role||card.voice_anchors?.[0]||'已建立人物边界')}</small></div></div>`).join(''):'<p>还没有正式人物卡。可以直接在对话里让 Agent 先提出讨论草稿。</p>'}<button type="button" class="quiet" data-agent-open-library="characters">在资料栏查看人物卡</button></div></details><details class="agent-inline-artifact"><summary><span class="artifact-kind">世界与事实</span><div><b>${confirmedWorld.length} 条设定 · ${(canon.facts||[]).length} 条事实</b><small>仅统计已经确认的内容</small></div><span class="artifact-open-label">展开</span></summary><div class="agent-inline-artifact-body">${confirmedWorld.slice(0,3).map(item=>`<div class="artifact-line"><b>${esc(item.name)}</b><span>${esc(item.summary)}</span></div>`).join('')||'<p>当前没有已确认的世界观卡。</p>'}<button type="button" class="quiet" data-agent-open-library="world">在资料栏查看世界观</button></div></details><details class="agent-inline-artifact structure"><summary><span class="artifact-kind">作品结构</span><div><b>${volumes.length} 卷 · ${chapters.length} 章 · ${sceneList.length} 场</b><small>由稳定 ID 维持，不依赖标题</small></div><span class="artifact-open-label">展开</span></summary><div class="agent-inline-artifact-body">${volumes.map((volume,index)=>`<div class="artifact-structure-line"><span>卷 ${String(index+1).padStart(2,'0')}</span><div><b>${esc(volume.title)}</b><small>${(volume.chapters||[]).map(chapter=>esc(chapter.title)).join(' · ')||'尚无章节'}</small></div></div>`).join('')||'<p>尚未建立作品结构。</p>'}<button type="button" class="primary" data-section="writing">进入章节写作</button></div></details></div></div></div></article>`;
}

function workAgentProposalMarkup(proposal){
  if(!proposal)return'';
  const candidate=proposal.candidate||{},plan=candidate.story_blueprint||{},briefCandidate=candidate.brief||{};
  return `<article class="conversation-message assistant proposal-message"><div class="message-avatar" aria-hidden="true">HC</div><div class="message-column"><div class="message-role">HaloCue 创作导演<span>需要你决定</span></div><div class="message-bubble"><p>我把目前讨论整理成了一份故事方向。它现在只是 Proposal，采纳前不会修改正式创作想法。</p><details class="agent-inline-artifact proposal" open><summary><span class="artifact-kind">方向候选</span><div><b>${esc(plan.title||'故事方向方案')}</b><small>Proposal · 未写入</small></div><span class="artifact-open-label">展开</span></summary><div class="agent-inline-artifact-body"><p class="artifact-premise">${esc(plan.premise||briefCandidate.idea||'')}</p>${plan.central_conflict?`<div class="artifact-field"><span>核心变化</span><b>${esc(plan.central_conflict)}</b></div>`:''}${plan.direction?.length?`<ol>${plan.direction.map(item=>`<li>${esc(item)}</li>`).join('')}</ol>`:''}<div class="artifact-decision-actions"><button class="primary" type="button" data-accept-director-proposal="${esc(proposal.id)}">采纳为正式方向</button><button class="quiet" type="button" data-reject-director-proposal="${esc(proposal.id)}">退回继续讨论</button></div></div></details></div></div></article>`;
}

function renderUnifiedWorkAgent(){
  const thread=workConversationThread(),proposal=workPlanProposal(),task=conversationTaskContract(thread),messages=thread?.messages||[];
  const providerSimulated=Boolean(state.capabilities?.providers?.[0]?.is_simulation);
  const taskLabels={
    'brief.build':'理解你的想法，确认接下来最值得讨论的问题',
    'blueprint.generate':'继续讨论整篇作品，并把共识整理成方向候选',
    'structure.plan':'根据已经确认的方向，维护卷、章和场景骨架',
    'scene.draft.generate':'围绕当前场景讨论目标和修改要求',
    'release.review':'检查全篇连续性、人物一致性和未决伏笔'
  };
  const taskLabel=taskLabels[task?.id]||'继续理解作品，并维护清晰、可追溯的创作成果';
  return `<main class="work-agent-canvas"><header class="work-agent-header"><div class="work-agent-identity"><span class="work-agent-mark">HC</span><div><p class="eyebrow">WORK AGENT</p><h2>${esc(state.work.title)}</h2><p>和创作导演持续讨论整篇作品。方向、人物、世界观和结构会以可审查产物出现在对话中。</p></div></div><div class="work-agent-status"><span class="scope-chip">整部作品</span><span class="agent-provider-chip">${providerSimulated?'本地模拟':'模型已连接'}</span></div></header><section class="work-agent-task"><div><span>Agent 当前任务</span><b>${esc(taskLabel)}</b></div><small>正式修改会先交给你确认</small></section><section class="work-agent-thread" data-work-discussion-scroll>${currentWorkArtifactMarkup()}${messages.length?conversationHistoryMarkup(messages):'<div class="work-agent-empty"><span>HC</span><h3>从一个想法开始</h3><p>你可以补充、反悔或推翻前面的方向。Agent 会自己判断下一步应该讨论人物、世界观还是故事结构。</p><div><button type="button" data-agent-continue-draft="先复述你对这部作品的理解，并指出目前最关键的不确定项。">复述当前理解</button><button type="button" data-agent-continue-draft="检查目前还缺少哪些人物卡或世界观依据。">检查创作资料</button></div></div>'}${workAgentProposalMarkup(proposal)}</section>${thread?`<form id="workConversationForm" class="conversation-composer work-agent-composer"><label><span class="sr-only">给创作导演发送消息</span><textarea name="text" required placeholder="告诉 Agent 你的想法，或要求它创建人物卡、整理世界观、调整故事方向……"></textarea></label><div class="composer-actions"><div class="composer-tools">${renderPermissionMenu(thread)}${renderConversationAction(task,proposal)}</div><button class="primary" type="submit" title="发送消息">发送</button></div></form>`:'<div class="notice">当前作品主对话未能恢复。</div>'}</main>`;
}

function renderUnifiedWorkRail(){
  const rail=$('#stageList'),tree=$('#sceneTree'),note=$('.work-surface-note');
  if(!rail)return;
  rail.className='work-agent-rail';
  rail.setAttribute('aria-label','作品 Agent');
  rail.innerHTML=`<li><button type="button" class="work-agent-rail-item active" data-work-surface="discussion"><span class="work-agent-rail-avatar">HC</span><div><b>作品 Agent</b><small>全作讨论与产物维护</small></div></button></li><li class="work-agent-rail-state"><span>当前作品</span><b>${esc(state.work.title)}</b><small>${workConversationThread()?.messages?.length||0} 条对话 · ${state.work.artifacts?.length||0} 项正式产物</small></li>`;
  tree?.replaceChildren();
  if(note)note.innerHTML='<p>作品栏目</p><b>一个持续的创作对话</b><small>人工资料管理请使用主导航中的“资料”。</small>';
}

const renderBeforeUnifiedWorkAgent=render;
render=function(){
  if(state.work&&state.surface==='works'&&['brief','blueprint','structure'].includes(state.stage))state.stage='overview';
  renderBeforeUnifiedWorkAgent();
  const active=Boolean(state.work&&state.surface==='works'&&state.mobileView==='writing'&&state.stage==='overview');
  const libraryActive=Boolean(state.work&&state.mobileView==='writing'&&state.stage==='references');
  $('#app')?.classList.toggle('work-agent-stage',active);
  $('#app')?.classList.toggle('library-stage',libraryActive);
  if(!active)return;
  renderUnifiedWorkRail();
  const workspace=$('#workspace');
  if(workspace)workspace.innerHTML=renderUnifiedWorkAgent();
  if($('#crumb'))$('#crumb').textContent=`${state.work.title} / 作品 Agent`;
  const scroll=workspace?.querySelector('[data-work-discussion-scroll]');
  if(scroll)scroll.scrollTop=(workConversationThread()?.messages?.length||0)?scroll.scrollHeight:0;
};

document.addEventListener('click',event=>{
  const continueButton=event.target.closest('[data-agent-continue-draft]');
  if(continueButton){
    event.preventDefault();event.stopImmediatePropagation();
    const input=$('#workConversationForm textarea');
    if(input){input.value=continueButton.dataset.agentContinueDraft||'';input.focus();input.setSelectionRange(input.value.length,input.value.length)}
    return;
  }
  const libraryButton=event.target.closest('[data-agent-open-library]');
  if(libraryButton){
    event.preventDefault();event.stopImmediatePropagation();
    state.stage='references';state.mobileView='writing';state.libraryView=libraryButton.dataset.agentOpenLibrary;state.libraryEditorOpen=false;render();
  }
},true);

document.addEventListener('click',event=>{
  const button=event.target.closest('[data-agent-propose-knowledge]');
  if(!button||!state.work)return;
  event.preventDefault();event.stopImmediatePropagation();
  const thread=workConversationThread(),kind=button.dataset.agentProposeKnowledge;
  if(!thread)return;
  button.disabled=true;
  (async()=>{try{
    setBusy('Agent 正在整理资料候选');
    const result=await api(`/works/${state.work.id}/threads/${thread.id}/knowledge:propose`,{method:'POST',body:JSON.stringify({expected_version:state.work.version,expected_thread_version:thread.version,kind})});
    state.work=result.work;
    setBusy('资料候选等待决定');
    toast(kind==='character_card'?'人物卡候选已生成，采纳前不会写入资料库':'世界观候选已生成，采纳前不会写入资料库');
    render();
  }catch(error){setBusy('未能整理资料候选');toast(error.message,true);button.disabled=false}
  })();
},true);

/* ==========================================================================
   HaloCue 1.0 统一设置中心控制器 (Settings Controller)
   ========================================================================== */
const SettingsController = {
  dialog: null,
  cachedPresets: [
    { id: 'deepseek', name: 'DeepSeek 官方', provider: 'openai', base_url: 'https://api.deepseek.com/v1', default_model: 'deepseek-chat', models: ['deepseek-chat', 'deepseek-reasoner'] },
    { id: 'siliconflow', name: '硅基流动', provider: 'openai', base_url: 'https://api.siliconflow.cn/v1', default_model: 'deepseek-ai/DeepSeek-V3', models: ['deepseek-ai/DeepSeek-V3', 'deepseek-ai/DeepSeek-R1', 'Qwen/Qwen2.5-72B-Instruct'] },
    { id: 'zhipu', name: '智谱 GLM', provider: 'openai', base_url: 'https://open.bigmodel.cn/api/paas/v4', default_model: 'glm-4-plus', models: ['glm-4-plus', 'glm-4-flash', 'glm-4-long'] },
    { id: 'moonshot', name: '月之暗面 Kimi', provider: 'openai', base_url: 'https://api.moonshot.cn/v1', default_model: 'moonshot-v1-auto', models: ['moonshot-v1-auto', 'moonshot-v1-8k', 'moonshot-v1-32k', 'moonshot-v1-128k'] },
    { id: 'qwen', name: '通义千问 Qwen', provider: 'openai', base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1', default_model: 'qwen-max', models: ['qwen-max', 'qwen-plus', 'qwen-turbo'] },
    { id: 'ollama', name: '本地 Ollama', provider: 'openai', base_url: 'http://127.0.0.1:11434/v1', default_model: 'qwen2.5:7b', models: ['qwen2.5:7b', 'deepseek-r1:7b', 'llama3.1:8b'] },
    { id: 'openai', name: 'OpenAI 官方', provider: 'openai', base_url: 'https://api.openai.com/v1', default_model: 'gpt-4o', models: ['gpt-4o', 'gpt-4o-mini', 'o3-mini', 'o1-preview'] },
    { id: 'anthropic', name: 'Anthropic Claude', provider: 'anthropic', base_url: 'https://api.anthropic.com', default_model: 'claude-3-5-sonnet-20241022', models: ['claude-3-5-sonnet-20241022', 'claude-3-5-haiku-20241022', 'claude-3-opus-20240229'] },
  ],
  activeTab: 'models',
  activePresetId: 'deepseek',

  init() {
    this.dialog = document.getElementById('settingsDialog');
    if (!this.dialog) return;

    // Open / Close Settings. Feedback has its own persisted controller above.
    const feedbackDialog = document.getElementById('feedbackDialog');

    document.addEventListener('click', (e) => {
      const openBtn = e.target.closest('[data-action="settings"], #openSettingsButton');
      const closeBtn = e.target.closest('[data-close-settings]');

      if (openBtn) {
        e.preventDefault();
        e.stopImmediatePropagation();
        this.open();
        return;
      }
      if (closeBtn) {
        e.preventDefault();
        e.stopImmediatePropagation();
        this.close();
        return;
      }
    }, true);

    const urlParams = new URLSearchParams(window.location.search);
    if (urlParams.get('open_feedback') === '1') {
      feedbackDialog?.showModal();
    }

    // Tab navigation & Preset card clicks & Eye toggle
    this.dialog.addEventListener('click', (e) => {
      const tabBtn = e.target.closest('.settings-nav-btn[data-tab]');
      if (tabBtn) {
        this.switchTab(tabBtn.dataset.tab);
        return;
      }

      const vendorCard = e.target.closest('.vendor-card[data-preset-id]');
      if (vendorCard) {
        this.selectPreset(vendorCard.dataset.presetId);
        return;
      }

      const eyeBtn = e.target.closest('#toggleApiKeyVisibility');
      if (eyeBtn) {
        const input = document.getElementById('settingsApiKey');
        if (input) {
          const isPass = input.type === 'password';
          input.type = isPass ? 'text' : 'password';
          eyeBtn.textContent = isPass ? '隐藏' : '显示';
        }
        return;
      }
    });

    // Fetch Models button
    document.getElementById('fetchModelsBtn')?.addEventListener('click', async () => {
      await this.fetchModels();
    });

    // Test Connection button
    document.getElementById('testConnectionBtn')?.addEventListener('click', async () => {
      await this.testConnection();
    });

    // Model Form Submit (Save & Apply instantly)
    document.getElementById('settingsModelForm')?.addEventListener('submit', async (e) => {
      e.preventDefault();
      await this.saveModel(e.target);
    });

    // AA Inspector button
    document.getElementById('inspectAaBtn')?.addEventListener('click', async () => {
      await this.inspectAa();
    });

    // AA Adopt button
    document.getElementById('adoptAaBtn')?.addEventListener('click', async () => {
      await this.adoptAa();
    });

    // Preferences Form Submit
    document.getElementById('preferencesForm')?.addEventListener('submit', async (e) => {
      e.preventDefault();
      await this.savePreferences(e.target);
    });

    // Data maintenance actions
    document.getElementById('backupDataBtn')?.addEventListener('click', () => {
      this.exportBackup();
    });

    document.getElementById('clearTempCacheBtn')?.addEventListener('click', () => {
      toast('临时预览缓存已清理完成');
    });

    // Initial load of model status for topbar badge
    this.refreshTopBarBadge();
  },

  async open() {
    this.dialog?.showModal();
    await this.loadAll();
  },

  close() {
    this.dialog?.close();
  },

  switchTab(tabName) {
    this.activeTab = tabName;
    this.dialog.querySelectorAll('.settings-nav-btn').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.tab === tabName);
    });
    this.dialog.querySelectorAll('.settings-pane').forEach(pane => {
      pane.classList.toggle('active', pane.id === `pane-${tabName}`);
    });
  },

  async loadAll() {
    try {
      const [modelRes, prefRes, diagRes] = await Promise.allSettled([
        api('/settings/writing-model'),
        api('/settings/preferences'),
        api('/settings/diagnostics'),
      ]);

      if (modelRes.status === 'fulfilled' && modelRes.value) {
        this.renderModelSettings(modelRes.value);
      }
      if (prefRes.status === 'fulfilled' && prefRes.value?.preferences) {
        this.renderPreferences(prefRes.value.preferences);
      }
      if (diagRes.status === 'fulfilled' && diagRes.value) {
        this.renderDiagnostics(diagRes.value);
      }
    } catch (e) {
      console.warn('Settings load error:', e);
    }
  },

  renderModelSettings(data) {
    const model = data.model || {};
    const presets = data.presets || [];
    this.cachedPresets = presets;
    this.activePresetId = model.preset_id || 'custom';

    // 1. 渲染当前生效大模型运行状态看板
    const board = document.getElementById('activeModelStatusBoard');
    const nameEl = document.getElementById('activeModelDisplayName');
    const roleBadge = document.getElementById('activeModelRoleBadge');
    const latencyPill = document.getElementById('activeModelLatencyPill');
    const idText = document.getElementById('activeModelIdText');
    const vendorText = document.getElementById('activeModelVendorText');
    const endpointText = document.getElementById('activeModelEndpointText');
    const secretText = document.getElementById('activeModelSecretText');
    const scopeText = document.getElementById('activeModelScopeText');

    if (board) {
      if (model.configured && model.model) {
        board.className = 'active-model-card configured';
        if (nameEl) nameEl.textContent = `当前生效主力：${model.model}`;
        if (roleBadge) {
          roleBadge.textContent = '运行时已载入';
          roleBadge.className = 'model-role-badge';
        }
        if (latencyPill) latencyPill.textContent = '已配置 · 待测试';
        if (idText) idText.textContent = `${model.model} (${model.provider === 'anthropic' ? 'Anthropic 协议' : 'OpenAI 协议'})`;

        const currentPreset = presets.find(p => p.id === model.preset_id);
        if (vendorText) {
          vendorText.textContent = currentPreset?.name || (model.base_url?.includes('deepseek') ? 'DeepSeek 官方' : (model.base_url?.includes('siliconflow') ? '硅基流动 SiliconFlow' : (model.base_url?.includes('11434') ? '本地 Ollama' : '自定义接入点')));
        }
        if (endpointText) endpointText.textContent = model.base_url || '默认服务端点';
        if (secretText) {
          secretText.textContent = model.secret_source === 'dpapi'
            ? 'Windows DPAPI 本地强加密已保护 (无明文存储)'
            : (model.secret_source === 'environment' ? `读取环境变量 ${model.api_key_env || ''}` : '免密钥 / 本地直连');
        }
        if (scopeText) {
          scopeText.textContent = '写作运行时已载入这份配置。AA 制作模型是否同步，以本次保存结果为准。';
        }
      } else {
        board.className = 'active-model-card unconfigured';
        if (nameEl) nameEl.textContent = '尚未接入外部大模型';
        if (roleBadge) {
          roleBadge.textContent = '本地规则模拟';
          roleBadge.className = 'model-role-badge';
        }
        if (latencyPill) latencyPill.textContent = '未连接';
        if (idText) idText.textContent = '本地模拟规则引擎 (Fake Provider)';
        if (vendorText) vendorText.textContent = '未连接外部服务商';
        if (endpointText) endpointText.textContent = '内置离线生成规则';
        if (secretText) secretText.textContent = 'Windows DPAPI 本地保护就绪';
        if (scopeText) scopeText.textContent = '当前处于离线规则模拟模式。请在下方选择厂商预设或输入 API Key，然后点击“保存并立即启用”。';
      }
    }

    // 2. 厂商预设快捷卡片
    const grid = document.getElementById('vendorPresetGrid');
    if (grid && presets.length) {
      grid.innerHTML = presets.map(p => `
        <button type="button" class="vendor-card ${p.id === (model.preset_id || 'deepseek') ? 'active' : ''}" data-preset-id="${p.id}">
          <strong>${p.name}</strong>
          <small>${p.notes || p.default_model}</small>
        </button>
      `).join('');
    }

    const providerEl = document.getElementById('settingsProvider');
    const baseUrlEl = document.getElementById('settingsBaseUrl');
    const modelNameEl = document.getElementById('settingsModelName');
    const maxTokensEl = document.getElementById('settingsMaxTokens');
    const timeoutEl = document.getElementById('settingsTimeout');
    const reasoningEl = document.getElementById('settingsReasoningMode');
    const statusBadge = document.getElementById('modelConfigStatusBadge');
    const hintEl = document.getElementById('apiKeyStatusHint');

    if (providerEl && model.provider) providerEl.value = model.provider;
    if (baseUrlEl && model.base_url !== undefined) baseUrlEl.value = model.base_url;
    if (modelNameEl && model.model) modelNameEl.value = model.model;
    if (maxTokensEl && model.max_tokens) maxTokensEl.value = model.max_tokens;
    if (timeoutEl && model.timeout) timeoutEl.value = model.timeout;
    if (reasoningEl && model.reasoning_mode) reasoningEl.value = model.reasoning_mode;

    if (hintEl) {
      if (model.secret_source === 'dpapi') {
        hintEl.textContent = '已使用 Windows DPAPI 本地安全加密保存，重新配置时输入新 Key 即可覆盖。';
      } else if (model.secret_source === 'environment') {
        hintEl.textContent = `已从环境变量 ${model.api_key_env || ''} 读取。`;
      } else {
        hintEl.textContent = '支持粘贴 API Key（本地加密保存）或使用环境变量。';
      }
    }

    if (statusBadge) {
      if (model.configured) {
        statusBadge.className = 'status-chip good';
        statusBadge.textContent = `已配置: ${model.model} (${model.provider || 'openai'})`;
      } else {
        statusBadge.className = 'status-chip amber';
        statusBadge.textContent = '尚未完成大模型接入';
      }
    }
  },

  selectPreset(presetId) {
    this.activePresetId = presetId;
    this.dialog.querySelectorAll('.vendor-card').forEach(c => {
      c.classList.toggle('active', c.dataset.presetId === presetId);
    });

    const preset = this.cachedPresets.find(p => p.id === presetId);
    if (!preset) return;

    const providerEl = document.getElementById('settingsProvider');
    const baseUrlEl = document.getElementById('settingsBaseUrl');
    const modelNameEl = document.getElementById('settingsModelName');
    const datalist = document.getElementById('settingsModelDatalist');

    if (providerEl) providerEl.value = preset.provider;
    if (baseUrlEl) baseUrlEl.value = preset.base_url;
    if (modelNameEl) modelNameEl.value = preset.default_model || (preset.models && preset.models[0]) || '';

    if (datalist && preset.models) {
      datalist.innerHTML = preset.models.map(m => `<option value="${m}">`).join('');
    }

    toast(`已加载【${preset.name}】官方预设配置`);
  },

  async fetchModels() {
    const btn = document.getElementById('fetchModelsBtn');
    const baseUrl = document.getElementById('settingsBaseUrl')?.value || '';
    const apiKey = document.getElementById('settingsApiKey')?.value || '';
    const provider = document.getElementById('settingsProvider')?.value || 'openai';

    if (btn) {
      btn.disabled = true;
      btn.textContent = '获取中...';
    }

    try {
      const res = await api('/settings/writing-model/fetch-models', {
        method: 'POST',
        body: JSON.stringify({ base_url: baseUrl, api_key: apiKey, provider })
      });
      const models = res.models || [];
      const datalist = document.getElementById('settingsModelDatalist');
      if (datalist && models.length) {
        datalist.innerHTML = models.map(m => `<option value="${m}">`).join('');
        toast(provider === 'anthropic'
          ? `已载入 ${models.length} 个 Anthropic 推荐模型，请确认后选择`
          : `已从接口获取 ${models.length} 个可用模型，请在模型输入框中选择`);
        document.getElementById('settingsModelName')?.focus();
      } else {
        toast('接口返回了空模型列表，请手动输入');
      }
    } catch (e) {
      toast(e.message || '获取模型列表失败', true);
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.textContent = '获取模型';
      }
    }
  },

  async testConnection() {
    const btn = document.getElementById('testConnectionBtn');
    const diagCard = document.getElementById('modelDiagnosticsCard');
    const baseUrl = document.getElementById('settingsBaseUrl')?.value || '';
    const apiKey = document.getElementById('settingsApiKey')?.value || '';
    const provider = document.getElementById('settingsProvider')?.value || 'openai';
    const model = document.getElementById('settingsModelName')?.value || '';

    if (!model) {
      toast('请先输入或选择要测试的模型名称', true);
      return;
    }

    if (btn) {
      btn.disabled = true;
      btn.textContent = '正在体检...';
    }
    if (diagCard) {
      diagCard.classList.remove('hidden', 'error');
      diagCard.innerHTML = '<p>正在发送测试请求，诊断接口连通性与网络延迟...</p>';
    }

    try {
      const res = await api('/settings/writing-model/test', {
        method: 'POST',
        body: JSON.stringify({ base_url: baseUrl, api_key: apiKey, provider, model })
      });

      if (diagCard) {
        diagCard.className = 'diagnostics-card';
        diagCard.innerHTML = `
          <strong>连通性测试通过</strong>
          <p>模型 <b>${res.model}</b> 响应正常，往返延迟 <b>${res.latency_ms}ms</b>。</p>
          <div class="diagnostics-step-list">
            ${(res.diagnostics || []).map(d => `
              <div class="diagnostics-step-item ${d.status}">
                <span>${d.label}</span>
                <span>正常</span>
              </div>
            `).join('')}
          </div>
        `;
      }
      const latencyPill = document.getElementById('activeModelLatencyPill');
      if (latencyPill) latencyPill.textContent = `${res.latency_ms}ms · 正常`;
      toast(`连接成功，往返延迟 ${res.latency_ms}ms`);
    } catch (e) {
      if (diagCard) {
        diagCard.className = 'diagnostics-card error';
        const details = e.details?.diagnostics || [];
        diagCard.innerHTML = `
          <strong style="color:#a33f34;">连通测试失败</strong>
          <p style="margin:4px 0 8px;color:#7a2c22;">${e.message || '网络或鉴权错误'}</p>
          ${details.length ? `
            <div class="diagnostics-step-list">
              ${details.map(d => `
                <div class="diagnostics-step-item ${d.status}">
                  <span>${d.label}</span>
                  <small>${d.hint || ''}</small>
                </div>
              `).join('')}
            </div>
          ` : ''}
        `;
      }
      toast(e.message || '连通性测试失败', true);
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.textContent = '测试连通性';
      }
    }
  },

  async saveModel(form) {
    const submitBtn = document.getElementById('saveAndApplyModelBtn');
    if (submitBtn) {
      submitBtn.disabled = true;
      submitBtn.textContent = '正在保存并启用...';
    }

    let writingApplied = false;
    let directionApplied = false;
    try {
      const formData = new FormData(form);
      const payload = {
        preset_id: this.activePresetId,
        api_key_env: this.cachedPresets.find(item => item.id === this.activePresetId)?.api_key_env || '',
        provider: formData.get('provider'),
        base_url: formData.get('base_url'),
        model: formData.get('model'),
        api_key: formData.get('api_key'),
        max_tokens: parseInt(formData.get('max_tokens') || '8192', 10),
        timeout: parseInt(formData.get('timeout') || '120', 10),
        reasoning_mode: formData.get('reasoning_mode') || 'balanced',
      };

      const scope = formData.get('apply_scope') || 'both';

      if (scope === 'both' || scope === 'writing') {
        await api('/settings/writing-model/test', {
          method: 'POST',
          body: JSON.stringify(payload),
        });
        await api('/settings/writing-model', {
          method: 'POST',
          body: JSON.stringify(payload),
        });
        writingApplied = true;
      }

      if (scope === 'both' || scope === 'direction') {
        const requestProduction = async path => {
          const response = await fetch(`/production/api/v1/settings/direction-model${path}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
          });
          const result = await response.json().catch(() => ({}));
          if (!response.ok || result.ok === false) {
            throw new Error(result.error?.message || `AA 制作模型服务返回 HTTP ${response.status}`);
          }
          return result.data || result;
        };
        try {
          await requestProduction('/test');
          await requestProduction('');
          directionApplied = true;
        } catch (error) {
          if (writingApplied) {
            await this.loadAll();
            this.updateTopBarBadge(payload.model, true);
            throw new Error(`写作模型已测试并启用，但 AA 制作同步失败：${error.message}`);
          }
          throw error;
        }
      }

      await this.loadAll();
      if (writingApplied) this.updateTopBarBadge(payload.model, true);
      else await this.refreshTopBarBadge();
      const scopeLabel = writingApplied && directionApplied ? '写作与 AA 制作' : writingApplied ? '写作' : 'AA 制作';
      toast(`模型【${payload.model}】已通过测试，并启用于${scopeLabel}`);

      setTimeout(() => {
        this.close();
      }, 350);
    } catch (e) {
      toast(e.message || '保存模型设置失败', true);
    } finally {
      if (submitBtn) {
        submitBtn.disabled = false;
        submitBtn.textContent = '保存并立即启用';
      }
    }
  },

  async refreshTopBarBadge() {
    try {
      const res = await api('/settings/writing-model');
      if (res?.model) {
        this.updateTopBarBadge(res.model.model, res.model.configured);
      }
    } catch (e) {}
  },

  updateTopBarBadge(modelName, configured) {
    const badge = document.getElementById('providerBadge');
    if (!badge) return;
    if (configured && modelName) {
      badge.style.background = 'var(--action-soft, #edf2fa)';
      badge.style.color = 'var(--action, #3f69a7)';
      badge.style.borderColor = 'var(--action-line, #b9c9df)';
      badge.textContent = `${modelName} · 已配置`;
      badge.title = `当前写作主力模型: ${modelName}`;
    } else {
      badge.style.background = 'var(--amber-soft)';
      badge.style.color = '#76500f';
      badge.style.borderColor = '#d3a34a';
      badge.textContent = '未配置大模型';
      badge.title = '点击导航栏“设置”配置 API 模型';
    }
  },

  async inspectAa() {
    const input = document.getElementById('aaWorkspaceInput');
    const card = document.getElementById('aaEnvironmentCard');
    const adoptBtn = document.getElementById('adoptAaBtn');
    const raw = (input?.value || '').trim();

    if (card) {
      card.className = 'environment-status-card';
      card.innerHTML = '<p>正在检查 AzureArchive 路径与工作区有效性...</p>';
    }

    try {
      const resp = await fetch('/production/api/v1/settings/aa-environment', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ selection: raw })
      });
      const data = await resp.json();
      if (!resp.ok || data.ok === false) throw new Error(data.error?.message || 'AA 环境探测失败');

      const result = data.data || data;
      if (card) {
        card.className = 'environment-status-card valid';
        card.innerHTML = `
          <strong style="color:#15846d;">检测到有效的 AzureArchive 制作环境</strong>
          <p>工作区路径: <code>${result.workspace_path || result.resolved_workspace || raw}</code></p>
          <small>结构完整: projects, saves, overrides, settings 就绪。</small>
        `;
      }
      if (adoptBtn) adoptBtn.disabled = false;
      toast('AA 制作环境检测通过');
    } catch (e) {
      if (card) {
        card.className = 'environment-status-card';
        card.innerHTML = `
          <strong style="color:#a33f34;">未检测到有效 AA 工作区</strong>
          <p>${e.message || '请确认目录是否存在且为标准的 AzureArchive data 结构'}</p>
        `;
      }
      if (adoptBtn) adoptBtn.disabled = true;
      toast(e.message || 'AA 检测失败', true);
    }
  },

  async adoptAa() {
    const input = document.getElementById('aaWorkspaceInput');
    const raw = (input?.value || '').trim();
    try {
      const resp = await fetch('/production/api/v1/settings/aa-workspace', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ aa_data: raw })
      });
      const data = await resp.json();
      if (!resp.ok || data.ok === false) throw new Error(data.error?.message || '采用工作区失败');
      toast('已成功采用并绑定该 AzureArchive 制作工作区');
    } catch (e) {
      toast(e.message || '采用 AA 工作区失败', true);
    }
  },

  renderPreferences(prefs) {
    const tone = document.getElementById('prefWritingTone');
    const charWarn = document.getElementById('prefCharWarning');
    const pacing = document.getElementById('prefAaPacing');
    const maxChars = document.getElementById('prefMaxStageCharacters');

    if (tone && prefs.writing_tone) tone.value = prefs.writing_tone;
    if (charWarn && prefs.char_warning_threshold) charWarn.value = prefs.char_warning_threshold;
    if (pacing && prefs.aa_pacing_wait_ms) pacing.value = prefs.aa_pacing_wait_ms;
    if (maxChars && prefs.max_stage_characters) maxChars.value = prefs.max_stage_characters;
  },

  async savePreferences(form) {
    const formData = new FormData(form);
    const payload = {
      writing_tone: formData.get('writing_tone'),
      char_warning_threshold: parseInt(formData.get('char_warning_threshold') || '35', 10),
      aa_pacing_wait_ms: parseInt(formData.get('aa_pacing_wait_ms') || '2500', 10),
      max_stage_characters: parseInt(formData.get('max_stage_characters') || '4', 10),
    };

    try {
      await api('/settings/preferences', {
        method: 'POST',
        body: JSON.stringify(payload),
      });
      toast('创作与演出偏好已成功保存');
    } catch (e) {
      toast(e.message || '保存偏好设置失败', true);
    }
  },

  renderDiagnostics(diag) {
    const writingEl = document.getElementById('diagWritingPort');
    const prodEl = document.getElementById('diagProductionPort');
    const dpapiEl = document.getElementById('diagDpapiStatus');
    const corpusEl = document.getElementById('corpusRecordStatus');

    if (writingEl) writingEl.textContent = `运行中 · ${diag.writing_service?.data_dir || '本地数据'}`;
    if (prodEl) {
      const ok = diag.production_service?.status === 'online';
      prodEl.textContent = ok ? '在线 · 端口 8892' : '离线 (需启动制作服务)';
      prodEl.style.color = ok ? '#15846d' : '#a33f34';
    }
    if (dpapiEl) {
      dpapiEl.textContent = diag.writing_service?.dpapi_available ? '已启用 (Windows DPAPI 强加密)' : '环境模式';
    }
    if (corpusEl) {
      corpusEl.textContent = diag.corpus_status?.available
        ? `已收录 ${diag.corpus_status.count} 条 BA 官方剧情演出对照记录。`
        : '官方演出语料库未就绪。';
    }
  },

  async exportBackup() {
    try {
      const works = await api('/works');
      const blob = new Blob([JSON.stringify(works, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `halocue-works-backup-${new Date().toISOString().slice(0,10)}.json`;
      a.click();
      URL.revokeObjectURL(url);
      toast('作品全量备份已导出');
    } catch (e) {
      toast('导出备份失败: ' + e.message, true);
    }
  }
};

if (document.readyState === 'loading') {
  window.addEventListener('DOMContentLoaded', () => SettingsController.init());
} else {
  SettingsController.init();
}

/* Final work-agent presentation pass: one conversation surface, with the
   durable thread list kept beside it. The older workflow renderers remain
   available for the Writing surface, but never compete with Works. */
function workAgentThreadScope(thread){
  if(thread?.scope_type==='chapter'){
    const chapter=(state.work?.chapters||[]).find(item=>item.id===thread.scope_id);
    return chapter?`章节 · ${chapter.title}`:'章节讨论';
  }
  return '作品全局';
}

function workAgentThreadPreview(thread){
  const messages=thread?.messages||[];
  const last=[...messages].reverse().find(item=>messageText(item).trim());
  const text=messageText(last).replace(/\s+/g,' ').trim();
  return text||'还没有开始讨论';
}

function workAgentThreadTime(value){
  if(!value)return '刚刚';
  const time=new Date(value),seconds=Math.max(0,Math.floor((Date.now()-time.getTime())/1000));
  if(Number.isNaN(time.getTime()))return '';
  if(seconds<60)return '刚刚';
  if(seconds<3600)return `${Math.floor(seconds/60)} 分钟前`;
  if(seconds<86400)return `${Math.floor(seconds/3600)} 小时前`;
  if(seconds<604800)return `${Math.floor(seconds/86400)} 天前`;
  return `${time.getMonth()+1}月${time.getDate()}日`;
}

function workAgentRailStats(){
  const artifacts=state.work?.artifacts||[];
  const characterCount=artifacts.filter(item=>item.kind==='character_card'&&item.current_revision).length;
  const world=artifacts.find(item=>item.kind==='world_bible')?.current_revision?.content||{};
  const worldCount=(world.entities||[]).filter(item=>item.status!=='archived').length;
  const chapters=(state.work?.volumes||[]).flatMap(volume=>volume.chapters||[]);
  return {characterCount,worldCount,chapterCount:chapters.length};
}

function workAgentNextAction(){
  const thread=workConversationThread(),proposal=workPlanProposal(),messages=thread?.messages||[];
  if(proposal)return {kicker:'等待你的决定',title:'审查故事方向候选',detail:'Agent 已整理出方案；采纳前不会改变正式资料。',label:'查看候选',action:'data-agent-review-current'};
  if(!messages.length)return {kicker:'从这里开始',title:'告诉 Agent 你想写什么',detail:'一句想法就够了，人物、世界观和故事方向会在对话中逐步讨论。',label:'开始讨论',action:'data-agent-focus-composer'};
  if(!brief())return {kicker:'建议下一步',title:'继续讨论，形成全作方案',detail:'想法仍可反悔或补充；觉得清楚后再让 Agent 整理。',label:'继续讨论',action:'data-agent-focus-composer'};
  return {kicker:'全作方向已保存',title:'进入章节写作',detail:'选择当前章节，继续讨论章内细纲、场景与正文。',label:'打开写作',action:'data-section="writing"'};
}

function renderWorkAgentThreadList(){
  const allThreads=state.work?.conversation_threads||[],filter=state.threadRailFilter||'active',query=(state.threadRailQuery||'').trim().toLocaleLowerCase();
  const threads=allThreads.filter(item=>item.status===filter).filter(item=>!query||`${item.title||''} ${workAgentThreadPreview(item)}`.toLocaleLowerCase().includes(query));
  const selected=workConversationThread();
  const activeCount=allThreads.filter(item=>item.status==='active').length,archivedCount=allThreads.filter(item=>item.status==='archived').length;
  const stats=workAgentRailStats(),next=workAgentNextAction();
  return `<div class="work-agent-rail-shell"><header class="work-agent-project"><div><span class="project-kicker">当前作品</span><b>${esc(state.work?.title||'未选择作品')}</b><small>${brief()?'全作方向已保存':'正在建立创作方向'}</small></div><button type="button" class="rail-work-switch" data-open-work-switch title="切换作品" aria-label="切换作品">⌄</button></header><section class="work-agent-rail-head"><div><p class="eyebrow">AGENT THREADS</p><h3>创作对话</h3></div><div class="rail-head-actions"><button type="button" class="rail-new-thread" data-thread-create title="新建一段对话"><span aria-hidden="true">＋</span>新对话</button><button type="button" class="rail-close-thread" data-mobile-thread-toggle title="关闭对话列表" aria-label="关闭对话列表">×</button></div></section><div class="thread-rail-tools"><label class="thread-search"><span aria-hidden="true">⌕</span><input type="search" value="${esc(state.threadRailQuery||'')}" placeholder="搜索对话" aria-label="搜索对话" data-thread-search></label><div class="thread-filter" role="tablist" aria-label="对话状态"><button type="button" role="tab" aria-selected="${filter==='active'}" class="${filter==='active'?'active':''}" data-thread-filter="active">当前 <span>${activeCount}</span></button><button type="button" role="tab" aria-selected="${filter==='archived'}" class="${filter==='archived'?'active':''}" data-thread-filter="archived">已归档 <span>${archivedCount}</span></button></div></div><div class="work-agent-thread-list">${threads.map(thread=>{const active=thread.id===selected?.id&&filter==='active',rename=state.renamingThreadId===thread.id;return `<div class="work-agent-thread-row ${active?'active':''}"><button type="button" class="work-agent-thread-select" data-thread-select="${esc(thread.id)}" ${thread.status==='archived'?'disabled':''}><span class="thread-avatar">${esc((thread.title||'新').slice(0,1))}</span><span class="thread-copy"><span class="thread-title-line"><b>${esc(thread.title)}</b><time>${esc(workAgentThreadTime(thread.updated_at))}</time></span><small>${esc(workAgentThreadPreview(thread))}</small><em>${esc(workAgentThreadScope(thread))} · ${thread.messages?.length||0} 条消息</em></span></button><details class="thread-actions"><summary aria-label="对话操作">•••</summary><div>${thread.status==='active'?`<button type="button" data-thread-rename="${esc(thread.id)}">重命名</button><button type="button" data-thread-archive="${esc(thread.id)}">归档</button>`:`<button type="button" data-thread-restore="${esc(thread.id)}">恢复对话</button>`}</div></details>${rename?`<form class="thread-rename-form" data-thread-rename-form="${esc(thread.id)}"><input name="title" value="${esc(thread.title)}" maxlength="80" aria-label="对话名称"><button type="submit" class="quiet">保存</button><button type="button" class="quiet" data-thread-rename-cancel>取消</button></form>`:''}</div>`}).join('')||`<div class="thread-list-empty"><b>${query?'没有匹配的对话':filter==='archived'?'还没有归档对话':'还没有创作对话'}</b><span>${query?'换一个关键词试试。':filter==='archived'?'归档后可以随时在这里恢复。':'新建对话后，每段讨论都会独立保存。'}</span></div>`}</div><footer class="work-agent-rail-footer"><section class="rail-next-action"><span>${esc(next.kicker)}</span><b>${esc(next.title)}</b><small>${esc(next.detail)}</small><button type="button" ${next.action}>${esc(next.label)} <span aria-hidden="true">→</span></button></section><nav class="rail-resource-links" aria-label="作品快捷入口"><button type="button" data-agent-open-library="characters"><b>${stats.characterCount}</b><span>人物</span></button><button type="button" data-agent-open-library="world"><b>${stats.worldCount}</b><span>设定</span></button><button type="button" data-section="writing"><b>${stats.chapterCount}</b><span>章节</span></button></nav></footer></div>`;
}

function renderWorkAgentComposer(thread, task, proposal){
  const attachments=(state.work?.conversation_threads||[]).find(item=>item.id===thread?.id)?.attachments||[];
  const staged=(state.composerAttachmentIds||[]).map(id=>attachments.find(item=>item.id===id)).filter(Boolean);
  return `<form id="workConversationForm" class="conversation-composer work-agent-composer"><div class="composer-attachments">${staged.map(item=>`<div class="composer-attachment"><img src="${esc(item.content_url)}" alt="${esc(item.filename)}"><button type="button" title="移除附件" aria-label="移除 ${esc(item.filename)}" data-composer-attachment-remove="${esc(item.id)}">×</button></div>`).join('')}</div><label><span class="sr-only">给创作导演发送消息</span><textarea name="text" required placeholder="告诉 Agent 你的想法，或要求它创建人物卡、世界观和故事方向……"></textarea></label><div class="composer-actions"><div class="composer-tools"><details class="attachment-menu"><summary title="添加附件" aria-label="添加附件">＋</summary><div class="attachment-popover"><button type="button" data-attachment-upload>上传图片</button><small>PNG、JPEG、WebP、GIF · 单张不超过 5 MB</small></div></details>${renderPermissionMenu(thread)}${renderConversationAction(task,proposal)}</div><input id="workAgentImageInput" type="file" accept="image/png,image/jpeg,image/webp,image/gif" hidden><button class="send-button" type="submit">发送</button></div></form>`;
}

renderUnifiedWorkAgent=function(){
  const thread=workConversationThread(),proposal=workPlanProposal(),task=conversationTaskContract(thread),messages=thread?.messages||[];
  const providerSimulated=Boolean(state.capabilities?.providers?.[0]?.is_simulation);
  const taskLabel=task?.task||'继续理解作品，并维护清晰、可追溯的创作成果';
  return `<main class="work-agent-canvas"><header class="work-agent-header"><div class="work-agent-identity"><button type="button" class="mobile-thread-trigger" data-mobile-thread-toggle title="查看对话列表">☰</button><span class="work-agent-mark">HC</span><div><p class="eyebrow">作品 Agent · ${esc(workAgentThreadScope(thread))}</p><h2>${esc(thread?.title||'作品讨论')}</h2><p>${esc(state.work.title)} · ${esc(taskLabel)}</p></div></div><div class="work-agent-status"><span class="agent-provider-chip">${providerSimulated?'本地模拟 Provider':'模型已连接'}</span><button type="button" class="quiet agent-expand-button" data-work-agent-expand title="${state.workAgentExpanded?'退出专注模式':'展开聊天'}">${state.workAgentExpanded?'收起':'展开'}</button></div></header><section class="work-agent-thread" data-work-discussion-scroll>${currentWorkArtifactMarkup()}${messages.length?conversationHistoryMarkup(messages):'<div class="work-agent-empty"><span>HC</span><h3>从一个想法开始</h3><p>你可以补充、反悔或推翻前面的方向。Agent 会自己判断下一步应该讨论人物、世界观还是故事结构。</p><div><button type="button" data-agent-continue-draft="先复述你对这部作品的理解，并指出目前最关键的不确定项。">复述当前理解</button><button type="button" data-agent-continue-draft="检查目前还缺少哪些人物卡或世界观依据。">检查创作资料</button></div></div>'}${workAgentProposalMarkup(proposal)}</section>${thread?renderWorkAgentComposer(thread,task,proposal):'<div class="notice">当前作品对话未能恢复。</div>'}</main>`;
};

renderUnifiedWorkRail=function(){
  const rail=$('#stageList'),tree=$('#sceneTree'),note=$('.work-surface-note');
  if(!rail)return;
  rail.className='work-agent-rail';rail.setAttribute('aria-label','作品对话列表');rail.innerHTML=`<li>${renderWorkAgentThreadList()}</li>`;
  tree?.replaceChildren();
  if(note){note.hidden=true;note.replaceChildren();}
};

const renderBeforeFinalWorkAgentLayout=render;
render=function(){
  renderBeforeFinalWorkAgentLayout();
  const active=Boolean(state.work&&state.surface==='works'&&state.mobileView==='writing'&&state.stage==='overview');
  $('#app')?.classList.toggle('work-agent-expanded',active&&state.workAgentExpanded);
  $('#app')?.classList.toggle('mobile-thread-open',active&&state.mobileThreadOpen);
  const note=$('.work-surface-note');
  if(note)note.hidden=active;
  if(!active)return;
  renderUnifiedWorkRail();
  const workspace=$('#workspace');if(workspace)workspace.innerHTML=renderUnifiedWorkAgent();
  if($('#crumb'))$('#crumb').textContent=`${state.work.title} / ${workConversationThread()?.title||'作品 Agent'}`;
  const scroll=workspace?.querySelector('[data-work-discussion-scroll]');if(scroll)scroll.scrollTop=scroll.scrollHeight;
};

document.addEventListener('click',event=>{
  const filter=event.target.closest('[data-thread-filter]');
  if(filter){event.preventDefault();event.stopImmediatePropagation();state.threadRailFilter=filter.dataset.threadFilter;state.renamingThreadId='';renderUnifiedWorkRail();return;}
  const select=event.target.closest('[data-thread-select]');
  if(select&&state.work){event.preventDefault();event.stopImmediatePropagation();state.conversationThreadId=select.dataset.threadSelect;state.renamingThreadId='';state.mobileThreadOpen=false;render();return;}
  const create=event.target.closest('[data-thread-create]');
  if(create&&state.work){event.preventDefault();event.stopImmediatePropagation();(async()=>{try{const result=await api(`/works/${state.work.id}/threads`,{method:'POST',body:JSON.stringify({expected_version:state.work.version,title:'新对话',scope_type:'work'})});state.work=result.work;state.conversationThreadId=result.thread_id;toast('已建立新的作品讨论');render();}catch(error){toast(error.message,true)}})();return;}
  const rename=event.target.closest('[data-thread-rename]');
  if(rename){event.preventDefault();event.stopImmediatePropagation();state.renamingThreadId=rename.dataset.threadRename;render();setTimeout(()=>document.querySelector(`[data-thread-rename-form="${rename.dataset.threadRename}"] input`)?.focus(),0);return;}
  const cancel=event.target.closest('[data-thread-rename-cancel]');
  if(cancel){event.preventDefault();event.stopImmediatePropagation();state.renamingThreadId='';render();return;}
  const archive=event.target.closest('[data-thread-archive]');
  if(archive&&state.work){event.preventDefault();event.stopImmediatePropagation();const thread=(state.work.conversation_threads||[]).find(item=>item.id===archive.dataset.threadArchive);if(!thread)return;(async()=>{try{const result=await api(`/works/${state.work.id}/threads/${thread.id}`,{method:'POST',body:JSON.stringify({expected_thread_version:thread.version,status:'archived'})});state.work=result.work;state.conversationThreadId='';toast('对话已归档');render();}catch(error){toast(error.message,true)}})();return;}
  const restore=event.target.closest('[data-thread-restore]');
  if(restore&&state.work){event.preventDefault();event.stopImmediatePropagation();const thread=(state.work.conversation_threads||[]).find(item=>item.id===restore.dataset.threadRestore);if(!thread)return;(async()=>{try{const result=await api(`/works/${state.work.id}/threads/${thread.id}`,{method:'POST',body:JSON.stringify({expected_thread_version:thread.version,status:'active'})});state.work=result.work;state.conversationThreadId=thread.id;state.threadRailFilter='active';toast('对话已恢复');render();}catch(error){toast(error.message,true)}})();return;}
  const focusComposer=event.target.closest('[data-agent-focus-composer]');
  if(focusComposer){event.preventDefault();event.stopImmediatePropagation();document.querySelector('#workConversationForm textarea')?.focus();return;}
  const reviewCurrent=event.target.closest('[data-agent-review-current]');
  if(reviewCurrent){event.preventDefault();event.stopImmediatePropagation();document.querySelector('.proposal-message')?.scrollIntoView({block:'center'});return;}
  const expand=event.target.closest('[data-work-agent-expand]');
  if(expand){event.preventDefault();event.stopImmediatePropagation();state.workAgentExpanded=!state.workAgentExpanded;render();return;}
  const mobileThreads=event.target.closest('[data-mobile-thread-toggle]');
  if(mobileThreads){event.preventDefault();event.stopImmediatePropagation();state.mobileThreadOpen=!state.mobileThreadOpen;render();return;}
  const remove=event.target.closest('[data-composer-attachment-remove]');
  if(remove){event.preventDefault();event.stopImmediatePropagation();state.composerAttachmentIds=(state.composerAttachmentIds||[]).filter(id=>id!==remove.dataset.composerAttachmentRemove);render();return;}
  const upload=event.target.closest('[data-attachment-upload]');
  if(upload){event.preventDefault();event.stopImmediatePropagation();document.querySelector('#workAgentImageInput')?.click();return;}
},true);

document.addEventListener('input',event=>{
  const input=event.target.closest('[data-thread-search]');
  if(!input)return;
  state.threadRailQuery=input.value;
  renderUnifiedWorkRail();
  const next=document.querySelector('[data-thread-search]');
  if(next){next.focus();next.setSelectionRange(next.value.length,next.value.length);}
},true);

document.addEventListener('submit',event=>{
  const form=event.target.closest('[data-thread-rename-form]');
  if(!form||!state.work)return;
  event.preventDefault();event.stopImmediatePropagation();const thread=(state.work.conversation_threads||[]).find(item=>item.id===form.dataset.threadRenameForm);if(!thread)return;const title=new FormData(form).get('title');
  (async()=>{try{const result=await api(`/works/${state.work.id}/threads/${thread.id}`,{method:'POST',body:JSON.stringify({expected_thread_version:thread.version,title,status:thread.status})});state.work=result.work;state.renamingThreadId='';toast('对话名称已保存');render();}catch(error){toast(error.message,true)}})();
},true);

document.addEventListener('change',event=>{
  const input=event.target.closest('#workAgentImageInput');
  if(!input||!state.work||!input.files?.length)return;
  const thread=workConversationThread(),file=input.files[0];input.value='';
  const reader=new FileReader();reader.onload=async()=>{try{setBusy('正在保存图片附件');const result=await api(`/works/${state.work.id}/threads/${thread.id}/attachments`,{method:'POST',body:JSON.stringify({expected_thread_version:thread.version,filename:file.name,media_type:file.type,content_base64:String(reader.result).split(',')[1]||''})});state.work=result.work;const updated=workConversationThread();const attachment=(updated?.attachments||[]).find(item=>item.id===result.attachment_id);if(attachment)state.composerAttachmentIds=[...(state.composerAttachmentIds||[]),attachment.id];toast('图片已加入本轮消息');render();}catch(error){toast(error.message,true)}finally{setBusy('')}};reader.readAsDataURL(file);
},true);

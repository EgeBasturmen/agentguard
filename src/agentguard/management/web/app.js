'use strict';
let credential='';let project='';let timer=null;let currentCases=[];let viewEpoch=0;
const downloadUrls=new Set();
const el=id=>document.getElementById(id);
const text=(node,value)=>{node.textContent=typeof value==='string'?value:JSON.stringify(value,null,2);};
function node(tag,value){const n=document.createElement(tag);if(value!==undefined)text(n,value);return n;}
function clearPrivate(resetForms=false){viewEpoch++;for(const id of ['cases','runs','evidence','enrollment','comparison','usage','pilot','connections','suites','funnel','operations','account-info'])el(id).replaceChildren();el('delete-account').querySelector('button').disabled=true;currentCases=[];for(const url of downloadUrls)URL.revokeObjectURL(url);downloadUrls.clear();if(resetForms)for(const form of document.querySelectorAll('form'))if(form.id!=='login')form.reset();}
function staleView(){const error=Error('STALE_VIEW_DISCARDED');error.stale=true;return error;}
async function api(path,body){const epoch=viewEpoch;const response=await fetch('/api/'+path,{method:body===undefined?'GET':'POST',headers:{Authorization:'Bearer '+credential,...(body===undefined?{}:{'Content-Type':'application/json'})},body:body===undefined?undefined:JSON.stringify(body),cache:'no-store',redirect:'error'});if(epoch!==viewEpoch)throw staleView();const value=await response.json();if(epoch!==viewEpoch)throw staleView();if(!response.ok){if(response.status===401){credential='';project='';clearPrivate(true);el('projects').replaceChildren();if(timer)clearInterval(timer);}throw Error(value.error||'İstek doğrulanamadı');}return value;}
const path=suffix=>'projects/'+project+'/'+suffix;
const recoveryHints={RATE_LIMITED:'Bir dakika sonra yeniden deneyin.',QUOTA_LEDGER_UNVERIFIED:'Yeni koşu başlatmayın; operatör ledger/backup kanıtını uzlaştırsın.',VERIFIED_QUIESCENCE_REQUIRED:'Queued koşuları iptal edin; çalışan/unknown cleanup için operatör recovery kanıtı gerekir.',PLAN_CANCELLATION_REQUIRED:'Önce plan iptalini isteyin; operatör harici iptali teyit etmelidir.',CONFIRM_PROJECT_NAME:'Proje adını aynen yazın.',DELETION_JOURNAL_UNVERIFIED:'İşlemi durdurun; güncel imzalı silme günlüğünü operatör geri yüklemeli.'};
recoveryHints.REVIEW_CHANGED_RELOAD='Review başka bir işlemde değişti. Sayfayı yenileyip güncel kaydı inceleyin; değişiklikleriniz kaydedilmedi.';
recoveryHints.ATOMIC_SCENARIO_STORAGE_REQUIRED='İşlemi durdurun; operatör SQLite rollback-journal desteğini doğrulamalı. Veritabanı modunu otomatik değiştirmeyin.';
function action(fn){return async event=>{if(event)event.preventDefault();try{text(el('status'),'');await fn();}catch(error){if(!error.stale){const code=String(error.message||'İşlem doğrulanamadı');text(el('status'),code+(recoveryHints[code]?' · '+recoveryHints[code]:''));}}};}
function select(id,rows,label){const target=el(id);const selected=target.value;target.replaceChildren();for(const row of rows){const option=node('option',row[label]||row.id);option.value=row.id;target.append(option);}if(rows.some(row=>row.id===selected))target.value=selected;}
async function loadProject(){const next=el('projects').value;clearPrivate(next!==project);project=next;const account=await api('account');text(el('account-info'),account);el('delete-account').querySelector('button').disabled=!account.workspace_owner_actions;if(!project)return;select('connections',await api(path('connections')),'name');select('suites',await api(path('suites')),'name');await loadCases();await loadRuns();text(el('usage'),await api(path('usage')));text(el('pilot'),await api(path('pilot')));text(el('funnel'),await api(path('onboarding')));}
async function loadCases(){
    currentCases=await api(path('cases'));
    el('cases').replaceChildren();
    for(const item of currentCases){
        const article=node('article');
        const chosen=node('input');chosen.type='checkbox';chosen.dataset.case=item.id;chosen.disabled=item.status!=='approved';
        article.append(chosen,node('strong',item.case.axis+' · '+item.status+' · '+item.case.id));
        const details=node('details');details.append(node('summary','Görev, oracle, kaynak ve fixture'),node('pre',item));article.append(details);
        const choices={};
        for(const [id,oracle] of Object.entries(item.oracle_checks)){
            const label=node('label',JSON.stringify(oracle));const input=node('select');
            for(const value of ['unknown','confirmed','rejected']){const option=node('option',value);option.value=value;input.append(option);}
            input.value=item.review?.decisions?.[id]??'unknown';
            choices[id]=input;label.append(input);article.append(label);
        }
        const fixture=node('input');fixture.type='checkbox';fixture.checked=item.review?.fixture_confirmed===true;
        const classification=node('input');classification.type='checkbox';classification.checked=item.review?.classification_confirmed===true;
        const f=node('label',' Fixture bağımsız doğrulandı');f.prepend(fixture);
        const c=node('label',' Benign/adversarial/edge sınıfı doğrulandı');c.prepend(classification);article.append(f,c);
        const review=node('button','İncelemeyi kaydet');
        review.addEventListener('click',action(async()=>{
            const decisions={};for(const [id,input] of Object.entries(choices))decisions[id]=input.value;
            const body={
                decisions,fixture:fixture.checked,classification:classification.checked,
                notes:item.review?.notes??''
            };
            // A running pre-Sprint-1 server may still serve these updated static files.
            if(item.review_concurrency==='fingerprint-v1')body.expected_review_fingerprint=item.review?.fingerprint??'';
            await api(path('cases/'+item.id+'/review'),body);
            await loadCases();
        }));
        const approve=node('button','Bu sürümü onayla');
        approve.addEventListener('click',action(async()=>{await api(path('cases/'+item.id+'/approve'),{fingerprint:item.case.fingerprint});await loadCases();}));
        article.append(review,approve);el('cases').append(article);
    }
}
function evidence(view){el('evidence').replaceChildren(node('h3','Case kanıtları'));const cases=view.evidence?.execution?.cases||[];for(const item of cases){const run=item.result;const article=node('article');article.append(node('h4',item.id+' · '+(run?.status||'unexecuted')),node('p','Cleanup: '+(run?.cleanup_status||'unknown')+' / Policy: '+(run?.policy_status||'unknown')));for(const finding of run?.assertions||[])article.append(node('p',(finding.passed?'PASS':'FAIL')+' · '+finding.name+' · '+(finding.message||'')));for(const finding of run?.evaluations||[])article.append(node('p',finding.kind+' · '+finding.status));const details=node('details');details.append(node('summary','Adımlar, audit, observed/expected ve kapsam'),node('pre',run));article.append(details);el('evidence').append(article);}el('evidence').append(node('pre',view));}
async function loadRuns(){const rows=await api(path('runs'));el('runs').replaceChildren();for(const run of rows){const article=node('article');article.append(node('strong',run.id+' · '+run.state+' · '+run.gate_decision),node('p','Cleanup: '+run.cleanup_status),node('small',run.assurance||'Historical customer report'));const cancel=node('button','İptal iste');cancel.disabled=!['queued','leased','running'].includes(run.state);cancel.addEventListener('click',action(async()=>{await api(path('runs/'+run.id+'/cancel'),{});await loadRuns();}));article.append(cancel);if(run.artifact){const show=node('button','Raporu görüntüle / indir');show.addEventListener('click',action(async()=>{const grant=await api(path('artifacts/'+run.artifact.id+'/grant'),{});const view=await api(path('artifacts/'+run.artifact.id)+'?grant='+encodeURIComponent(grant.grant));evidence(view);const blob=new Blob([JSON.stringify(view,null,2)],{type:'application/json'});const url=URL.createObjectURL(blob);downloadUrls.add(url);const a=node('a','Redacted JSON raporunu indir');a.href=url;a.download=run.id+'.json';a.addEventListener('click',()=>setTimeout(()=>{URL.revokeObjectURL(url);downloadUrls.delete(url);},1000),{once:true});el('evidence').append(a);}));article.append(show);}el('runs').append(article);}}
el('runtime').defaultValue=JSON.stringify({agent:{transport:'python',reference:'agent:Agent',source_dir:'.'},environment:{kind:'filesystem'},tools:[{connector:'environment'}],policy:{allowed_tools:['file.write']}},null,2);
el('fixture').defaultValue=JSON.stringify({files:{'note.txt':'draft'}},null,2);el('rules').defaultValue=JSON.stringify([{id:'goal',text:'expect files.note\\.txt = "done"'}],null,2);
el('login').addEventListener('submit',action(async()=>{clearPrivate(true);el('projects').replaceChildren();project='';if(timer)clearInterval(timer);credential=el('token').value;el('token').value='';select('projects',await api('projects'),'name');await loadProject();timer=setInterval(()=>{if(project)action(loadRuns)();},5000);}));
el('logout').addEventListener('click',()=>{credential='';project='';if(timer)clearInterval(timer);location.reload();});
el('projects').addEventListener('change',action(loadProject));
el('project-create').addEventListener('submit',action(async()=>{await api('projects',{name:el('project-name').value});select('projects',await api('projects'),'name');await loadProject();}));
el('connect').addEventListener('submit',action(async()=>{const result=await api(path('connections'),{name:el('connection-name').value,runtime:JSON.parse(el('runtime').value),artifact_digest:el('digest').value});text(el('status'),result);select('connections',await api(path('connections')),'name');}));
el('enroll').addEventListener('submit',action(async()=>{const result=await api(path('runner-enrollment'),{name:el('runner-name').value});text(el('enrollment'),result.enrollment_token);setTimeout(()=>text(el('enrollment'),''),600000);}));
el('hide-enrollment').addEventListener('click',()=>text(el('enrollment'),''));
el('generate').addEventListener('submit',action(async()=>{await api(path('generate'),{connection:el('connections').value,task:el('task').value,fixture:JSON.parse(el('fixture').value),rules:JSON.parse(el('rules').value)});await loadCases();}));
el('freeze').addEventListener('submit',action(async()=>{const cases=Array.from(document.querySelectorAll('input[data-case]:checked')).map(input=>input.dataset.case);await api(path('suites'),{name:el('suite-name').value,cases});select('suites',await api(path('suites')),'name');}));
for(const [id,kind] of [['preflight','preflight'],['run','suite']])el(id).addEventListener('click',action(async()=>{await api(path('runs'),{suite:el('suites').value,idempotency_key:crypto.randomUUID(),kind});await loadRuns();}));
el('refresh').addEventListener('click',action(loadProject));
el('compare').addEventListener('submit',action(async()=>{const epoch=viewEpoch;const baseline=el('baseline-file').files[0];const candidate=el('candidate-file').files[0];if(baseline.size+candidate.size>1800000)throw Error('Comparison upload boyut sınırı aşıldı');const before=JSON.parse(await baseline.text());if(epoch!==viewEpoch)throw staleView();const after=JSON.parse(await candidate.text());if(epoch!==viewEpoch)throw staleView();text(el('comparison'),await api(path('compare'),{baseline_job:el('baseline-id').value,candidate_job:el('candidate-id').value,baseline_report:before,candidate_report:after}));}));
el('plan').addEventListener('click',action(async()=>text(el('status'),await api(path('plans'),{plan:'pilot'}))));
el('cancel-plan').addEventListener('click',action(async()=>text(el('status'),await api(path('cancellation'),{}))));
el('feedback').addEventListener('submit',action(async()=>text(el('status'),await api(path('feedback'),{category:el('feedback-category').value,title:el('feedback-title').value,body:el('feedback-body').value}))));
el('operations-check').addEventListener('click',action(async()=>text(el('operations'),await api(path('operations')))));
el('delete-project').addEventListener('submit',action(async()=>{await api(path('delete'),{confirmation:el('delete-confirmation').value});clearPrivate(true);project='';select('projects',await api('projects'),'name');await loadProject();text(el('status'),'Proje primary verisi silindi. Private cache ve backup retention sorumluluklarını tamamlayın.');}));
el('delete-account').addEventListener('submit',action(async()=>{await api('account/delete',{confirmation:el('delete-account-confirmation').value});credential='';project='';if(timer)clearInterval(timer);clearPrivate(true);el('projects').replaceChildren();text(el('status'),'Hesap/workspace primary verisi silindi. Private cache ve backup retention sorumluluklarını tamamlayın.');}));

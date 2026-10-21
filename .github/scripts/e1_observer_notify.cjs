'use strict';
const {createHash}=require('node:crypto');
const hash=text=>createHash('sha256').update(text).digest('hex');
const keyPattern=/^[a-f0-9]{64}$/;
const markerPattern=/^<!-- DOGE-E1-OBSERVER:([a-f0-9]{64}) -->\r?$/gm;
// Escape every report value before putting it in a fenced JSON block. This also
// prevents closing the fence, mentions, HTML and machine marker injection.
function safe(value){return JSON.stringify(value??null,null,2).replace(/[@`<>&]/g,char=>'\\u'+char.charCodeAt(0).toString(16).padStart(4,'0'));}
function summary(value,limit=256){const text=JSON.stringify(value??null);return text.length<=limit?value:text.slice(0,limit)+'…（摘要；完整报告见本次运行 artifact）';}
function displayAnomaly(item){return {key:item.key,kind:summary(item.kind),event_id:summary(item.event_id),deadline_utc:summary(item.deadline_utc),details:summary(item.details,1024),...(item.probe_id?{probe_id:item.probe_id}:{})};}

module.exports=async function notify({github,context,report,probeId,issueNumber,recipient='lzwislam-collab'}){
 const receipt={schema:'DOGE_E1_OBSERVER_NOTIFY_RECEIPT_V1',status:'FAILED',keys:[],comment_ids:[],probe_id:probeId??null,attempts:0,deferred_count:0};
 try{
  if(typeof recipient!=='string'||!/^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$/.test(recipient)||recipient.includes('--'))throw new TypeError('invalid recipient');
  if(!Number.isSafeInteger(issueNumber)||issueNumber<=0)throw new TypeError('invalid issue number');
  if(probeId!==undefined&&probeId!==null&&(typeof probeId!=='string'||! /^[A-Za-z0-9_-]{1,64}$/.test(probeId)))throw new TypeError('invalid probe ID');
  const repo=context?.repo;
  if(!repo||! /^[A-Za-z0-9_-]+$/.test(repo.owner)||! /^[A-Za-z0-9_.-]+$/.test(repo.repo)||! /^[1-9][0-9]*$/.test(String(context.runId)))throw new TypeError('invalid workflow context');
  const namespace=repo.owner+'/'+repo.repo;
  let anomalies;
  const probe=probeId!==undefined&&probeId!==null;
  if(probe){
   anomalies=[{key:hash('DOGE-E1-OBSERVER-PROBE-V1:'+namespace+':'+probeId),kind:'OBSERVER_TEST',details:'仅通知自测；无真实 E1 样本改动，不代表生产失败。',probe_id:probeId}];
  }else{
   const valid=report&&typeof report==='object'&&report.schema==='DOGE_E1_OBSERVER_V1'&&['CHECKED','OBSERVATION_FAILED'].includes(report.status)&&Array.isArray(report.anomalies)&&report.anomalies.every(a=>a&&typeof a==='object'&&typeof a.key==='string'&&keyPattern.test(a.key)&&typeof a.kind==='string'&&a.kind.length>0);
   if(!valid||report.status==='OBSERVATION_FAILED'&&report.anomalies.length===0){
    const identity=typeof report?.registration_identity==='string'?report.registration_identity:'UNKNOWN_REGISTRATION';
    anomalies=[{key:hash('DOGE-E1-OBSERVER-FAILURE-V1:'+namespace+':'+identity),kind:'OBSERVATION_FAILED',details:'无法读取有效观测报告；未据此判定漏发、缺评分或缺见证。'}];
   }else anomalies=report.anomalies;
  }
  anomalies=[...new Map(anomalies.map(a=>[a.key,a])).values()];
  receipt.keys=anomalies.map(a=>a.key);
  if(!anomalies.length){receipt.status='QUIET';return receipt;}
  const params={owner:repo.owner,repo:repo.repo,issue_number:issueNumber};
  async function readMarkers(){
   const markers=new Map();
   for(let page=1;page<=10000;page++){
    const response=await github.rest.issues.listComments({...params,page,per_page:100});
    if(!Array.isArray(response.data))throw new TypeError('invalid comments response');
    for(const comment of response.data){
     if(comment.user?.login!=='github-actions[bot]'||comment.user?.type!=='Bot'||typeof comment.body!=='string')continue;
     for(const match of comment.body.matchAll(markerPattern))markers.set(match[1],comment.id);
    }
    if(response.data.length<100)return markers;
   }
   throw new Error('comment pagination limit');
  }
  let seen=await readMarkers();
  const unnotified=anomalies.filter(a=>!seen.has(a.key)).sort((a,b)=>a.key<b.key?-1:a.key>b.key?1:0);
  let pending=unnotified.slice(0,20);
  if(!pending.length){receipt.status='DEDUPLICATED';receipt.comment_ids=[...new Set(anomalies.map(a=>seen.get(a.key)))];return receipt;}
  const makeBody=items=>'@'+recipient+'\n\n'+(probe?'OBSERVER_TEST｜E1 通知链路自测':'DOGE E1 云端观测异常')+
   '\n\n'+(probe?'仅通知自测，不代表生产失败。':'仅报告新的观测异常；不构成预测、绩效验收或交易授权。')+
   '\n\n截止时间采用 UTC（deadline_utc）；日本时间 JST 为 UTC 加 9 小时。'+
   '\n\n运行：https://github.com/'+namespace+'/actions/runs/'+context.runId+
   '\n\n完整报告：本次运行的 e1-observer artifact：https://github.com/'+namespace+'/actions/runs/'+context.runId+'#artifacts'+
   '\n\n```json\n'+safe({head:summary(context.sha),checked_at_utc:summary(report?.checked_at_utc),source_commit:summary(report?.source_commit),registration_identity:summary(report?.registration_identity),event_count:summary(report?.event_count),score_count:summary(report?.score_count),anomalies:items.map(displayAnomaly)})+'\n```\n\n'+items.map(a=>'<!-- DOGE-E1-OBSERVER:'+a.key+' -->').join('\n');
  while(pending.length>1&&makeBody(pending).length>60000)pending.pop();
  receipt.keys=pending.map(a=>a.key);
  receipt.deferred_count=unnotified.length-pending.length;
  for(let attempt=0;attempt<2;attempt++){
   const body=makeBody(pending);
   if(body.length>60000)throw new Error('notification exceeds safe comment size');
   receipt.attempts++;
   try{
    const response=await github.rest.issues.createComment({...params,body});
    if(!Number.isSafeInteger(response.data?.id)||response.data.id<=0)throw new Error('missing published comment identity');
    receipt.status='NOTIFIED';receipt.comment_ids.push(response.data.id);return receipt;
   }catch(error){
    // A failed HTTP response may follow a successful server-side creation.
    // Read before retrying; any inability to read stops further POSTs.
    seen=await readMarkers();
    pending=pending.filter(a=>!seen.has(a.key));
    receipt.comment_ids=[...new Set(anomalies.filter(a=>seen.has(a.key)).map(a=>seen.get(a.key)))];
    if(!pending.length){receipt.status='NOTIFIED';return receipt;}
    if(attempt===1)throw error;
   }
  }
 }catch(error){receipt.error_type=error?.name||'Error';const failure=new Error('E1 notification could not be confirmed');failure.receipt=receipt;throw failure;}
};

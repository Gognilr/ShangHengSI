/* 商衡SI · 澄明二版「衡线」效果稿脚本：往返线、主图与切屏。只用于设计稿，F-01 起按规范第四节写成 Vue 组件。 */
(function(){
const $=(s,r=document)=>r.querySelector(s), $$=(s,r=document)=>[...r.querySelectorAll(s)];
const H=document.documentElement;
const fx=()=>H.dataset.fx; const full=()=>fx()==='full'&&!H.classList.contains('shot');

/* ---------- 图标与标志 ---------- */
function ic(name,cls){const n=ICONS[name];if(!n)return '';return '<svg class="i'+(cls?' '+cls:'')+'" viewBox="0 0 24 24">'+n.map(([t,a])=>'<'+t+' '+Object.entries(a).map(([k,v])=>k+'="'+v+'"').join(' ')+'/>').join('')+'</svg>';}
function paintIcons(r=document){$$('[data-i]',r).forEach(e=>{if(e.dataset.done)return;const nm=ICONS[e.dataset.i]?e.dataset.i:e.dataset.alt;e.insertAdjacentHTML('afterbegin',ic(nm));e.dataset.done=1;});}
function mark(size,detail,inverse){
  const t=inverse?'#33C0A6':'var(--si-logo)', s=inverse?'#8B7CFF':'var(--si-logo-star)', f=inverse?'#1F5E55':'var(--si-logo-faint)';
  return `<svg class="mark" viewBox="0 0 24 24" width="${size}" height="${size}" fill="none" aria-label="商衡SI">
  <path class="paper-s" d="M12.5 2.5H8.5a2 2 0 0 0-2 2v15a2 2 0 0 0 2 2h7a2 2 0 0 0 2-2V10" stroke="${t}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
  ${detail?`<path class="paper-s" d="M9.5 6.5h2M9.5 17.5h5" stroke="${f}" stroke-width="2" stroke-linecap="round"/>`:''}
  <rect class="bar" x="3" y="10" width="18" height="4" rx="2" fill="${t}"/>
  <path class="star" d="M17.5 1.5c.3 1.9.9 2.6 3 3-2.1.4-2.7 1.1-3 3-.3-1.9-.9-2.6-3-3 2.1-.4 2.7-1.1 3-3z" fill="${s}" stroke="${s}" stroke-width=".8" stroke-linejoin="round"/></svg>`;
}
function paintMarks(){$$('[data-mark]').forEach(e=>{e.innerHTML=mark(+e.dataset.mark,!!e.dataset.detail,!!e.dataset.inverse);if(e.dataset.tilt)e.querySelector('.bar').style.transform='rotate(-6deg)';});}
function paintIllus(r=document){$$('[data-illus]',r).forEach(e=>{e.innerHTML=ILLUS[e.dataset.illus]||'';$$('.il-bars rect',e).forEach((x,i)=>x.style.setProperty('--n',i));});}

/* ================= SiTrip：站名、迷你映射与状态（F-01 写进 utils/trip.ts） ================= */
const TRIPS={
  review:{n:['读取','确认遮住','复检','模型审核','还原']},
  chat:{n:['读取附件','遮住','复检','模型回答','还原']},
  bid:{n:['读取材料','识别扫描页','本机检查','完成'],mini:[0,1,1,2],local:1},
  bidx:{n:['读取材料','识别扫描页','本机检查','遮住','复检','模型补查','还原'],mini:[0,0,0,1,2,3,4]},
  meet:{n:['本机转写','校对','确认遮住','复检','生成纪要','还原'],mini:[0,0,1,2,3,4]},
  transL:{n:['读取','本机翻译','完成'],local:1},
  transC:{n:['读取','确认遮住','复检','模型翻译','还原']},
  local:{n:['读取','本机处理','完成'],local:1},
};
const GEN5=['读取','遮住','复检','模型','还原'], GEN3=['读取','本机处理','完成'];
const isM=n=>/模型|生成纪要/.test(n);
const ST_WORD={run:'进行中',wait:'等你',block:'被拦截',fail:'失败',stop:'已停止',done:'已完成',partial:'部分完成',todo:'未开始'};

/* 每站状态：at 之前已过；at 为当前；之后未到。done 时全线已过 */
function stations(names,at,st){
  return names.map((n,i)=>{
    let s='todo';
    if(st==='done'||st==='partial') s='done';
    else if(i<at) s='done';
    else if(i===at) s=st==='todo'?'todo':st;
    return {n,z:isM(n)?'m':'l',s,partial:st==='partial'&&isM(n)};
  });
}
function segZone(a,b){ if(a.z==='m'&&b.z==='l') return 'r'; return (a.z==='m'||b.z==='m')?'m':'l'; }

/* 迷你：五个 8px 点（本机三站时为三个），2px 线，宽 80 */
function miniSvg(ss,opt={}){
  const k=ss.length, step=18, w=8+(k-1)*step;
  let s=`<svg viewBox="-2 -2 ${w+4} 16" aria-hidden="true">`;
  for(let i=1;i<k;i++){
    const a=ss[i-1], b=ss[i], x1=4+(i-1)*step+5, x2=4+i*step-5, pass=b.s!=='todo'&&a.s==='done';
    if(!pass){s+=`<path class="tl-seg" d="M${x1} 6H${x2}"/>`;continue;}
    const z=segZone(a,b);
    if(z==='r'){const xm=(x1+x2)/2;s+=`<path class="tl-seg m" d="M${x1} 6H${xm}"/><path class="tl-seg l" d="M${xm} 6H${x2}"/>`;}
    else s+=`<path class="tl-seg ${z}" d="M${x1} 6H${x2}"/>`;
  }
  ss.forEach((t,i)=>{
    const cx=4+i*step;
    if(t.s==='run') s+=`<circle class="tl-halo ${t.z} run" cx="${cx}" cy="6" r="5"/>`;
    if(t.s==='wait'||t.partial) s+=`<circle class="tl-halo w" cx="${cx}" cy="6" r="6.5"/>`;
    const cls=t.s==='run'?'cur':t.s;
    s+=`<circle class="tl-dot ${cls} ${t.z}" cx="${cx}" cy="6" r="${t.s==='fail'?5:4}"/>`;
    if(t.s==='fail') s+=`<path class="tl-x" d="M${cx-2} 4l4 4M${cx+2} 4l-4 4"/>`;
  });
  return s+'</svg>';
}
function miniOf(cfg,at,st){
  const names=cfg.n, map=cfg.mini||names.map((_,i)=>i), gen=cfg.local?GEN3:GEN5;
  const gAt=st==='done'||st==='partial'?gen.length-1:map[Math.min(at,names.length-1)];
  // 压成通用站：被映射到同一通用站的多站里，当前站的状态说了算
  return stations(gen,gAt,st).map((t,i)=>{if(cfg.n===names&&st==='partial'&&i===3)t.partial=true;return t;});
}
function ariaOf(cfg,at,st,note){
  if(st==='done') return `${cfg.n.length} 站全部完成${note?'：'+note:''}`;
  return `第 ${at+1} 站 ${cfg.n[at]}：${ST_WORD[st]}${note?'，'+note:''}`;
}
function renderTrip(el){
  const cfg=TRIPS[el.dataset.trip]||TRIPS.review, at=+(el.dataset.at||0), st=el.dataset.st||'run', note=el.dataset.note||'', scale=el.dataset.scale||'mini';
  if(scale==='bar') return renderBar(el,cfg,at,st,note);
  const ms=miniOf(cfg,at,st), label=ariaOf(cfg,at,st,note);
  if(scale==='dot'){ // 侧栏：只画当前站
    const cur=ms.find(t=>t.s!=='done'&&t.s!=='todo')||ms[ms.length-1];
    const one=[{...cur}];
    el.innerHTML=`<span class="tdot" role="img" aria-label="${label}">${miniSvg(one).replace('viewBox="-2 -2 12 16"','viewBox="-3 -1 14 14"')}</span>`;
    return;
  }
  const stCls=st==='done'?'done':st;
  el.className='trip-mini st-'+stCls+(cfg.local?' n3':'');
  el.setAttribute('role','img'); el.setAttribute('aria-label',label); el.title=cfg.n.map((n,i)=>(i<at||st==='done'?'✓ ':i===at?'● ':'○ ')+n).join('　');
  const curName=st==='done'?'':(cfg.n[at]||'');
  el.innerHTML=miniSvg(ms)+`<span class="tm-t">${curName?'<b>'+curName+'</b>'+(note?' · '+note:''):(note||'已完成')}</span>`;
}
function renderBar(el,cfg,at,st,note){
  if(st==='done'){
    const all=stations(cfg.local?GEN3:GEN5,0,'done');
    el.innerHTML=`<span class="trip-done${cfg.local?' local':''}" role="status">${miniSvg(all).replace('<svg','<svg class="tdl"')}${ic('check')}已完成 · 用时 ${el.dataset.took||'1 分 12 秒'}${cfg.local?' · 全程本机':''}</span>`;
    return;
  }
  const ss=stations(cfg.n,at,st);
  let h=`<ol class="trip-bar" aria-label="进度">`;
  ss.forEach((t,i)=>{
    let pass='';
    if(i>0&&ss[i-1].s==='done'&&t.s!=='todo') pass=' pass-'+segZone(ss[i-1],t);
    const cur=i===at;
    const cls=`ts ${t.z} ${t.s==='run'?'cur':t.s}${t.partial?' partial':''}${cur&&t.s!=='todo'?' cur':''}${pass}`;
    h+=`<li class="${cls}"${cur?' aria-current="step"':''}><span class="d">${t.s==='fail'?ic('x'):''}</span>${t.n}`;
    if(cur&&st==='run'&&el.dataset.tm) h+=` <span class="tm">${el.dataset.tm}</span>`;
    if(cur&&note) h+=`<span class="note ${st}">${note}${el.dataset.act?` <button class="btn sm ${st==='fail'?'sec':'pri'}" data-act="trip-act">${el.dataset.act}</button>`:''}</span>`;
    h+='</li>';
  });
  h+='<li class="spark" aria-hidden="true"><i class="sl"></i><i class="sm"></i></li></ol>';
  el.innerHTML=h;
}
function paintTrips(r=document){$$('[data-trip]',r).forEach(renderTrip);}

/* 走线：光点沿线前进，过复检后由青变紫，回到还原时变回青。只在状态变化时走一次 */
function walk(el,from,to){
  if(!full()) return;
  const ol=$('.trip-bar',el); if(!ol) return;
  const lis=$$('.ts',ol), sp=$('.spark',ol), o=ol.getBoundingClientRect();
  const x=i=>{const d=$('.d',lis[i]).getBoundingClientRect();return d.left+d.width/2-o.left;};
  if(!lis[from]||!lis[to]) return;
  const zf=lis[from].classList.contains('m'), zt=lis[to].classList.contains('m');
  sp.animate([{transform:`translateX(${x(from)}px)`,opacity:1},{transform:`translateX(${x(to)}px)`,opacity:1},{transform:`translateX(${x(to)}px)`,opacity:0}],{duration:900,easing:'cubic-bezier(.22,1,.36,1)'});
  $('.sm',sp).animate([{opacity:zf?1:0},{opacity:zt?1:0}],{duration:900,fill:'forwards'});
}

/* ================= 组件页 ================= */
const KIT_ST=[['todo','未开始',0],['run0','读取中',0],['wait','等你',1],['block','被拦截',2],['run3','模型中',3],['fail','失败',3],['stop','已停止',3],['partial','部分完成',4],['done','完成',4]];
const KIT_MOD=[['review','文件审核'],['chat','对话'],['bid','标书（本机）'],['bidx','标书 + 补查'],['meet','会议纪要'],['transL','翻译（本机）'],['transC','翻译（云端）']];
let kit={m:'review',s:'wait'};
function kitPaint(){
  const k=KIT_ST.find(x=>x[0]===kit.s), cfg=TRIPS[kit.m], st=k[0].replace(/\d/,'');
  let at=Math.min(k[2],cfg.n.length-1);
  if(kit.s==='block') at=Math.max(0,cfg.n.indexOf('复检'));
  if(kit.s==='run3'||kit.s==='fail'||kit.s==='stop') at=Math.max(0,cfg.n.findIndex(isM)); if(at<0) at=cfg.n.length-2;
  if(cfg.local&&(kit.s==='run3'||kit.s==='fail'||kit.s==='stop')) at=cfg.n.length-2;
  const note={todo:'',run:kit.s==='run0'?'第 23 / 76 页':'正在审阅',wait:'等你确认',block:'复检拦截 2 处',fail:'接口超时',stop:'已停止',partial:'部分完成',done:'严重 2'}[st];
  const act={wait:'去确认',block:'去处理',fail:'重试'}[st]||'';
  const set=(e,scale)=>{e.dataset.trip=kit.m;e.dataset.at=at;e.dataset.st=st;e.dataset.note=note;e.dataset.scale=scale;e.dataset.tm=st==='run'?'0:38':'';e.dataset.act=act;renderTrip(e);};
  set($('#kitBar'),'bar'); set($('#kitMini'),'mini'); set($('#kitDot'),'dot');
  $('#kitAria').textContent=ariaOf(cfg,at,st,note);
  $$('#kitMod button').forEach(b=>b.classList.toggle('on',b.dataset.v===kit.m));
  $$('#kitSt button').forEach(b=>b.classList.toggle('on',b.dataset.v===kit.s));
}
function kitInit(){
  $('#kitMod').innerHTML=KIT_MOD.map(([v,t])=>`<button data-v="${v}">${t}</button>`).join('');
  $('#kitSt').innerHTML=KIT_ST.map(([v,t])=>`<button data-v="${v}">${t}</button>`).join('');
  $('#kitMod').onclick=e=>{const b=e.target.closest('button');if(b){kit.m=b.dataset.v;kitPaint();}};
  $('#kitSt').onclick=e=>{const b=e.target.closest('button');if(b){kit.s=b.dataset.v;kitPaint();}};
  $('#kitWalk').onclick=()=>{
    const bar=$('#kitBar'), n=TRIPS[kit.m].n.length; let i=0;
    const tick=()=>{bar.dataset.trip=kit.m;bar.dataset.st=i>=n?'done':'run';bar.dataset.at=Math.min(i,n-1);bar.dataset.note=i>=n?'':'进行中';bar.dataset.act='';bar.dataset.tm='';bar.dataset.scale='bar';renderTrip(bar);if(i>0&&i<n)walk(bar,i-1,i);i++;if(i<=n)setTimeout(tick,1000);};
    tick();
  };
  kitPaint();
  const all=[['只在本机 · 读取中','local',0,'run','解析原件'],['只在本机 · 完成','local',2,'done','全程本机'],['文件审核 · 读取中','review',0,'run','第 23 / 76 页'],['文件审核 · 等你','review',1,'wait','等你确认'],['文件审核 · 被拦截','review',2,'block','复检拦截 2 处'],['文件审核 · 模型中','review',3,'run','正在审阅'],['文件审核 · 失败','review',3,'fail','接口超时'],['文件审核 · 已停止','review',3,'stop','已停止'],['文件审核 · 部分完成','review',4,'partial','3 段未返回'],['文件审核 · 完成','review',4,'done','严重 2'],['会议 · 待校对','meet',1,'wait','待校对'],['标书 · 本机检查中','bid',2,'run','第 3 / 5 家']];
  $('#kitAll').innerHTML=all.map(([t,m,a,s,n])=>`<div class="row gap12" style="height:30px"><span class="f-aux" style="width:150px">${t}</span><span data-trip="${m}" data-at="${a}" data-st="${s}" data-note="${n}"></span></div>`).join('');
  // 对比度表（数值由 WCAG 相对亮度公式算出，算法见报告）
  const L=[['单位','k-org','#2F6BF0','4.07','#1E5FEF','4.63'],['人名','k-person','#8B4CF0','4.16','#8340EF','4.63'],['联系方式','k-contact','#0E97B2','3.08','#0B778C','4.65'],['证件','k-id','#E5711C','2.76','#AB5414','4.63'],['地址','k-addr','#6E8B1F','3.50','#5E761A','4.60'],['金额','k-amount','#C27A05','3.13','#9A6104','4.66'],['其他','k-other','#6B7686','4.06','#636D7C','4.62'],['往返线占位点（图形）','','#94A0B2','2.65','#8794A8','3.07']];
  $('#ctL').innerHTML=L.map(r=>`<tr><td>${r[1]?`<span class="rx ${r[1]}"><span class="r" style="color:${r[2]}">${r[0]}</span></span>`:r[0]}</td><td class="n">${r[2]}</td><td class="n low">${r[3]}</td><td>${r[1]?`<span class="rx ${r[1]}"><span class="r">${r[0]}</span></span>`:''} <span class="n">${r[4]}</span></td><td class="n ok">${r[5]}</td></tr>`).join('');
  const D=[['灵紫实色','#8B7CFF','#121820','5.46'],['灵紫深字 / 浅紫底','#B8AEFF','#242741','7.31'],['辅助字 / 浅紫底','#8E9AAB','#242741','5.11'],['辅助字 / 浅青底','#8E9AAB','#162E31','5.01'],['单位胶囊','#7EA6FF','#263145','5.46'],['人名胶囊','#B791FF','#2E2E45','5.34'],['联系方式胶囊','#4CC6DE','#1E343E','6.46'],['证件胶囊','#FF9F57','#372E2B','6.52'],['地址胶囊','#A9C85A','#2B342C','6.81'],['金额胶囊','#F1B544','#353128','7.05'],['其他胶囊','#9AA6B6','#282F38','5.47'],['往返线占位点（图形）','#5E6A7B','#121820','3.25']];
  $('#ctD').innerHTML=D.map(r=>`<tr><td>${r[0]}</td><td class="n">${r[1]}</td><td class="n">${r[2]}</td><td class="n ok">${r[3]}</td></tr>`).join('');
}

/* ---------- 迷你图表 ---------- */
function paintCharts(r=document){
  $$('[data-spark]',r).forEach(e=>{const v=e.dataset.spark.split(',').map(Number), mx=Math.max(...v,1), t0=e.dataset.start?+e.dataset.start:3;
    e.innerHTML=v.map((x,i)=>`<i class="${x===0?'z':''}${(i+t0)%7>=5?' we':''}" style="height:${Math.max(2,Math.round(x/mx*28))}px" title="11-${String(i+1).padStart(2,'0')} · ${x} 次"></i>`).join('');});
  $$('[data-ring]',r).forEach(e=>{const p=+e.dataset.ring, c=2*Math.PI*18;e.innerHTML=`<svg viewBox="0 0 40 40"><circle class="rb" cx="20" cy="20" r="18"/><circle class="rv" cx="20" cy="20" r="18" stroke-dasharray="${c*p/100} ${c}"/></svg><b>${p}%</b>`;if(p>85)e.classList.add('hot');e.title=`本月用量 ${p}%`;});
  $$('[data-stack]',r).forEach(e=>{
    const items=e.dataset.stack.split(',').map(x=>x.split(':')), cols=e.dataset.colors?e.dataset.colors.split(','):null, unit=e.dataset.unit||'', names={org:'单位',person:'人名',contact:'联系方式',id:'证件',addr:'地址',amount:'金额',other:'其他'};
    const tot=items.reduce((s,x)=>s+ +x[1],0);
    e.innerHTML=items.map(([k,v],i)=>`<i data-k="${k}" class="${names[k]?'k-'+k:''}" style="flex:${v};${cols?'--sc:'+cols[i]:''}" title="${names[k]||k} ${v}${unit||' 处'}"></i>`).join('');
    const lg=document.createElement('div');lg.className='stack-lg';
    lg.innerHTML=items.map(([k,v],i)=>`<span data-k="${k}" class="${names[k]?'k-'+k:''}" style="${cols?'--sc:'+cols[i]:''}"><i></i>${names[k]||k}<b>${v}${unit}</b></span>`).join('');
    e.after(lg);
    if(e.dataset.pick){const pick=k=>{const on=e.dataset.on===k?'':k;e.dataset.on=on;[e,lg].forEach(x=>{x.classList.toggle('pick',!!on);$$('[data-k]',x).forEach(y=>y.classList.toggle('on',y.dataset.k===on));});revealFilter(on);};
      e.onclick=lg.onclick=ev=>{const t=ev.target.closest('[data-k]');if(t)pick(t.dataset.k);};}
  });
}

/* ================= 外壳：近期会话 ================= */
const RECENT=[['设备采购合同（第二稿）','review','review',1,'wait','等你确认','confirm'],['泵站改造工程 · 开标初审','bid','bid',2,'run','本机检查中','bidp'],['周例会 11-18','meet','meet',1,'wait','待校对','meetp'],['年度维保服务合同','review','review',4,'done','已完成','result'],['Supply Agreement 译稿','trans','transL',1,'run','第 23 / 76 段','transp'],['违约金上限怎么写','chat','chat',4,'done','已回答','chat'],['报价单 · 华北区域','review','review',2,'block','复检拦截','confirm-b'],['项目启动会','meet','meet',0,'fail','转写失败','meet']];
function paintRecent(){
  $('#recent').innerHTML=RECENT.map(([t,m,tr,at,st,n,go])=>`<a class="rc m-${m}" data-nav="${go}"><span data-trip="${tr}" data-at="${at}" data-st="${st}" data-scale="dot" data-note="${n}"></span><span>${t}</span><span class="rc-tip"><span data-trip="${tr}" data-at="${at}" data-st="${st}" data-note="${n}"></span></span></a>`).join('');
}

/* ================= 6.3 登录开场往返线 ================= */
function loginTrip(){
  const g=$('#ltNodes'), xs=[70,210,350,490], col=['#33C0A6','#33C0A6','#8B7CFF','#33C0A6'];
  g.innerHTML=xs.map((x,i)=>`<circle class="lt-n" cx="${x}" cy="32" r="9" stroke="${col[i]}"/><g transform="translate(${x-7} 25) scale(.58)" stroke="${col[i]}" fill="none" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">${(ICONS[['hard-drive','eye-off','sparkle','undo-2'][i]]||[]).map(([t,a])=>'<'+t+' '+Object.entries(a).map(([k,v])=>k+'="'+v+'"').join(' ')+'/>').join('')}</g>`).join('');
  const run=$('#ltRun'), labs=$$('.lt-labels>div');
  run.setAttribute('stroke','url(#ltG)');
  if(!$('#ltG')){run.parentNode.insertAdjacentHTML('afterbegin','<defs><linearGradient id="ltG" x1="70" x2="490" gradientUnits="userSpaceOnUse"><stop offset="0" stop-color="#33C0A6"/><stop offset=".5" stop-color="#33C0A6"/><stop offset=".55" stop-color="#8B7CFF"/><stop offset=".78" stop-color="#8B7CFF"/><stop offset=".9" stop-color="#33C0A6"/></linearGradient></defs>');}
  const L=420;run.style.strokeDasharray=L;
  if(!full()){run.style.strokeDashoffset=0;labs.forEach(l=>l.classList.add('on'));return;}
  // 衡线标志的横线伸出纸边后延长成往返线：标志开场 0.6 秒后开始走，约 2.6 秒走完定格
  labs.forEach(l=>l.classList.remove('on'));
  run.animate([{strokeDashoffset:L},{strokeDashoffset:0}],{duration:2000,delay:600,easing:'cubic-bezier(.65,0,.35,1)',fill:'both'});
  labs.forEach((l,i)=>setTimeout(()=>l.classList.add('on'),600+i*660));
}

/* ================= 6.6 纸叠 ================= */
function pileInit(){
  const p=$('#pile'), host=$('.il-host',p);
  const add=()=>{p.classList.remove('drag');host.classList.remove('on');
    const box=$('#pileIn'), d=document.createElement('div');d.className='pfile';
    d.innerHTML=`<span class="mod-ic s m-review"><span data-i="file-text"></span></span><div class="grow"><b class="f-body" style="color:var(--si-t1);font-weight:600">设备采购合同（第三稿）.docx</b><div class="f-aux">1.2 MB</div></div><span data-trip="review" data-at="0" data-st="run" data-note="正在读取"></span>`;
    box.prepend(d);paintIcons(d);paintTrips(d);
    setTimeout(()=>{const t=$('[data-trip]',d);t.dataset.at=1;t.dataset.st='wait';t.dataset.note='等你确认';renderTrip(t);},1600);};
  p.addEventListener('dragover',e=>{e.preventDefault();p.classList.add('drag');host.classList.add('on');$('.ph',p).textContent='松开即可加入';});
  p.addEventListener('dragleave',()=>{p.classList.remove('drag');host.classList.remove('on');});
  p.addEventListener('drop',e=>{e.preventDefault();add();});
  $('#pilePick').onclick=()=>{host.classList.add('on');setTimeout(add,500);};
}

/* ================= 时间线（6.6、6.12、6.14、6.18） ================= */
const HIST=[
  ['今天','11-26',[['设备采购合同（第二稿）.docx','review','文件审核 · 采购方','review',1,'wait','等你确认','14:20','badge b-mid','待确认'],['泵站改造工程施工','bid','标书初审 · 5 家投标人','bid',2,'run','第 3 / 5 家','11:02','',''],['周例会 11-18','meet','会议纪要 · 52 分钟','meet',1,'wait','待校对','09:40','badge b-mid','待校对']]],
  ['昨天','11-25',[['年度维保服务合同.pdf','review','文件审核 · 销售方','review',4,'done','','17:35','badge b-hi','严重 2'],['Supply Agreement','trans','翻译 · 英 → 中 · 本机','transL',2,'done','','15:10','badge b-brand','全程本机']]],
  ['本周','11-24 周一',[['违约金上限怎么写','chat','对话','chat',4,'done','','16:02','',''],['报价单 · 华北区域.xlsx','review','文件审核 · 采购方','review',2,'block','复检拦截 2 处','10:12','badge b-mid','被拦截']]],
  ['更早','11-19',[['设备维保协调会','meet','会议纪要 · 38 分钟','meet',5,'done','','14:00','',''],['物业服务采购 · 开标初审','bid','标书初审 · 4 家投标人','bid',3,'done','','09:30','badge b-neu','没有严重问题']]],
];
function tl(el,filter,max){
  if(!el) return; let n=0;
  el.innerHTML=HIST.map(([d,sub,items])=>{
    const its=items.filter(x=>!filter||filter.includes(x[1])); if(!its.length||(max&&n>=max)) return '';
    const rows=its.filter(()=>!max||n++<max).map(([t,m,aux,tr,at,st,note,tm,bc,bt])=>`<div class="tl-it m-${m==='chat'?'chat':m}"><div class="ti-n"><b>${t}</b><span>${aux}</span></div><span data-trip="${tr}" data-at="${at}" data-st="${st}" data-note="${note}"></span>${bt?`<span class="${bc}">${bt}</span>`:''}<span class="ti-t">${tm}${el.id==='tlHist'?' · 保留至 12-'+(10+tm.length):''}</span><button class="ibtn sm" title="更多操作"><span data-i="ellipsis"></span></button></div>`).join('');
    return `<div class="tl-day"><div class="dl"><b>${d}</b>${sub}</div><div class="tl-items">${rows}</div></div>`;
  }).join('');
}

/* ================= 6.7 揭幕滑块 ================= */
const DOC=[
  ['h','设备采购合同'],
  ['ni',['甲方（采购方）：',['org','远拓精密制造有限公司','〔甲方单位〕']]],
  ['ni',['乙方（供货方）：',['org','青禾科技有限公司','〔乙方单位〕']]],
  ['ni',['乙方联系人：',['person','林晓舟','〔人名 1〕'],'　电话：',['contact','139 0000 6218','〔电话 1〕']]],
  ['ni',['乙方地址：',['addr','江宁区科创大道 88 号 6 栋','〔地址 1〕']]],
  ['p',[['cl','第一条　标的'],'　乙方向甲方供应数控加工中心 3 台，型号、配置见附件一《技术协议》，由乙方项目经理',['person','宋明远','〔人名 2〕'],'负责交付。']],
  ['p',[['cl','第四条　付款方式'],'　4.1 合同总价为人民币 ',['amount','4,200,000.00 元','〔金额 1〕'],'（大写：',['amount','肆佰贰拾万元整','〔金额 1·大写〕'],'），甲方按以下方式支付：签约后 7 日内支付 30%；设备到货后支付 40%；安装后支付 25%；余款 5% 作为质保金。']],
  ['p',['4.2 ',['org','青禾科技','〔乙方〕'],' 应在收到每期款项后 5 个工作日内开具等额增值税专用发票。开户行：',['other','6222 **** **** 3307','〔账号 1〕'],'，统一社会信用代码 ',['id','91320115MA1X8K2Q7R','〔证件 1〕'],'。']],
  ['p',['4.3 甲方逾期付款的，每逾期一日按应付未付金额的万分之五向乙方支付违约金。本合同经双方盖章后生效。']],
];
function docHtml(){
  return DOC.map(([t,c])=>{
    if(t==='h') return `<h3>${c}</h3>`;
    const inner=c.map(x=>typeof x==='string'?x:x[0]==='cl'?`<span class="cl">${x[1]}</span>`:`<span class="rx k-${x[0]}" data-k="${x[0]}"><span class="r">${x[1]}</span><span class="c">${x[2]}</span></span>`).join('');
    return `<p class="${t==='ni'?'ni':''}">${inner}</p>`;
  }).join('');
}
let revX=50;
function revealInit(){
  const r=$('#reveal'), html=docHtml();
  $('#lyRaw').innerHTML=html; $('#lyCode').innerHTML=html;
  const knob=$('#knob');
  const set=x=>{revX=Math.max(0,Math.min(100,x));r.style.setProperty('--x',revX+'%');knob.setAttribute('aria-valuenow',Math.round(revX));knob.setAttribute('aria-valuetext',revX<=0?'全部为模型将看到的':revX>=100?'全部为原文':`左边 ${Math.round(revX)}% 原文`);};
  set(50);
  let drag=false;
  const mv=e=>{if(!drag)return;const b=r.getBoundingClientRect();set((e.clientX-b.left)/b.width*100);};
  r.addEventListener('pointerdown',e=>{if(e.target.closest('.rx'))return;drag=true;r.classList.add('dragging');r.setPointerCapture(e.pointerId);mv(e);});
  r.addEventListener('pointermove',mv);
  r.addEventListener('pointerup',()=>{drag=false;r.classList.remove('dragging');}); // 松手不回弹
  knob.addEventListener('keydown',e=>{if(e.key==='ArrowLeft'){set(revX-5);e.preventDefault();}if(e.key==='ArrowRight'){set(revX+5);e.preventDefault();}if(e.key==='Home')set(0);if(e.key==='End')set(100);});
  document.addEventListener('keydown',e=>{if(!$('#s-confirm').classList.contains('on'))return;if((e.key==='v'||e.key==='V')&&!/INPUT|TEXTAREA/.test(document.activeElement.tagName)){set(revX>0?0:100);}});
  $('#srcSeg').onclick=e=>{const b=e.target.closest('button');if(!b)return;$$('#srcSeg button').forEach(x=>x.classList.toggle('on',x===b));const scan=b.dataset.v==='scan';$('#revealHost').hidden=scan;$('#scanHost').hidden=!scan;};
}
function revealFilter(k){const r=$('#reveal');r.classList.toggle('kfilter',!!k);$$('.rx',r).forEach(x=>x.classList.toggle('kon',x.dataset.k===k));}
function confirmState(blocked){
  const t=$('#confirmTrip');
  if(blocked){t.dataset.at=2;t.dataset.st='block';t.dataset.note='发送前复检拦截 2 处';t.dataset.act='去处理';t.dataset.tm='';}
  else{t.dataset.at=1;t.dataset.st='wait';t.dataset.note='等你确认';t.dataset.act='去确认';}
  renderTrip(t); $('#blockCard').hidden=!blocked;
}

/* ================= 6.8 地形条与还原扫描 ================= */
let restored=false;
function terrainInit(){
  const p=$('#resPaper'), t=$('#terrain'), H0=p.offsetHeight;
  t.innerHTML=$$('.hl',p).map((h,i)=>`<i class="${h.dataset.s}" style="top:${Math.round(h.offsetTop/H0*100)}%" data-i="${i}"><span class="tip">${h.dataset.t}</span></i>`).join('');
  t.onclick=e=>{const i=e.target.closest('i');if(!i)return;const h=$$('.hl',p)[+i.dataset.i];h.scrollIntoView({behavior:fx()==='off'?'auto':'smooth',block:'center'});h.classList.remove('flash2');void h.offsetWidth;h.classList.add('flash2');};
}
function playRestore(force){
  const p=$('#resPaper'), rs=$$('.rs',p), sw=$('#sweep');
  if(restored&&!force) return; restored=true;
  rs.forEach(x=>x.classList.remove('on','done'));
  if(fx()==='off'||H.classList.contains('shot')){rs.forEach(x=>x.classList.add('on','done'));return;}
  if(fx()==='lite'){rs.forEach(x=>x.classList.add('on','done'));return;} // 精简档定格终态
  const Hh=p.offsetHeight, dur=1700;
  sw.animate([{transform:'translateY(0)',opacity:0},{opacity:1,offset:.06},{opacity:1,offset:.94},{transform:`translateY(${Hh}px)`,opacity:0}],{duration:dur,easing:'linear'});
  rs.forEach(x=>{const y=x.offsetTop;setTimeout(()=>{x.classList.add('on');setTimeout(()=>x.classList.add('done'),700);},y/Hh*dur);});
}

/* ================= 6.9 合同对比：两张纸与缝线 ================= */
const CMP=[
  ['第三条　交货期','乙方应于合同生效后 <span class="chg cm" data-p="1">45 日</span>内交货。','乙方应于合同生效后 <span class="chg cm" data-p="1">60 日</span>内交货。'],
  ['第四条　付款方式','合同总价为人民币 4,200,000.00 元。<span class="chg cm" data-p="2">签约后 7 日内支付 20%</span>；到货后支付 40%；验收合格后支付 35%；质保金 5%。','合同总价为人民币 4,200,000.00 元。<span class="chg cm" data-p="2">签约后 7 日内支付 30%</span>；到货后支付 40%；安装后支付 25%；质保金 5%。'],
  ['','',' <span class="chg ca" data-p="3">乙方收到预付款后，应向甲方提供等额增值税专用发票。</span>'],
  ['第六条　验收','设备安装调试完毕后，甲方应在 <span class="chg cm" data-p="4">15 个工作日</span>内组织验收。<span class="chg cd" data-p="5">验收不合格的，乙方应在 10 日内免费更换。</span>','设备安装调试完毕后，甲方应在 <span class="chg cm" data-p="4">3 日</span>内组织验收；逾期未验收的，视为验收合格。'],
  ['第七条　质量保证','质保期为验收合格之日起 12 个月。','质保期为验收合格之日起 12 个月。<span class="chg ca" data-p="6">质保期内乙方应在 48 小时内响应维修。</span>'],
  ['第九条　违约责任','乙方逾期交货的，每逾期一日按合同总价的千分之一支付违约金，违约金总额<span class="chg cm" data-p="7">不超过合同总价的 10%</span>。','乙方逾期交货的，每逾期一日按合同总价的千分之一支付违约金，违约金总额<span class="chg cm" data-p="7">不超过合同总价的 5%</span>。'],
  ['第十二条　争议解决','因本合同引起的争议，提交<span class="chg cm" data-p="8">甲方所在地人民法院</span>诉讼解决。','因本合同引起的争议，提交<span class="chg cm" data-p="8">乙方所在地仲裁委员会</span>仲裁。'],
];
const DL=[[1,'cm','第三条 交货期','45 日 → 60 日'],[2,'cm','第四条 付款比例','预付 20% → 30%，验收款改为安装后'],[3,'ca','第四条 新增','预付款发票'],[4,'cm','第六条 验收期','15 个工作日 → 3 日，逾期视为合格'],[5,'cd','第六条 删除','不合格免费更换'],[6,'ca','第七条 新增','48 小时响应维修'],[7,'cm','第九条 违约金上限','10% → 5%'],[8,'cm','第十二条 争议解决','甲方所在地法院 → 乙方所在地仲裁']];
let ribRaf=0, ribT=0;
function compareInit(){
  const filler='其他条款两稿相同，此处省略。双方应本着诚实信用原则履行本合同，未尽事宜另行协商。';
  const side=k=>'<h3>设备采购合同</h3>'+CMP.map(c=>`${c[0]?`<p><span class="cl">${c[0]}</span>　${c[k]}</p>`:(c[k]?`<p>${c[k]}</p>`:'')}<p style="color:var(--si-t3)">${filler}</p>`).join('');
  $('#paperA').innerHTML=side(1); $('#paperB').innerHTML=side(2);
  const col=t=>({cm:'var(--si-warn)',ca:'var(--si-brand-icon)',cd:'var(--si-danger)'})[t];
  $('#dlist').innerHTML='<div class="f-aux" style="padding:4px 8px 10px">差异 8 处 · 点一条两边同时定位</div>'+DL.map(([p,t,a,b])=>`<div class="dli" data-p="${p}"><span class="bar" style="background:${col(t)}"></span><div><b>${a}</b><span>${b}</span></div></div>`).join('');
  $('#dlist').onclick=e=>{const d=e.target.closest('.dli');if(!d)return;const p=d.dataset.p;$$('.dli').forEach(x=>x.classList.toggle('on',x===d));
    $$('.chg').forEach(x=>x.classList.toggle('on',x.dataset.p===p));
    ['#colA','#colB'].forEach(c=>{const col=$(c), el=$(`.chg[data-p="${p}"]`,col)||$$('.chg',col).find(x=>+x.dataset.p>+p);if(el)col.scrollTo({top:el.offsetTop-col.clientHeight/3,behavior:fx()==='off'?'auto':'smooth'});});
    $$('#rib path').forEach(x=>x.classList.toggle('on',x.dataset.p===p));};
  const onScroll=()=>{
    if(fx()==='off'){$('#rib').innerHTML='';return;}
    if(fx()==='lite'){clearTimeout(ribT);$('#rib').style.opacity=.0;ribT=setTimeout(()=>{drawRib();$('#rib').style.opacity=1;},140);return;} // 精简：滚动停止后再画
    if(!ribRaf) ribRaf=requestAnimationFrame(()=>{ribRaf=0;drawRib();});
  };
  $('#colA').addEventListener('scroll',onScroll); $('#colB').addEventListener('scroll',onScroll);
  window.addEventListener('resize',()=>{if($('#s-compare-r').classList.contains('on'))drawRib();});
  $$('.sheet-drop').forEach(s=>s.onclick=()=>{s.classList.add('full');$('.sp',s).innerHTML=ic('file-text');$('.f-aux',s).textContent=(s.dataset.sheet==='a'?'设备采购合同（第一稿）':'设备采购合同（第二稿）')+'.docx';const ready=$$('.sheet-drop.full').length===2;$('#cmpGo').disabled=!ready;$('#midGo').classList.toggle('ready',ready);});
}
function drawRib(){
  const g=$('#gut'), svg=$('#rib'); if(fx()==='off'){svg.innerHTML='';return;}
  const gb=g.getBoundingClientRect(), W=gb.width, Hh=gb.height; let s='';
  $$('#paperA .chg,#paperB .chg').length;
  const ids=[...new Set($$('.chg').map(x=>x.dataset.p))];
  ids.forEach(p=>{
    const a=$(`#paperA .chg[data-p="${p}"]`), b=$(`#paperB .chg[data-p="${p}"]`);
    const ra=a?a.getBoundingClientRect():null, rb=b?b.getBoundingClientRect():null;
    const t=(a||b).classList.contains('ca')?'ca':(a||b).classList.contains('cd')?'cd':'cm';
    // 新增或删除的一侧没有对应文字时，缎带收成那一侧的一个点
    const ya1=ra?ra.top-gb.top:null, ya2=ra?ra.bottom-gb.top:null, yb1=rb?rb.top-gb.top:null, yb2=rb?rb.bottom-gb.top:null;
    const A1=ya1??((yb1+yb2)/2), A2=ya2??A1, B1=yb1??((ya1+ya2)/2), B2=yb2??B1;
    if(Math.max(A2,B2)<-40||Math.min(A1,B1)>Hh+40) return; // 只画可见的部分
    const c=W*.5;
    s+=`<path class="${t}" data-p="${p}" d="M0 ${A1}C${c} ${A1} ${c} ${B1} ${W} ${B1}L${W} ${B2}C${c} ${B2} ${c} ${A2} 0 ${A2}Z"/>`;
  });
  svg.innerHTML=s;
}

/* ================= 6.10 开标时间尺 ================= */
const BIDS=[['泵站改造工程施工','水务工程',-5,'done',2,'严重 3'],['城北管网改造监理','水务工程',-2,'done',0,'没有严重问题'],['水厂加药系统采购','设备采购',0,'run',0,'本机检查中'],['数控设备年度维保','设备采购',3,'wait',0,'1 家待识别'],['园区物业服务','物业服务',6,'todo',0,'未上传投标文件'],['二期泵房设备采购','设备采购',12,'todo',0,'未上传投标文件'],['南区排涝泵站','水务工程',19,'todo',0,'未上传投标文件']];
function bidInit(){
  const r=$('#ruler'), days=30, start=-7, X=d=>((d-start)/days*100).toFixed(2)+'%';
  let h='<div class="axis"></div>';
  for(let d=start;d<=start+days;d++){const date=new Date(2026,10,26+d), wk=date.getDay()===1;h+=`<span class="dd${wk?' wk':''}" style="left:${X(d)}"></span>`;if(wk)h+=`<span class="lab" style="left:${X(d)}">${date.getMonth()+1}-${date.getDate()}</span>`;}
  h+=`<span class="today" style="left:${X(0)}"><span>今天 11-26</span></span>`;
  const byDay={};BIDS.forEach((b,i)=>(byDay[b[2]]=byDay[b[2]]||[]).push(i));
  Object.entries(byDay).forEach(([d,is])=>{h+=`<span class="pj" data-d="${d}" style="left:${X(+d)}" title="${is.map(i=>BIDS[i][0]).join('、')}">${is.map(i=>`<i class="${BIDS[i][4]?'bad':''}${+d<0?' past':''}"></i>`).join('')}</span>`;});
  r.innerHTML=h;
  const rows=d=>{$('#bidRows').innerHTML=BIDS.map((b,i)=>[b,i]).filter(([b])=>d==null||b[2]===d).sort((x,y)=>Math.abs(x[0][2])-Math.abs(y[0][2])).map(([[n,c,dd,st,bad,word]])=>{
    const date=new Date(2026,10,26+dd), at={done:3,run:2,wait:1,todo:0}[st];
    return `<div class="rowc m-bid" data-go="bidp"><div class="rc-m"><span class="mod-ic s m-bid"><span data-i="file-stack"></span></span><div class="rc-n"><b>${n}</b><span>${c} · 开标 ${date.getMonth()+1}-${String(date.getDate()).padStart(2,'0')} 09:30${dd===0?' · 今天':dd>0?` · ${dd} 天后`:''}</span></div><span data-trip="bid" data-at="${at}" data-st="${st}" data-note="${st==='done'?'':word}"></span>${st==='done'?`<span class="badge ${bad?'b-hi':'b-neu'}">${bad?'<i class="d"></i>':''}${word}</span>`:''}<button class="ibtn sm"><span data-i="ellipsis"></span></button></div></div>`;}).join('');paintIcons($('#bidRows'));paintTrips($('#bidRows'));};
  rows(null);
  r.onclick=e=>{const p=e.target.closest('.pj');if(!p)return;const on=!p.classList.contains('on');$$('.pj',r).forEach(x=>x.classList.remove('on'));if(on){p.classList.add('on');rows(+p.dataset.d);}else rows(null);};
  $('#rulerAll').onclick=()=>{$$('.pj',r).forEach(x=>x.classList.remove('on'));rows(null);};
  // 6.11 开标桌
  const BD=[['A','甲泵业','4 个文件 · 218 页',0,'提示 2'],['B','乙水务','3 个文件 · 176 页',2,'提示 1'],['C','丙机电','5 个文件 · 240 页',1,'提示 2'],['D','丁环境','3 个文件 · 152 页',0,'提示 1'],['E','戊建设','2 个文件 · 98 页',0,'提示 1']];
  $('#bdocs').innerHTML=BD.map(([c,n,f,bad,tip])=>`<div class="bdoc${bad?' bad':''}" data-b="${c}" tabindex="0">${bad?`<span class="rn" title="严重 ${bad}">${bad}</span>`:''}<div class="bh"><span class="code">${c}</span><b>${n}</b></div><div class="lines"><i></i><i></i><i></i></div><span class="f-aux">${f}</span><div class="row gap4 mt6">${bad?`<span class="badge b-hi"><i class="d"></i>严重 ${bad}</span>`:'<span class="badge b-neu">没有严重问题</span>'}<span class="badge b-neu">${tip}</span></div></div>`).join('');
  $('#bdocs').onclick=e=>{const b=e.target.closest('.bdoc');if(!b)return;const on=!b.classList.contains('on');$$('.bdoc').forEach(x=>x.classList.remove('on'));$('#bdocs').classList.toggle('dim',on);if(on)b.classList.add('on');
    $('#bidWho').textContent=on?$('b',b).textContent+' · 再点卡片取消':'全部投标人';$$('#s-bidp .iss').forEach(x=>x.hidden=on&&x.dataset.b!==b.dataset.b);};
}

/* ================= 6.12 声纹带 ================= */
function vband(el,n){
  el.innerHTML=Array.from({length:n},(_,i)=>`<i style="--h:${10+Math.round(Math.abs(Math.sin(i*.55)*40+Math.sin(i*.17)*26+Math.cos(i*1.3)*10))}px;--n:${i}"></i>`).join('');
}
function meetInit(){
  vband($('#vbStrip'),96); vband($('#vbStripRec'),96);
  const s=$('#vbStrip');
  s.addEventListener('pointermove',e=>{if(!full())return;const b=s.getBoundingClientRect(), x=e.clientX-b.left;$$('i',s).forEach(i=>{const d=Math.abs(i.offsetLeft-x);i.classList.toggle('near',d<60);i.style.transform=d<90?`scaleY(${1+(90-d)/90*.6})`:'';});});
  s.addEventListener('pointerleave',()=>$$('i',s).forEach(i=>{i.classList.remove('near');i.style.transform='';}));
}
let recTimer=0;
function recTick(on){clearInterval(recTimer);if(!on)return;let t=766;recTimer=setInterval(()=>{t++;const h=String(Math.floor(t/3600)).padStart(2,'0'),m=String(Math.floor(t/60)%60).padStart(2,'0'),s=String(t%60).padStart(2,'0');$('#recT').textContent=`${h}:${m}:${s}`;},1000);}

/* ================= 6.13 发言泳道 ================= */
const SPK=[['王经理','var(--si-m-meet)'],['李工','var(--si-info)'],['陈主管','var(--si-k-addr)'],['赵会计','var(--si-k-id)']];
const LINES=[[0,0,62,'今天主要对一下泵站项目的进度和下周的验收安排。'],[1,64,190,'设备已经到了两台，第三台供应商说要推迟到下周三。'],[0,192,260,'推迟的原因说清楚了吗？合同里交货期是六十天。'],[1,262,420,'说是电机厂那边排产，我让他们出个书面说明。'],[2,430,560,'验收方案我这边已经写好，按十五个工作日安排。'],[3,570,700,'付款这块，到货款要等三台都到了再一起付吗？'],[0,702,840,'对，按合同第四条，三台到齐验收合格以后再付到货款。'],[2,850,1100,'那验收组的名单周五前报给我。'],[1,1110,1500,'好的，另外现场还缺一个吊装方案，我周四前补上。'],[3,1520,1900,'质保金比例我核了一下是百分之五，没有问题。'],[0,1910,2300,'那就这样，下周一再碰一次。'],[2,2320,2800,'我把验收的时间表发到群里。'],[1,2820,3124,'散会前我再确认一下吊装的时间。']];
const TOT=3124; let ph=500, phTimer=0;
function lanesInit(){
  $('#lanes').innerHTML=SPK.map(([n,c],k)=>`<div class="lane" style="--lc:${c}"><span class="ln"><i></i>${n}</span><span class="lt"></span>${LINES.map((l,i)=>l[0]===k?`<span class="sg" data-i="${i}" style="left:${l[1]/TOT*100}%;width:${(l[2]-l[1])/TOT*100}%" title="${l[3]}"></span>`:'').join('')}</div>`).join('')+'<span class="ph" id="ph"></span>';
  $('#transcript').innerHTML=LINES.slice(0,9).map(([k,a,b,t],i)=>`<div class="ts-line" data-i="${i}" style="--lc:${SPK[k][1]}"><span class="tw"><b>${SPK[k][0]}</b>${fmt(a)}</span><span>${t}</span></div>`).join('');
  $('#lanes').onclick=e=>{const s=e.target.closest('.sg');if(s)seek(LINES[+s.dataset.i][1]+1);};
  $('#lanePlay').onclick=()=>{if(phTimer){clearInterval(phTimer);phTimer=0;return;}phTimer=setInterval(()=>seek(ph+12),100);};
  document.addEventListener('keydown',e=>{if(!$('#s-meetp').classList.contains('on'))return;const i=LINES.findIndex(l=>ph>=l[1]&&ph<=l[2]);if(e.key==='ArrowRight')seek(LINES[Math.min(LINES.length-1,i+1)][1]+1);if(e.key==='ArrowLeft')seek(LINES[Math.max(0,i-1)][1]+1);});
  seek(ph);
}
function fmt(s){return `${Math.floor(s/60)}:${String(s%60).padStart(2,'0')}`;}
function seek(t){ph=Math.max(0,Math.min(TOT,t));$('#ph').style.left=`calc(${ph/TOT*100}% - ${ph/TOT*96}px)`;$('#ph').style.left=`${ph/TOT*100}%`;$('#ph').style.transform=`translateX(${-ph/TOT*96}px)`;
  $('#laneT').textContent=fmt(Math.round(ph)).padStart(5,'0');
  const i=LINES.findIndex(l=>ph>=l[1]&&ph<=l[2]);
  $$('#lanes .sg').forEach(s=>s.classList.toggle('cur',+s.dataset.i===i));
  $$('#transcript .ts-line').forEach(s=>s.classList.toggle('cur',+s.dataset.i===i));
}

/* ================= 6.14 / 6.15 翻译 ================= */
function transLine(m){
  const svg=$('#tlSvg'), cloud=m==='c';
  const st=cloud?['读取','确认遮住','复检','模型翻译','还原']:['读取','本机翻译','完成'];
  const xs=cloud?[10,80,150,220,290]:[10,150,290];
  let s='';
  for(let i=1;i<xs.length;i++){const z=cloud&&(i===3)?'m':cloud&&i===4?'r':'l';
    if(z==='r'){const xm=(xs[i-1]+xs[i])/2;s+=`<path d="M${xs[i-1]} 14H${xm}" stroke="var(--si-trip-m)" stroke-width="3" stroke-linecap="round"/><path d="M${xm} 14H${xs[i]}" stroke="var(--si-trip-l)" stroke-width="3" stroke-linecap="round"/>`;}
    else s+=`<path d="M${xs[i-1]} 14H${xs[i]}" stroke="${z==='m'?'var(--si-trip-m)':'var(--si-trip-l)'}" stroke-width="3" stroke-linecap="round"/>`;}
  xs.forEach((x,i)=>{const mz=isM(st[i]);s+=`<circle cx="${x}" cy="14" r="6" fill="${mz?'var(--si-trip-m)':'var(--si-trip-l)'}"/><text x="${x}" y="36" text-anchor="middle" font-size="11.5" fill="var(--si-t3)">${st[i]}</text>`;});
  svg.innerHTML=s;
  if(full()) svg.animate([{opacity:.2,transform:'translateX(-6px)'},{opacity:1,transform:'none'}],{duration:420,easing:'cubic-bezier(.22,1,.36,1)'});
  $('#tlSays').innerHTML=cloud?'<b>遮住、复检后发送，回来在本机还原</b><span>名称与金额换成代号后才交给模型 · 万字约三分钟</span>':'<b>全程本机 · 不外发</b><span>在公司服务器上的本地翻译模型完成 · 万字约一小时</span>';
}
function transInit(){
  $('#tmode').onclick=e=>{const b=e.target.closest('button');if(!b)return;$$('#tmode button').forEach(x=>x.classList.toggle('on',x===b));transLine(b.dataset.m);};
  transLine('l');
  const src=['1. Definitions. In this Agreement, the following terms shall have the meanings set out below.','2. Supply. The Supplier shall supply the Products to the Buyer in accordance with the Purchase Orders.','3. Price. The prices for the Products shall be as set out in Schedule 1 and are exclusive of VAT.','4. Payment. The Buyer shall pay each invoice within thirty (30) days of receipt.','5. Delivery. Delivery shall be made DDP to the Buyer\'s premises.','6. Warranty. The Supplier warrants that the Products shall be free from defects for twelve (12) months.'];
  const dst=['1. 定义。本协议中，下列术语具有以下含义。','2. 供货。供应商应按照采购订单向买方供应产品。','3. 价格。产品价格见附表一，不含增值税。','4. 付款。买方应在收到每张发票后三十（30）日内付款。','<span style="color:var(--si-t3)">正在翻译</span><span class="caret"></span>',''];
  $('#trSrc').innerHTML=src.map(t=>`<p class="ni" style="margin-bottom:14px;font-size:14.5px;line-height:26px">${t}</p>`).join('');
  $('#trDst').innerHTML=dst.map(t=>`<p class="ni" style="margin-bottom:14px;font-size:14.5px;line-height:26px;min-height:52px">${t}</p>`).join('');
  $('#trRuler').innerHTML=src.map((_,i)=>`<i class="${i<4?'d':i===4?'g':''}" style="--f:1"></i>`).join('');
  tl($('#tlTrans'),['trans'],3);
}

/* ================= 6.19 设置 · 个人 ================= */
function meInit(){
  const th=[['浅色','#F3F6F9','#FFFFFF','#E1E6ED','#078A76','#6D5BF6'],['深色','#0B0F14','#121820','#222B36','#14A88E','#8B7CFF'],['跟随系统','','','','','']];
  const thumb=(c,s,l,b,a)=>`<div class="thumb" style="background:${c}"><div class="s" style="background:${s};border-right:1px solid ${l}"><i style="background:${b};width:70%"></i><i style="background:${l}"></i><i style="background:${l}"></i><i style="background:${l};width:60%"></i></div><div class="m"><i style="background:${l};width:50%"></i><div class="cd" style="background:${s};border:1px solid ${l}"><i style="width:14px;height:6px;background:${b};border-radius:3px"></i><i style="width:24px;height:6px;background:${a};border-radius:3px;opacity:.7"></i></div><i style="background:${l};width:80%"></i></div></div>`;
  $('#th3').innerHTML=th.map(([n,...c],i)=>`<div class="tc${i===0?' on':''}" data-th="${i}">${i<2?thumb(...c):`<div class="thumb" style="grid-template-columns:1fr 1fr;border:0"><div style="overflow:hidden">${thumb(...th[0].slice(1))}</div><div style="overflow:hidden">${thumb(...th[1].slice(1))}</div></div>`}<div class="tl3"><span class="radio${i===0?' on':''}"></span>${n}</div></div>`).join('');
  $('#th3').onclick=e=>{const t=e.target.closest('.tc');if(!t)return;$$('#th3 .tc').forEach(x=>x.classList.toggle('on',x===t));if(t.dataset.th<2)setTheme(t.dataset.th==='1'?'dark':'light');};
  $('#fsSeg').onclick=e=>{const b=e.target.closest('button');if(!b)return;$$('#fsSeg button').forEach(x=>x.classList.toggle('on',x===b));$('#fsSample').style.fontSize=(15*+b.dataset.f)+'px';};
  const F=[['auto','自动','按电脑与系统设置'],['full','完整','光点走线、依次弹入'],['lite','精简','直接切换、定格终态'],['off','关闭','只留淡入淡出']];
  $('#fx4').innerHTML=F.map(([v,n,d])=>`<div class="fc${v==='auto'?' on':''}" data-fx="${v}"><b>${n}</b><div class="pv" data-fxv="${v}" data-trip="review" data-at="3" data-st="run" data-note=""></div><span>${d}</span></div>`).join('');
  $('#fx4').onclick=e=>{const c=e.target.closest('.fc');if(!c)return;$$('#fx4 .fc').forEach(x=>x.classList.toggle('on',x===c));if(c.dataset.fx!=='auto')setFx(c.dataset.fx);};
}

/* ================= 6.20 设置 · 模型 ================= */
const MODELS=[['DeepSeek-V3','deepseek-chat · api.deepseek.com · 密钥 …3f9a','审核默认',612,9,'0.9 s','99.1%',46,'¥ 38.20',[18,22,30,12,0,0,26,31,28,35,40,8,0,0,29,33,38,27,30,0,0,34,36,24,28,31]],
              ['通义千问 Max','qwen-max · dashscope.aliyuncs.com · 密钥 …a2c1','助手默认',248,3,'1.4 s','98.8%',0,'¥ 21.65',[6,8,12,4,0,0,9,11,10,14,12,3,0,0,10,12,14,9,11,0,0,13,12,9,10,11]],
              ['备用模型','gpt-compatible · 10.0.0.8 · 密钥 …77b0','已停用',0,0,'—','—',0,'¥ 0.00',[0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0]]];
let mapDone=false;
function modelInit(){
  // ① 出口地图
  const local=[['名称识别','GLiNER',1],['语义检索','bge-m3',1],['录音转写','FireRed 一组',1],['本地翻译','Hy-MT2',0]];
  const gates=[['1','调用上限','每人每天 200 次'],['2','发送前复检','本月拦截 7 次',1],['3','发送','本月 860 次 · 99.0%'],['4','记录在案','本月 ¥ 59.85<br>2 次未收录价格']];
  const nodes=[['DeepSeek-V3','612 次',612,'审核默认',0],['通义千问 Max','248 次',248,'助手默认',0],['备用模型','已停用',0,'',1]];
  let s=`<rect class="zbox-l" x="0" y="20" width="210" height="270" rx="14"/><text class="zt l" x="16" y="44">本机 · 原件不出内网</text>`;
  local.forEach(([a,b,on],i)=>{const y=62+i*54;s+=`<g class="lm lit${on?'':' off'}" style="--i:${i}"><rect x="14" y="${y}" width="182" height="42" rx="9"/><circle cx="32" cy="${y+21}" r="4"/><text x="46" y="${y+18}">${a}</text><text class="s" x="46" y="${y+33}">${b}${on?' · 运行中':' · 未启用'}</text></g>`;});
  s+=`<path class="pipe lit" style="--i:4" d="M210 155H246"/>`;
  gates.forEach(([n,a,b,hot],i)=>{const x=250+i*118;s+=`<g class="gate lit${hot?' hot':''}" style="--i:${5+i}"><rect x="${x}" y="104" width="106" height="104" rx="10"/><text class="gn" x="${x+12}" y="128">${n}</text><text x="${x+12}" y="154">${a}</text><foreignObject x="${x+10}" y="160" width="92" height="46"><div xmlns="http://www.w3.org/1999/xhtml" style="font-size:11.5px;line-height:15px;color:var(--${hot?'si-warn-ink':'si-t3'})">${b}</div></foreignObject></g>`;if(i<3)s+=`<path class="pipe lit" style="--i:${5+i}" d="M${x+106} 155H${x+118}"/>`;});
  s+=`<text class="zt l" x="250" y="96" style="fill:var(--si-t2)">模型网关 · gateway.py 的顺序</text>`;
  s+=`<rect class="zbox-m" x="744" y="20" width="156" height="270" rx="14"/><text class="zt m" x="760" y="44">云端 · 只见代号</text>`;
  const maxC=612;
  nodes.forEach(([n,c,v,def,off],i)=>{const y=70+i*74, w=off?2:2+v/maxC*8;
    s+=`<path class="wire lit${off?' off':''}" style="--i:${9+i};stroke-width:${w}" d="M722 155C736 155 736 ${y+24} 756 ${y+24}"/>`;
    s+=`<g class="node lit${off?' off':''}" data-n="${i}" style="--i:${10+i}"><rect x="756" y="${y}" width="132" height="48" rx="10"/><text x="770" y="${y+21}">${n}</text><text class="s" x="770" y="${y+37}">${c}${def?' · '+def:''}</text>${def?`<path class="star" transform="translate(866 ${y+8}) scale(.62)" d="M17.5 1.5c.3 1.9.9 2.6 3 3-2.1.4-2.7 1.1-3 3-.3-1.9-.9-2.6-3-3 2.1-.4 2.7-1.1 3-3z"><title>${def}</title></path>`:''}</g>`;});
  s+=`<path class="pipe lit" style="--i:9;stroke:var(--si-trip-m)" d="M710 155H722"/><circle class="ping" id="ping" r="5" cx="0" cy="0" opacity="0"/>`;
  $('#xm').innerHTML=s;
  const io=new IntersectionObserver(es=>{es.forEach(e=>{if(e.isIntersecting&&!mapDone){mapDone=true;$('#xm').classList.add('go');}});},{threshold:.3});
  io.observe($('#xm'));
  $('#mapReplay').onclick=()=>{const x=$('#xm');x.classList.remove('go');void x.getBoundingClientRect();x.classList.add('go');};
  // ② 值班席位
  const seats=[['审核默认','DeepSeek-V3','结构化输出已通过',0],['助手默认','通义千问 Max','对话与追问',1]];
  $('#duty').innerHTML=seats.map(([l,m,d,i])=>`<div class="card seat" data-seat="${i}"><span class="mod-ic m-chat"><span data-i="${i?'message-square':'list-checks'}"></span></span><div class="grow"><span class="lbl">${l}</span><b>${m}</b><span class="f-aux">${d}</span></div><div style="position:relative"><button class="btn sm sec" data-act="seat"><span data-i="replace"></span>更换</button><div class="pop menu" hidden style="position:absolute;right:0;top:36px;width:240px;z-index:9;padding:6px">
    <div class="mi hv"><span data-i="sparkle"></span>DeepSeek-V3<span class="badge b-ok ml-auto">可用</span></div><div class="mi"><span data-i="sparkle"></span>通义千问 Max<span class="badge b-ok ml-auto">可用</span></div>${i?'':`<div class="mi" style="opacity:.5"><span data-i="sparkle"></span><span class="grow">备用模型<br><span class="f-aux">结构化输出未通过</span></span></div>`}</div></div></div>`).join('');
  // ③ 已接入
  $('#mrows').innerHTML=MODELS.map(([n,sub,role,calls,fail,med,ok,ring,cost,sp],i)=>{
    const off=role==='已停用';
    return `<div class="rowc mrow${off?' off':''}" data-m="${i}"><div class="rc-m"><span class="mod-ic m-chat"${off?' style="--mc:var(--si-t4);--mcs:var(--si-hover)"':''}><span data-i="sparkle"></span></span>
    <div class="rc-n"><b>${n}${role==='审核默认'?'<span class="badge b-ai"><span data-i="sparkle"></span>审核默认</span>':role==='助手默认'?'<span class="badge b-ai">助手默认</span>':''}${i===1?'<span class="badge b-warn-line" title="余额对账与价格表相差超过 10%">价格表可能已过期</span>':''}</b><span>${sub}</span>
      <div class="chk2" data-chk></div></div>
    <div class="spk"><span class="spark-c" data-spark="${sp.join(',')}" style="--sc:${off?'var(--si-t4)':'var(--si-ai)'}"></span><span>${off?'本月没有调用':`中位 ${med} · 成功 ${ok}`}</span></div>
    <div class="use">${ring?`<span class="sring" data-ring="${ring}"></span>`:''}<div><b>${calls}</b><span>次 · ${cost}</span></div></div>
    <span class="badge ${off?'b-neu':'b-ok'}">${off?'已停用':'可用'}</span>
    <button class="btn sm sec" data-act="probe"${off?' disabled':''}><span data-i="activity"></span>体检</button><button class="ibtn sm" data-act="expand" title="展开"><span data-i="chevron-down"></span></button></div>
    <div class="rc-x"><div class="x3">
      <div><h5>体检</h5><div class="kv"><span>接通</span><span>通过 · 0.6 s</span><span>结构化输出</span><span>${i===2?'未通过':'通过'}</span><span>上次</span><span>11-26 08:00</span></div></div>
      <div><h5>本月用途</h5><div class="stackbar" data-stack="审核:${[420,0,0][i]},助手:${[96,212,0][i]},标书:${[84,24,0][i]},测试:${[12,12,1][i]}" data-colors="var(--si-m-review),var(--si-ai),var(--si-m-bid),var(--si-t4)" data-unit=" 次"></div></div>
      <div><h5>参数与单价</h5><div class="kv"><span>输入</span><span>¥ 2.00 / 百万</span><span>输出</span><span>¥ 8.00 / 百万</span><span>价格表</span><span>2026-11-20 · 厂商公开价</span><span>月上限</span><span>¥ 100</span></div><div class="row gap8 mt12"><button class="btn sm sec"><span data-i="pencil"></span>编辑</button><button class="btn sm txt">${off?'启用':'停用'}</button></div></div>
    </div></div></div>`;}).join('');
  // ④ 接入新模型
  const T=[['K','Kimi','月之暗面'],['G','GLM-4.6','智谱'],['D','豆包','火山引擎'],['H','混元','腾讯云'],['W','文心','百度智能云'],['+','自定义模型','兼容 OpenAI 接口']];
  $('#tiles').innerHTML=T.map(([l,n,v],i)=>`<div class="tile${i===5?' cus':''}" data-t="${i}" tabindex="0"><span class="tlg">${l}</span><b>${n}</b><span>${v}</span></div>`).join('');
  $('#tiles').onclick=e=>{const t=e.target.closest('.tile');if(!t)return;$$('.tile').forEach(x=>x.classList.toggle('on',x===t));
    const c=$('#connect');if(t.dataset.t==='5'){c.hidden=false;c.innerHTML='<div class="ntc info"><span class="ni"><span data-i="info"></span></span><div class="nb"><b>自定义模型在抽屉里填写</b><p>接口地址、模型名与单价都需要手动填写。</p><div class="na"><button class="btn sm sec">打开抽屉</button></div></div></div>';paintIcons(c);return;}
    const n=T[+t.dataset.t][1];c.hidden=false;
    c.innerHTML=`<div class="frm"><div class="fr"><label>API 密钥</label><div class="fx"><div class="inp36"><span data-i="key-round"></span><input placeholder="粘贴 ${n} 的 API 密钥"></div><em>密钥加密保存，界面只显示尾号。</em></div></div><div class="fr"><label>模型与接口</label><div class="fx"><div class="inp36 ro"><span>推荐值已填好</span><button class="btn sm txt ml-auto">修改</button></div></div></div><div class="fr"><label></label><div class="fx"><div class="row gap14"><button class="btn pri" data-act="enable" data-n="${n}"><span data-i="plug"></span>测试并启用</button><div class="steps3" id="steps3"></div></div></div></div></div>`;paintIcons(c);};
}
function probe(row){
  const i=+row.dataset.m, chk=$('[data-chk]',row), ping=$('#ping'), node=$(`.node[data-n="${i}"] rect`);
  chk.innerHTML=`<span class="run">${ic('loader-circle')}接通</span><span>${ic('circle')}结构化输出</span>`;
  if(full()&&node){const nb=node.getBBox();ping.animate([{transform:'translate(722px,155px)',opacity:1},{transform:`translate(${nb.x}px,${nb.y+nb.height/2}px)`,opacity:1},{opacity:0}],{duration:900,easing:'cubic-bezier(.22,1,.36,1)'});}
  setTimeout(()=>{chk.innerHTML=`<span class="ok">${ic('circle-check')}接通 0.6 s</span><span class="run">${ic('loader-circle')}结构化输出</span>`;},900);
  setTimeout(()=>{chk.innerHTML=`<span class="ok">${ic('circle-check')}接通 0.6 s</span><span class="ok">${ic('circle-check')}结构化输出</span><span>刚刚</span>`;},1900);
}

/* ================= 6.21～6.28 ================= */
function setNav(){
  const L=[['set-me','user-round','个人'],null,['set-acct','users','账号'],['set-role','key-round','角色'],['set-model','sparkle','模型'],['set-req','list-checks','审核要求'],['set-law','gavel','法规'],['set-tpl','bookmark','范本与知识库'],['set-cat','tags','标书分类'],['set-redact','eye-off','脱敏'],['set-data','database','数据']];
  $$('[data-setnav]').forEach(n=>{n.innerHTML=L.map(x=>x?`<a class="sn${x[0]===n.dataset.setnav?' on':''}" data-nav="${x[0]}"><span data-i="${x[1]}"></span>${x[2]}</a>`:'<div class="sn-g">管理</div>').join('');});
}
function setOthers(){
  const A=[['王','王经理','wang.jl','业务管理员','今天 09:12','启用'],['李','李工','li.gong','普通用户','昨天 17:40','启用'],['陈','陈主管','chen.zg','审核专员','11-24 10:02','启用'],['赵','赵会计','zhao.kj','普通用户','11-02 15:30','停用'],['系','系统管理员','admin','系统管理员','今天 08:30','启用']];
  $('#accts').innerHTML=A.map(([a,n,u,r,t,s])=>`<div class="rowc"><div class="rc-m"><div class="avatar" style="width:40px;height:40px">${a}</div><div class="rc-n"><b>${n}</b><span>${u}</span></div><span class="badge ${r.includes('管理员')?'b-ai':'b-neu'}">${r}</span><span class="f-aux" style="width:140px">最近登录 ${t}</span><span class="badge ${s==='启用'?'b-ok':'b-neu'}">${s}</span><button class="ibtn sm"><span data-i="ellipsis"></span></button></div></div>`).join('');
  const R=['普通用户','审核专员','业务管理员','系统管理员'];
  const G=[['文件审查','m-review',[['文件审核',[1,1,1,0]],['合同对比',[1,1,1,0]],['标书初审',[0,1,1,0]]]],['工具','m-meet',[['会议纪要',[1,1,1,0]],['翻译',[1,1,1,0]]]],['管理','m-chat',[['范本与知识库',[0,1,1,0]],['审核要求',[0,0,1,0]],['模型',[0,0,0,1]],['账号与角色',[0,0,0,1]]]]];
  let h=`<thead><tr><th>功能</th>${R.map(r=>`<th>${r}</th>`).join('')}</tr></thead><tbody>`;
  G.forEach(([g,m,rows])=>{h+=`<tr class="g ${m}"><td colspan="5">${g}</td></tr>`;rows.forEach(([n,v])=>{h+=`<tr><td>${n}</td>${v.map((x,j)=>`<td data-o="${x}"><span class="cb2${x?' on':''}" role="checkbox" aria-checked="${!!x}" tabindex="0" aria-label="${R[j]} · ${n}">${x?ic('check'):''}</span></td>`).join('')}</tr>`;});});
  $('#matrix').innerHTML=h+'</tbody>';
  const upd=()=>{const n=$$('#matrix td.chg').length;$('#savebar').hidden=!n;$('#saveN').textContent=`${n} 处改动`;};
  $('#matrix').onclick=e=>{const c=e.target.closest('.cb2');if(!c)return;const on=!c.classList.contains('on');c.classList.toggle('on',on);c.innerHTML=on?ic('check'):'';c.setAttribute('aria-checked',on);const td=c.parentNode;td.classList.toggle('chg',String(+on)!==td.dataset.o);upd();};
  $('#saveDrop').onclick=()=>{$$('#matrix td.chg').forEach(td=>{const c=$('.cb2',td),on=td.dataset.o==='1';c.classList.toggle('on',on);c.innerHTML=on?ic('check'):'';td.classList.remove('chg');});upd();};
  const Q=[['合同',[['付款条件须与验收结果挂钩','采购方','公司要求',1],['违约金上限不低于合同总价 10%','采购方','公司范本',1],['争议解决优先选我方所在地','通用','公司要求',1],['预付款须有银行保函','采购方','公司要求',0]]],['报价单',[['含税与不含税须注明','通用','公司要求',1],['有效期不少于 30 天','采购方','公司要求',1]]],['函件',[['回复期限须明确','通用','公司要求',1]]]];
  $('#reqcols').innerHTML=Q.map(([t,cs])=>`<div class="reqcol"><h5>${ic('file-text')}${t}<span class="badge b-neu">${cs.length}</span></h5>${cs.map(([n,p,s,on])=>`<div class="reqc${on?'':' off'}"><span class="gr">${ic('grip-vertical')}</span><div class="rq-b"><b>${n}</b><div class="rq-t"><span class="badge b-brand">${p}</span><span class="badge b-line">${s}</span><button class="ibtn sm ml-auto" title="上移">${ic('chevron-up')}</button><button class="ibtn sm" title="下移">${ic('chevron-down')}</button></div></div><span class="sw2${on?' on':''}"></span></div>`).join('')}</div>`).join('');
  const P=[['设备采购合同（2026 版）','条款 42'],['技术服务合同','条款 28'],['保密协议','条款 16'],['物业服务合同','条款 35'],['框架采购协议','解析中',1]];
  $('#thumbs').innerHTML=P.map(([n,c,busy])=>`<div class="thm"><div class="pg"><i class="h"></i>${'<i></i>'.repeat(8)}<span class="cn">${c}</span></div><b>${n}</b><span class="f-aux">${busy?'<span class="badge b-lo">解析中</span>':'<span class="badge b-ok">可用</span>'}</span></div>`).join('');
  $('#thumbs2').innerHTML=[['验收条款 · 设备类','片段 6','陈主管 11-20'],['违约金上限写法','片段 3','陈主管 11-12'],['质保金比例','片段 2','王经理 11-05']].map(([n,c,w])=>`<div class="thm"><div class="pg"><i class="h"></i>${'<i></i>'.repeat(6)}<span class="cn">${c}</span></div><b>${n}</b><span class="f-aux">${w}<button class="btn sm txt ml-auto" style="height:22px;padding:0 6px">撤下</button></span></div>`).join('')+'<div class="thm"><div class="pg fail"><i class="h"></i>'+'<i></i>'.repeat(6)+'</div><b>付款节点说明.docx</b><span class="f-aux"><span class="badge b-hi">解析失败</span><button class="btn sm txt" style="height:22px;padding:0 6px">重新上传</button></span></div>';
  const K=[['org','单位',['青禾','远拓','华东区']],['person','人名',['宋明远']],['contact','联系方式',[]],['id','证件',['HX-2026-']],['addr','地址',['科创大道']],['amount','金额',[]],['other','其他',['6222 ****']]];
  $('#kwall').innerHTML=K.map(([k,n,w])=>`<div class="kcard k-${k}"><div class="kh"><i class="kdot"></i><b>${n}</b><span class="num">${w.length} 个自定义词</span></div><div class="kw">${w.map(x=>`<span>${x}</span>`).join('')}<span class="add">+ 添加</span></div></div>`).join('');
  const ret=[3,0,5,8,2,0,0,6,4,9,3,7,0,0,12,5,4,6,2,0,0,8,6,3,5,4,0,0,2,3];
  $('#retl').innerHTML=ret.map((v,i)=>`<i class="${v?'':'z'}${(i+4)%7>=5?' wk':''}" style="height:${v/12*60}px" title="${v} 件"></i>`).join('');
}

/* ================= 切屏 ================= */
const SCREENS=[
  ['kit','〇 衡线组件与对比度'],['login','6.3 登录'],['home','6.1 / 6.4 工作台'],['search','6.2 全局搜索'],['chat','6.5 对话页'],
  ['review','6.6 文件审核 · 入口'],['confirm','6.7 脱敏确认 · 揭幕滑块'],['confirm-b','6.7 发送前复检拦截'],['result','6.8 审核结果 · 地形条与还原'],
  ['compare','6.9 合同对比 · 两张纸'],['compare-r','6.9 合同对比 · 缝线'],
  ['bid','6.10 标书 · 开标时间尺'],['bidp','6.11 标书 · 开标桌'],
  ['meet','6.12 会议 · 声纹带'],['meet-rec','6.12 录音中'],['meetp','6.13 会议页 · 发言泳道'],
  ['trans','6.14 翻译 · 开关'],['transp','6.15 翻译页 · 刻度'],
  ['states','6.16 / 6.17 空状态与提示'],['nf','6.16 404'],['history','6.18 历史记录'],
  ['set-me','6.19 设置 · 个人'],['set-model','6.20 设置 · 模型（样板）'],['set-acct','6.21 设置 · 账号'],['set-role','6.22 设置 · 角色'],['set-req','6.23 设置 · 审核要求'],
  ['set-law','6.24 设置 · 法规'],['set-tpl','6.25 设置 · 范本与知识库'],['set-cat','6.26 设置 · 标书分类'],['set-redact','6.27 设置 · 脱敏'],['set-data','6.28 设置 · 数据'],
];
const ALIAS={'confirm-b':'confirm'};
const NAV={home:'home',search:'search',review:'review',confirm:'review','confirm-b':'review',result:'review',compare:'compare','compare-r':'compare',bid:'bid',bidp:'bid',meet:'meet','meet-rec':'meet',meetp:'meet',trans:'trans',transp:'trans',history:'history'};
function show(id){
  if(!SCREENS.some(s=>s[0]===id)) id='kit';
  const sid=ALIAS[id]||id;
  $$('.screen').forEach(s=>s.classList.toggle('on',s.id==='s-'+sid));
  $('#app').style.display=id==='login'?'none':'';
  $$('.nav').forEach(n=>n.classList.toggle('on',n.dataset.nav===NAV[id]));
  $('#dzScreen').value=id;
  const scr=$('#s-'+sid); $('#main').scrollTop=0;
  $$('.enter',scr).forEach(e=>{e.classList.remove('enter');void e.offsetWidth;e.classList.add('enter');});
  $$('.greet-mark .mark,.lb-mark .mark',scr).forEach(m=>{m.classList.remove('play');void m.getBoundingClientRect();m.classList.add('play');});
  countUp(scr);
  if(id==='login') loginTrip();
  if(sid==='confirm') confirmState(id==='confirm-b');
  if(id==='result') requestAnimationFrame(()=>{terrainInit();playRestore(false);});
  if(id==='compare-r') requestAnimationFrame(drawRib);
  recTick(id==='meet-rec');
  if(id==='set-model'&&!full()) $('#xm').classList.add('go');
  writeHash(id);
}
function countUp(scr){
  if(!full()) return;
  $$('[data-count]',scr).forEach(e=>{const to=+e.dataset.count,t0=performance.now();const step=t=>{const k=Math.min(1,(t-t0)/900);e.textContent=Math.round(to*(1-Math.pow(1-k,3))).toLocaleString('en-US');if(k<1)requestAnimationFrame(step);};requestAnimationFrame(step);});
}
function setTheme(t){
  H.dataset.shell=t; $('#dzTheme').textContent=t==='dark'?'浅色':'深色';
  $$('[data-act="theme"]').forEach(b=>{b.innerHTML='';b.dataset.i=t==='dark'?'sun':'moon';delete b.dataset.done;paintIcons(b.parentNode);});
}
function setFx(f){H.dataset.fx=f;$('#dzFx').value=f;}
function readHash(){
  const p=location.hash.slice(1).split('&').filter(Boolean);
  setTheme(p.includes('dark')?'dark':'light');
  setFx(p.includes('lite')?'lite':p.includes('off')?'off':'full');
  H.classList.toggle('clean',p.includes('clean')); H.classList.toggle('shot',p.includes('shot'));
  return p.find(x=>SCREENS.some(s=>s[0]===x))||'kit';
}
function writeHash(id){const p=[id];if(H.dataset.shell==='dark')p.push('dark');if(H.dataset.fx!=='full')p.push(H.dataset.fx);if(H.classList.contains('clean'))p.push('clean');if(H.classList.contains('shot'))p.push('shot');history.replaceState(null,'','#'+p.join('&'));}

/* ---------- 启动 ---------- */
paintMarks(); paintIllus(); paintRecent();
kitInit(); revealInit(); compareInit(); bidInit(); meetInit(); lanesInit(); transInit(); meInit(); setNav(); modelInit(); setOthers(); pileInit();
tl($('#tlReview'),['review'],5); tl($('#tlMeet'),['meet'],5); tl($('#tlHist'));
paintCharts(); paintTrips(); paintIcons();
const sel=$('#dzScreen'); sel.innerHTML=SCREENS.map(([v,t])=>`<option value="${v}">${t}</option>`).join('');
sel.onchange=()=>show(sel.value);
$('#dzTheme').onclick=()=>{setTheme(H.dataset.shell==='dark'?'light':'dark');writeHash(sel.value);if(sel.value==='compare-r')drawRib();};
$('#dzFx').onchange=e=>{setFx(e.target.value);writeHash(sel.value);};
$('#dzReplay').onclick=()=>{if(sel.value==='result'){playRestore(true);}show(sel.value);};
$('#replayRestore').onclick=()=>playRestore(true);
document.addEventListener('click',e=>{
  const th=e.target.closest('[data-act="theme"]'); if(th){e.stopPropagation();setTheme(H.dataset.shell==='dark'?'light':'dark');writeHash(sel.value);return;}
  const rc=e.target.closest('[data-act="receipt"] .rh'); if(rc){rc.parentNode.classList.toggle('open');return;}
  const ta=e.target.closest('[data-act="trip-act"]'); if(ta){const s=$('#confirmStage');if(s&&s.offsetParent){$('#confirmFoot').scrollIntoView({behavior:'smooth'});const b=$('#confirmFoot .btn');b.classList.remove('flash2');void b.offsetWidth;b.classList.add('flash2');}return;}
  const pr=e.target.closest('[data-act="probe"]'); if(pr){probe(pr.closest('.rowc'));return;}
  const ex=e.target.closest('[data-act="expand"]'); if(ex){const r=ex.closest('.rowc');r.classList.toggle('open');ex.innerHTML=ic(r.classList.contains('open')?'chevron-up':'chevron-down');return;}
  const st=e.target.closest('[data-act="seat"]'); if(st){const m=st.nextElementSibling;m.hidden=!m.hidden;return;}
  const mi=e.target.closest('.seat .mi'); if(mi&&mi.style.opacity!=='0.5'){const seat=mi.closest('.seat');$('b',seat).textContent=mi.textContent.replace('可用','').trim();mi.parentNode.hidden=true;const n=$(`.node[data-n="${mi.parentNode.children.length===3?0:1}"]`)||$('.node');const idx=[...mi.parentNode.children].indexOf(mi);const node=$(`.node[data-n="${idx}"]`);if(node){node.classList.remove('flash');void node.getBBox();node.classList.add('flash');}return;}
  const en=e.target.closest('[data-act="enable"]'); if(en){const s3=$('#steps3'),n=en.dataset.n;const step=(a)=>{s3.innerHTML=['接通','结构化输出','启用'].map((t,i)=>`<span class="${i<a?'ok':i===a?'run':''}">${ic(i<a?'circle-check':i===a?'loader-circle':'circle')}${t}</span>`).join('');};step(0);setTimeout(()=>step(1),800);setTimeout(()=>step(2),1600);setTimeout(()=>{step(3);const d=document.createElement('div');d.className='rowc mrow new-row';d.innerHTML=`<div class="rc-m"><span class="mod-ic m-chat"><span data-i="sparkle"></span></span><div class="rc-n"><b>${n}</b><span>按推荐值接入 · 密钥 …e41d</span></div><span class="badge b-ok">可用</span></div>`;$('#mrows').append(d);paintIcons(d);},2300);return;}
  const cp=e.target.closest('[data-act="copy"]'); if(cp){const t=$('#toast');t.hidden=false;clearTimeout(t._h);t._h=setTimeout(()=>t.hidden=true,1800);return;}
  const go=e.target.closest('[data-nav],[data-go]'); if(go&&!go.disabled){show(go.dataset.nav||go.dataset.go);return;}
});
document.addEventListener('keydown',e=>{if(e.key==='Enter'&&e.target.matches('.bdoc,.tile'))e.target.click();});
window.addEventListener('hashchange',()=>show(readHash()));
show(readHash());
})();

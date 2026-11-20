/* 商衡SI · 澄明设计稿脚本：图标、标志、插画、切屏、主题与动效档。只用于设计稿。 */
(function(){
const $=(s,r=document)=>r.querySelector(s), $$=(s,r=document)=>[...r.querySelectorAll(s)];

/* ---------- 图标（lucide） ---------- */
function ic(name){
  const n=ICONS[name]; if(!n) return '';
  return '<svg class="i" viewBox="0 0 24 24">'+n.map(([t,a])=>'<'+t+' '+Object.entries(a).map(([k,v])=>k+'="'+v+'"').join(' ')+'/>').join('')+'</svg>';
}
function paintIcons(r=document){
  $$('[data-i]',r).forEach(e=>{
    if(e.dataset.done) return;
    const name=ICONS[e.dataset.i]?e.dataset.i:e.dataset.alt;
    e.insertAdjacentHTML('afterbegin',ic(name)); e.dataset.done=1;
  });
}

/* ---------- 标志「衡线」 ---------- */
function mark(size,detail,inverse){
  const t=inverse?'#33C0A6':'var(--si-logo)', s=inverse?'#8B7CFF':'var(--si-logo-star)', f=inverse?'#1F5E55':'var(--si-logo-faint)';
  return `<svg class="mark" viewBox="0 0 24 24" width="${size}" height="${size}" fill="none" aria-label="商衡SI">
  <path class="paper-s" d="M12.5 2.5H8.5a2 2 0 0 0-2 2v15a2 2 0 0 0 2 2h7a2 2 0 0 0 2-2V10" stroke="${t}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
  ${detail?`<path class="paper-s" d="M9.5 6.5h2M9.5 17.5h5" stroke="${f}" stroke-width="2" stroke-linecap="round"/>`:''}
  <rect class="bar" x="3" y="10" width="18" height="4" rx="2" fill="${t}"/>
  <path class="star" d="M17.5 1.5c.3 1.9.9 2.6 3 3-2.1.4-2.7 1.1-3 3-.3-1.9-.9-2.6-3-3 2.1-.4 2.7-1.1 3-3z" fill="${s}" stroke="${s}" stroke-width=".8" stroke-linejoin="round"/></svg>`;
}
function paintMarks(){
  $$('[data-mark]').forEach(e=>{
    e.innerHTML=mark(+e.dataset.mark,!!e.dataset.detail,!!e.dataset.inverse);
    if(e.dataset.tilt) e.querySelector('.bar').style.transform='rotate(-6deg)';
  });
}
function playMarks(scope){
  $$('.greet-mark .mark,.lb-mark .mark',scope).forEach(m=>{m.classList.remove('play');void m.getBoundingClientRect();m.classList.add('play');});
}

/* ---------- 模块插画（线条，模块色，悬停才动） ---------- */
const ILLUS={
  review:`<svg viewBox="0 0 220 104" fill="none">
    <rect x="66" y="12" width="88" height="84" rx="8" class="il-paper"/>
    <path d="M80 30h44M80 42h60M80 54h52M80 66h60M80 78h36" class="il-line"/>
    <rect x="76" y="48" width="68" height="12" rx="3" class="il-hl sweep"/>
    <circle cx="160" cy="22" r="10" class="il-soft"/><path d="m155 22 3.5 3.5L166 18" class="il-stroke"/></svg>`,
  bid:`<svg viewBox="0 0 220 104" fill="none">
    <g class="bcards">
    <rect x="40" y="18" width="62" height="32" rx="6" class="il-paper"/><path d="M50 30h22M50 40h36" class="il-line"/>
    <rect x="118" y="18" width="62" height="32" rx="6" class="il-paper"/><path d="M128 30h22M128 40h36" class="il-line"/>
    <rect x="40" y="58" width="62" height="32" rx="6" class="il-paper"/><path d="M50 70h22M50 80h36" class="il-line"/>
    <rect x="118" y="58" width="62" height="32" rx="6" class="il-paper red"/><path d="M128 70h22M128 80h36" class="il-line red"/>
    </g><circle cx="178" cy="60" r="7" class="il-alert"/><path d="M178 56.5v4M178 63h.01" class="il-alert-s"/></svg>`,
  meet:`<svg viewBox="0 0 220 104" fill="none"><g class="bars">${
    [14,26,40,22,52,34,62,30,46,20,36,16].map((h,i)=>`<rect x="${44+i*12}" y="${52-h/2}" width="6" height="${h}" rx="3" class="il-bar" style="--d:${(i%5)*.12}s"/>`).join('')}</g></svg>`,
  trans:`<svg viewBox="0 0 220 104" fill="none">
    <rect x="34" y="14" width="66" height="78" rx="8" class="il-paper"/><rect x="120" y="14" width="66" height="78" rx="8" class="il-paper"/>
    <path d="M46 32h40M46 48h30M46 64h42M46 78h24" class="il-line"/>
    <path d="M132 32h36M132 48h42M132 64h28M132 78h38" class="il-line shift"/>
    <path d="M100 48h20M100 64h20" class="il-link"/></svg>`,
};
function paintIllus(){ $$('[data-illus]').forEach(e=>e.innerHTML=ILLUS[e.dataset.illus]); }

/* ---------- 声波 ---------- */
function paintWaves(){
  const w=$('#wave'); if(w){w.innerHTML=Array.from({length:56},(_,i)=>`<i style="--h:${18+Math.round(Math.abs(Math.sin(i*1.7)*46+Math.cos(i*.6)*18))}px;--d:${(i%9)*.09}s"></i>`).join('');}
  const p=$('#pwave'); if(p){p.innerHTML=Array.from({length:120},(_,i)=>`<i class="${i<28?'past':''}" style="height:${4+Math.round(Math.abs(Math.sin(i*.9)*12+Math.sin(i*.23)*8))}px"></i>`).join('');}
}

/* ---------- 切屏 ---------- */
const SCREENS=[
  ['login','8.1 登录'],['home','8.2 工作台'],['home-busy','8.2 工作台 · 文件处理中'],['search','8.0 全局搜索'],['chat','8.3 对话页'],
  ['review','8.4① 文件审核 · 入口'],['review-confirm','8.4② 脱敏确认'],['review-blocked','8.4② 发送前复检拦截'],['review-run','8.4③ 审核中'],['review-result','8.4③ 审核结果'],
  ['bid','8.5① 标书初审 · 列表'],['bid-check','8.5② 项目页 · 检查中'],['bid-result','8.5② 项目页 · 结果'],
  ['meet','8.6① 会议纪要 · 入口'],['meet-rec','8.6② 录音中'],['meet-page','8.6③ 会议页'],
  ['trans','8.7① 翻译 · 入口'],['trans-page','8.7② 翻译页'],
  ['history','8.8 历史记录'],['settings','8.9 设置 · 个人'],['settings-model','8.9 设置 · 模型'],
  ['states','8.10 通用状态'],['nf','8.10 404'],['export','8.11 导出文件'],
];
const NAV={home:'home','home-busy':'home',search:'search',review:'review','review-confirm':'review','review-blocked':'review','review-run':'review','review-result':'review',
  bid:'bid','bid-check':'bid','bid-result':'bid',meet:'meet','meet-rec':'meet','meet-page':'meet',trans:'trans','trans-page':'trans',history:'history'};

function show(id){
  if(!SCREENS.some(s=>s[0]===id)) id='home';
  $$('.screen').forEach(s=>s.classList.toggle('on',s.id==='s-'+id));
  $('#app').style.display=id==='login'?'none':'';
  $$('.nav').forEach(n=>n.classList.toggle('on',n.dataset.nav===NAV[id]));
  $$('.rc').forEach(n=>n.classList.toggle('on',id==='chat'&&n.dataset.nav==='chat'));
  $('#dzScreen').value=id;
  const scr=$('#s-'+id); $('#main').scrollTop=0;
  // 重放进入动效
  $$('.enter',scr).forEach(e=>{e.classList.remove('enter');void e.offsetWidth;e.classList.add('enter');});
  playMarks(scr);
  countUp(scr);
  placeFloats(scr);
  writeHash(id);
}
/* 胶囊菜单贴在被点的胶囊下方，选区工具条浮在选区上方 */
function placeFloats(scr){
  const paper=$('#redactPaper',scr); if(!paper) return;
  const pr=paper.getBoundingClientRect();
  const a=$('#pillMenuAnchor',paper), m=$('.pill-menu',paper);
  if(a&&m){const r=a.getBoundingClientRect();m.style.left=(r.left-pr.left)+'px';m.style.top=(r.bottom-pr.top+6)+'px';}
  const s=$('.selhl',paper), b=$('.sel-bar',paper);
  if(s&&b){const r=s.getClientRects()[0];b.style.left=(r.left-pr.left)+'px';b.style.top=(r.top-pr.top-b.offsetHeight-8)+'px';}
}
function countUp(scr){
  if(document.documentElement.dataset.fx!=='full'||document.documentElement.classList.contains('shot')) return;
  $$('[data-count]',scr).forEach(e=>{
    const to=+e.dataset.count, t0=performance.now();
    const step=t=>{const k=Math.min(1,(t-t0)/900), v=Math.round(to*(1-Math.pow(1-k,3)));e.textContent=v.toLocaleString('en-US');if(k<1)requestAnimationFrame(step);};
    requestAnimationFrame(step);
  });
}

/* ---------- 主题与动效档 ---------- */
function setTheme(t){
  document.documentElement.dataset.shell=t;
  $$('.themes .th').forEach((b,i)=>{const on=i===(t==='dark'?1:0);b.classList.toggle('on',on);const r=b.querySelector('.radio');if(r)r.classList.toggle('on',on);});
  $('#dzTheme').textContent=t==='dark'?'浅色':'深色';
  $$('[data-act="theme"]').forEach(b=>{b.innerHTML='';b.dataset.i=t==='dark'?'sun':'moon';delete b.dataset.done;paintIcons(b.parentNode);});
}
function setFx(f){ document.documentElement.dataset.fx=f; $('#dzFx').value=f; }

/* 地址参数：#屏幕&dark&lite&clean */
function readHash(){
  const p=location.hash.slice(1).split('&').filter(Boolean);
  setTheme(p.includes('dark')?'dark':'light');
  setFx(p.includes('lite')?'lite':p.includes('off')?'off':'full');
  document.documentElement.classList.toggle('clean',p.includes('clean'));
  document.documentElement.classList.toggle('shot',p.includes('shot'));
  return p.find(x=>SCREENS.some(s=>s[0]===x))||'home';
}
let hashLock=false;
function writeHash(id){
  const h=document.documentElement, parts=[id];
  if(h.dataset.shell==='dark')parts.push('dark');
  if(h.dataset.fx!=='full')parts.push(h.dataset.fx);
  if(h.classList.contains('clean'))parts.push('clean');
  hashLock=true; history.replaceState(null,'','#'+parts.join('&')); hashLock=false;
}

/* ---------- 启动 ---------- */
paintMarks(); paintIllus(); paintWaves(); paintIcons();
const sel=$('#dzScreen'); sel.innerHTML=SCREENS.map(([v,t])=>`<option value="${v}">${t}</option>`).join('');
sel.onchange=()=>show(sel.value);
$('#dzTheme').onclick=()=>{setTheme(document.documentElement.dataset.shell==='dark'?'light':'dark');writeHash(sel.value);};
$('#dzFx').onchange=e=>{setFx(e.target.value);writeHash(sel.value);};
$('#dzReplay').onclick=()=>show(sel.value);
document.addEventListener('click',e=>{
  const th=e.target.closest('[data-act="theme"]'); if(th){e.stopPropagation();setTheme(document.documentElement.dataset.shell==='dark'?'light':'dark');writeHash(sel.value);return;}
  const go=e.target.closest('[data-nav],[data-go]'); if(go){show(go.dataset.nav||go.dataset.go);return;}
  const cp=e.target.closest('[data-act="copy"]'); if(cp){const t=$('#toast');t.hidden=false;clearTimeout(t._h);t._h=setTimeout(()=>t.hidden=true,1800);return;}
  const sb=e.target.closest('.seg button'); if(sb){$$('button',sb.parentNode).forEach(b=>b.classList.toggle('on',b===sb));
    if(sb.parentNode.dataset.seg==='redact'){const code=sb.dataset.v==='code';$$('#redactPaper .pill[data-code]').forEach(p=>{p.dataset.raw=p.dataset.raw||p.textContent;p.textContent=code?p.dataset.code:p.dataset.raw;p.classList.toggle('code',code);});}}
});
window.addEventListener('hashchange',()=>{if(!hashLock)show(readHash());});
document.addEventListener('keydown',e=>{if(e.ctrlKey&&e.key.toLowerCase()==='k'){e.preventDefault();show('search');}});
show(readHash());
document.fonts.ready.then(()=>placeFloats(document));
})();

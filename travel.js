(() => {
  'use strict';
  const core=window.StratfordTravel;
  const panel=document.getElementById('currentTravel');
  const plannedStatus=document.getElementById('plannedTravelStatus');
  if (!core || !panel) return;
  const FIVE_MINUTES=5*60*1000, SIX_HOURS=6*60*60*1000;
  let current=null, planned=null, currentBusy=false, lastCurrent=0;
  const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const stamp=value=>value?new Intl.DateTimeFormat('en-GB',{day:'numeric',month:'short',hour:'2-digit',minute:'2-digit',timeZone:'Europe/London',timeZoneName:'short'}).format(new Date(value)):'not yet available';
  function noticeMarkup(notice,outdated=false,isPlanned=false) {
    return `<details class="travel-notice"><summary>⚠ ${isPlanned?'Planned travel: ':''}${esc(notice.title)}${outdated?' · update overdue':''}</summary><p>${esc(notice.detail)}</p><p class="travel-scope">${esc(notice.scope)}</p>${notice.start?`<p class="travel-scope">${esc(stamp(notice.start))} – ${esc(stamp(notice.end))}</p>`:''}</details>`;
  }
  function renderPlanned() {
    const groups=planned?.groups || [];
    const missing=!groups.length || groups.some(g=>core.stale(g,7*60*60*1000));
    const oldest=groups.map(g=>g.checkedAt).filter(Boolean).sort()[0];
    plannedStatus.textContent=missing
      ? `Planned travel notices: updates incomplete or overdue. Last available check: ${stamp(oldest)}. Check TfL before travelling.`
      : `Planned travel notices checked ${stamp(oldest)}. Dated notices appear in the affected tiles. No notice does not guarantee normal service.`;
    document.querySelectorAll('[data-travel-date]').forEach(slot=>{
      const seen=new Set();
      slot.innerHTML=groups.flatMap(group=>group.notices.filter(n=>core.onDate(n,slot.dataset.travelDate)).map(notice=>{
        const id=JSON.stringify(notice);if(seen.has(id))return '';seen.add(id);
        return noticeMarkup(notice,core.stale(group,7*60*60*1000),true);
      })).join('');
      slot.hidden=!slot.innerHTML;
    });
  }
  function renderCurrent() {
    if (!current) { panel.textContent='Checking current TfL travel information…'; return; }
    const groups=current.groups;
    const failed=groups.filter(g=>core.stale(g,FIVE_MINUTES*2));
    const notices=core.unique(groups.flatMap(g=>g.notices.filter(n=>core.active(n))));
    const oldest=groups.map(g=>g.checkedAt).filter(Boolean).sort()[0];
    panel.innerHTML=`<p class="travel-check">Last available check: ${esc(stamp(oldest))} · refreshes every 5 minutes while this page is open</p>`+
      (failed.length?`<p class="travel-unavailable">Some travel updates are unavailable or overdue. Earlier notices may be shown; this is not confirmation of normal service.</p><details class="travel-coverage"><summary>Unavailable checks (${failed.length})</summary><p>${esc(failed.map(g=>g.label).join('; '))}</p></details>`:'')+
      (notices.length?notices.map(n=>noticeMarkup(n,failed.length>0)).join(''):failed.length?'':'<p>No current disruptions reported by the checked TfL feeds.</p>')+
      `<details class="travel-coverage"><summary>Services checked for Westfield Stratford City</summary><p>Stratford Underground, Elizabeth line, Mildmay and DLR; Stratford International DLR; Stratford City and Stratford bus stations; Stratford International bus stops.</p><p>Bus routes: ${esc(core.BUSES.join(', ').toUpperCase())}. Route-wide notices can describe problems away from Westfield.</p><p>National Rail and Southeastern services, roads, parking and Westfield access are not covered.</p></details>`;
  }
  async function refreshCurrent() {
    if(currentBusy)return;
    currentBusy=true;
    try { current=await core.collect({previous:current});lastCurrent=Date.now();renderCurrent(); }
    finally {currentBusy=false;}
  }
  async function refreshPlanned() {
    try {
      const response=await fetch(`travel-data.json?t=${Date.now()}`,{signal:AbortSignal.timeout(15000)});
      if(!response.ok)throw Error('Unavailable');
      const next=await response.json();
      if(!Array.isArray(next.groups)||!next.groups.length||next.groups.some(g=>!Array.isArray(g.notices)))throw Error('Invalid data');
      planned=next;
    } catch {
      if(planned)planned={...planned,groups:planned.groups.map(g=>({...g,status:'unavailable'}))};
    }
    renderPlanned();
  }
  document.addEventListener('calendar-rendered',renderPlanned);
  document.addEventListener('visibilitychange',()=>{if(!document.hidden && Date.now()-lastCurrent>=FIVE_MINUTES)refreshCurrent();});
  renderCurrent();renderPlanned();refreshCurrent();refreshPlanned();
  setInterval(()=>{if(!document.hidden)refreshCurrent();},FIVE_MINUTES);
  setInterval(refreshPlanned,SIX_HOURS);
})();

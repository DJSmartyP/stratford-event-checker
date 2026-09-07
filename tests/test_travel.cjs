const {test}=require('node:test');
const assert=require('node:assert/strict');
const t=require('../travel-core.js');
const rails=()=>t.RAIL.map(id=>({id,name:id,lineStatuses:[{statusSeverity:10}]}));
test('planned rail status must be explicitly planned and dated',()=>{
  const data=rails();
  data[0].lineStatuses=[{statusSeverity:6,statusSeverityDescription:'Severe delays',reason:'Incident',disruption:{category:'RealTime'},validityPeriods:[{fromDate:'2026-12-04T10:00:00Z',toDate:'2026-12-05T00:00:00Z'}]}];
  assert.equal(t.railNotices(data,true).length,0);
  data[0].lineStatuses[0].disruption.category='PlannedWork';
  const [notice]=t.railNotices(data,true);
  assert.ok(t.onDate(notice,'2026-12-04'));
  assert.ok(!t.onDate(notice,'2026-12-05'));
});
test('London midnight and BST are respected',()=>{
  const n={start:'2026-08-31T23:00:00Z',end:'2026-09-01T23:00:00Z'};
  assert.ok(t.onDate(n,'2026-09-01'));
  assert.ok(!t.onDate(n,'2026-08-31'));
  assert.ok(!t.onDate(n,'2026-09-02'));
});
test('undated bus messages stay out of future date tiles',()=>{
  const payload=[{category:'PlannedWork',description:'Roadworks'}];
  assert.equal(t.busNotices(payload,true).length,0);
  assert.equal(t.busNotices(payload,false).length,1);
});
test('station notices cover all children and only explicit dates',()=>{
  const payload={disruptions:[],children:[{disruptions:[{commonName:'Stratford City',description:'Stop closed',fromDate:'2026-12-12T08:00:00Z',toDate:'2026-12-12T18:00:00Z'}],children:[]}]};
  const [n]=t.stopNotices(payload,'Stratford',true);
  assert.ok(t.onDate(n,'2026-12-12'));
  assert.ok(!t.active(n,Date.parse('2026-12-11T12:00:00Z')));
});
test('future notices do not appear as current conditions',()=>{
  assert.ok(!t.active({start:'2026-12-01T00:00:00Z',end:'2026-12-02T00:00:00Z'},Date.parse('2026-09-01')));
});
test('partial API failures retain previous notices and timestamp',async()=>{
  const old={groups:[{id:'rail',checkedAt:'2026-09-01T12:00:00Z',notices:[{title:'Retained'}]}]};
  const result=await t.collect({previous:old,fetcher:async url=>{
    if(url.includes('/Line/central'))throw Error('network error');
    return {ok:true,json:async()=>url.includes('/StopPoint/')?{disruptions:[],children:[]}:[]};
  }});
  const rail=result.groups.find(g=>g.id==='rail');
  assert.deepEqual(rail.notices,old.groups[0].notices);
  assert.equal(rail.checkedAt,old.groups[0].checkedAt);
  assert.equal(rail.status,'unavailable');
  assert.equal(result.groups.find(g=>g.id==='stratford').status,'success');
});
test('schema errors and missing line results cannot produce all-clear',()=>{
  assert.throws(()=>t.railNotices([],false));
  assert.throws(()=>t.stopNotices({message:'error'},'Stratford',false));
  assert.throws(()=>t.busNotices({message:'error'},false));
});
test('stale and invalid timestamps are flagged',()=>{
  assert.ok(t.stale({status:'success',checkedAt:'invalid'},1000));
  assert.ok(t.stale({status:'unavailable',checkedAt:new Date().toISOString()},1000));
});

test('live rail covers the whole network while planned checks remain local', async()=>{
  const full=t.LIVE_RAIL.map(id=>({id,name:id,lineStatuses:[{statusSeverity:10}]}));
  full.find(l=>l.id==='lioness').lineStatuses=[{statusSeverity:6,reason:'Delays'}];
  assert.equal(t.railNotices(full,false)[0].service,'lioness');
  assert.throws(()=>t.railNotices(rails(),false));
  assert.deepEqual(t.railNotices(rails(),true),[]);
  const urls=[];
  await t.collect({fetcher:async url=>{urls.push(url);return {ok:true,json:async()=>url.includes('/Mode/')?full:url.includes('/StopPoint/')?{disruptions:[],children:[]}:[]};}});
  assert.ok(urls.some(url=>url.includes('/Line/Mode/tube,overground,elizabeth-line,dlr/Status')));
  assert.equal(urls.filter(url=>url.includes('/Disruption') && url.includes('/Line/')).length,2);
});

test('bus titles identify local routes without mistaking dates or postcodes for routes',()=>{
  const notices=t.busNotices([
    {description:'ROYAL CREST AVENUE, E16: ROUTE 241 is terminating early.'},
    {description:'Until 23:00 Thursday 31 December 2026, routes 276 and N25 are diverted.'},
    {description:'Road blocked',affectedRoutes:[{lineId:'108'}]},
    {description:'Roadworks until 25 December at E13'}
  ],false);
  assert.match(notices[0].title,/Bus 241/);
  assert.match(notices[1].title,/Bus 276, N25/);
  assert.match(notices[2].title,/Bus 108/);
  assert.equal(notices[3].title,'Local bus disruption');
});

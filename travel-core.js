/* Shared TfL reader for GitHub Actions and the browser. No credentials required. */
((root) => {
  'use strict';
  const API = 'https://api.tfl.gov.uk';
  const RAIL = ['central', 'jubilee', 'dlr', 'elizabeth', 'mildmay'];
  const LIVE_RAIL = ['bakerloo','central','circle','district','dlr','elizabeth','hammersmith-city','jubilee','liberty','lioness','metropolitan','mildmay','northern','piccadilly','suffragette','victoria','waterloo-city','weaver','windrush'];
  // TfL HUBSRA family: Stratford and Stratford City bus stations.
  const BUSES = ['104','108','158','238','241','25','257','262','276','308','339','388','425','473','678','69','86','97','d8','n205','n25','n8','n86'];
  const londonDate = value => new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Europe/London', year: 'numeric', month: '2-digit', day: '2-digit'
  }).format(new Date(value));
  function instant(value) {
    if (typeof value !== 'string' || !/(Z|[+-]\d{2}:\d{2})$/i.test(value)) return null;
    return Number.isFinite(Date.parse(value)) ? new Date(value).toISOString() : null;
  }
  function period(start, end) {
    start = instant(start); end = instant(end);
    return start && end && start < end ? { start, end } : { start: null, end: null };
  }
  function onDate(notice, date) {
    return !!notice.start && !!notice.end && londonDate(notice.start) <= date &&
      londonDate(Date.parse(notice.end) - 1) >= date;
  }
  function active(notice, now = Date.now()) {
    return (!notice.start || Date.parse(notice.start) <= now) && (!notice.end || Date.parse(notice.end) > now);
  }
  function unique(notices) {
    return [...new Map(notices.map(n => [JSON.stringify(n), n])).values()];
  }
  function railNotices(data, planned) {
    if (!Array.isArray(data) || (planned ? RAIL : LIVE_RAIL).some(id => !data.some(line => line.id === id))) throw Error('Incomplete rail response');
    const notices = [];
    for (const line of data) {
      if (!Array.isArray(line.lineStatuses) || !line.lineStatuses.length) throw Error('Missing line status');
      for (const status of line.lineStatuses) {
        if (typeof status.statusSeverity !== 'number') throw Error('Invalid line status');
        if (status.statusSeverity === 10) continue;
        const disruption = status.disruption || {};
        // Date-range results can include realtime incidents. They are never
        // promoted into future engineering notices just because dates match.
        if (planned && disruption.category !== 'PlannedWork') continue;
        const periods = status.validityPeriods?.length ? status.validityPeriods : [{}];
        for (const p of periods) {
          const dates = period(p.fromDate, p.toDate);
          if (planned && !dates.start) continue;
          notices.push({ service: line.id, title: `${line.name} · ${status.statusSeverityDescription || 'Service notice'}`,
            detail: status.reason || disruption.description || 'See TfL for details.',
            scope: planned ? 'Line-wide notice; may affect journeys to Stratford' : 'Network-wide rail status; disruption may be away from Stratford', ...dates });
        }
      }
    }
    return notices;
  }
  function busNotices(data, planned) {
    if (!Array.isArray(data)) throw Error('Invalid bus response');
    return data.flatMap(d => {
      if (typeof d.description !== 'string') throw Error('Invalid bus notice');
      const dates = period(d.fromDate, d.toDate);
      if (planned && (d.category !== 'PlannedWork' || !dates.start)) return [];
      // The bus API often supplies no validity dates. Such notices are current
      // only and are never assigned to a future performance tile.
      return [{ service: 'bus', title: 'Bus route disruption', detail: d.description,
        scope: 'Route serving Stratford; disruption may be elsewhere on the route', ...dates }];
    });
  }
  function stopNotices(data, label, planned) {
    const notices = [];
    function walk(node) {
      if (Array.isArray(node)) { node.forEach(walk); return; }
      if (!node || !Array.isArray(node.disruptions) || !Array.isArray(node.children)) throw Error('Invalid station family');
      for (const d of node.disruptions) {
        if (typeof d.description !== 'string') throw Error('Invalid station notice');
        const dates = period(d.fromDate, d.toDate);
        if (planned && !dates.start) continue;
        notices.push({ title: d.commonName || label, detail: d.description,
          scope: d.closureText || 'Station or stop notice', ...dates });
      }
      node.children.forEach(walk);
    }
    walk(data); return notices;
  }
  function endpoints(range) {
    const planned = !!range;
    const end = planned ? new Date(Date.parse(range.end+'T00:00:00Z')+86400000).toISOString().slice(0,10) : null;
    const railPath = planned ? `/Status/${range.start}/to/${end}?detail=true` : '/Status?detail=true';
    return [
      { id:'rail', label:planned?'Central, Jubilee, Elizabeth line, DLR and Mildmay':'All TfL Tube, Overground, Elizabeth line and DLR lines', path:planned?`/Line/${RAIL.join(',')}${railPath}`:'/Line/Mode/tube,overground,elizabeth-line,dlr/Status?detail=true', parse:d=>railNotices(d,planned) },
      ...[BUSES.slice(0,16),BUSES.slice(16)].map((ids,i)=>({id:`buses${i}`,label:`Bus routes ${ids.join(', ').toUpperCase()}`,path:`/Line/${ids.join(',')}/Disruption`,parse:d=>busNotices(d,planned)})),
      {id:'stratford',label:'Stratford station and both bus stations',path:'/StopPoint/HUBSRA/Disruption?getFamily=true',parse:d=>stopNotices(d,'Stratford',planned)},
      {id:'international',label:'Stratford International DLR',path:'/StopPoint/940GZZDLSIT/Disruption?getFamily=true',parse:d=>stopNotices(d,'Stratford International DLR',planned)},
      ...['490002268YY','490002268ZZ','490018554C','490018554E'].map(id=>({id,label:'Stratford International bus stop',path:`/StopPoint/${id}/Disruption?getFamily=true`,parse:d=>stopNotices(d,'Stratford International bus stop',planned)}))
    ];
  }
  async function collect({range=null, previous=null, fetcher=fetch, now=new Date().toISOString(), key=''} = {}) {
    const groups = await Promise.all(endpoints(range).map(async endpoint => {
      const old = previous?.groups?.find(g=>g.id===endpoint.id);
      try {
        const url = API + endpoint.path + (key ? (endpoint.path.includes('?')?'&':'?')+'app_key='+encodeURIComponent(key) : '');
        const response = await fetcher(url,{signal:AbortSignal.timeout(15000)});
        if (!response.ok) throw Error(`TfL HTTP ${response.status}`);
        return {id:endpoint.id,label:endpoint.label,status:'success',checkedAt:now,error:null,notices:unique(endpoint.parse(await response.json()))};
      } catch (error) {
        // Do not persist URLs or credentials in diagnostic messages.
        return {id:endpoint.id,label:endpoint.label,status:'unavailable',checkedAt:old?.checkedAt || null,
          error:'TfL update unavailable',notices:old?.notices || []};
      }
    }));
    return {range,attemptedAt:now,groups};
  }
  function stale(group, age, now=Date.now()) {
    return group.status !== 'success' || !Number.isFinite(Date.parse(group.checkedAt)) || now-Date.parse(group.checkedAt)>age;
  }
  const api={collect,onDate,active,stale,unique,railNotices,busNotices,stopNotices,RAIL,LIVE_RAIL,BUSES};
  if (typeof module !== 'undefined' && module.exports) module.exports=api;
  else root.StratfordTravel=api;
})(typeof window !== 'undefined' ? window : globalThis);

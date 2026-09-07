const fs = require('node:fs');
const path = require('node:path');
const travel = require('../travel-core.js');
const file = path.join(__dirname, '..', 'travel-data.json');
(async () => {
  const text=fs.readFileSync(path.join(__dirname,'..','schedule-data.js'),'utf8');
  const calendar=JSON.parse(text.replace(/^window.CALENDAR_DATA = /,'').trim().replace(/;$/,''));
  const previous=fs.existsSync(file)?JSON.parse(fs.readFileSync(file,'utf8')):null;
  const result=await travel.collect({range:calendar.range,previous,key:process.env.TFL_APP_KEY || ''});
  fs.writeFileSync(file,JSON.stringify(result,null,2)+'\n');
  for (const group of result.groups) console.log(`${group.label}: ${group.status}, ${group.notices.length} dated notices`);
})().catch(error=>{console.error(error.message);process.exitCode=1;});

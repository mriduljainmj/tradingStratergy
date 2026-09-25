const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),ts=require('typescript'),path=require('node:path');
const code=ts.transpileModule(fs.readFileSync(path.join(__dirname,'../src/app/shared/stream-candles.ts'),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
const scope={exports:{}};vm.runInNewContext(code,scope);
const merge=scope.exports.mergeChartStream;
const open=Date.parse('2026-09-25T09:15:00+05:30')/1000;
const minutes=[{time:open,open:100,high:103,low:99,close:102,last_tick:open+50},{time:open+60,open:102,high:110,low:101,close:109,last_tick:open+110}];
assert.equal(merge([],minutes,'minute').length,2);
for(const interval of ['5minute','15minute','60minute']){
 const rows=merge([],minutes,interval);assert.equal(rows.length,1);assert.equal(rows[0].time,open);assert.equal(rows[0].high,110);assert.equal(rows[0].open,100);
}
for(const [interval,day] of [['day',25],['week',21],['month',1]]){
 const rows=merge([],minutes,interval);assert.equal(rows.length,1);assert.equal(rows[0].time.day,day);assert.equal(rows[0].time.month,9);
}
const history=[{time:{year:2026,month:9,day:21},open:90,high:108,low:80,close:105}];
const week=merge(history,minutes,'week',open);
assert.equal(week[0].open,90);assert.equal(week[0].low,80);assert.equal(week[0].high,110);assert.equal(week[0].close,109);
assert.equal(merge(history,minutes,'week',open+120)[0].close,105);
assert.equal(history[0].close,105);
console.log('PASS: minute, intraday and calendar timeframe streaming; NSE hourly alignment; history reconciliation.');

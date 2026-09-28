const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const ts = require('typescript');
const path = require('node:path');
function load(name) {
  const source = fs.readFileSync(path.join(__dirname, '../src/app/shared/', name+'.ts'),'utf8');
  const scope = {exports: {}, Date, require: n=>load(n.replace('./',''))};
  vm.runInNewContext(ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,scope);
  return scope.exports;
}
const {drawingSymbol,drawingPosition}=load('drawing-time');
assert.equal(drawingSymbol('NIFTY 50:5minute'),'NIFTY 50');
assert.equal(drawingSymbol('NIFTY 50:day'),'NIFTY 50');
assert.equal(drawingSymbol('option:1234'),'option:1234');
const open=Date.parse('2026-09-21T03:45:00Z')/1000;
const days=[{time:'2026-09-21'},{time:'2026-09-22'}];
const bars=[{time:open},{time:open+300},{time:open+600}];
const p={time:open,price:100,offset:1,interval:'5minute'};
assert.equal(drawingPosition(p,bars,'5minute'),1);
assert.ok(Math.abs(drawingPosition(p,days,'day')-300/86400)<1e-9);
assert.equal(drawingPosition({time:'2026-09-21',offset:0,interval:'day'},bars,'5minute'),0);
assert.equal(drawingPosition({...p,offset:4},bars,'5minute'),4);
assert.equal(drawingPosition(p,[],'day'),null);
assert.equal(drawingPosition({time:'2026-09-21',offset:0,interval:'day'},[{time:'2026-09-14'},{time:'2026-09-21'}],'week'),1);
console.log('PASS drawing anchors across intraday, daily, weekly, empty and future candle ranges');

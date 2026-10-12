/* Check the generated report's own script against a small DOM model. */
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const fixture=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));
const elements=fixture.claims.map(c=>({dataset:{kind:c.kind},textContent:c.text,hidden:false,classList:{toggle(name,value){elements.find(n=>n.classList===this).hidden=value;},remove(name){elements.find(n=>n.classList===this).hidden=false;}},querySelectorAll(){return [this.detail];},detail:{open:false}}));
const input={value:'',events:{},addEventListener(name,cb){this.events[name]=cb;}};
const filter={...input,events:{},value:'all'};
const button={...input,events:{}};let printed=false;
const window={events:{},addEventListener(name,cb){this.events[name]=cb;},print(){printed=true;}};
const location={hash:''};
const document={querySelector(q){return {'#search':input,'#filter':filter,'#print':button}[q];},querySelectorAll(){return elements;},getElementById(id){return elements[fixture.claims.findIndex(c=>c.id===id)];}};
vm.runInNewContext(fixture.script,{document,window,location});
input.value='operating';input.events.input();
assert.equal(elements.filter(n=>!n.hidden).length,fixture.claims.filter(c=>c.text.toLowerCase().includes('operating')).length);
input.value='';filter.value='calculated';filter.events.change();
assert.equal(elements.filter(n=>!n.hidden).length,fixture.claims.filter(c=>c.kind==='calculated').length);
filter.value='all';filter.events.change();assert.equal(elements.filter(n=>!n.hidden).length,elements.length);
button.events.click();assert.equal(printed,true);
location.hash='#'+fixture.claims[0].id;window.events.hashchange();assert.equal(elements[0].detail.open,true);
console.log('Claim search, type filters, anchor disclosures and print action passed.');

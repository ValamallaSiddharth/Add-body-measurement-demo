/* Run with node tests/test_frontend.cjs; no browser dependencies required. */
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const html=fs.readFileSync('app/static/index.html','utf8');
class Element {
  constructor(){this.value='';this.textContent='';this.hidden=false;this.children=[];this.events={};this.files=[];}
  addEventListener(n,f){this.events[n]=f;} appendChild(x){this.children.push(x);} replaceChildren(){this.children=[];}
  querySelectorAll(){return [];} scrollIntoView(){} focus(){} reset(){} removeAttribute(){}
}
const nodes=new Map([...html.matchAll(/id="([^"]+)"/g)].map(m=>[m[1],new Element()]));
let mode='success';
nodes.get('mode').value='demo';
const response={person_id:'P001',measurements:{height_cm:170,shoulder_cm:40,waist_cm:85,front_waist_width_cm:32,side_waist_depth_cm:22},confidence:.91,status:'good',created_at:'2026-09-21T12:00:00Z',notes:'Estimate'};
const context={document:{getElementById(id){assert(nodes.has(id),id);return nodes.get(id);},createElement(){return new Element();}},URL:{createObjectURL(){return 'blob:test';},revokeObjectURL(){}},FormData:class{append(){}},fetch:async url=>['/api/measure','/api/demo/measure'].includes(url)?{ok:mode==='success',status:mode==='success'?200:409,json:async()=>mode==='success'?{...response,mode:url.includes('/demo/')?'demo':'real'}:{error:'This Person ID already has a saved measurement.'}}:{ok:true,json:async()=>url==='/health'?{ok:true}:[]}};
vm.runInNewContext(fs.readFileSync('app/static/app.js','utf8'),context);
const $=id=>nodes.get(id);
(async()=>{
 await $('measurement-form').events.submit({preventDefault(){}});assert($('error').textContent);
 $('person-id').value='P001';
 await $('measurement-form').events.submit({preventDefault(){}});
 assert.equal($('result-status').textContent,'DEMO ONLY');assert.equal($('result-demo-notice').hidden,false);
 $('mode').value='real';$('mode').events.change();
 assert.equal($('real-instructions').hidden,false);
 await $('measurement-form').events.submit({preventDefault(){}});assert($('error').textContent.includes('Select both'));
 for(const view of ['front','side']){$(view+'-file').files=[{name:view+'.jpg',size:2000}];$(view+'-file').events.change({target:$(view+'-file')});assert.equal($(view+'-preview').hidden,false);}
 await $('measurement-form').events.submit({preventDefault(){}});
 assert.equal($('height-value').textContent,'170.0 cm');assert.equal($('result-status').textContent,'GOOD');assert.equal($('result').hidden,false);assert.equal($('processing').hidden,true);
 mode='duplicate';await $('measurement-form').events.submit({preventDefault(){}});assert($('error').textContent.includes('already has'));assert.equal($('result').hidden,true);
 $('another').events.click();assert.equal($('front-preview').hidden,true);
 $('front-file').files=[{name:'bad.heic',size:2000}];$('front-file').events.change({target:$('front-file')});assert($('error').textContent.includes('HEIC'));
 assert(!html.includes('name="height_cm"'));
 console.log('PASS: frontend wiring, previews, validation, results, duplicate errors, reset, and unsupported images');
})().catch(error=>{console.error(error);process.exitCode=1;});


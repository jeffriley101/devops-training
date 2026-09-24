const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const sandbox = {window:{}, document:{getElementById:()=>null}, Uint8ClampedArray};
vm.runInNewContext(fs.readFileSync('static/js/woodchuck-appearance.js','utf8'), sandbox);
const recolor = sandbox.window.WWColorizer.recolor;
const color = (key,hue) => ({key,hue});
test('one original-source pass independently recolors blue and green, preserving alpha and unrelated pixels',()=>{
  const original = new Uint8ClampedArray([0,0,200,133, 0,200,0,255, 200,30,20,71, 0,0,255,0]);
  const result = recolor(original,color('green',120),color('blue',220));
  assert.deepEqual(Array.from(result.slice(0,4)),[0,200,0,133]);
  assert.ok(result[6] > result[5]);
  assert.equal(result[7],255);
  assert.deepEqual(Array.from(result.slice(8)),Array.from(original.slice(8)));
  assert.deepEqual(Array.from(original.slice(0,4)),[0,0,200,133]);
});
test('default colors preserve original source shading exactly',()=>{
  const original = new Uint8ClampedArray([30,70,150,255, 40,120,20,200, 230,230,230,255]);
  assert.deepEqual(Array.from(recolor(original,color('blue',220),color('green',120))),Array.from(original));
});
test('brightness differences remain visible in the replacement family',()=>{
  const result = recolor(new Uint8ClampedArray([0,0,220,255, 0,0,100,255]),color('red',0),color('green',120));
  assert.ok(result[0]>result[4]); assert.equal(result[2],0); assert.equal(result[6],0);
});

for (const [key,hue] of [['red',0],['purple',275],['pink',330]]) {
  test(`${key} hat replaces dark and muted greens with a visibly saturated target`,()=>{
    const greens=[[12,18,10],[58,64,54],[70,73,60],[55,62,56],[35,125,40]];
    const original=new Uint8ClampedArray(greens.flatMap((rgb,i)=>[...rgb,100+i]));
    const result=recolor(original,color('blue',220),{key,hue,saturation_floor:.65,lightness_lift:key==='pink'?.22:0});
    greens.forEach((rgb,i)=>{
      const [r,g,b,a]=result.slice(i*4,i*4+4);
      assert.equal(a,100+i);
      assert.ok(r>g*1.3,`${key}: ${r},${g},${b}`);
      if(key==='red') assert.ok(r>b*1.3);
      if(key==='purple') assert.ok(b>r && b>g*1.3);
      if(key==='pink') assert.ok(r>b && b>g*1.3 && r>80);
    });
    assert.ok(result[0]<result[16], 'source brightness still separates shadow and highlight');
    assert.deepEqual(Array.from(original.slice(0,3)),greens[0]);
  });
}
test('muted and dark hoodie blue recolors; neutral gray remains neutral',()=>{
  const original=new Uint8ClampedArray([12,15,20,87, 55,60,65,255, 58,58,58,255]);
  const result=recolor(original,color('red',0),color('green',120));
  assert.ok(result[0]>result[1] && result[0]>result[2]);
  assert.ok(result[4]>result[5] && result[4]>result[6]);
  assert.equal(result[3],87);
  assert.deepEqual(Array.from(result.slice(8)),[58,58,58,255]);
});

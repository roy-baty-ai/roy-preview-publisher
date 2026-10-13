const { chromium } = require('playwright');
const assert = require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:'/usr/bin/chromium',args:['--no-sandbox']});
 const report=[];
 for(const width of [1440,393,320]){
  const page=await browser.newPage({viewport:{width,height:900},deviceScaleFactor:1});
  const errors=[], unexpected=[];
  page.on('pageerror',e=>errors.push(e.message));
  page.on('request',r=>{if(!r.url().startsWith('http://127.0.0.1:8765/'))unexpected.push(r.url())});
  await page.goto('http://127.0.0.1:8765/preview-assets/window-door-demo/greenwork-miyako/',{waitUntil:'networkidle'});
  await page.locator('footer').scrollIntoViewIfNeeded(); await page.waitForTimeout(150);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  assert.equal(await page.locator('img').evaluateAll(imgs=>imgs.every(i=>i.complete&&i.naturalWidth>0)),true);
  await page.evaluate(()=>scrollTo(0,0));await page.waitForTimeout(250);
  await page.screenshot({path:`/workspace/greenwork-qa/${width}.png`,fullPage:true});
  for(let i=0;i<3;i++){
   const opener=page.locator('[data-open]').nth(i);await opener.click();
   assert.equal(await page.locator('dialog').evaluate(d=>d.open),true);
   assert.equal(await page.locator('.close').evaluate(e=>e===document.activeElement),true);
   await page.keyboard.press('Shift+Tab');
   assert.equal(await page.locator('dialog').evaluate(d=>d.contains(document.activeElement)),true);
   for(let k=0;k<9;k++){await page.keyboard.press('Tab');assert.equal(await page.locator('dialog').evaluate(d=>d.contains(document.activeElement)),true)}
   await page.locator('#note').fill('<script>test</script>');
   await page.locator('form button').click();
   assert.match(await page.locator('#result').innerText(),/送信・保存されていません/);
   assert.equal(await page.evaluate(()=>localStorage.length+sessionStorage.length),0);
   await page.keyboard.press('Escape');
   assert.equal(await page.locator('dialog').evaluate(d=>d.open),false);
   assert.equal(await opener.evaluate(e=>e===document.activeElement),true);
   await opener.click(); assert.equal(await page.locator('#note').inputValue(),'');
   await page.locator('.close').click();
   assert.equal(await opener.evaluate(e=>e===document.activeElement),true);
  }
  assert.deepEqual(errors,[]);assert.deepEqual(unexpected,[]);
  report.push({width,overflow:false,images:2,openers:3,focusTrap:true,escape:true,focusReturn:true,closeButton:true,reset:true,demoNoExternalRequests:true,storageEmpty:true,errors});
  await page.close();
 }
 console.log(JSON.stringify(report,null,2));await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});

// Test-only page fixture. No external form is submitted; production authorization is reused.
const fs=require('node:fs');
const guard=require(process.env.WEBTASK_PRODUCTION_GUARD);
const html='<!doctype html><html><head><title>WebTask fixture feedback</title></head><body><h1>Controlled fixture</h1><form method="post" action="/__webtask_submit"><label>Topic<input name="topic" required></label><label>Message<textarea name="message" required></textarea></label><button type="submit">Send fixture</button></form></body></html>';
exports.default=async({page})=>{
  await page.context().route('**/*',async route=>{
    const request=route.request(),url=new URL(request.url());
    if(url.hostname!=='example.com' || !['/__webtask_fixture','/__webtask_submit'].includes(url.pathname))return route.abort('blockedbyclient');
    if(!await guard.authorize(request))return route.abort('blockedbyclient');
    if(url.pathname==='/__webtask_fixture' && request.method()==='GET')return route.fulfill({status:200,contentType:'text/html',body:html});
    if(url.pathname==='/__webtask_submit' && request.method()==='POST'){
      fs.appendFileSync(process.env.WEBTASK_FIXTURE_RECEIPTS,JSON.stringify({url:url.href,body:request.postData()})+'\n');
      return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({received:true,fixture:true})});
    }
    return route.abort('blockedbyclient');
  });
};

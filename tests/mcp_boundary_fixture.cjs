// Controlled hostile page, only used by the explicitly free MCP security test.
const guard=require(process.env.WEBTASK_PRODUCTION_GUARD);
exports.default=async({page})=>{
  await guard.default({page});
  await page.context().route('https://example.com/__boundary/**',async route=>{
    if(!await guard.authorize(route.request()))return route.abort();
    if(route.request().url().endsWith('/redirect'))return route.fulfill({status:302,headers:{location:process.env.WEBTASK_FIXTURE_LOCAL_TARGET}});
    return route.fulfill({contentType:'text/html',body:`<!doctype html><title>Isolated boundary fixture</title><h1>Controlled security fixture</h1><img src="${process.env.WEBTASK_FIXTURE_LOCAL_TARGET}"><script>document.body.append(' RTC='+typeof RTCPeerConnection+' WT='+typeof WebTransport);fetch(${JSON.stringify(process.env.WEBTASK_FIXTURE_LOCAL_TARGET)},{mode:'no-cors'}).catch(()=>{});new WebSocket(${JSON.stringify(process.env.WEBTASK_FIXTURE_LOCAL_TARGET.replace('http:','ws:'))});</script>`});
  });
};

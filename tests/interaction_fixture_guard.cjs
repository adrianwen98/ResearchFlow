// Deterministic controlled fixture, never evidence of a public website success.
const guard=require(process.env.WEBTASK_PRODUCTION_GUARD);
exports.default=async({page})=>{
 guard.installDownloads(page);
 await page.addInitScript(()=>document.addEventListener('DOMContentLoaded',()=>{
   const a=document.querySelector('a[download]');
   if(a)a.href=URL.createObjectURL(new Blob(['group,value\nA,-1.5\nB,2\nA,-2.5\n'],{type:'text/csv'}));
 }));
 await page.context().route('**/*',async route=>{
  const req=route.request(),url=new URL(req.url());
  if(url.hostname!=='example.com'||!await guard.authorize(req))return route.abort();
  if(url.pathname==='/__interaction')return route.fulfill({contentType:'text/html',body:`<!doctype html><title>Controlled dataset fixture</title><h1>Fixture only</h1><form method="get" action="/__interaction"><label>Search data<input type="search" name="q"></label><button>Search</button></form><label>Category<select name="category" onchange="document.querySelector('#result').textContent='Selected '+this.value"><option value="all">All</option><option value="negative">Negative</option></select></label><p id="result">Query ${url.searchParams.get('q')||''}</p><a href="/__data.csv" download="fixture.csv">Download CSV</a><button type="button" onclick="fetch('/__write',{method:'POST',body:'no'})">Delete record</button>`});
  if(url.pathname==='/__data.csv')return route.fulfill({headers:{'content-type':'text/csv','content-disposition':'attachment; filename="fixture.csv"'},body:'group,value\nA,-1.5\nB,2\nA,-2.5\n'});
  return route.abort();
 });
};

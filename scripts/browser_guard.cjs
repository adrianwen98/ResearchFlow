// Defense in depth for redirects/subresources. Not a network namespace sandbox.
const dns = require('node:dns').promises;
const fs = require('node:fs');
const crypto = require('node:crypto');
const { BlockList } = require('node:net');
const blocked = new BlockList();
const blocked6 = new BlockList();
for (const [ip,bits] of [['::',96],['::1',128],['::ffff:0:0',96],['64:ff9b::',96],['64:ff9b:1::',48],['100::',64],['2001::',32],['2001:db8::',32],['2001:10::',28],['2001:20::',28],['2002::',16],['fc00::',7],['fe80::',10],['ff00::',8]]) blocked6.addSubnet(ip,bits,'ipv6');
const public6=address=>/^2[0-9a-f]{3}:/i.test(address) && !blocked6.check(address,'ipv6');
for (const [ip, bits] of [['0.0.0.0',8],['10.0.0.0',8],['127.0.0.0',8],['169.254.0.0',16],['172.16.0.0',12],['192.168.0.0',16],['100.64.0.0',10],['192.0.0.0',24],['192.0.2.0',24],['198.51.100.0',24],['203.0.113.0',24],['198.18.0.0',15],['224.0.0.0',4],['240.0.0.0',4]]) blocked.addSubnet(ip,bits,'ipv4');
const domains = (process.env.WEBTASK_ALLOWED_DOMAINS || '').split(',').filter(Boolean);
const cache = new Map();
async function publicHost(host) {
  if (host === 'localhost' || /\.(localhost|local|internal|test|invalid)$/.test(host)) return false;
  const net = require('node:net');
  if (net.isIP(host) === 4) return !blocked.check(host, 'ipv4');
  if (net.isIP(host) === 6) return public6(host);
  // The per-session egress proxy resolves and pins every TCP connection. Do not
  // accept synthetic addresses from the OS resolver or ask Chrome to resolve again.
  if (process.env.WEBTASK_PINNED_EGRESS === 'true') return host.includes('.');
  if (cache.has(host) && Date.now()-cache.get(host).at < 5000) return cache.get(host).ok;
  const addresses = await dns.lookup(host,{all:true});
  const ok = addresses.length > 0 && addresses.every(({address,family}) => family === 4
    ? !blocked.check(address,'ipv4') : public6(address));
  cache.set(host,{ok,at:Date.now()}); return ok;
}
async function authorize(request) {
  const url = new URL(request.url());
  if (!['http:','https:'].includes(url.protocol) || (url.port && !['80','443'].includes(url.port))) return false;
  const host = url.hostname.replace(/^\[|\]$/g,'').toLowerCase().replace(/\.$/,'');
  if (url.username || url.password || !(await publicHost(host))) return false;
  if (request.isNavigationRequest() && (/\/(?:logout|signout|delete|remove|unsubscribe|checkout|purchase|publish|send)(?:\/|$)/i.test(url.pathname) || /(?:^|&)(?:action|do|operation|cmd)=(?:delete|remove|send|publish|buy|pay|logout)(?:&|$)/i.test(url.search.slice(1)))) return false;
  if (request.isNavigationRequest() && domains.length && !domains.some(d => host===d || host.endsWith('.'+d))) return false;
  if (['GET','HEAD'].includes(request.method())) return true;
  const path = process.env.WEBTASK_ACTION_CAPABILITY;
  if (request.method()!=='POST' || !path || (domains.length && !domains.some(d=>host===d || host.endsWith('.'+d)))) return false;
  try {
    const cap = JSON.parse(fs.readFileSync(path,'utf8'));
    const digest = crypto.createHash('sha256').update(request.postData() || '').digest('hex');
    if (cap.url!==url.href || cap.method!=='POST' || cap.body_sha256!==digest || cap.expires_at*1000<Date.now()) return false;
    fs.renameSync(path,path+'.consumed');
    return true;
  } catch { return false; }
}
exports.authorize = authorize;
function installDownloads(page) {
  const folder=process.env.WEBTASK_DOWNLOAD_DIR;
  if (!folder) return;
  fs.mkdirSync(folder,{recursive:true});
  page.on('download',async download=>{
    const id=crypto.randomBytes(12).toString('hex');
    const suggested=download.suggestedFilename(),ext=require('node:path').extname(suggested).toLowerCase();
    const meta={url:download.url(),page_url:page.url(),suggested_filename:suggested,file:id+ext};
    try {
      if (!['.csv','.xlsx','.pdf'].includes(ext)) {await download.cancel();throw Error('Only CSV/XLSX/PDF downloads are supported');}
      const target=require('node:path').join(folder,meta.file);
      await download.saveAs(target);
      if(fs.statSync(target).size>Number(process.env.WEBTASK_DOWNLOAD_MAX_BYTES||8388608)) {fs.unlinkSync(target);throw Error('Downloaded file exceeds size limit');}
    } catch(error) {meta.error=String(error.message).slice(0,300);}
    fs.writeFileSync(require('node:path').join(folder,id+'.pending'),JSON.stringify(meta));
    fs.renameSync(require('node:path').join(folder,id+'.pending'),require('node:path').join(folder,id+'.json'));
  });
}
exports.installDownloads=installDownloads;
exports.default = async ({page}) => {
  if (process.env.WEBTASK_PINNED_EGRESS !== 'true') throw Error('Pinned public egress is required');
  installDownloads(page);
  await page.context().addInitScript(() => {
    // These transports cannot be policed through HTTP routing. Disable them
    // before page scripts, in every frame; this is still not an OS sandbox.
    for (const name of ['RTCPeerConnection','webkitRTCPeerConnection','WebTransport']) {
      Object.defineProperty(globalThis,name,{value:undefined,writable:false,configurable:false});
    }
  });
  // WebSockets and service workers must not create unobserved write paths.
  await page.context().routeWebSocket('**/*', socket => socket.close());
  await page.context().route('**/*', async route => {
    try {
      if (await authorize(route.request())) return route.continue();
      const file=process.env.WEBTASK_DENIAL_FILE;
      if(file) fs.writeFileSync(file,JSON.stringify({time:Date.now()/1000,url:route.request().url(),message:'网页请求被安全策略阻止（目标、协议、端口或未审批写入）'}));
      return route.abort('blockedbyclient');
    } catch { return route.abort('blockedbyclient'); }
  });
};

import json
import os
from pathlib import Path
import shutil
import subprocess
import pytest


@pytest.mark.parametrize('domains',['example.com',''])
def test_actual_node_guard_requires_exact_unexpired_single_use_capability(tmp_path,domains):
    root=Path(__file__).resolve().parents[1]
    node=shutil.which('node') or r'D:\dev\sdk\nodejs\node.exe'
    source=r'''
const fs=require('node:fs'),crypto=require('node:crypto');
require('node:dns').promises.lookup=async()=>[{address:'1.1.1.1',family:4}];
const {authorize}=require(process.env.GUARD_MODULE);
const file=process.env.WEBTASK_ACTION_CAPABILITY;
const request=(body='topic=ok',url='https://example.com/submit')=>({url:()=>url,method:()=> 'POST',postData:()=>body,isNavigationRequest:()=>false});
const cap=(expires=Date.now()/1000+30)=>fs.writeFileSync(file,JSON.stringify({url:'https://example.com/submit',method:'POST',body_sha256:crypto.createHash('sha256').update('topic=ok').digest('hex'),expires_at:expires}));
(async()=>{
 const none=await authorize(request());cap(Date.now()/1000-1);const expired=await authorize(request());
 cap();const wrongBody=await authorize(request('topic=changed'));const wrongTarget=await authorize(request('topic=ok','https://example.com/elsewhere'));
 const allowed=await authorize(request());const replay=await authorize(request());
 console.log(JSON.stringify({none,expired,wrongBody,wrongTarget,allowed,replay}));
})();
'''
    done=subprocess.run([node,'-e',source],capture_output=True,text=True,timeout=15,
                        env={**os.environ,'GUARD_MODULE':str(root/'scripts'/'browser_guard.cjs'),
                             'WEBTASK_ACTION_CAPABILITY':str(tmp_path/'cap.json'),'WEBTASK_ALLOWED_DOMAINS':domains})
    assert done.returncode==0,done.stderr
    assert json.loads(done.stdout)=={'none':False,'expired':False,'wrongBody':False,'wrongTarget':False,'allowed':True,'replay':False}

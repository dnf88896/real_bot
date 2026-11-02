// Executes the actual bundled handler with fake sends; never posts a QQ message.
const fs=require('node:fs'),vm=require('node:vm'),cp=require('node:child_process');
const source=fs.readFileSync('/tmp/snowluma-security-audit.mjs','utf8');
function extract(name){const a=source.indexOf('function '+name+'(');if(a<0)throw Error('Missing '+name);const b=source.indexOf('\n}',a)+2;return source.slice(a,b);}
const a=source.indexOf('\thandleStatusCommand(event) {'),b=source.indexOf('\n\tasync replyStatus(',a);
if(a<0||b<0)throw Error('Missing status handler');
const handler='function '+source.slice(a+1,b);
const sandbox={toInt:n=>Number(n),Date,Map};
vm.createContext(sandbox);
vm.runInContext(['matchesStatusCommand','normalize','isObject','statusCooldownElapsed'].map(extract).join('\n')+'\n'+handler,sandbox);
function invoke(enabled,text,type){let sends=0;const fake={ctx:{config:{statusCommand:{enabled,trigger:'#sl',cooldownSeconds:5,swallow:false}}},statusCommandCooldown:new Map(),trackAction:()=>sends++,replyStatus:()=>{throw Error('No actual sends permitted');}};
 sandbox.handleStatusCommand.call(fake,{post_type:'message',message_type:type,user_id:100000001,group_id:12345,message:[{type:'text',data:{text}}]});return sends;}
const read=name=>JSON.parse(cp.execFileSync('docker',['exec','snowluma-lab','cat','/app/data/config/'+name],{encoding:'utf8'}));
const base=read('onebot.json');const results=[];
for(const name of ['onebot.json','onebot_3654076639.json','onebot_3231181613.json']){
 const cfg=read(name),enabled=(cfg.statusCommand||base.statusCommand||{}).enabled;
 if(enabled!==false)throw Error('Status still enabled: '+name);
 for(const text of ['#sl','#SL','  #sl  '])for(const type of ['group','private'])if(invoke(enabled,text,type)!==0)throw Error('Unexpected automatic reply');
 results.push({file:name,automatic_replies:0});
}
if(base.statusCommand.enabled!==false)throw Error('Future account defaults unsafe');
if(invoke(true,'#sl','group')!==1)throw Error('Positive control did not detect reply');
console.log(JSON.stringify({negative_cases:18,positive_control:'passed',future_accounts_inherit_disabled:true,results}));

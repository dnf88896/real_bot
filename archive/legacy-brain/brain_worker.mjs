import fs from 'node:fs';
import { execFileSync } from 'node:child_process';
import { NodeApiClient, unwrap } from '/opt/qq-bridge-lab/src/dsh-client.js';
const api=new NodeApiClient('http://127.0.0.1:3082',undefined,{});
const file='/opt/qq-brain/session.json';
let sessionId=fs.existsSync(file)?JSON.parse(fs.readFileSync(file,'utf8')).sessionId:null;
function backend(action,args={}){return JSON.parse(execFileSync('/usr/bin/python3',['/opt/qq-brain/brain_backend.py',action],{input:JSON.stringify(args),encoding:'utf8',timeout:15000}));}
while(true){
  try{
    if(!sessionId){
      sessionId=unwrap(await api.sessions.create({cwd:'/opt/qq-brain/work',agentPreset:'qq-brain'}),'create').sessionId;
      unwrap(await api.sessions.selectModel({sessionId,provider:'deepseek-official',model:'deepseek-flash',reasoningEffort:'high'}),'model');
      fs.writeFileSync(file,JSON.stringify({sessionId}),{mode:0o600});
      console.log('Dedicated brain session created.');
    }
    const batch=backend('poll');
    if(batch.pending && Date.now()/1000-batch.notified>300){
      unwrap(await api.sessions.prompt({sessionId,mode:'queue',content:[{type:'text',text:`新信息批次 ${batch.batch_id}（${batch.count}条）。请读取本批新消息，查询相关上下文/历史，判断是否回复并调用 record_decision 记录决定。口由独立API生成文字，再由程序发送允许的QQ私聊：你只准备材料，不自行生成聊天回复或发送QQ。完成后等待下一次通知。`}]}),'prompt');
      backend('notified',{batch_id:batch.batch_id});console.log(JSON.stringify({event:'brain_notified',batch:batch.batch_id,chat_type:batch.chat_type,count:batch.count}));
    }
  }catch{console.error('Brain dispatch failed; will retry.');}
  await new Promise(r=>setTimeout(r,2000));
}

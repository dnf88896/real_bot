// Keep the same brain session, cancel interrupted work and redispatch pending input.
import fs from 'node:fs';
import { execFileSync } from 'node:child_process';
import { NodeApiClient } from '/opt/qq-bridge-lab/src/dsh-client.js';
const file='/opt/qq-brain/session.json';
if (fs.existsSync(file)) {
  const {sessionId}=JSON.parse(fs.readFileSync(file,'utf8'));
  const api=new NodeApiClient('http://127.0.0.1:3082',undefined,{});
  await api.stopSessionWork(sessionId);
}
execFileSync('/usr/bin/python3',['-c',"import sqlite3; db=sqlite3.connect('/opt/qq-brain/state.sqlite3'); db.execute('UPDATE batches SET notified=0 WHERE done=0'); db.commit(); db.close()"]);
console.log('Startup recovery complete; session retained and pending batches ready.');

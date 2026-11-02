import fs from 'node:fs';
import { SnowLumaWebSocketClient } from '@snowluma/sdk';
import { createRecorder } from './recorder.mjs';

const account = '100000002';
const cfg = JSON.parse(fs.readFileSync('/opt/snowluma/config/onebot_' + account + '.json', 'utf8'));
const endpoint = cfg.networks.wsServers[0];
const record = createRecorder('/opt/qq-eyes/data/qq-received.jsonl', account);
const bot = new SnowLumaWebSocketClient({
  url: `ws://${endpoint.host}:${endpoint.port}`,
  accessToken: endpoint.accessToken,
  reconnect: true,
});
bot.onEvent(event => {
  try { if (record(event)) console.log('Inbound message recorded.'); }
  catch { console.error('Message write failed; stopping to surface the failure.'); process.exit(1); }
});
bot.on('open', () => console.log('QQ eyes connected.'));
bot.on('close', () => console.log('QQ eyes disconnected; reconnecting.'));
bot.on('error', () => console.error('QQ eyes connection error.'));
await bot.connect();

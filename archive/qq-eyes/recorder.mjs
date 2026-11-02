import fs from 'node:fs';
import path from 'node:path';

export function createRecorder(file, account) {
  fs.mkdirSync(path.dirname(file), { recursive: true, mode: 0o700 });
  // Keep recent event identities across restarts; never collapse equal text.
  const seen = new Set();
  if (fs.existsSync(file)) {
    let tail;
    let tailStart;
    const fd = fs.openSync(file, 'r');
    try {
      const size = fs.fstatSync(fd).size;
      const start = Math.max(0, size - 2 * 1024 * 1024);
      const buf = Buffer.alloc(size - start);
      fs.readSync(fd, buf, 0, buf.length, start);
      tail = buf;
      tailStart = start;
      const lines = buf.toString('utf8').split('\n');
      if (start) lines.shift();
      for (const line of lines) {
        try { const r = JSON.parse(line); if (r.event_key) seen.add(r.event_key); } catch {}
      }
    } finally { fs.closeSync(fd); }
    // Repair only an incomplete trailing line left by a crash, retaining its bytes.
    const bytes = tail;
    if (bytes.length && bytes.at(-1) !== 10) {
      const end = bytes.lastIndexOf(10) + 1;
      if (!end && tailStart) throw new Error('Incomplete record exceeds recovery window; manual recovery required');
      fs.appendFileSync(file + '.incomplete', bytes.subarray(end), { mode: 0o600 });
      fs.truncateSync(file, tailStart + end);
    }
  }
  return event => {
    if (event?.post_type !== 'message' || !['private', 'group'].includes(event.message_type)) return false;
    if (String(event.self_id) !== String(account)) return false;
    const sender = event.user_id ?? event.sender?.user_id;
    if (String(sender) === String(account)) return false;
    const conversation = event.message_type === 'group' ? event.group_id : sender;
    const key = event.message_id == null ? null : [account, event.message_type, conversation, sender, event.time, event.message_id].join(':');
    if (key && seen.has(key)) return false;
    const record = {
      schema_version: 1,
      received_at: new Date().toISOString(),
      event_key: key,
      account_id: String(account),
      platform: 'qq',
      chat_type: event.message_type,
      conversation_id: String(conversation),
      sender_id: String(sender),
      event_time: event.time ?? null,
      message_id: event.message_id == null ? null : String(event.message_id),
      raw_event: event,
    };
    const fd = fs.openSync(file, 'a', 0o600);
    try { fs.writeFileSync(fd, JSON.stringify(record) + '\n'); fs.fsyncSync(fd); }
    finally { fs.closeSync(fd); }
    if (key) {
      seen.add(key);
      if (seen.size > 20000) seen.delete(seen.values().next().value);
    }
    return true;
  };
}

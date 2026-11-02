import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createRecorder } from './recorder.mjs';

test('records private/group raw data, filters self/heartbeat, survives restart without duplicating events', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'qq-eyes-test-'));
  try {
    const file = path.join(dir, 'received.jsonl');
    const base = { post_type: 'message', self_id: 100000002, user_id: 123, time: 100, message_id: 1, message_type: 'private', message: [{ type: 'text', data: { text: '你好\n后续' } }] };
    let record = createRecorder(file, '100000002');
    assert.equal(record(base), true);
    assert.equal(record(base), false);
    assert.equal(record({ ...base, message_id: 2 }), true);
    assert.equal(record({ ...base, message_id: 3, message_type: 'group', group_id: 999, message: [{ type: 'image', data: { file: 'reference.png' } }] }), true);
    assert.equal(record({ ...base, self_id: 1 }), false);
    assert.equal(record({ ...base, user_id: 100000002 }), false);
    assert.equal(record({ post_type: 'meta_event' }), false);
    fs.appendFileSync(file, '{"partial":');
    record = createRecorder(file, '100000002');
    assert.equal(record(base), false);
    assert.equal(record({ ...base, message_id: 4 }), true);
    const rows = fs.readFileSync(file, 'utf8').trim().split('\n').map(JSON.parse);
    assert.equal(rows.length, 4);
    assert.deepEqual(rows[0].raw_event, base);
    assert.equal(rows[2].conversation_id, '999');
    assert.equal(fs.readFileSync(file + '.incomplete', 'utf8'), '{"partial":');
  } finally { fs.rmSync(dir, { recursive: true, force: true }); }
});

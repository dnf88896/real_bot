"""Reproducible narrow runtime patch: complete QQ MiniApp release-share request."""
import hashlib,json,subprocess,time
from pathlib import Path

HELPER='''
// unified-mouth: complete MiniApp release-share parameters (2026-10-25).
function unifiedMiniAppShareRequest(type, appid, title, desc, picUrl, jumpUrl, iconUrl, webUrl) {
  const v = value => {
    let n = BigInt(value), out = [];
    do { let b = Number(n & 127n); n >>= 7n; out.push(b | (n ? 128 : 0)); } while(n);
    return Buffer.from(out);
  };
  const bytes = (field, value) => Buffer.concat([v((field << 3) | 2), v(value.length), value]);
  const text = (field, value) => bytes(field, Buffer.from(String(value ?? ''), 'utf8'));
  const uint = (field, value) => Buffer.concat([v(field << 3), v(value)]);
  const versionId = type === 'weibo' ? 'e482a3cc4e574d9b772e96ba6eec9ba2' : 'cfc5f7b05b44b5956502edaecf9d2240';
  const body = Buffer.concat([
    bytes(1, bytes(2, Buffer.alloc(0))), text(2, appid), text(3, title), text(4, desc),
    uint(5, Date.now()), uint(6, 1), uint(7, 1), uint(8, 0), text(9, picUrl), text(10, ''),
    text(11, jumpUrl), text(12, iconUrl), uint(13, 3), uint(14, 0), text(15, versionId),
    uint(16, 0), text(17, webUrl), bytes(18, Buffer.alloc(0)),
    bytes(19, Buffer.concat([text(1, ''), text(2, '')])), text(20, '')
  ]);
  return Buffer.concat([text(2, 'V1_PC_MINISDK_99.99.99_1_APP_A'), bytes(4, body)]);
}
'''

def build(source):
    if 'function unifiedMiniAppShareRequest(' in source:raise ValueError('Already patched')
    start=source.index('async getMiniAppArk(type, title, desc, picUrl, jumpUrl) {')
    end=source.index('\n\tclickInlineKeyboardButton(',start)
    old=source[start:end]
    new=old.replace('async getMiniAppArk(type, title, desc, picUrl, jumpUrl) {',
        'async getMiniAppArk(type, title, desc, picUrl, jumpUrl, webUrl = "") {',1)
    a=new.index('const request = protobuf_encode_MiniAppShareReq({')
    b=new.index('\n\t\tconst result =',a)
    new=new[:a]+'const request = unifiedMiniAppShareRequest(type, appid, title, desc, picUrl, jumpUrl, iconUrl, webUrl);'+new[b:]
    source=source[:start]+new+source[end:]
    a=source.index('name: "get_mini_app_ark",')
    b=source.index('\n\t}),',a)+len('\n\t}),')
    old=source[a:b]
    assert old.count('jump_url: f$1.string().optional()')==1
    new=old.replace('jump_url: f$1.string().optional()',
        'jump_url: f$1.string().optional(),\n\t\t\twebUrl: f$1.string().optional(),\n\t\t\tweb_url: f$1.string().optional()',1)
    new=new.replace('getMiniAppArk(type, title, desc, picUrl, jumpUrl)',
        'getMiniAppArk(type, title, desc, picUrl, jumpUrl, p.webUrl || p.web_url || "")',1)
    assert new!=old
    return source[:a]+new+source[b:]+HELPER

def main():
    root=Path('/opt/astrbot-mouth');folder=root/'runtime-patches'/('miniapp-'+time.strftime('%Y%m%d-%H%M%S'))
    folder.mkdir(parents=True,mode=0o700)
    original=folder/'original.mjs';patched=folder/'patched.mjs'
    subprocess.run(['docker','cp','snowluma-lab:/app/runtime/index.mjs',str(original)],check=True)
    patched.write_text(build(original.read_text()))
    subprocess.run(['docker','cp',str(patched),'snowluma-lab:/tmp/unified-miniapp-check.mjs'],check=True)
    subprocess.run(['docker','exec','snowluma-lab','node','--check','/tmp/unified-miniapp-check.mjs'],check=True)
    manifest={'container':'snowluma-lab','runtime':'/app/runtime/index.mjs','original_sha256':hashlib.sha256(original.read_bytes()).hexdigest(),
        'patched_sha256':hashlib.sha256(patched.read_bytes()).hexdigest(),'backup':str(original),'patched':str(patched),'time':time.time()}
    (folder/'manifest.json').write_text(json.dumps(manifest,indent=2));(root/'miniapp-runtime-patch.txt').write_text(str(folder))
    subprocess.run(['docker','cp',str(patched),'snowluma-lab:/app/runtime/index.mjs'],check=True)
    subprocess.run(['docker','restart','snowluma-lab'],check=True)
    print('Applied complete MiniApp request; backup',folder)

if __name__=='__main__':main()

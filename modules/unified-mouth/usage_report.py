"""Read-only Beijing-day/rolling-window cost report for the robot ledger."""
import argparse
import json
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path('/opt/astrbot-mouth')
LABELS = {
 'mouth.private': '口·被动私聊', 'mouth.group': '口·被动群聊',
 'mouth.idle': '口·会话主动', 'mouth.interest_expression': '口·兴趣表达',
 'mouth.other': '口·其他', 'interest.explore': '兴趣·普通探索',
 'interest.special_browser': '兴趣·特殊DSH探索', 'interest.summary': '兴趣·摘要整理',
 'memory.recall_query': '记忆·检索词生成', 'memory.recall_select': '记忆·候选筛选',
 'memory.mid_summary': '记忆·中期整理', 'memory.other': '记忆·其他',
 'participation': '参与判断', 'affect.global_mood': '全局情绪',
 'profile.private': '私聊资料', 'profile.group_style': '群聊风格资料',
 'relay.mark': '中转标记', 'vision.image': '图片理解',
 'web.search': '网络搜索接口', 'web.fetch': '网页读取接口'}


def window(day=None, hours=None):
    zone = ZoneInfo('Asia/Shanghai')
    if hours:
        return time.time() - hours * 3600, time.time()
    today = datetime.now(zone).date()
    date = today if day in (None, 'today') else today - timedelta(days=1) if day == 'yesterday' else datetime.strptime(day, '%Y-%m-%d').date()
    start = datetime.combine(date, datetime.min.time(), zone)
    return start.timestamp(), min((start + timedelta(days=1)).timestamp(), time.time())


def report(start, end, root=ROOT):
    db = sqlite3.connect((Path(root) / 'api-usage.sqlite3').resolve().as_uri() + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in db.execute('''SELECT category,count(*) calls,
          sum(coalesce(input_miss,0)) input_miss,sum(coalesce(input_hit,0)) input_hit,
          sum(coalesce(output_tokens,0)) output_tokens,
          sum(coalesce(estimated_yuan,0)) yuan,
          sum(CASE WHEN estimated_yuan IS NULL THEN 1 ELSE 0 END) unknown_cost,
          sum(CASE WHEN status='pending' THEN 1 ELSE 0 END) pending,
          sum(CASE WHEN status='error' THEN 1 ELSE 0 END) errors,
          sum(CASE WHEN repair_attempt>0 THEN 1 ELSE 0 END) repair_calls
          FROM requests WHERE started>=? AND started<? GROUP BY category ORDER BY yuan DESC''', (start,end))]
        total = sum(r['yuan'] for r in rows)
        for r in rows:
            r['label'] = LABELS.get(r['category'], r['category'])
            r['share_percent'] = round(r['yuan'] / total * 100, 2) if total else 0
        return {'start': start, 'end': end, 'estimated_yuan': round(total, 6),
                'calls': sum(r['calls'] for r in rows),
                'unknown_cost_calls': sum(r['unknown_cost'] for r in rows),
                'categories': rows,
                'key_ids': [dict(r) for r in db.execute('''SELECT key_id,count(*) calls,
                     sum(coalesce(estimated_yuan,0)) yuan FROM requests
                     WHERE started>=? AND started<? GROUP BY key_id''', (start,end))]}
    finally:
        db.close()


def markdown(result):
    zone = ZoneInfo('Asia/Shanghai')
    stamp = lambda n: datetime.fromtimestamp(n,zone).strftime('%Y-%m-%d %H:%M:%S')
    lines = ['# 机器人API用量报告', '', f"北京时间：{stamp(result['start'])} 至 {stamp(result['end'])}", '',
             f"已知估算费用：**¥{result['estimated_yuan']:.4f}**；记录 {result['calls']} 次调用；费用未知 {result['unknown_cost_calls']} 次。", '',
             '| 用途 | 调用 | 未命中输入token | 命中输入token | 输出token | 估算元 | 已知费用占比 | 费用未知 | 格式重试调用 |',
             '|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    for r in result['categories']:
        lines.append(f"| {r['label']} | {r['calls']} | {r['input_miss']} | {r['input_hit']} | {r['output_tokens']} | {r['yuan']:.4f} | {r['share_percent']:.1f}% | {r['unknown_cost']} | {r['repair_calls']} |")
    lines += ['', '说明：费用为按当前Flash官方单价、北京时间工作日高峰/非高峰计算的估算，非官方账单。缓存命中不重复加到未命中输入，思考token已包含在输出中，不二次计费。', '',
              '缺少usage、失败后没有返回用量、运行中被终止等记录归为费用未知，不能当成免费。SDK内部网络重试未单独暴露usage；每个可见模型生成及格式重试分别记账。', '',
              'Search/Fetch按成功且非空请求0.005元/次计算；其返回内容进入后续模型时消耗的token计在模型调用项，不重复分摊。B站/小黑盒的原生读取与本地OCR、ASR、TTS没有在本账本虚构模型token。', '',
              '特殊DSH模型用量从机器人隔离会话导入，按session/seq去重，不包含人工DSH。该路径的服务端搜索/抓取费用若未暴露次数，不能据此报表认定为零；官方公告此类入口于2026-11-01起收费。', '',
              '定价快照：2026-10-29；节假日覆盖可在usage-pricing.json中维护。工作日跨高峰边界的调用按开始时刻估算。', '',
              '[DeepSeek官方价格](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)']
    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--day', default='today', help='today/yesterday/YYYY-MM-DD, Beijing time')
    parser.add_argument('--hours', type=float)
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()
    start,end = window(args.day,args.hours)
    result = report(start,end)
    print(json.dumps(result,ensure_ascii=False,indent=2) if args.json else markdown(result))


if __name__ == '__main__':
    main()

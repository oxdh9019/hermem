#!/usr/bin/env python3
"""Intent 覆盖率测试：从历史 session 提取 Oliver 的消息并分类"""
import json, sys, os
from collections import Counter

sys.path.insert(0, '.')
from impl.intent_classifier import IntentClassifier

SESSION_DIR = '/Users/oliver/.hermes/sessions'

# 收集所有 Oliver 消息
messages = []
for fname in sorted(os.listdir(SESSION_DIR)):
    if not fname.endswith('.jsonl'):
        continue
    fpath = os.path.join(SESSION_DIR, fname)
    with open(fpath, 'r') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except:
                continue
            role = obj.get('role', '')
            content = obj.get('content', '')
            platform = obj.get('platform', '')
            # Oliver 的消息：role=user 且来自飞书/微信/本地
            if role == 'user' and content and len(content) > 3:
                # 排除系统消息格式
                if content.startswith('[') or '```' in content:
                    continue
                messages.append(content.strip())

print(f'提取 Oliver 消息: {len(messages)} 条\n')

# 分类
classifier = IntentClassifier()
counts = Counter()
samples = {}

for msg in messages:
    intent = classifier.classify(msg)
    counts[intent] += 1
    if len(samples.get(intent, [])) < 2:
        samples.setdefault(intent, []).append(msg[:80])

total = len(messages)
print('=== Intent 分布 ===')
for intent, cnt in counts.most_common():
    pct = 100 * cnt / total
    bar = '█' * int(pct * 2)
    print(f'  {intent:12s}: {cnt:3d} ({pct:5.1f}%) {bar}')
    if samples.get(intent):
        print(f'    → "{samples[intent][0][:70]}"')

other_pct = 100 * counts.get('other', 0) / total
print()
print(f'"other" 比例: {other_pct:.1f}%  ', end='')
if other_pct < 10:
    print('✅ 覆盖率可接受')
elif other_pct < 30:
    print('⚠️ 有一定未覆盖内容')
else:
    print('❌ 覆盖率不足，需补充意图清单')

# other 样本
other_msgs = [m[:80] for m in messages if classifier.classify(m) == 'other']
if other_msgs:
    print()
    print(f'=== other 样本（前15条）===')
    for m in other_msgs[:15]:
        print(f'  "{m}"')

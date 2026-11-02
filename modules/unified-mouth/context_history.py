"""Past searches are execution context, not future conversation history."""
import copy

REASONING_FIELDS = {'reasoning_content', 'reasoning', 'reasoning_details', 'thinking'}
REASONING_PARTS = {'think', 'thinking', 'reasoning', 'redacted_thinking'}


def conversation_only(messages):
    result, kept = [], []
    for i, original in enumerate(messages):
        if original.get('role') in ('tool', 'function') or original.get('tool_calls') or original.get('function_call'):
            continue
        m = copy.deepcopy(original)
        if m.get('role') == 'assistant':
            for field in REASONING_FIELDS:
                m.pop(field, None)
            if isinstance(m.get('content'), list):
                m['content'] = [p for p in m['content']
                                if not isinstance(p, dict) or p.get('type') not in REASONING_PARTS]
            if not m.get('content'):
                continue
        result.append(m)
        kept.append(i)
    return result, kept

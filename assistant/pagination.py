"""Workspace-owned read helpers. No retained data or process-wide result cache."""
import hashlib
import json


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def page(value, args):
    """Page a JSON pointer, rereading and checking the source on each continuation. The fingerprint covers the pointer and the node
    it selects, not the whole answer: a field elsewhere (an overview's `generated_at`, a running job's progress) never stops a
    continuation, while any change to the paged node itself still does (2026-10-09 review)."""
    pointer = args.get('pointer', '')
    if not isinstance(pointer, str) or (pointer and not pointer.startswith('/')):
        raise ValueError('Use a JSON pointer such as /entries or /entries/0/body_text.')
    selected = value
    try:
        for part in pointer.split('/')[1:]:
            part = part.replace('~1', '/').replace('~0', '~')
            if isinstance(selected, list) and not (part.isascii() and part.isdigit()):
                raise ValueError('Array indexes must be nonnegative.')
            selected = selected[int(part)] if isinstance(selected, list) else selected[part]
    except (KeyError, IndexError, TypeError):
        raise ValueError('That source field no longer exists. Read its parent again.') from None
    digest = fingerprint({'pointer': pointer, 'node': selected})
    if args.get('fingerprint') and args['fingerprint'] != digest:
        raise ValueError('ASSISTANT: this source changed. Start a fresh read before continuing.')
    offset, limit = args.get('offset', 0), args.get('limit', 40)
    maximum = 6000 if isinstance(selected, str) else 60
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= maximum:
        raise ValueError(f'Use a nonnegative offset and a limit from 1 to {maximum}.')
    if isinstance(selected, dict):
        entries = list(selected.items())
        result = dict(entries[offset:offset + limit])
    elif isinstance(selected, (list, str)):
        entries = selected
        result = entries[offset:offset + limit]
    else:
        entries, result = [selected], selected
    total = len(entries)
    if offset > total:
        raise ValueError('The offset is past the end of this source field.')
    return {'data': result, 'page': {'pointer': pointer, 'offset': offset, 'total': total,
            'next_offset': offset + limit if offset + limit < total else None, 'fingerprint': digest}}

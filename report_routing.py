"""Presentation-only navigation for two independent historical daily snapshots.

Read existing report/JSON times; never generate forecasts or refresh evidence.
An ``ok`` result means the snapshot's displayed times correspond, not that the
snapshot is current or that coverage, prediction, or profitability has passed.
"""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re

BEGIN = b'<!-- DOGE-P1-6-LEGACY-NOTICE:BEGIN -->\n'
END = b'<!-- DOGE-P1-6-LEGACY-NOTICE:END -->\n\n'
TOKEN = b'<!-- DOGE-P1-6-LEGACY-NOTICE:'
REPORTS = (
    ('reports/DOGE_SPOT_PLUS_DAILY.md',
     'data/stage5/spot_plus/evolution_latest.json',
     ('generated_at_utc', 'reporting_as_of_utc'),
     r'(?:\*\*运行UTC[：:]\*\*|本次报告截至)\s*([^\s。]+)', '旧 Plus 兼容研究轨'),
    ('reports/DOGE_FORWARD_VALIDATION_V2_DAILY.md',
     'data/stage5/forward_validation_v2/daily_latest.json',
     ('audited_at_utc',), r'截止\s*UTC[：:]\s*([^\s]+)', 'V2 独立日报快照'),
)


def _without_notice(raw):
    if TOKEN not in raw:
        return raw
    if (not raw.startswith(BEGIN) or raw.count(TOKEN) != 2
            or raw.count(BEGIN) != 1 or raw.count(END) != 1):
        raise ValueError('malformed owned legacy notice; original bytes retained')
    return raw.split(END, 1)[1]


def _time(value, now, prefix, reasons):
    if value is None or value == '':
        reasons.append(prefix + '_time_missing')
        return None
    try:
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if stamp.tzinfo is None or stamp.utcoffset() != timedelta(0):
            raise ValueError('aware UTC required')
    except (AttributeError, TypeError, ValueError, OverflowError):
        reasons.append(prefix + '_time_invalid')
        return None
    if stamp > now:
        reasons.append(prefix + '_time_future')
    return stamp


def _shown(value):
    if value is None or value == '':
        return '缺失'
    # Source metadata cannot inject another notice or new Markdown sections.
    return str(value).replace('\r', '\\r').replace('\n', '\\n').replace('<', '&lt;').replace('>', '&gt;')


def _prepare(root, relative, source_relative, fields, pattern, scope, now):
    path = root / relative
    if (root / 'reports').is_symlink() or path.is_symlink():
        raise ValueError('report notice cannot replace a symbolic link')
    raw = path.read_bytes() if path.exists() else b''
    body = _without_notice(raw)
    reasons = []
    body_stamps = []
    if not body.strip():
        reasons.append('body_missing')
    else:
        try:
            text = body.decode('utf-8-sig')
        except UnicodeDecodeError:
            reasons.append('body_invalid_utf8')
        else:
            matches = re.findall(pattern, text)
            if not matches:
                reasons.append('body_time_missing')
            for value in matches:
                stamp = _time(value, now, 'body', reasons)
                if stamp is not None:
                    body_stamps.append(stamp)
            if len(set(body_stamps)) > 1:
                reasons.append('body_times_inconsistent')
    body_stamp = body_stamps[0] if body_stamps else None
    source = {}
    try:
        source = json.loads((root / source_relative).read_bytes())
        if not isinstance(source, dict):
            raise ValueError('object required')
    except FileNotFoundError:
        reasons.append('source_missing')
        source = {}
    except (ValueError, UnicodeError):
        reasons.append('source_invalid_json')
        source = {}
    except OSError:
        reasons.append('source_unreadable')
    source_status = source.get('status')
    if source_status != 'ok':
        reasons.append('source_status_not_ok')
    version = source.get('protocol_id') if len(fields) == 1 else source.get('schema_version')
    if not isinstance(version, (str, int)) or isinstance(version, bool) or version == '':
        reasons.append('source_version_missing')
        version = None
    json_cutoffs = {field: source.get(field) for field in fields}
    source_stamps = []
    for value in json_cutoffs.values():
        stamp = _time(value, now, 'source', reasons)
        if stamp is not None:
            source_stamps.append(stamp)
    if len(set(source_stamps)) > 1:
        reasons.append('source_times_inconsistent')
    if body_stamp is not None and any(stamp != body_stamp for stamp in source_stamps):
        reasons.append('body_source_time_mismatch')
    reasons = list(dict.fromkeys(reasons))
    status = 'NOT VERIFIED' if reasons else 'ok'
    body_cutoff = body_stamp.isoformat() if body_stamp else None
    lines = [
        '# 历史 / 独立日报快照，非当前入口', '',
        '当前导航：[仓库唯一当前报告入口](../README.md#current-reports)。'
        '正式当前成绩见 [V2 正式前瞻报告](DOGE_FORWARD_VALIDATION_V2.md)，各自截止以对应 JSON 为准。', '',
        '以下完整正文按原字节保留；其中“即时”“current”“PASS”均仅指原报告时点。'
        '本标识不会刷新来源、合并统计或改变原结论。', '',
        '- 范围：' + scope + '；不能作为当前状态或滚动 168 小时 P0-2 验收。',
        '- 正文截止 UTC：' + _shown(body_cutoff) + ('；原报告正文缺失。' if 'body_missing' in reasons else '。'),
        '- 配套 JSON：`../' + source_relative + '`；来源状态：' + _shown(source_status) + '；版本：' + _shown(version) + '。',
    ]
    lines.extend('- JSON ' + field + '：' + _shown(value) + '。' for field, value in json_cutoffs.items())
    lines.append('- 时间与来源对应核对：' + ('NOT VERIFIED（降级）：' + ', '.join(reasons) if reasons else '可对应；不代表当前仍有效') + '。')
    notice = BEGIN + ('\n'.join(lines) + '\n').encode('utf-8') + END
    output = notice + body
    row = {'status': status, 'body_cutoff_utc': body_cutoff, 'json_cutoffs': json_cutoffs,
           'source_file': source_relative, 'source_status': source_status,
           'version': version, 'scope': scope, 'reasons': reasons, 'changed': output != raw}
    return path, output, row


def annotate_legacy_reports(root, now=None):
    """Annotate only the two legacy reports, retaining every original body byte.

    All marker parsing finishes before either write. Malformed owned markers
    abort without modifying either report. Missing evidence is displayed as
    NOT VERIFIED rather than substituted from another report or the clock.
    """
    root = Path(root).resolve()
    now = datetime.now(timezone.utc) if now is None else now
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() != timedelta(0):
        raise ValueError('notice check clock must be aware UTC')
    prepared = [_prepare(root, *spec, now) for spec in REPORTS]
    rows = {}
    for spec, (path, output, row) in zip(REPORTS, prepared):
        if row['changed']:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(output)
        rows[spec[0]] = row
    return {'status': 'NOT VERIFIED' if any(row['status'] != 'ok' for row in rows.values()) else 'ok',
            'scope': 'historical snapshot navigation only; no evidence refresh', 'reports': rows}


def main():
    result = annotate_legacy_reports(Path(__file__).resolve().parent)
    print(json.dumps(result))
    # Degraded evidence is an explicit display result, not a new production gate.
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

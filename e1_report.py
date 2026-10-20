"""Read-only engineering display, separate from frozen R2 performance."""
import html


def panel(online=True):
    try:
        import e1_forward
        return e1_forward.panel(online=online)
    except Exception as exc:
        return {'status': 'E1_UNAVAILABLE', 'registered': False,
                'error_type': type(exc).__name__, 'latest': None,
                'validated_70pct': False, 'statistical_gate_passed': False,
                'real_orders_authorized': False}


def render(value):
    if value is None:
        return ''
    esc = lambda item: html.escape(str(item), quote=True)
    parts = ['<section id="e1-engineering"><h2>E1 工程前瞻续采（独立登记，尚未绩效验收）</h2>',
             '<p>独立工程记录不并入原 R2 成绩、固定分母或历史缺口。</p>',
             '<p>状态：' + esc(value.get('status', 'E1_UNAVAILABLE')) + '</p>',
             '<p>协议：' + esc(value.get('protocol_id', '未确认')) +
             '；登记：' + ('已核实' if value.get('registered') is True else '尚未核实') +
             '；登记运行：' + esc(value.get('registration_run_id', '未确认')) + '</p>',
             '<p>独立窗口起点（UTC）：' + esc(value.get('effective_from_utc', '未确认')) + '</p>']
    latest = value.get('latest') or {}
    if latest.get('prediction_verified') is True:
        direction = {'UP': '上涨', 'DOWN': '跌／平'}.get(latest.get('direction'), '方向未知')
        parts.append('<p>最新已核实发行：' + esc(latest.get('event_id')) +
                     '；发行（UTC）：' + esc(latest.get('issued_at_utc')) +
                     '；目标（UTC）：' + esc(latest.get('target_utc')) +
                     '；方向：' + esc(direction) + '</p>')
        parts.append('<p>' + ('评分证据已核实' if latest.get('score_verified') is True else '评分尚未核实') + '</p>')
    else:
        parts.append('<p>尚无已核实的正式发行；来源与发行证据尚未确认。</p>')
    parts.append('<p>工程记录数（非绩效验收）：' + esc(value.get('counts', {})) +
                 '；独立缺口：' + esc(value.get('gaps', {})) + '</p>')
    parts.append('<p>70%尚未证明；统计验收：未通过；自动晋升：关闭；交易授权：关闭。</p></section>')
    return ''.join(parts)

"""Independent supplemental observations; never modifies frozen score protocols."""
import argparse, json, os
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request,urlopen
import source_evidence_snapshot as sources
import evidence_retention as retention
import forward_execution_evidence as execution

def capture_execution_quote(root):
    output=root/'data/forward_execution_evidence/observations.jsonl'
    forecasts=execution.load_forecasts(root/'data/p0_operations/latest.json',root/'data/direction_v42/forward_registry.csv')
    execution.activate(output)
    requested=datetime.now(timezone.utc)
    with urlopen(Request(execution.URL,headers={'User-Agent':'DOGE-Supplementary-Evidence/1.0'}),timeout=15) as response:
        raw=response.read(2_000_001)
    if len(raw)>2_000_000:
        raise ValueError('oversized response')
    return execution.capture(output,forecasts,json.loads(raw),requested,datetime.now(timezone.utc))

def run(root, capture_execution=False, receipt_config=None):
    root=Path(root)
    now=datetime.now(timezone.utc)
    result={'schema_version':1,'reporting_as_of_utc':now.isoformat(),'status':'ok',
            'verdict':'REVISE','real_orders_authorized':False,'automatic_promotion':False,
            'frozen_protocols_changed':False,'execution_capture_requested':capture_execution}
    if capture_execution:
        try:
            row=capture_execution_quote(root)
            result['execution_capture']={'status':'captured','record_sha256':row['record_sha256'],
                'observations':len(row['observations']),'captured_at_utc':row['captured_at_utc'],
                'actual_fill':False,'profit_validated':False}
        except Exception as exc:
            result.update(status='blocked',execution_error=type(exc).__name__+': '+str(exc))
    else:
        result['execution_capture']={'status':'not_requested_daily_snapshot_only'}
    snapshot=sources.capture(root)
    result.update(manifest_path=snapshot['manifest_path'],source_inventory_status=snapshot['status'])
    export_path,export=retention.export_manifest(root,root/snapshot['manifest_path'],receipt_config)
    result.update(retention_export_path=export_path.relative_to(root).as_posix(),
        retention_status=export['status'],independent_retention_verified=export['independent_retention_verified'])
    base=root/'data/supplemental_evidence'
    base.mkdir(parents=True,exist_ok=True)
    temp=base/'latest.json.tmp'
    temp.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    os.replace(temp,base/'latest.json')
    report=root/'reports/DOGE_SUPPLEMENTAL_EVIDENCE.md'
    report.parent.mkdir(parents=True,exist_ok=True)
    lines=['# DOGE 补充证据审计','',
        '**结论：REVISE。本层仅补录未来证据，不替代冻结V2/P1/P2正式成绩，也不授权交易。**','',
        f"报告统一观察时点UTC：{result['reporting_as_of_utc']}；各来源原始截止仍分别列示。",
        f"来源版本清单：[{snapshot['manifest_path']}](../{snapshot['manifest_path']})。",
        f"外部保留验证：{export['status']}；independent_retention_verified={export['independent_retention_verified']}。",
        f"公开后盘口采样：{result.get('execution_capture',{}).get('status','blocked')}；错误：{result.get('execution_error','无')}。",
        '', '已修复证据记录能力：首次可用时点及版本归档、公开后盘口深度、VWAP纸面估值及采样路径缺口、独立30天可靠性统计。',
        '实际新增预测或报价须严格晚于补充层启用与预测公开时点；历史缺失不补造。迟到记录显示排除/漏采，不改原预测。',
        '盘口深度与纸面VWAP不是实际成交；费用、延迟和冲击仍需核验，采样回撤不等于连续峰谷最大回撤。',
        '实体归属、全球强平、完整历史PIT宏观、因果效果均未获新证据，不授权对应数值特征。',
        '没有S3版本保留锁及逐对象回读凭证时，外部不可改写存证保持NOT_CONFIGURED/NOT_VERIFIED。',
        'Sheets触发器、执行详情与表格新增回读仍必须独立验收；不能用GitHub或摘要PASS替代。',
        '', '实施顺序见 [单一权威路线图](DOGE_NEXT_STAGE_IMPLEMENTATION_2026-10-12.md)。']
    report.write_text('\n'.join(lines)+'\n',encoding='utf-8')
    return result

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,default=Path(__file__).resolve().parent)
    p.add_argument('--capture-execution',action='store_true')
    p.add_argument('--receipt-config',type=Path)
    args=p.parse_args()
    config=json.loads(args.receipt_config.read_text(encoding='utf-8')) if args.receipt_config else None
    try:
        result=run(args.root,args.capture_execution,config)
        print(json.dumps(result,ensure_ascii=False))
        return int(result['status']!='ok')
    except Exception as exc:
        print(json.dumps({'status':'blocked','error':type(exc).__name__+': '+str(exc)},ensure_ascii=False))
        return 1
if __name__=='__main__':
    raise SystemExit(main())

#!/usr/bin/env python3
"""Fail-closed DOGE V4.1 research report. Evidence labels, no trading instructions."""
import csv,json,math,os
from datetime import datetime,timezone,date,timedelta
from pathlib import Path
from forecast_v41 import ROOT,BASE,load_prices,atomic,iso
OUT=ROOT/'reports/DOGE_V41_LATEST.md'
def read(relative):
 try:
  j=json.loads((ROOT/relative).read_text(encoding='utf-8'))
  return j if isinstance(j,dict) else {'status':'error','reason':'not_object'}
 except Exception as e:return {'status':'unavailable','reason':type(e).__name__}
def stamp(item):
 for k in ('collected_at_utc','observed_to_utc','observed_at_utc','ended_at_utc','run_utc','generated_utc','generated_at_utc','evaluated_at_utc','evaluated_utc','audited_at_utc'):
  if item.get(k):return str(item[k])
 return '未提供'
def age_label(item,now,hours):
 s=stamp(item)
 if s=='未提供':return '时效未知'
 try:
  dt=datetime.fromisoformat(s.replace('Z','+00:00'))
  if dt.tzinfo is None:return '时间戳缺少时区'
  lag=(now-dt).total_seconds()/3600
  return ('有效窗口内' if 0<=lag<=hours else '过期/时钟异常')+f'（采集时间 {s}）'
 except Exception:return '时间戳不可解析'
def fmt(x,digits=2):
 try:return f'{float(x):,.{digits}f}'
 except (TypeError,ValueError):return '不可用'
def main():
 now=datetime.now(timezone.utc)
 forecast=read('data/forecast_v41/latest.json')
 back=read('data/forecast_v41/backtest_latest.json')
 scoring=read('data/forecast_v41/scores.json')
 direction=read('data/direction_v42/latest.json')
 direction_forward=read('data/direction_v42/forward_latest.json')
 research=read('data/forecast_v41/calibration_research.json')
 hist=read('data/history/latest.json')
 if forecast.get('status')!='ok' or scoring.get('status')!='ok' or hist.get('status')!='ok':
  raise ValueError('critical v41 report inputs not ok')
 cutoff=hist['quality']['symbols']['DOGE-USDT']['last_utc']
 if cutoff!=forecast['anchor_date_utc'] or cutoff!=back.get('source_cutoff_utc'):
  raise ValueError('forecast/backtest historical cutoff mismatch')
 if cutoff!=(now.date()-timedelta(days=1)).isoformat():
  raise ValueError('v41 report cannot use stale daily anchor')
 if scoring.get('source_cutoff_utc')!=cutoff:raise ValueError('score source cutoff mismatch')
 times,prices=load_prices(True,now)
 prior=prices[-2] if len(prices)>1 else None
 spot=read('data/latest.json');liquidity=read('data/liquidity/latest.json')
 liquidations=read('data/liquidations/latest.json');bybit=read('data/bybit_liquidations/latest.json')
 binance=read('data/binance_liquidations/latest.json')
 whale=read('data/whales/latest.json');onchain=read('data/onchain/latest.json')
 confirmed_whales=read('data/whales_blocks/latest.json')
 por_balances=read('data/okx_address_balances/latest.json')
 chain_activity=read('data/onchain/metrics_latest.json')
 etf=read('data/etf/latest.json');macro=read('data/macro/latest.json')
 policy=read('data/policy_events/latest.json');sec=read('data/sec_events/latest.json')
 cftc=read('data/regulatory_events/latest.json')
 crash=read('data/crash_research/pilot_1h_latest.json')
 crash_live=read('data/crash_research/prospective_scores.json')
 crash_v42=read('data/crash_research/v42_scores.json')
 minute_probe=read('data/v42_microprobe/latest.json')
 quality=read('data/quality/latest.json')
 old_baseline=read('data/prob_baseline/latest.json')
 old_gate=read('data/prediction_gate/latest.json')
 prices_by_symbol={}
 with (ROOT/'data/history/daily.csv').open(newline='',encoding='utf-8') as f:
  for r in csv.DictReader(f):
   if r['symbol'] in ('DOGE-USDT','BTC-USDT','ETH-USDT'):
    prices_by_symbol.setdefault(r['symbol'],{})[int(r['open_utc_ms'])]=float(r['close'])
 ix=times[-1]
 metrics={}
 for name in ('DOGE-USDT','BTC-USDT','ETH-USDT'):
  seq=prices_by_symbol[name];nowp=seq[ix]
  metrics[name]={'last':nowp,'return_7d_pct':100*(nowp/seq[ix-7*86400000]-1),
    'return_30d_pct':100*(nowp/seq[ix-30*86400000]-1)}
 relative=100*((metrics['DOGE-USDT']['last']/metrics['BTC-USDT']['last'])/
  (prices_by_symbol['DOGE-USDT'][ix-7*86400000]/prices_by_symbol['BTC-USDT'][ix-7*86400000])-1)
 price30=prices[-30:]
 immutable_issue_times=[]
 with (ROOT/'data/forecast_v41/registry.csv').open(newline='',encoding='utf-8') as source:
  for entry in csv.DictReader(source):
   if entry.get('anchor_date_utc')==cutoff:
    immutable_issue_times.append(entry['registered_utc'])
 first_registered=min(immutable_issue_times) if immutable_issue_times else None
 prev_close_utc=datetime.combine(date.fromisoformat(cutoff)+timedelta(days=1),datetime.min.time(),timezone.utc)
 first_delay=round((datetime.fromisoformat(first_registered.replace('Z','+00:00'))-prev_close_utc).total_seconds()/60,2) if first_registered else None
 t=['# DOGE全面预测报告 V4.1｜自动研究版','','**状态：RESEARCH_ONLY / NO_VALIDATED_EDGE；非买卖指令。**',
    f'生成于：{now.isoformat()}；行情起点：OKX已收盘1Dutc {cutoff}；DOGE起点价格：{fmt(prices[-1],5)} USDT。',
    '预测对象为目标UTC日线收盘价，不是盘中最高最低价。不能把历史频率当成已校准真实概率。',
    f'**时点警示：UTC收盘基准 {forecast.get("origin_close_at_utc","未提供")}，原始预测首次不可修改登记 {first_registered or "未登记"}（日线收盘后 {first_delay if first_delay is not None else "N/A"} 分钟），本报告生成已过 {forecast.get("target_window_elapsed_before_issue_minutes","未提供")} 分钟；这不是报告发布后完整24小时预测。**','',
    '## F01｜首要目标：DOGE四周期涨跌方向预测（70%待验证）',
    '|目标UTC日|周期|P10区间低点|P50中位值|P90区间高点|历史上涨频率|历史大跌频率|',
    '|---|---|---:|---:|---:|---:|---:|']
 if direction.get('status')=='ok' and direction.get('sources',{}).get('daily_last_closed_start_utc','').startswith(cutoff):
  direction_lines=['**方向主任务：每个周期分别验收 ≥70% 的上涨/下跌判断正确率；以下仅历史研究回测，不是实时方向预测。**',
   '|周期|历史测试数|历史多数类准确率|DOGE动量|反转|BTC动量|非重叠数|',
   '|---|---:|---:|---:|---:|---:|---:|']
  for period in ('1h','24h','7d','30d'):
   histrows=direction['horizons'][period];allrows=histrows['all_overlapping'];non=histrows['fixed_phase_nonoverlap']
   direction_lines.append(f"|{period}|{allrows['n']}|{fmt(allrows['expanding_majority']['accuracy_pct'],2)}%|{fmt(allrows['doge_momentum']['accuracy_pct'],2)}%|{fmt(allrows['doge_reversal']['accuracy_pct'],2)}%|{fmt(allrows['btc_momentum']['accuracy_pct'],2)}%|{non['n']}|")
  direction_lines.append('这四类规则均未证明70%优势；测试集已经用于研发探索，不能作为一次独立最终验证。7天/30天收益相互重叠，独立样本量更少。')
  direction_lines+=['','**真实前瞻登记与到期评分（发行时价格→未来目标价格）**',
   '|目标周期|已封存|已到期评分|历史多数类准确率|反转准确率|BTC动量准确率|',
   '|---|---:|---:|---:|---:|---:|']
  for h,name in ((1,'1小时'),(24,'24小时'),(168,'7天'),(720,'30天')):
   live=direction_forward.get('horizons',{}).get(str(h)+'h',{})
   m=live.get('models',{})
   getacc=lambda key: (str(m[key]['all_scored']['accuracy_pct'])+'%') if m.get(key,{}).get('all_scored',{}).get('accuracy_pct') is not None else '尚无评分'
   direction_lines.append(f"|{name}|{live.get('total_registered',0)}|{m.get('majority',{}).get('all_scored',{}).get('n',0)}|{getacc('majority')}|{getacc('doge_reversal')}|{getacc('btc_momentum')}|")
  direction_lines.append(f'前瞻记录截至 {direction_forward.get("generated_utc","未知")}；OKX现货报价 {direction_forward.get("quote_price","未知")} USDT（报价时间 {direction_forward.get("quote_at_utc","未知")}）；状态 {direction_forward.get("status","未验证")}；漏采无法评分 {direction_forward.get("unscorable_count","未知")} 条、待评分 {direction_forward.get("pending_count","未知")} 条。NO_VALIDATED_EDGE。')
  registerfile=ROOT/'data/direction_v42/forward_registry.csv'
  if registerfile.exists():
   recent={}
   with registerfile.open(newline='',encoding='utf-8') as stream:
    for row in csv.DictReader(stream):
     h=int(row['horizon_hours'])
     if h in (1,24,168,720) and (h not in recent or row['issued_at_utc']>recent[h]['issued_at_utc']):recent[h]=row
   direction_lines+=['**最近各周期实际封存的方向判断（非实时交易建议）**',
    '|周期|实际发布时间UTC|参考价 USDT|历史多数类|反转|BTC动量|目标UTC|',
    '|---|---|---:|---|---|---|---|']
   for h,name in ((1,'1小时'),(24,'24小时'),(168,'7天'),(720,'30天')):
    if h not in recent:
     direction_lines.append(f'|{name}|未登记|—|—|—|—|—|')
    else:
     x=recent[h];as_dir=lambda k: '涨' if x[k]=='UP' else '跌/平'
     direction_lines.append(f"|{name}|{x['issued_at_utc']}|{x['reference_price']}|{as_dir('majority_direction')}|{as_dir('doge_reversal_direction')}|{as_dir('btc_momentum_direction')}|{x['target_utc']}|")
 else:
  direction_lines+=['真实前瞻方向登记暂不可用：仅展示历史探索性回测，不推断已有实时预测。']

 else:
  direction_lines=['**方向模型的本期历史审计缺失或截止时间不一致，不能发布70%准确率声明。**']
 t[-2:-2]=direction_lines+['','### 辅助：收益分布和历史价格区间（非主要验收目标）','']
 for h in (1,7,30):
  x=forecast['price_intervals'][str(h)+'d'];b=x['p10_p50_p90_usdt']
  t.append(f"|{x['target_utc']}|{h}天|{fmt(b['p10'],5)}|{fmt(b['p50'],5)}|{fmt(b['p90'],5)}|{fmt(x['historical_up_frequency_pct'],2)}%|跌幅≤{x['research_large_down_threshold_pct']}%: {fmt(x['historical_large_down_frequency_pct'],2)}%|")
 if not forecast.get('registration_window_ok',False):
  t+=['**本轮登记窗口已关闭：已有合格历史登记仍正常保留、等待到期评分；未登记的当日预测不能事后补登。**']
 t+=['','365天：仅提供宏观、网络结构及牛熊压力情景；长期独立样本不足，**不提供精确概率或已校准价格区间**。',
     '情景解释：上行条件为BTC风险环境改善、DOGE相对强弱回升且流动性确认；下行条件为BTC走弱、杠杆压力和流动性收缩。上述是条件假设，尚未验证为预测规则。',
     '所有P10/P90均来自历史经验收益分位数，尚未实现80%校准。','',
     '## F02｜OKX现货及日线/1H时效',
     f'- 已收盘DOGE日线共 {hist["quality"]["symbols"]["DOGE-USDT"]["days"]} 条，开始于 {hist["quality"]["symbols"]["DOGE-USDT"]["first_utc"]}；内部日线缺失0，历史审计重复键{hist["quality"]["duplicate_keys"]}。',
     f'- 最近DOGE日线收盘：{fmt(prices[-1],5)} USDT；相对前日变化 {fmt(100*(prices[-1]/prior-1),2) if prior else "不可用"}%。',
     f'- OKX现货快照：{age_label(spot,now,2)}。**若过期，不称为实时价格**。',
     '','## F03｜趋势、波动、相对强弱',
     f'- DOGE最近7天收盘收益：{fmt(metrics["DOGE-USDT"]["return_7d_pct"],2)}%；30天：{fmt(metrics["DOGE-USDT"]["return_30d_pct"],2)}%。',
     f'- BTC最近7天收盘收益：{fmt(metrics["BTC-USDT"]["return_7d_pct"],2)}%；ETH最近7天：{fmt(metrics["ETH-USDT"]["return_7d_pct"],2)}%。',
     f'- DOGE/BTC七日相对强弱变化：{fmt(relative,2)}%。',
     f'- DOGE最近30根日线**收盘参考范围**：{fmt(min(price30),5)}–{fmt(max(price30),5)} USDT（非已验证支撑阻力位）。',
     '','## F04｜宏观、ETF、政策与催化事件',
     f'- Farside ETF资金流：状态 {etf.get("status","不可用")}；最近BTC/ETH交易日 {etf.get("latest_trade_date",{})}；存档记录 {etf.get("stored_rows","未知")}；修订和首次发布可见时点未完全验证。',
     f'- FRED宏观数据：状态 {macro.get("status","不可用")}；{age_label(macro,now,48)}。观察日期不等于市场首次获知时间。',
     f'- Fed/BLS政策及日历：状态 {policy.get("status","不可用")}，记录 {policy.get("records","未知")}；日程未等于真实公告。',
     f'- SEC/CFTC公告：状态分别为 {sec.get("status","不可用")} / {cftc.get("status","不可用")}。未完成前瞻效应检验。',
     '','## F05｜永续衍生品及强平',
     f'- OKX衍生品/盘口：{age_label(liquidity,now,2)}；OI约 {fmt(liquidity.get("derivatives",{}).get("open_interest_usd"),0)} USDT；资金费率 {fmt(100*float(liquidity["derivatives"]["funding_rate"]),5) if liquidity.get("derivatives",{}).get("funding_rate") is not None else "不可用"}%。',
     f'- OKX强平通知：{age_label(liquidations,now,2)}，状态 {liquidations.get("status","不可用")}；这是**部分监听窗口**，不是全市场总额。',
     f'- Bybit/Binance采样状态：{bybit.get("status","不可用")} / {binance.get("status","不可用")}；0条样本不等于无爆仓。',
     '','## F06｜巨鲸、交易所地址与流动性',
     f'- **确认区块大额转账**：{confirmed_whales.get("status","不可用")}；已累计记录 {confirmed_whales.get("cumulative_unique_whale_transactions","未知")} 条≥100万DOGE链上转账；扫描区块高度至 {confirmed_whales.get("last_completed_height","未知")}；采集 {stamp(confirmed_whales)}。仅覆盖自检查点起的已扫描区块，不代表全历史或独立巨鲸人数。',
     f'- **旧SSE抽样（单独列示）**：{whale.get("status","不可用")}；累计验证 {whale.get("cumulative_verified_unique_transfers","未知")} 条；{age_label(whale,now,8)}。不可与确认区块累计量直接相加。',
     f'- **OKX PoR地址余额抽样**：状态 {por_balances.get("status","不可用")}；已采样地址 {por_balances.get("sampled_addresses_ever","未知")}/{por_balances.get("addresses_in_allowlist","未知")}（{por_balances.get("sampled_fraction_pct","未知")}%），PoR静态披露日期 {por_balances.get("por_snapshot_date_utc","未知")}；余额变化不能直接换算交易所充值、提现或卖压。',
     '- 交易所地址标签及净转入转出尚无可审计的全面覆盖。未知转账不可视作抛售。',
     f'- 当前盘口指标仅参考已标注时点的OKX快照；数据质量不合格时不计算执行滑点。',
     '','## F07｜DOGE原生链健康',
     f'- 状态 {onchain.get("status","不可用")}；{age_label(onchain,now,36)}；主网区块高度 {onchain.get("metrics",{}).get("chain_height","不可用")}。',
     f'- 前10大地址供应占比 {fmt(onchain.get("metrics",{}).get("richlist_top10_pct_of_supply"),2)}%；地址包含交易所与托管可能，不等于10个独立巨鲸。',
     f'- **原生链日度活动**：源状态 {chain_activity.get("status","不可用")}；已完成日期 {chain_activity.get("latest_complete_core_day_utc","未知")}；当日活跃地址 {fmt(chain_activity.get("daily_activity_latest",{}).get("active_addresses"),0)}，交易笔数 {fmt(chain_activity.get("daily_activity_latest",{}).get("tx_count"),0)}；历史日期数 {chain_activity.get("daily_observations","未知")}。活跃地址不等于独立用户数。',
     '- UTXO找零、网络活跃地址等指标仍需去重和历史口径统一。',
     '','## F08｜新闻与市场情绪',
     '当前缺乏可审计、连续、同一时间点可见的情绪数据，**本章数据不足，不生成情绪评分**。',
     '','## F09｜上下行风险和反证',
     '- 下行条件：BTC持续走弱、DOGE/BTC相对强弱恶化、盘口收缩或合约风险显著增加。',
     '- 上行条件：BTC企稳、DOGE相对强弱改善、成交/流动性得到确认。',
     '- 上述条件属于待检验的风险监控规则，不是已经验证的预测因子。',
     f'- **V4.2暴跌风险辅助评分（不替代主任务）**：状态 {crash_v42.get("status","不可用")}；历史登记 {crash_v42.get("registered","未知")}，目标到期 {crash_v42.get("scored_count","未知")}，待评分 {crash_v42.get("pending_count","未知")}；真实预警精确率 {(str(crash_v42["alert_precision_pct"])+"%") if crash_v42.get("alert_precision_pct") is not None else "样本不足"}，真实暴跌事件召回率 {(str(crash_v42["event_recall_pct"])+"%") if crash_v42.get("event_recall_pct") is not None else "样本不足"}，模型授权 {crash_v42.get("approval","BLOCKED")}。必须先在实际发出后等待至少1小时，才允许计算随后K线的暴跌命中。',
     f'- **V4.2分钟级可行性**：{minute_probe.get("status","未验证")}；样本 {minute_probe.get("total_verified_sample_rows","未知")}条；源采集 {minute_probe.get("observed_utc","未提供")}。仅为小时采样的1m/5m确认K线测试，非连续分钟级流和已验证风险预测。',
     f'- **真实未来1小时监测**：{crash_live.get("status","未建立")}；已登记 {crash_live.get("registered","未知")} 条观察，成熟评分 {len(crash_live.get("scored",[]))} 条；当前精确率 {(str(crash_live["precision_pct"])+"%") if crash_live.get("precision_pct") is not None else "样本不足"}，召回率 {(str(crash_live["recall_pct"])+"%") if crash_live.get("recall_pct") is not None else "样本不足"}；阈值在10月9日暴跌后选择，前瞻积累不足，**不构成70%通过证据**。',
     f'- 1小时极端暴跌历史研究：{crash.get("status","unavailable")}；计算时间 {stamp(crash)}，历史K线截止 {crash.get("history_last_start_utc","未知")}；有效1H样本 {crash.get("hourly_rows_by_symbol","未知")}小时，≥5%小时暴跌 {crash.get("crash_count_total","未知")}次，规则事后触发 {crash.get("alert_count","未知")}次；精确率 {crash.get("precision_pct","不可用")}%、召回率 {crash.get("recall_pct","不可用")}%、误报 {crash.get("false_alerts_per_week","不可用")} 次/周，命中事件提前 {", ".join(str(n) for n in crash.get("warning_lead_hours_for_caught_events",[])) or "无有效事件"} 小时。这是事后选参研究，不是独立测试或可靠预警。',
     '','## F10｜历史回测与前瞻兑现',
     '|周期|滚动样本外窗口数|经验P10–P90覆盖率|滚动区间分数|扩展历史基线区间分数|非重叠窗口数|',
     '|---|---:|---:|---:|---:|---:|']
 for h in (1,7,30):
  x=back['horizons'][str(h)+'d'];full=x['all_anchors_overlapping'];non=x['nonoverlap_fixed_start_phase']
  t.append(f"|{h}天|{full['n']}|{fmt(full['rolling']['coverage_pct'],2)}%|{fmt(full['rolling']['mean_interval_score_log_pct'],3)}|{fmt(full['expanding']['mean_interval_score_log_pct'],3)}|{non['n']}|")
 if research.get('status')=='ok' and research.get('history_cutoff_utc')==cutoff:
  t+=['','### 区间校准研究候选（不自动上线）',
      '|周期|原模型区间评分|PIT校准候选评分|候选覆盖率|裁决|',
      '|---|---:|---:|---:|---|']
  for h in (1,7,30):
   r=research['horizons'][str(h)+'d']['all_overlapping']
   old=r['baseline'];new=r['challenger']
   t.append(f"|{h}天|{fmt(old['interval_score_log_pct'],3)}|{fmt(new['interval_score_log_pct'],3)}|{fmt(new['coverage_pct'],2)}%|研究中／无自动晋级|")
  t.append('校准回测不是全新未碰触测试集；多次调参与周期目标相关性仍可能造成过拟合。')
 else:t+=['历史区间校准候选：未提供同步的有效研究文件；不推断新模型优于原模型。']
 t+=['覆盖目标为80%，过高也可能意味着区间过宽；区间分数越低越好，但历史回测未证明未来优势。',
     f'真实前瞻：登记 {scoring["registered"]} 条，已评分 {scoring["counts"]["scored"]} 条，待评分 {scoring["counts"]["pending"]} 条，隔离 {scoring["counts"]["quarantined"]} 条。**NO_VALIDATED_EDGE**。',
     '','## F11｜来源、缺失、时效及最终审计',
     f'- OKX日线截止：{cutoff} UTC；OKX市场快照：{stamp(spot)}；衍生品：{stamp(liquidity)}；旧巨鲸SSE：{stamp(whale)}；确认区块扫描：{stamp(confirmed_whales)}；PoR：{stamp(por_balances)}；链上日度活动：{stamp(chain_activity)}；原始链快照：{stamp(onchain)}。',
     f'- ETF：{stamp(etf)}；FRED：{stamp(macro)}；政策日历：{stamp(policy)}；样本外评分：{stamp(scoring)}。',
     '- ①链上/巨鲸/交易所净流：PARTIAL；②强平/流动性：PARTIAL；③ETF：采集PASS；④宏观政策：采集PASS/因果效应未验证；⑤历史/预测：工程PASS/预测有效性未验证。',
     f'- **独立数据质量门禁**：状态 {quality.get("status","未运行")}，报告时记录 {stamp(quality)}；关键故障 {quality.get("critical_failures","未知")}，可选数据告警 {quality.get("optional_source_warnings","未知")}。对已过期文件不能凭历史PASS宣称实时。',
     f'- **旧模型审计时间一致性**：旧多因子/概率基线截止 {old_baseline.get("data_end_utc","不可用")}，当前DOGE日线截止 {cutoff}；'+('旧模型统计滞后：STALE_NOT_USED_FOR_V41。' if old_baseline.get('data_end_utc')!=cutoff else '旧基线时间匹配，但不代表预测优势。')+f' 旧预测门禁状态 {old_gate.get("status","未知")}，不决定V4.1有效性。',
     f'- V4.2实测窗口审计时间：{stamp(crash_v42)}；1m/5m可行性样本时间：{minute_probe.get("observed_utc","无")}。V4.2的70%资格门禁保持BLOCKED，历史回测不能替代前瞻评分。',
     '- **Google Sheets/Apps Script单独验收**：本GitHub报告不代表Apps Script触发、执行记录及表格回读已验证；禁止合并声称成功。',
     '- 数据事实与情景判断严格区分；本报告不修改DOGE定投执行规则。',
     '**综合裁决：RESEARCH_ONLY / NO_VALIDATED_EDGE。**',
     '']
 atomic(OUT,'\n'.join(t))
 print(json.dumps({'status':'ok','report':str(OUT),'anchor':cutoff,'sections':11}))
 return 0

if __name__=='__main__':
 try:raise SystemExit(main())
 except Exception as exc:
  print(json.dumps({'status':'blocked','error':type(exc).__name__+': '+str(exc)[:400]}))
  raise

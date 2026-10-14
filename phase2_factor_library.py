"""Fixed causal price hypotheses. Historical close availability is not PIT proof."""
import numpy as np
import phase1_research as prior

NAMES=['doge_ret_1h','doge_ret_4h','doge_ret_12h','doge_ret_24h','doge_ret_72h',
       'doge_vol_12h','doge_vol_24h','doge_range_24h',
       'doge_volume_ratio_6h','doge_volume_ratio_24h','doge_volume_ratio_72h',
       'btc_ret_1h','btc_ret_4h','btc_ret_24h','btc_ret_72h',
       'doge_minus_btc_ret_1h','doge_minus_btc_ret_24h']
GROUPS={'doge_momentum':list(range(5)),'doge_volatility':[5,6,7],
        'doge_volume':[8,9,10],'btc_context':[11,12,13,14],'relative_returns':[15,16]}


def variants():
    names=list(GROUPS)
    return {'full':names,**{'drop_'+g:[n for n in names if n!=g] for g in names},
            **{'only_'+g:[g] for g in names}}


def rows(frame,h,mode):
    if type(h) is not int or h not in (1,24):raise ValueError('1H/24H required')
    base=prior.features(frame,h,mode)
    for symbol in ('DOGE-USDT','BTC-USDT'):
        volume=np.asarray(frame[symbol]['volume'],dtype=float)
        if not np.isfinite(volume).all() or (volume<0).any():raise ValueError('invalid source volume')
    d=np.log(np.asarray(frame['DOGE-USDT']['close'],dtype=float))
    b=np.log(np.asarray(frame['BTC-USDT']['close'],dtype=float))
    v=np.asarray(frame['DOGE-USDT']['volume'],dtype=float)
    answer=[None]*len(base)
    for i in range(72,len(base)-h):
        if base[i] is None:continue
        if mode=='receipt_time' and any(frame[s]['seen'][j] is None or frame[s]['seen'][j]>base[i]['anchor_ms']
                    for s in ('DOGE-USDT','BTC-USDT') for j in range(i-72,i+1)):continue
        means=[float(v[i-lag:i].mean()) for lag in (6,24,72)]
        if min(means)<=0:continue
        x=[d[i]-d[i-k] for k in (1,4,12,24,72)]
        x.extend([float(np.std(np.diff(d[i-12:i+1]))),float(np.std(np.diff(d[i-24:i+1]))),
                  float(np.max(d[i-24:i+1])-np.min(d[i-24:i+1]))])
        x.extend(float(v[i]/mean-1) for mean in means)
        x.extend(b[i]-b[i-k] for k in (1,4,24,72))
        x.extend((d[i]-d[i-k])-(b[i]-b[i-k]) for k in (1,24))
        answer[i]={**base[i],'x':prior.canonical([float(q) for q in x])}
    return answer

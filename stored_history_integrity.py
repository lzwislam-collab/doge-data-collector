"""Full stored-candle validation, shared by non-frozen collection entrypoints."""
from datetime import datetime,timedelta
from decimal import Decimal,InvalidOperation

SYMBOLS=('DOGE-USDT','BTC-USDT','ETH-USDT')
VALUES=('open','high','low','close','volume_base')

def numbers(row):
 try:values=[Decimal(str(row[k])) for k in VALUES]
 except (KeyError,InvalidOperation,TypeError,ValueError) as exc:raise ValueError('invalid stored candle number') from exc
 if any(not v.is_finite() for v in values):raise ValueError('nonfinite stored candle')
 o,h,l,c,v=values
 if min(o,h,l,c)<=0 or v<0 or h<max(o,c,l) or l>min(o,c,h):raise ValueError('invalid stored OHLCV')
 return values

def validate(row,step,source,now_ms,receipt=False):
 if row.get('symbol') not in SYMBOLS or row.get('source')!=source:raise ValueError('unknown stored candle symbol/source')
 try:stamp=int(row['open_utc_ms'])
 except (ValueError,TypeError,KeyError) as exc:raise ValueError('invalid stored candle timestamp') from exc
 if str(stamp)!=str(row['open_utc_ms']) or stamp<0 or stamp%step or stamp+step>now_ms:raise ValueError('unaligned/future/unclosed stored candle')
 numbers(row)
 if receipt:
  try:at=datetime.fromisoformat(row['first_ingested_utc'])
  except (ValueError,TypeError,KeyError) as exc:raise ValueError('missing/invalid stored ingestion time') from exc
  if at.utcoffset()!=timedelta(0) or not stamp+step<=at.timestamp()*1000<=now_ms:raise ValueError('non-UTC/early/future stored ingestion time')
 return row

def same_candle(old,new):
 if numbers(old)!=numbers(new):raise ValueError('confirmed stored candle revision conflict; original history preserved')

"""Research-only, point-in-time news event triage. Never emits price directions or orders.

Consumes existing append-only enhancement_sources_v1 observations. Does not touch the
frozen original18/R2 or earlier NEWS_TONE protocols. No new network calls.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

NEWS_SOURCES = frozenset({"FED_NEWS", "ECB_NEWS", "CRYPTO_NEWS"})
TOKENS = {
    "DOGE_SPECIFIC": (r"\bdogecoin\b", r"\bdoge\b", r"\belon musk\b", r"\b(?:x|twitter) payments?\b"),
    "MARKET_INFRASTRUCTURE": (r"\bhack(?:ed|ing)?\b", r"\bexploit\b", r"\bbankrupt(?:cy)?\b", r"\binsolvenc(?:y|ies)\b", r"\bwithdrawals? (?:halted|suspended|paused)\b", r"\bdepeg(?:ged|ging)?\b", r"\boutage\b", r"\bsecurity breach\b"),
    "REGULATION": (r"\bsec\b", r"\bcftc\b", r"\bregulat(?:ion|or|ory)\b", r"\bcrypto(?:currency)? (?:ban|lawsuit|rule|bill|law)\b", r"\betf (?:approved|approval|rejected|rejection)\b"),
    "MACRO_POLICY": (r"\bfederal reserve\b", r"\bfed (?:raises?|cuts?|rate|policy|meeting)\b", r"\binterest rates?\b", r"\bcpi\b", r"\binflation\b", r"\bnonfarm payroll\b", r"\bjobs report\b", r"\becb\b"),
    "GEOPOLITICAL": (r"\bsanctions?\b", r"\btariffs?\b", r"\bmilitary strike\b", r"\bwar (?:starts?|breaks? out|escalates?)\b"),
}
NEG = tuple(re.compile(x, re.I) for x in (r"\bhack(?:ed)?\b",r"\bexploit\b",r"\bban(?:ned)?\b",r"\breject(?:ed|ion)?\b",r"\bcrash\b",r"\bdepeg(?:ged)?\b",r"\bhalt(?:ed)?\b",r"\battack\b",r"\bdefault\b",r"\bbankrupt(?:cy)?\b",r"\blawsuit\b",r"\bsanction(?:s|ed)?\b"))
POS = tuple(re.compile(x, re.I) for x in (r"\bapprov(?:e|ed|al)\b",r"\blaunch(?:ed)?\b",r"\badopt(?:ed|ion)?\b",r"\bintegrat(?:e|ed|ion)\b",r"\brecover(?:y|ed)\b"))
RULES = {k:tuple(re.compile(v, re.I) for v in ps) for k,ps in TOKENS.items()}
PRIORITY = ("MARKET_INFRASTRUCTURE", "DOGE_SPECIFIC", "REGULATION", "MACRO_POLICY", "GEOPOLITICAL")


def timestamp(value: str) -> datetime:
    if not value or not isinstance(value, str):
        raise ValueError("missing_timestamp")
    d = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if d.tzinfo is None: raise ValueError("naive_timestamp")
    return d.astimezone(timezone.utc)


def canonical_url(value: str) -> str:
    if not isinstance(value,str) or not value.startswith(("https://","http://")):
        raise ValueError("invalid_news_url")
    p = urlsplit(value)
    if not p.hostname: raise ValueError("invalid_news_host")
    q = [(k,v) for k,v in parse_qsl(p.query,keep_blank_values=True)
         if not (k.lower().startswith('utm_') or k.lower() in {'fbclid','gclid'})]
    return urlunsplit((p.scheme.lower(), p.netloc.lower(),p.path.rstrip('/') or '/',urlencode(sorted(q)),''))


def classify(title: str, source: str) -> dict:
    if not isinstance(title,str): raise ValueError("title_required")
    tags=[kind for kind,ps in RULES.items() if any(p.search(title) for p in ps)]
    # Source alone cannot certify a headline's event nature or future DOGE impact.
    if source == "FED_NEWS" and "MACRO_POLICY" not in tags: tags.append("MACRO_POLICY")
    if source == "ECB_NEWS" and "MACRO_POLICY" not in tags: tags.append("MACRO_POLICY")
    adverse=any(p.search(title) for p in NEG)
    favorable=any(p.search(title) for p in POS)
    polarity="MIXED" if adverse and favorable else "NEGATIVE_WORDING" if adverse else "POSITIVE_WORDING" if favorable else "UNSPECIFIED"
    return {"event_types":sorted(tags),"primary_type":next((k for k in PRIORITY if k in tags),"UNCLASSIFIED"),
            "wording":polarity,"market_direction":"UNKNOWN","verified_event_impact":False,
            "macro_surprise":"UNKNOWN","critical_watch_only":bool(set(tags)&{"MARKET_INFRASTRUCTURE","DOGE_SPECIFIC","REGULATION","MACRO_POLICY","GEOPOLITICAL"})}


def build_report(rows: list[dict], asof: datetime, *, max_age_hours: float = 24.0,
                 expected_sources: frozenset[str] = NEWS_SOURCES) -> dict:
    if asof.tzinfo is None: raise ValueError('asof_must_be_timezone_aware')
    asof=asof.astimezone(timezone.utc)
    dedup={}
    errors=Counter()
    latest_seen={}
    scanned=0
    for row in rows:
        source=row.get('source_id')
        if source not in expected_sources: continue
        scanned += 1
        try:
            published=timestamp(row['published_at_utc'])
            usable=timestamp(row['usable_from_utc'])
            seen=timestamp(row['first_seen_utc'])
            # Publication is not enough: exact historical FIRST observer receipt controls PIT.
            if published>asof or seen>asof or usable>asof: continue
            if usable<seen or usable<published:
                errors['clock_conflict']+=1;continue
            age=asof-max(usable,published,seen)
            if age<timedelta(0): continue
            url=canonical_url(row['url'])
            title=row.get('title','')
            if not title.strip():errors['missing_title']+=1;continue
            if not row.get('observation_key') or not row.get('raw_sha256'):
                errors['missing_source_identity']+=1;continue
            key=(source,str(row['observation_key']))
            old=dedup.get(key)
            if old is not None and usable<=old['usable']:
                continue
            dedup[key]={"source":source,"title":title,"url":url,"usable":usable,"first_seen":seen,
                        "published":published,"raw_sha256":row['raw_sha256'],
                        "revision_of_sha256":row.get('revision_of_sha256'),"observation_key":key[1]}
            if source not in latest_seen or usable>latest_seen[source]:latest_seen[source]=usable
        except (ValueError,KeyError,TypeError,OverflowError) as e:
            errors[str(e)[:80] or 'invalid_row']+=1
    # Cross-provider copies with same canonical link collapse into one observable event.
    by_url={}
    for r in dedup.values():
        key=r['url'];old=by_url.get(key)
        if old is None or (r['usable'],r['source']) < (old['usable'],old['source']):by_url[key]=r
    events=[]
    for r in sorted(by_url.values(), key=lambda e:(e['usable'],e['url'])):
        c=classify(r['title'],r['source'])
        events.append({"event_id":hashlib.sha256(r['url'].encode()).hexdigest(),
          "first_usable_utc":r['usable'].isoformat(),"source":r['source'],
          "source_key":r['observation_key'],"raw_sha256":r['raw_sha256'],
          "published_at_utc":r['published'].isoformat(),"title":r['title'],
          **c})
    ages={s:round((asof-t).total_seconds()/3600,3) for s,t in sorted(latest_seen.items())}
    stale=sorted(s for s in expected_sources if s not in latest_seen or ages[s]>max_age_hours)
    return {"schema":"NEWS_EVENT_RESEARCH_V2_PIT_DRY_RUN","asof_utc":asof.isoformat(),
      "research_only":True,"prediction_direction":None,"production_model_changed":False,
      "news_sources":sorted(expected_sources),"source_age_hours":ages,"missing_or_stale_sources":stale,
      "input_news_rows":scanned,"distinct_provider_records":len(dedup),"deduplicated_event_count":len(events),
      "critical_watch_count":sum(bool(e['critical_watch_only']) for e in events),
      "negative_wording_count":sum(e['wording']=='NEGATIVE_WORDING' for e in events),
      "positive_wording_count":sum(e['wording']=='POSITIVE_WORDING' for e in events),
      "rejected_rows":dict(errors),"coverage_sufficient_for_directional_claim":False,
      "unverified_source_scope":"RSS sources only, not comprehensive news/X/social coverage",
      "events":events}


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',default='data/enhancement_sources_v1/observations.jsonl')
    parser.add_argument('--asof',required=True,help='UTC ISO8601 point-in-time cutoff')
    parser.add_argument('--max-age-hours',type=float,default=24)
    a=parser.parse_args()
    if not 0<a.max_age_hours<=168:parser.error('max-age-hours must be 0-168')
    with Path(a.input).open(encoding='utf-8') as stream:
        rows=[json.loads(s) for s in stream if s.strip()]
    print(json.dumps(build_report(rows,timestamp(a.asof),max_age_hours=a.max_age_hours),ensure_ascii=False,indent=2))
    return 0

if __name__=='__main__':raise SystemExit(main())

// Shared production/self-test delivery. It never writes prediction or market data.
const hash = value => require('crypto').createHash('sha256').update(value).digest('hex');
function canonical(value) {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === 'object') return Object.fromEntries(Object.keys(value).sort().map(key => [key,canonical(value[key])]));
  return value;
}
function incidents(reasons) {
  const result = new Map();
  for (const reason of reasons) {
    // Each missing target is an immutable incident. Removing older targets from
    // the rolling window must not re-notify all the remaining historical gaps.
    const items = reason?.kind === 'missing_target_evidence' && Array.isArray(reason.forecast_ids) && reason.forecast_ids.length
      ? reason.forecast_ids.map(id => ({...reason,forecast_ids:[id]})) : [reason];
    for (const item of items) result.set(JSON.stringify(canonical(item)),item);
  }
  return result;
}
const incidentHash = rows => hash(JSON.stringify([...incidents(rows).keys()].sort()));
function deliveredIncidents(body, selftest) {
  if (typeof body !== 'string') return [];
  const marker = body.match(new RegExp(`^<!-- DOGE-P0:${selftest ? 'selftest-' : ''}(v2-)?([a-f0-9]{64}) -->\\n`));
  const evidence = body.match(/\nAffected horizons and evidence: (\[[^\n]*\])\.\nNo historical forecasts or target quotes have been fabricated\./);
  if (!marker || !evidence) return [];
  try {
    const reasons = JSON.parse(evidence[1]);
    // Read old delivery receipts without editing them; malformed/tampered
    // receipts cannot silence a current alert. Run failures stay independent.
    const expected = marker[1] ? incidentHash(reasons) : hash(JSON.stringify(reasons));
    return expected === marker[2] ? [...incidents(reasons).keys()] : [];
  } catch { return []; }
}
module.exports = async ({github, context, report, failed, selftest = false}) => {
  const reasons = report?.alert_reasons || [];
  if (!failed && reasons.length === 0) return {delivered:false, healthy:true};
  const comments = await github.paginate(github.rest.issues.listComments, {...context.repo, issue_number:5, per_page:100});
  let pending = reasons;
  if (!failed) {
    const delivered = new Set(comments.flatMap(c => deliveredIncidents(c?.body,selftest)));
    pending = [...incidents(reasons)].filter(([key]) => !delivered.has(key)).map(([,reason]) => reason);
    if (pending.length === 0) return {delivered:false, deduplicated:true};
  }
  const key = failed ? `run-${context.runId}` : `v2-${incidentHash(pending)}`;
  const marker = `<!-- DOGE-P0:${selftest ? 'selftest-' : ''}${key} -->`;
  if (failed && comments.some(c => typeof c?.body === 'string' && c.body.includes(marker))) return {delivered:false, deduplicated:true};
  const label = selftest ? 'CONTROLLED SELF-TEST: intentional isolated fault; production collectors are not failed' : (failed ? 'execution failed' : 'missing issuance or target evidence');
  const result = await github.rest.issues.createComment({...context.repo, issue_number:5,
    body:`${marker}\nP0 operational alert: ${label}.\nRun: https://github.com/${context.repo.owner}/${context.repo.repo}/actions/runs/${context.runId}\nAffected horizons and evidence: ${JSON.stringify(pending)}.\nNo historical forecasts or target quotes have been fabricated. P0 seven-day, P1 accuracy and P2 profit acceptance remain separate.`});
  return {delivered:true, comment_id:result.data.id};
};

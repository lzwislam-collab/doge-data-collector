// Shared production/self-test delivery. It never writes prediction or market data.
module.exports = async ({github, context, report, failed, selftest = false}) => {
  const reasons = report?.alert_reasons || [];
  if (!failed && reasons.length === 0) return {delivered:false, healthy:true};
  const key = failed ? `run-${context.runId}` : require('crypto').createHash('sha256').update(JSON.stringify(reasons)).digest('hex');
  const marker = `<!-- DOGE-P0:${selftest ? 'selftest-' : ''}${key} -->`;
  const comments = await github.paginate(github.rest.issues.listComments, {...context.repo, issue_number:5, per_page:100});
  if (comments.some(c => c.body.includes(marker))) return {delivered:false, deduplicated:true};
  const label = selftest ? 'CONTROLLED SELF-TEST: intentional isolated fault; production collectors are not failed' : (failed ? 'execution failed' : 'missing issuance or target evidence');
  const result = await github.rest.issues.createComment({...context.repo, issue_number:5,
    body:`${marker}\nP0 operational alert: ${label}.\nRun: https://github.com/${context.repo.owner}/${context.repo.repo}/actions/runs/${context.runId}\nAffected horizons and evidence: ${JSON.stringify(reasons)}.\nNo historical forecasts or target quotes have been fabricated. P0 seven-day, P1 accuracy and P2 profit acceptance remain separate.`});
  return {delivered:true, comment_id:result.data.id};
};

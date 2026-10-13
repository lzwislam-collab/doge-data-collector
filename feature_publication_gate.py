"""Only fresh feature decisions need new-publication verification.

This orchestration gate never edits frozen feature code or accepts a sample.
Existing immutable-row/hash and historical witness checks stay in the audit.
"""
import json,sys

def requires_publication(result):
    if not isinstance(result,dict) or result.get('status') not in ('ok','WAIT_ACTIVATION'):
        raise ValueError('successful frozen bind receipt required')
    count=result.get('new_contexts')
    if type(count) is not int or count<0:raise ValueError('nonnegative integer context count required')
    if result['status']=='WAIT_ACTIVATION' and count:raise ValueError('pre-activation context contradiction')
    return count>0

if __name__=='__main__':
    needed=requires_publication(json.load(sys.stdin))
    print('needs_publication='+str(needed).lower())

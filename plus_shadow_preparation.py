"""Deterministic identity preparation for review; never registers or issues."""
import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

import p2_plus_review as review
import phase2_factor_library as library
import phase2_forward_runtime_v2 as frozen
import phase2_forward_v2 as forward


def encode(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)+'\n').encode('utf-8')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def historical_source(root, commit, path):
    if not re.fullmatch('[0-9a-f]{40}', commit) or path != 'data/hourly_research/ohlc.csv':
        raise ValueError('unsafe historical source locator')
    return subprocess.check_output(['git', 'show', commit+':'+path], cwd=root, stderr=subprocess.DEVNULL)


def prepare(root, reader=None):
    root = Path(root)
    registration = json.loads((root/forward.REGISTRATION).read_bytes())
    files = frozen.verify_files(root, registration)
    model = json.loads(files[forward.MODEL])
    parent = json.loads(files[forward.PARENT])
    policy = json.loads(files[forward.POLICY])
    forward.check_policy(policy)
    if model != forward.make_model(parent) or registration['model_identity'] != model['trial_model_identity']:
        raise ValueError('existing frozen model identity differs')
    source = (reader or historical_source)(root, model['source_commit'], model['source_path'])
    if sha(source) != model['source_audit']['sha256']:
        raise ValueError('original training source digest mismatch; latest data is not a substitute')
    features = {'columns': model['columns'], 'names': [library.NAMES[i] for i in model['columns']],
                'feature_decimal_places': model['feature_decimal_places'],
                'feature_library_sha256': registration['sha256_lf']['phase2_factor_library.py'],
                'runtime_feature_code_sha256': registration['sha256_lf']['phase2_forward_v2.py'],
                'required_closed_hour_count': 73, 'horizon_seconds': policy['horizon_seconds']}
    baseline_assets = {name: registration['sha256_lf'][name] for name in (
        'direction_baseline_v42.py', 'direction_forward_v42.py', 'research/learned_shadow_v1/model_package.json')}
    baseline_definitions = {
        'frozen_v42_assets': baseline_assets,
        'probability_comparator': 'original18 frozen same-event replay; not a contemporaneous production probability',
        'direction_rule': 'same-event frozen V4.2 rule',
        'other_probability_baselines': ['constant_05', 'frozen_training_majority'],
        'paper_baselines': ['cash', 'same-period buy-and-hold'],
        'registered_forward_policy_sha256': registration['sha256_lf'][forward.POLICY],
        'independent_policy_sha256': sha(frozen.frozen.file_bytes(root, 'config/phase3_independent_v1.json')),
    }
    code = {name: digest for name, digest in registration['sha256_lf'].items() if name.endswith('.py')}
    for name in ('p2_plus_review.py', 'plus_shadow_preparation.py'):
        code[name] = sha(frozen.frozen.file_bytes(root, name))
    draft = review.future_protocol_draft()
    draft['identity_bindings'] = {
        'model_sha256': sha(files[forward.MODEL]),
        'feature_schema_sha256': sha(encode(features)),
        'training_data_sha256': sha(source),
        'frozen_v42_sha256': sha(encode(baseline_assets)),
        'baseline_definitions_sha256': sha(encode(baseline_definitions)),
        'code_sha256': sha(encode(code)),
    }
    return {
        'schema': 'DOGE_PLUS_EXISTING_R2_IDENTITY_PREPARATION_V1',
        'status': 'BOUND_REVIEW_DRAFT_NOT_REGISTERED',
        'candidate_reuse': {'protocol_id': registration['protocol_id'], 'model_identity': model['trial_model_identity'],
                            'weights_sha256': model['model_sha256'], 'model_file_sha256': sha(files[forward.MODEL]),
                            'existing_effective_from_utc': policy['effective_from_utc'],
                            'existing_observation_end_utc': policy['observation_end_utc']},
        'training_provenance': {'source_commit': model['source_commit'], 'source_path': model['source_path'],
                                'source_sha256': sha(source), 'training_matrix_sha256': model['training_matrix_sha256'],
                                'trained_at_ms': model['trained_at_ms'], 'latest_label_end_ms': model['latest_label_end_ms'],
                                'latest_label_available_ms': model['latest_label_available_ms'],
                                'training_n': model['training_n'], 'historical_pit_validated': False,
                                'source_git_snapshot_verified': True, 'training_matrix_recomputed': False},
        'feature_schema': features, 'baseline_definitions': baseline_definitions, 'code_manifest': code,
        'bound_review_draft': draft, 'review_draft_sha256': review.protocol_digest(draft),
        'preparation_validation': review.validate_future_protocol(draft),
        'limitations': ['local identity consistency only; public witness not replayed',
                       'new thresholds do not amend existing R2 registration',
                       'no new observation window chosen and no duplicate candidate queue',
                       'binding historical inputs does not validate historical PIT or future advantage'],
        'new_candidate_created': False, 'new_registration_performed': False,
        'runtime_activation_authorized': False, 'production_promotion_authorized': False,
        'real_orders_authorized': False, 'p0_pass_claimed': False,
    }


def verify(root, package):
    if encode(package) != encode(prepare(root)):
        raise ValueError('preparation differs from original frozen inputs or authority limits')
    return {'status': 'EXACT_LOCAL_BINDINGS_VERIFIED', 'public_registration_verified': False,
            'runtime_activation_authorized': False, 'production_promotion_authorized': False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--verify', type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    result = verify(root, json.loads(args.verify.read_bytes())) if args.verify else prepare(root)
    print(json.dumps(result, sort_keys=True, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()

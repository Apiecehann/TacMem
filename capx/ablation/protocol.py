"""CPU-only experiment contracts and fail-closed readiness checks."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
DATA = Path('/mnt/data/ljz/opentacbench')
MODES = ('tactile-rgb-opencv', 'no-tactile')
TASKS = ('weight_match', 'roughness_match', 'hardness_match', 'combined_match',
         'tension_strap', 'bulb_tightening', 'shadow_piano', 'blind_pick_balls')


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(part)
    return h.hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def resolve(path):
    path = Path(path).expanduser()
    return path if path.is_absolute() else ROOT / path


def load_spec(path):
    spec = yaml.safe_load(resolve(path).read_text(encoding='utf-8'))
    spec['mode'] = yaml.safe_load((ROOT / 'env_configs/ablations/mode.yaml').read_text(encoding='utf-8'))['mode']
    run_id = os.environ.get('CAPX_ABLATION_RUN_ID', 'not-started')
    if not run_id or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in run_id):
        raise ValueError('Invalid run ID')
    spec['output_root'] = str(DATA / 'ablation-runs' / spec['mode'] / spec['task'] / run_id)
    spec['phase'] = os.environ.get('CAPX_ABLATION_PHASE', 'smoke')
    if spec['phase'] not in ('smoke', 'formal'):
        raise ValueError('CAPX_ABLATION_PHASE must be smoke or formal')
    if spec['mode'] not in MODES or spec['task'] not in TASKS:
        raise ValueError('Unsupported ablation mode/task')
    if spec['model'] != 'gpt-6.1-sol' or spec['max_tokens'] != 8192:
        raise ValueError('Protocol model/output budget changed')
    return spec


def request_for(spec, seed):
    manifest = read_json(resolve(spec['seed_manifest']))
    if spec.get('phase') == 'smoke':
        if seed not in manifest['development_seeds']:
            raise ValueError('Smoke must use a reserved development seed')
        return manifest['smoke_requests'][spec['task']][str(seed)]
    if seed not in manifest['test_seeds']:
        raise ValueError('Seed absent from frozen test manifest')
    return manifest['requests'][spec['task']][str(seed)]


def contract_hash(spec, seed):
    assets = read_json(resolve(spec['assets_manifest']))['tasks'][spec['task']]
    record = {'task': spec['task'], 'mode': spec['mode'], 'seed': seed, 'phase': spec['phase'],
              'request': request_for(spec, seed), 'assets': assets,
              'action_contract': spec['action_contract'], 'action_call_budget': spec['action_call_budget']}
    return hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()


def clear_proxies():
    for key in ('http_proxy', 'https_proxy', 'all_proxy', 'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY'):
        os.environ.pop(key, None)


def readiness(spec, *, assets=None):
    """Missing evidence remains a blocker, never substituted by another task."""
    assets = assets or read_json(resolve(spec['assets_manifest']))
    item = assets['tasks'][spec['task']]
    errors = []
    root = Path(item['environment_root'])
    source = root / item['native_entry']
    if not source.is_file():
        errors.append(f'native task missing: {source}')
    if not item.get('environment_commit'):
        errors.append('environment commit not pinned')
    elif (root / '.git').exists():
        import subprocess
        head = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
        if head != item['environment_commit']:
            errors.append('environment HEAD differs from manifest')
    if source.is_file() and (not item.get('native_sha256') or sha256(source) != item['native_sha256']):
        errors.append('native source hash missing/mismatch')
    # Development smoke establishes wiring evidence; it cannot certify the
    # formal contract or train/test split. Pinned sources are required in both.
    formal = spec.get('phase', 'formal') != 'smoke'
    if formal and not item.get('native_contract_verified'):
        errors.append('native completion/action-rate/subtask contract not verified')
    if formal and not item.get('calibration_overlap_audited'):
        errors.append('development/calibration split audit incomplete')
    if spec['task'] == 'shadow_piano':
        path = Path(item['digit_config'])
        if not path.is_file() or sha256(path) != item.get('digit_config_sha256'):
            errors.append('DIGIT optical config missing/mismatch')
    seeds = read_json(resolve(spec['seed_manifest']))
    if len(seeds['test_seeds']) != 40 or len(set(seeds['test_seeds'])) != 40:
        errors.append('expected exactly 40 distinct test seeds')
    if set(seeds['test_seeds']) & set(item.get('development_seeds', [])):
        errors.append('split overlap')
    return errors


def require_ready(spec):
    errors = readiness(spec)
    if errors:
        raise RuntimeError('Ablation preflight blocked:\n' + '\n'.join(errors))


def main():
    import argparse
    parser = argparse.ArgumentParser(description='CPU-only readiness report; never starts a model or GPU')
    parser.add_argument('--config', required=True)
    parser.add_argument('--output')
    args = parser.parse_args()
    spec = load_spec(args.config)
    result = {'task': spec['task'], 'mode': spec['mode'], 'blockers': readiness(spec)}
    if args.output:
        write_json(args.output, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(bool(result['blockers']))


if __name__ == '__main__':
    main()

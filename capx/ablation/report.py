"""Merge result.json files into per-seed and per-task tables; never impute success."""
import argparse
import csv
from collections import Counter
from pathlib import Path

from .protocol import MODES, TASKS, read_json, resolve, write_json


def summarize(paths, seeds):
    by_key = {}
    for path in paths:
        r = read_json(path)
        if r.get('schema') != 'cap_tac_ablation_result.v1':
            continue
        if r.get('phase') != 'formal':
            continue
        key = (r['mode'], r['task'], r['seed'])
        if key in by_key:
            raise ValueError(f'Duplicate result {key}; choose one frozen run manifest explicitly')
        if key[0] not in MODES or key[1] not in TASKS or key[2] not in seeds:
            raise ValueError(f'Unexpected trial: {key}')
        by_key[key] = {**r, 'result_path': str(path)}
    rows = []
    for mode in MODES:
        for task in TASKS:
            results = [by_key[(mode, task, s)] for s in seeds if (mode, task, s) in by_key]
            failures = Counter(r['failure_type'] or 'native_task_failure' for r in results if not r['completed'])
            tokens = [(r.get('usage') or {}).get('total_tokens') for r in results]
            known_tokens = [v for v in tokens if v is not None]
            rows.append({'mode': mode, 'task': task, 'expected': 40, 'observed': len(results),
                         'successes': sum(r['completed'] for r in results), 'missing': 40-len(results),
                         'success_rate': sum(r['completed'] for r in results)/40 if len(results) == 40 else None,
                         'reported_token_trials': len(known_tokens), 'total_reported_tokens': sum(known_tokens),
                         'action_calls': sum(r.get('server_action_calls') or r['action_calls'] for r in results),
                         'elapsed_s': sum(r['elapsed_s'] for r in results),
                         'failures': dict(failures)})
    return rows, list(by_key.values())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('results', nargs='+', help='Exact result.json paths or run directories')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    paths = []
    for item in args.results:
        p = Path(item)
        paths.extend([p] if p.is_file() else p.rglob('result.json'))
    rows, trials = summarize(paths, read_json(resolve('env_configs/ablations/seeds40.json'))['test_seeds'])
    folder = Path(args.output)
    folder.mkdir(parents=True, exist_ok=True)
    write_json(folder/'per_seed.json', trials)
    write_json(folder/'per_task.json', rows)
    with (folder/'per_task.csv').open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


if __name__ == '__main__':
    main()

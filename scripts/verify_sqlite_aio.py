"""Verify persisted AIO tasks across two real Uvicorn processes."""
from contextlib import closing, contextmanager
import hashlib
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time

import requests

PROJECT = Path(__file__).resolve().parents[1]


@contextmanager
def server(environment, port):
    process = subprocess.Popen(
        [sys.executable, '-m', 'uvicorn', 'main:app', '--host', '127.0.0.1', '--port', str(port)],
        cwd=PROJECT, env=environment, stdout=subprocess.DEVNULL,
    )
    url = f'http://127.0.0.1:{port}'
    try:
        for _ in range(60):
            if process.poll() is not None:
                raise RuntimeError('Uvicorn 启动失败')
            try:
                response = requests.get(url + '/ui/create_task.html', timeout=1)
                response.raise_for_status()
                break
            except requests.RequestException:
                time.sleep(0.25)
        else:
            raise RuntimeError('Uvicorn 启动超时')
        yield url
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)


def request(method, url, **kwargs):
    response = requests.request(method, url, timeout=15, **kwargs)
    response.raise_for_status()
    return response


def terminal(base, task_id):
    for _ in range(180):
        task = request('GET', f'{base}/tasks/{task_id}').json()
        if task['status'] not in {'PENDING', 'RUNNING'}:
            return task
        time.sleep(0.5)
    raise RuntimeError(f'等待任务超时：{task_id}')


def file_hashes(base, task):
    return {
        artifact['filename']: hashlib.sha256(
            request('GET', f"{base}/artifacts/{artifact['id']}/download").content
        ).hexdigest()
        for artifact in task['artifacts']
    }


def main():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='boxpilot-acceptance-') as directory:
        root = Path(directory)
        db = root / 'boxpilot.db'
        environment = {
            **os.environ, 'DATABASE_PATH': str(db), 'ARTIFACT_ROOT': str(root / 'artifacts'),
            'LOG_FILE': str(root / 'boxpilot.jsonl'), 'LOG_LEVEL': 'WARNING',
            'SANDBOX_URL': os.environ.get('SANDBOX_URL', 'http://127.0.0.1:18080'),
        }
        cases = [
            ('CSV_ANALYSIS', '/tasks/csv',
             {'files': {'file': ('normal.csv', b'name,score\nA,10\nB,20\n')}}),
            ('WEB_DIGEST', '/tasks', {'json': {
                'task_type': 'WEB_DIGEST', 'params': {'url': 'https://example.com/'},
            }}),
            ('REPO_CHECK', '/tasks', {'json': {
                'task_type': 'REPO_CHECK',
                'params': {'repo_url': 'demo://python', 'install_deps': False},
            }}),
            ('WEB_ACCEPTANCE', '/tasks', {'json': {
                'task_type': 'WEB_ACCEPTANCE',
                'params': {'url': 'https://example.com/', 'expected_title': 'Example Domain'},
            }}),
        ]
        snapshots = {}
        with server(environment, port) as base:
            health = request('GET', base + '/health').json()
            assert health['store'] == 'sqlite' and health['status'] == 'ok', health
            for kind, path, args in cases:
                response = request('POST', base + path, **args)
                assert response.status_code == 202, response.status_code
                task = terminal(base, response.json()['id'])
                assert task['status'] == 'SUCCESS', task
                assert task['steps'] and all(step['status'] == 'SUCCESS' for step in task['steps']), task
                hashes = file_hashes(base, task)
                assert hashes, task
                snapshots[task['id']] = task, hashes
                summary = task['result']['summary']
                if kind == 'REPO_CHECK':
                    assert summary['test_status'] == 'passed', summary
                if kind == 'WEB_ACCEPTANCE':
                    assert summary['passed'] is True, summary
                print(kind, task['id'], len(task['steps']), sorted(hashes), flush=True)

            duplicate = request('POST', base + cases[0][1], **cases[0][2]).json()
            assert duplicate['id'] in snapshots and duplicate['deduplicated'], duplicate
            invalid = request('POST', base + '/tasks/csv',
                              files={'file': ('invalid.csv', b'a,b\n1,2,3\n')}).json()
            failed = terminal(base, invalid['id'])
            assert failed['status'] == 'FAILED' and failed['error_code'], failed
            assert any(step['status'] == 'FAILED' for step in failed['steps']), failed
            snapshots[failed['id']] = failed, {}

        with server(environment, port) as base:
            for task_id, (task, hashes) in snapshots.items():
                loaded = request('GET', f'{base}/tasks/{task_id}').json()
                assert loaded == task, loaded
                assert file_hashes(base, loaded) == hashes, loaded
        with closing(sqlite3.connect(db)) as connection:
            assert connection.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
            assert connection.execute('PRAGMA foreign_key_check').fetchall() == []
            counts = [connection.execute(f'SELECT count(*) FROM {table}').fetchone()[0]
                      for table in ('tasks', 'steps', 'artifacts')]
            print('RESTART OK', len(snapshots), 'records; tasks/steps/artifacts:', counts)


if __name__ == '__main__':
    main()

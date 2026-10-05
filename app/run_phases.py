"""Explicit, exclusive work intervals stored in the run's state transaction.

Only new runs get a ledger. Recovery invalidates the interrupted phase, never
turns downtime into work; completed phase measurements survive recovery.
"""
import json
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime

PHASES = ('waiting', 'masking', 'upload', 'model', 'other', 'paused')
_observer = ContextVar('run_phase_observer', default=None)


@contextmanager
def observe(callback):
    token = _observer.set(callback)
    try:
        yield
    finally:
        _observer.reset(token)


def notify(phase, **kwargs):
    callback = _observer.get()
    if callback:
        callback(phase, **kwargs)


def unknown():
    return {key: {'seconds': None, 'status': 'unknown'} for key in PHASES}


def create(db, ident, stamp):
    values = {key: {'seconds': 0, 'status': 'pending'} for key in PHASES}
    db.execute('INSERT INTO production_run_phases VALUES (?,?,?,?,?)',
               (ident, json.dumps(values), 'waiting', stamp, 'queued'))


def _elapsed(start, end):
    return max(0, (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds())


def transition(db, ident, phase, stamp, *, state=None, interrupted=False, cached=False):
    if phase is not None and phase not in PHASES:
        raise ValueError('Invalid run phase')
    row = db.execute('SELECT * FROM production_run_phases WHERE run_id=?', (ident,)).fetchone()
    if row is None:
        return
    if state is None and row['state'] != 'running':
        return
    values = json.loads(row['data'])
    if row['phase'] == phase and (state is None or state == row['state']) and not interrupted and not cached:
        return
    previous = row['phase']
    if previous:
        value = values[previous]
        if interrupted:
            value.update(seconds=None, status='unknown')
        elif value['seconds'] is not None:
            value['seconds'] += _elapsed(row['since'], stamp)
            value['status'] = 'complete'
    if cached and phase:
        values[phase]['cached'] = True
        # A cache hit marks completion without opening a masking interval.
        if values[phase]['seconds'] is not None:
            values[phase]['status'] = 'complete'
        phase = 'other'
    db.execute('UPDATE production_run_phases SET data=?,phase=?,since=?,state=? WHERE run_id=?',
               (json.dumps(values), phase, stamp, state or row['state'], ident))


def state_transition(db, ident, state, stamp, *, interrupted=False):
    row = db.execute('SELECT state FROM production_run_phases WHERE run_id=?', (ident,)).fetchone()
    if row is None or row['state'] == state:
        return
    phase = {'queued': 'waiting', 'running': 'other'}.get(state, 'paused')
    transition(db, ident, phase, stamp, state=state, interrupted=interrupted)


def summaries(db, ids, stamp):
    result = {ident: unknown() for ident in ids}
    for offset in range(0, len(ids), 500):
        batch = ids[offset:offset + 500]
        rows = db.execute('SELECT * FROM production_run_phases WHERE run_id IN (' +
                          ','.join('?' for _ in batch) + ')', batch).fetchall()
        for row in rows:
            values = json.loads(row['data'])
            phase = row['phase']
            if phase and row['state'] in {'queued', 'running'}:
                value = values[phase]
                if value['seconds'] is not None:
                    value['seconds'] += _elapsed(row['since'], stamp)
                    value['status'] = 'running'
            for value in values.values():
                if value['seconds'] is not None:
                    value['seconds'] = round(value['seconds'], 3)
            result[row['run_id']] = values
    return result

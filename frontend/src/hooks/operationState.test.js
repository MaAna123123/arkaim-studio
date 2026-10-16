import assert from 'node:assert/strict';
import test from 'node:test';

import {
  canApplyOperationSnapshot,
  formatEta,
  getOperationActions,
  isUnfinishedTranscription,
  matchesOperationEvent,
} from './useOperation.js';


test('running, paused, and stopped states expose only valid controls', () => {
  assert.deepEqual(getOperationActions('running'), ['pause', 'stop']);
  assert.deepEqual(getOperationActions('paused'), ['resume', 'stop']);
  assert.deepEqual(getOperationActions('stopped'), ['resume']);
  assert.deepEqual(getOperationActions('completed'), []);
});

test('unfinished transcription includes stopped resumable progress', () => {
  for (const status of ['queued', 'running', 'paused', 'stopped']) {
    assert.equal(
      isUnfinishedTranscription({ operation_type: 'transcription', status }),
      true,
    );
  }
  assert.equal(
    isUnfinishedTranscription({ operation_type: 'transcription', status: 'completed' }),
    false,
  );
});

test('operation events are scoped to the displayed operation and video', () => {
  const current = { operation_id: 'op-1', video_id: 'video-1' };
  assert.equal(
    matchesOperationEvent(
      { type: 'operation', operation_id: 'op-1', video_id: 'video-1' },
      current,
      'video-1',
    ),
    true,
  );
  assert.equal(
    matchesOperationEvent(
      { type: 'operation', operation_id: 'op-2', video_id: 'video-1' },
      current,
      'video-1',
    ),
    false,
  );
  assert.equal(
    matchesOperationEvent(
      { type: 'progress', operation_id: 'op-1', video_id: 'video-1' },
      current,
      'video-1',
    ),
    false,
  );
  assert.equal(
    matchesOperationEvent(
      {
        type: 'operation', operation_type: 'transcription',
        operation_id: 'unclaimed', video_id: 'video-1',
      },
      null,
      'video-1',
    ),
    false,
  );
});

test('a trusted start response can replace a failed operation', () => {
  const failed = { operation_id: 'old-op', video_id: 'video-1', status: 'failed' };
  const retry = { operation_id: 'new-op', video_id: 'video-1', status: 'queued' };

  assert.equal(canApplyOperationSnapshot(retry, failed, 'video-1'), false);
  assert.equal(
    canApplyOperationSnapshot(retry, failed, 'video-1', { claim: true }),
    true,
  );
});

test('eta is formatted without exposing invalid values', () => {
  assert.equal(formatEta(65), '1 мин 5 сек');
  assert.equal(formatEta(8), '8 сек');
  assert.equal(formatEta(null), '—');
  assert.equal(formatEta(-1), '—');
});

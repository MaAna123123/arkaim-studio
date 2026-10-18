import assert from 'node:assert/strict';
import test from 'node:test';

import {
  canApplyOperationSnapshot,
  claimRecoveredOperation,
  createOperationActionRunner,
  formatEta,
  getOperationActions,
  isUnfinishedTranscription,
  matchesOperationEvent,
} from './useOperation.js';


function deferred() {
  let resolve;
  const promise = new Promise((resolvePromise) => { resolve = resolvePromise; });
  return { promise, resolve };
}


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

test('a recovered operation is explicitly claimed only for its matching video and id', () => {
  const recovered = {
    operation_id: 'recovered-op',
    operation_type: 'transcription',
    video_id: 'video-1',
    status: 'stopped',
  };

  assert.equal(
    claimRecoveredOperation(recovered, 'recovered-op', 'video-1', ''),
    recovered,
  );
  assert.equal(
    claimRecoveredOperation(recovered, 'recovered-op', 'stale-video', ''),
    null,
  );
  assert.equal(
    claimRecoveredOperation(recovered, 'different-op', 'video-1', ''),
    null,
  );
});

test('a pending operation action suppresses a duplicate recovered resume claim', () => {
  const recovered = {
    operation_id: 'recovered-op',
    operation_type: 'transcription',
    video_id: 'video-1',
    status: 'stopped',
  };

  assert.equal(
    claimRecoveredOperation(recovered, 'recovered-op', 'video-1', 'resume'),
    null,
  );
});

test('Continue posts the recovered operation id once and rejects a stale-video success', async () => {
  const requests = [];
  const pending = [];
  const runner = createOperationActionRunner({
    fetchImpl: (url, options) => {
      const result = deferred();
      requests.push({ url, options, ...result });
      return result.promise;
    },
    readResponse: async (response) => response,
    onPendingAction: (action) => pending.push(action),
  });
  const currentOperation = {
    operation_id: 'recovered-op', video_id: 'video-1', status: 'stopped',
  };
  const accepted = [];

  const first = runner.run({
    apiBase: 'http://api.test', action: 'resume', operationId: 'recovered-op',
    videoId: 'video-1', currentOperation,
    acceptSnapshot: (snapshot) => { accepted.push(snapshot); return true; },
  });
  const duplicate = runner.run({
    apiBase: 'http://api.test', action: 'resume', operationId: 'recovered-op',
    videoId: 'video-1', currentOperation, acceptSnapshot: () => true,
  });

  assert.equal(await duplicate, null);
  assert.equal(requests.length, 1);
  assert.equal(requests[0].url, 'http://api.test/api/operations/recovered-op/resume');
  requests[0].resolve({
    operation_id: 'recovered-op', video_id: 'stale-video', status: 'running',
  });
  assert.equal(await first, null);
  assert.deepEqual(accepted, []);
  assert.deepEqual(pending, ['resume', '']);
});

test('operation action runner aborts and ignores a response after reset', async () => {
  const requests = [];
  const accepted = [];
  const runner = createOperationActionRunner({
    fetchImpl: (url, options) => {
      const result = deferred();
      requests.push({ url, options, ...result });
      return result.promise;
    },
    readResponse: async (response) => response,
  });
  const running = runner.run({
    apiBase: 'http://api.test', action: 'resume', operationId: 'operation-1',
    videoId: 'video-1',
    currentOperation: { operation_id: 'operation-1', video_id: 'video-1' },
    acceptSnapshot: (snapshot) => { accepted.push(snapshot); return true; },
  });

  runner.reset();
  assert.equal(requests[0].options.signal.aborted, true);
  requests[0].resolve({
    operation_id: 'operation-1', video_id: 'video-1', status: 'running',
  });
  assert.equal(await running, null);
  assert.deepEqual(accepted, []);
});

test('eta is formatted without exposing invalid values', () => {
  assert.equal(formatEta(65), '1 мин 5 сек');
  assert.equal(formatEta(8), '8 сек');
  assert.equal(formatEta(null), '—');
  assert.equal(formatEta(-1), '—');
});

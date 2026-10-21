import assert from 'node:assert/strict';
import test from 'node:test';

import { toEditorMedia } from './mediaAsset.js';

test('maps current ad metadata to an absolute editor preview URL', () => {
  assert.deepEqual(toEditorMedia('http://127.0.0.1:8000', {
    has_ad: true,
    url: '/ads/current_ad.mp4',
    duration: 4.25,
    width: 1280,
    height: 720,
    filename: 'current_ad.mp4',
    revision: 'abc-123',
  }, 'ad'), {
    has_media: true,
    url: 'http://127.0.0.1:8000/ads/current_ad.mp4?v=abc-123',
    raw_url: '/ads/current_ad.mp4',
    duration: 4.25,
    width: 1280,
    height: 720,
    filename: 'current_ad.mp4',
    revision: 'abc-123',
  });
});

test('returns an unavailable media object when a banner is missing', () => {
  assert.deepEqual(toEditorMedia('http://127.0.0.1:8000', { has_banner: false }, 'banner'), {
    has_media: false,
    url: '',
    raw_url: '',
    duration: 0,
    width: 0,
    height: 0,
    filename: '',
    revision: '',
  });
});

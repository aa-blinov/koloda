import { describe, it, expect, vi, beforeEach } from 'vitest';
import { get } from 'svelte/store';

// Сервер (app.py) отдаёт фото не больше чем за 12 каналов за запрос, остальные оставляет непроверенными.
vi.mock('./api.js', () => ({ api: { avatars: vi.fn() } }));

import { api } from './api.js';
import { avatars, loadAvatars } from './stores.js';

const PER_REQUEST = 12;

// Ответ как у сервера: фото и отметку проверки только для первых PER_REQUEST каналов запроса.
function serverAnswer(batch) {
  const done = batch.slice(0, PER_REQUEST);
  return {
    avatars: Object.fromEntries(done.map((c) => [c, `https://cdn4.telesco.pe/file/${c}.jpg`])),
    checked: done,
  };
}

function names(prefix, count) {
  return Array.from({ length: count }, (_, i) => `${prefix}${i}`);
}

describe('loadAvatars', () => {
  beforeEach(() => {
    api.avatars.mockReset();
    avatars.set(new Map());
  });

  it('asks in rounds until every channel has been checked', async () => {
    api.avatars.mockImplementation(async (batch) => serverAnswer(batch));
    const list = names('many', 30);
    await loadAvatars(list);
    expect(api.avatars).toHaveBeenCalledTimes(3);
    const photos = get(avatars);
    expect(list.every((c) => photos.has(c))).toBe(true);
  });

  it('asks again for a channel the server did not confirm', async () => {
    api.avatars.mockImplementation(async () => ({ avatars: {}, checked: [] }));
    await loadAvatars(['quiet_one']);
    expect(api.avatars).toHaveBeenCalledTimes(1);
    await loadAvatars(['quiet_one']);
    expect(api.avatars).toHaveBeenCalledTimes(2);
  });

  it('keeps a channel askable after a network failure', async () => {
    api.avatars.mockRejectedValueOnce(new Error('offline'));
    api.avatars.mockImplementation(async (batch) => serverAnswer(batch));
    await loadAvatars(['flaky_one']);
    expect(get(avatars).has('flaky_one')).toBe(false);
    await loadAvatars(['flaky_one']);
    expect(get(avatars).has('flaky_one')).toBe(true);
  });

  it('stops when a round brings no progress', async () => {
    api.avatars.mockImplementation(async () => ({ avatars: {}, checked: [] }));
    await loadAvatars(names('stuck', 40));
    expect(api.avatars).toHaveBeenCalledTimes(1);
  });
});

import { writable, derived, get } from 'svelte/store';
import { api } from './api.js';

/**
 * The state of the profile: the subscriptions, the read marks and the settings.
 * The browser keeps a copy, the server keeps the truth, and the changes made
 * offline wait in a queue and go out when the connection returns.
 */
const LS = {
  sub: 'koloda.subs',
  read: 'koloda.reads',
  pending: 'koloda.pending',
  view: 'koloda.view',
  showRead: 'koloda.showRead',
  immersive: 'koloda.immersive',
  migrated: 'koloda.migrated',
};

export const THEMES = ['gruvbox', 'github', 'dracula', 'nord', 'solarized', 'catppuccin'];
export const MODES = ['dark', 'light', 'auto'];
export const FONTS = ['sans', 'serif', 'mono'];

export const session = writable(null);
export const subs = writable([]);
export const reads = writable(new Set());
export const titles = writable({});
export const settings = writable({ theme: 'gruvbox', mode: 'dark', font: 'mono' });
export const collections = writable([]);
export const avatars = writable(new Map());
export const syncStatus = writable(null);
export const undo = writable(null);
export const stateReady = writable(false);

// Показывать ли прочитанные посты. По умолчанию они скрыты. Выбор помнится на
// устройстве: переключатель, который каждый раз встаёт в исходное положение,
// никому не нужен.
export const showRead = writable(readLocal(LS.showRead, false));

export function setShowRead(value) {
  const next = Boolean(value);
  showRead.set(next);
  writeLocal(LS.showRead, next);
}

// Полный экран колоды: шапка и панель разделов уходят, карточка занимает экран. Помнится на устройстве.
export const immersive = writable(readLocal(LS.immersive, false));

export function setImmersive(value) {
  const next = Boolean(value);
  immersive.set(next);
  writeLocal(LS.immersive, next);
}

export const isSubscribed = derived(subs, ($subs) => new Set($subs));

// --- the local copy ---

function readLocal(key, fallback) {
  try {
    const raw = localStorage.getItem(key);
    return raw ? JSON.parse(raw) : fallback;
  } catch (err) {
    return fallback;
  }
}

function writeLocal(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch (err) {
    // A full disk or a private window: the interface still works, the copy is lost.
  }
}

const pendingOps = () => readLocal(LS.pending, []);
const setPendingOps = (ops) => writeLocal(LS.pending, ops);

export function applyState(data) {
  if (!data) return;
  if (Array.isArray(data.subs)) {
    subs.set(data.subs);
    writeLocal(LS.sub, data.subs);
  }
  if (Array.isArray(data.reads)) {
    reads.set(new Set(data.reads));
    writeLocal(LS.read, data.reads);
  }
  if (data.settings) applySettings(data.settings);
  if (data.titles && typeof data.titles === 'object') titles.set(data.titles);
}

// The pending changes are applied on top of the server's answer: the server
// does not know about them yet, and the interface must not lose them.
export function replayPending() {
  const nextSubs = new Set(get(subs));
  const nextReads = new Set(get(reads));
  pendingOps().forEach((op) => {
    if (op.type === 'subscription') op.subscribed ? nextSubs.add(op.channel) : nextSubs.delete(op.channel);
    if (op.type === 'read') op.unread ? nextReads.delete(op.post) : nextReads.add(op.post);
  });
  subs.set([...nextSubs]);
  reads.set(nextReads);
  writeLocal(LS.sub, [...nextSubs]);
  writeLocal(LS.read, [...nextReads]);
}

// --- the theme ---

const darkQuery = typeof matchMedia === 'function' ? matchMedia('(prefers-color-scheme: dark)') : null;

export function applySettings(next) {
  if (next) settings.update((current) => ({ ...current, ...next }));
  const current = get(settings);
  const mode = current.mode === 'auto' ? (darkQuery && darkQuery.matches ? 'dark' : 'light') : current.mode;
  const root = document.documentElement;
  root.dataset.theme = current.theme;
  root.dataset.mode = mode;
  root.dataset.font = current.font;
  root.dataset.modePref = current.mode;
  syncThemeColor();
}

// Цвет полосы браузера и заголовка установленного приложения — из токена палитры:
// хекс в исходниках сломал бы смену палитры. Читаем после загрузки стилей, иначе
// токена ещё нет, и так и на экране входа, до настроек профиля.
export function syncThemeColor() {
  const themeColor = document.getElementById('themeColor');
  if (!themeColor) return;
  themeColor.setAttribute('content', getComputedStyle(document.documentElement).getPropertyValue('--ui-bg').trim());
}

if (typeof window !== 'undefined') window.addEventListener('load', syncThemeColor);

if (darkQuery) {
  darkQuery.addEventListener('change', () => {
    if (get(settings).mode === 'auto') applySettings();
  });
}

export async function saveSettings(patch) {
  applySettings(patch);
  try {
    const answer = await api.saveSettings(get(settings));
    if (answer && answer.settings) applySettings(answer.settings);
    syncStatus.set(null);
    return true;
  } catch (err) {
    syncStatus.set({ message: 'Не удалось сохранить настройки.', retry: false });
    return false;
  }
}

// --- the queue of the changes ---

let stateQueue = Promise.resolve();

export function queueStateOp(op) {
  const queued = pendingOps();
  queued.push(op);
  setPendingOps(queued);
  replayPending();
  if (get(stateReady)) drainStateOps();
}

export function drainStateOps() {
  if (!get(stateReady)) return Promise.resolve();
  stateQueue = stateQueue
    .catch(() => {})
    .then(async () => {
      while (pendingOps().length) {
        const op = pendingOps()[0];
        const data =
          op.type === 'subscription'
            ? await api.setSubscription(op.channel, op.subscribed)
            : await api.readPosts([op.post], op.unread === true);
        setPendingOps(pendingOps().slice(1));
        applyState(data);
        replayPending();
        syncStatus.set(null);
      }
    })
    .catch((err) => {
      if (err && err.status === 401) {
        session.set(null);
        syncStatus.set(null);
        return;
      }
      syncStatus.set({ message: 'Не удалось сохранить изменения. Проверь соединение: попробую снова сам, или нажми «Повторить».', retry: true });
    });
  return stateQueue;
}

// Очередь уходит сама: по таймеру, при возврате сети и по кнопке «Повторить».
// Пустая очередь ничего не делает.
export function flushPending() {
  return pendingOps().length ? drainStateOps() : Promise.resolve();
}

export function setSubscription(channel, subscribed) {
  queueStateOp({ type: 'subscription', channel, subscribed });
}

export function markRead(post) {
  queueStateOp({ type: 'read', post, unread: false });
}

export function markUnread(post) {
  queueStateOp({ type: 'read', post, unread: true });
}

// --- the profile ---

export async function loadAccountState({ migrate = false, legacy = null } = {}) {
  stateReady.set(false);
  // The queue is not touched: what was gathered offline has to go out after the
  // reload, not vanish with the page.
  if (migrate && localStorage.getItem(LS.migrated) !== '1') {
    try {
      const answer = await api.mergeState(legacy || { subs: [], reads: [] });
      applyState(answer);
    } catch (err) {
      // 409: у браузера больше отметок, чем разрешено на профиль. Слияние не
      // сделаем никогда, поэтому не держим экран ошибкой при каждом входе: читаем
      // состояние сервера как есть. Остальные ошибки пробрасываем.
      if (!err || err.status !== 409) throw err;
      const answer = await api.state();
      applyState(answer);
    }
    localStorage.setItem(LS.migrated, '1');
  } else {
    const answer = await api.state();
    applyState(answer);
  }
  stateReady.set(true);
  replayPending();
  refreshCollections();
}

// Коллекции нужны и странице канала, и ленте, а не только окну каналов: без
// этой загрузки они были пустыми, пока окно каналов не открыли.
export async function refreshCollections() {
  try {
    const answer = await api.collections();
    collections.set((answer && answer.collections) || []);
  } catch (err) {
    // Список остаётся прежним: окна показывают то, что уже загружено.
  }
}

export async function pullState() {
  if (!get(stateReady) || document.hidden) return;
  await stateQueue.catch(() => {});
  try {
    const data = await api.state();
    const before = { subs: get(subs), reads: [...get(reads)] };
    applyState(data);
    replayPending();
    const after = { subs: get(subs), reads: [...get(reads)] };
    // The order of the marks on the server and in the browser is different:
    // the composition matters, not the string.
    const changed =
      before.subs.length !== after.subs.length ||
      before.subs.some((c) => !after.subs.includes(c)) ||
      before.reads.length !== after.reads.length ||
      before.reads.some((p) => !after.reads.includes(p));
    if (changed) return true;
  } catch (err) {
    // The feed that is already on the screen stays on the screen.
  }
  return false;
}

// Строка «Вернуть» — подсказка: через 4 с она уходит сама, иначе закрывает
// фильтры в шапке ленты. Новая строка перезапускает отсчёт.
const UNDO_MS = 4000;
let undoTimer = 0;

export function showUndo(text, action) {
  clearTimeout(undoTimer);
  undo.set({ text, action });
  undoTimer = setTimeout(() => undo.set(null), UNDO_MS);
}

export function clearUndo() {
  clearTimeout(undoTimer);
  undo.set(null);
}

// --- the photos of the channels ---

const avatarChecked = new Set();
const avatarRenewed = new Set();
const avatarWaiting = new Set();
// Сервер ходит на t.me не больше чем за AVATAR_FETCH_CAP (12) каналов за запрос и оставляет
// остальные непроверенными. Отмечаем только то, что он подтвердил в `checked`, и спрашиваем
// остальных следующими порциями, пока есть прогресс.
const AVATAR_ROUNDS = 10;

export async function loadAvatars(list) {
  const asked = [...new Set(list)].filter((c) => !avatarChecked.has(c) && !avatarWaiting.has(c));
  if (!asked.length) return;
  asked.forEach((c) => avatarWaiting.add(c));
  let want = asked;
  try {
    for (let round = 0; round < AVATAR_ROUNDS && want.length; round++) {
      const batch = want.slice(0, 60);
      const data = await api.avatars(batch);
      if (!data || !Array.isArray(data.checked)) break;
      const done = new Set(data.checked);
      const found = batch.filter((c) => done.has(c));
      if (!found.length) break;
      found.forEach((c) => avatarChecked.add(c));
      if (data.avatars) {
        avatars.update((current) => {
          const next = new Map(current);
          for (const [channel, url] of Object.entries(data.avatars)) {
            if (url) next.set(channel, url);
          }
          return next;
        });
      }
      want = want.filter((c) => !avatarChecked.has(c));
    }
  } catch (err) {
    // No photos is not a failure: the letter of the channel stays, and the next call asks again.
  } finally {
    asked.forEach((c) => avatarWaiting.delete(c));
  }
}

// Картинка канала не открылась: ссылка протухла. Берём свежую у t.me, один раз на канал за сессию;
// если ссылка пришла новая, картинка перерисуется сама (компоненты ключуют ошибку по адресу).
export async function renewAvatar(channel) {
  if (!channel || avatarRenewed.has(channel)) return;
  avatarRenewed.add(channel);
  try {
    const data = await api.avatars([channel], true);
    const url = data && data.avatars && data.avatars[channel];
    if (url) avatars.update((current) => new Map(current).set(channel, url));
  } catch (err) {
    // The letter of the channel stays.
  }
}

export { LS, get, api };

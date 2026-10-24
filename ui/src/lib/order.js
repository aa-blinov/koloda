import { postKey } from './format.js';

// Порядок ленты тот же, что на сервере: ORDER BY ts DESC, channel ASC, msg_id DESC.
// Новее выше; при равном времени — канал по алфавиту, затем более новый номер сообщения.
export function comesBefore(a, b) {
  if (a.ts !== b.ts) return a.ts > b.ts;
  if (a.channel !== b.channel) return a.channel < b.channel;
  return a.msg_id > b.msg_id;
}

// Вернуть пост на его место по времени, а не наверх. Между жестом и отменой лента могла
// обновиться: тогда сверху уже новые посты, и пост, который старше их, наверх не ставим.
export function insertByTime(list, post) {
  const key = postKey(post);
  const rest = list.filter((item) => postKey(item) !== key);
  const at = rest.findIndex((item) => comesBefore(post, item));
  return at < 0 ? [...rest, post] : [...rest.slice(0, at), post, ...rest.slice(at)];
}

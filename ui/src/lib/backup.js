// Разбор файлов переноса: выгрузка ленты (JSONL) и список каналов (текст).
// Разбор идёт в браузере, а не на сервере: дальше всё равно идёт уже готовая
// проверка каналов, отметок и коллекций в /api/state/merge.

const CHANNEL_NAME = /^[A-Za-z0-9_]{3,32}$/;

/**
 * Выгрузка ленты: по записи в строке — канал, отметка или коллекция.
 * Пустые строки пропускаются. Повреждённая строка не молчит: перенос
 * останавливается с номером строки, половину файла не вносят.
 */
export function parseBackup(text) {
  const out = { subs: [], reads: [], collections: [] };
  const lines = String(text).split('\n');
  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i].trim();
    if (!line) continue;
    let record = null;
    try {
      record = JSON.parse(line);
    } catch (err) {
      throw new Error('Строка ' + (i + 1) + ' не разобрана: файл повреждён.');
    }
    if (!record || typeof record !== 'object') continue;
    if (record.type === 'channel' && typeof record.channel === 'string') {
      out.subs.push(record.channel);
    } else if (record.type === 'read' && typeof record.post === 'string') {
      out.reads.push(record.post);
    } else if (record.type === 'collection' && typeof record.name === 'string'
               && Array.isArray(record.channels)) {
      out.collections.push({
        name: record.name,
        channels: record.channels.filter((channel) => typeof channel === 'string'),
      });
    }
    // Незнакомый тип пропускаем: файл от новой версии сервиса не должен
    // ронять перенос на старой.
  }
  return out;
}

function channelFromToken(token) {
  // Ссылка может быть и без схемы, и в виде превью t.me/s/имя, и на пост.
  const stripped = token
    .replace(/^(https?:\/\/)?(www\.)?t\.me\/(s\/)?/i, '')
    .replace(/^@/, '');
  const name = stripped.split('/')[0];
  return CHANNEL_NAME.test(name) ? name : '';
}

/**
 * Список каналов: ссылки и @имена, по одному в строке — но перенос строки
 * не обязателен, список пишут и через запятую. Голое слово не берём: иначе
 * любое слово из блокнота стало бы подпиской.
 */
export function parseChannelList(text) {
  const tokens = String(text).split(/[\s,;]+/).filter(Boolean);
  const names = new Set();
  for (const token of tokens) {
    if (!(token.startsWith('@') || /t\.me\//i.test(token))) continue;
    const name = channelFromToken(token);
    if (name) names.add(name);
  }
  return [...names];
}

/**
 * Выгрузка простого списка: по ссылке в строке, чтобы файл открывался
 * блокнотом и его можно было переслать человеку.
 */
export function buildChannelList(subs) {
  return subs.map((channel) => 'https://t.me/' + channel).join('\n') + '\n';
}

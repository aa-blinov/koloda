// Форматирование: числа, даты, названия. Один формат на весь интерфейс.

export const postKey = (post) => post.channel + '/' + post.msg_id;

// Числа везде по-русски и в одном формате. Раньше подписчики показывались как
// «9.9M», а просмотры приходили из API готовой строкой «3.23K» — на одном
// экране два разных счёта. Теперь оба проходят через одну функцию.
export function nfmt(value) {
  const v = Number(value);
  if (!isFinite(v) || v <= 0) return '';
  if (v >= 1e6) return (v / 1e6).toFixed(1).replace('.0', '').replace('.', ',') + ' млн';
  if (v >= 1e3) return Math.round(v / 1e3) + ' тыс.';
  return String(Math.round(v));
}

export const kfmt = nfmt;

export function dayLabel(ts) {
  const d = new Date(ts * 1000), now = new Date();
  const same = (a) => a.toDateString() === d.toDateString();
  if (same(now)) return 'Сегодня';
  const y = new Date(now.getTime() - 86400000);
  if (same(y)) return 'Вчера';
  return d.getFullYear() === now.getFullYear()
    ? d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'long' })
    : d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'long', year: 'numeric' });
}

export function when(ts) {
  if (!ts) return '';
  const diff = Date.now() / 1000 - ts;
  if (diff < 3600) return Math.floor(diff / 60) + ' мин';
  if (diff < 86400) return Math.floor(diff / 3600) + ' ч';
  if (diff < 604800) return Math.floor(diff / 86400) + ' дн';
  // «11 окт.» и «11 окт. прошлого года» неразличимы, а лента живёт через
  // несколько календарных лет — в запасной дате год обязателен
  const d = new Date(ts * 1000), opts = { day: 'numeric', month: 'short' };
  if (d.getFullYear() !== new Date().getFullYear()) opts.year = 'numeric';
  return d.toLocaleDateString('ru-RU', opts);
}

// Категории из открытого каталога TGMania приходят по-английски. Интерфейс
// русский, поэтому переводим на лету; неизвестную категорию оставляем как есть,
// чтобы не выдумывать смысл.
const CAT_RU = {
  'News & Media': 'Новости и медиа',
  'Career & Jobs': 'Работа и карьера',
  Education: 'Образование',
  'Tech & Science': 'Технологии и наука',
  Finance: 'Финансы',
  Business: 'Бизнес',
  'Sports & Fitness': 'Спорт и тело',
  'Health & Fitness': 'Здоровье и тело',
  Travel: 'Путешествия',
  'Food & Drink': 'Еда и напитки',
  'Art & Design': 'Искусство и дизайн',
  Music: 'Музыка',
  Games: 'Игры',
  Entertainment: 'Развлечения',
  Celebrity: 'Знаменитости',
  Religion: 'Религия',
  Science: 'Наука',
  Automotive: 'Авто',
  Pets: 'Животные',
  Humor: 'Юмор',
  Books: 'Книги',
  Politics: 'Политика',
  'Family & Relationships': 'Семья и отношения',
  Crypto: 'Крипто',
  Insurance: 'Страхование',
  Military: 'Военное',
  Adult: 'Для взрослых',
  'Books & Art': 'Книги и искусство',
  'Cooking & Food': 'Кулинария',
  'Health': 'Здоровье',
  'Politics & Government': 'Политика и власть',
  'Science & Technology': 'Наука и технологии',
  'Sports': 'Спорт',
  'Ecosystem': 'Экосистема',
  'IT': 'IT',
  'E-commerce': 'Интернет-магазины',
  'Photo & Video': 'Фото и видео',
  'Marketing': 'Маркетинг',
  'Trading': 'Трейдинг',
  'Banking': 'Банки',
  'Apps': 'Приложения',
  'Design': 'Дизайн',
  'Movies': 'Кино',
  'Kids': 'Детям',
  'Fitness': 'Фитнес',
  'Recipes': 'Рецепты',
  'Sport': 'Спорт',
  'Football': 'Футбол',
  'Basketball': 'Баскетбол',
  'Lottery': 'Лотереи',
  'Crypto & Investing': 'Крипта и инвестиции',
  'Startups': 'Стартапы',
  'Language': 'Языки',
  'Psychology': 'Психология',
  'History': 'История',
  'Travel & Tourism': 'Путешествия и туризм',
  'Nature': 'Природа',
  'Spirituality': 'Духовное',
  'Podcasts': 'Подкасты',
  'Gaming': 'Игры',
  'News': 'Новости',
  'Tech': 'Технологии',
  'Productivity': 'Продуктивность',
  'Business & Finance': 'Бизнес и финансы',
  'Art': 'Искусство',
  'Education & Science': 'Образование и наука',
};

export const catRu = (value) => CAT_RU[String(value || '').trim()] || String(value || '').trim();

export function postLink(post) {
  if (!post) return 'https://t.me/';
  if (String(post.channel).startsWith('private:-100'))
    return 'https://t.me/c/' + String(post.channel).replace('private:-100', '') + '/' + post.msg_id;
  return post.url || 'https://t.me/' + post.channel + '/' + post.msg_id;
}

export function channelName(post) {
  const channel = String(post.channel || '');
  return channel.startsWith('private:-100')
    ? channel.replace('private:-100', '').replace(/^private:/, '')
    : channel;
}

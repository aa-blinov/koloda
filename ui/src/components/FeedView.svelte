<script>
  /**
   * The feed: the column of posts, the marks, the next page and the search.
   * The saved screen is the same column with another source.
   */
  import { onMount, onDestroy, tick } from 'svelte';
  import { createEventDispatcher } from 'svelte';
  import { fly } from 'svelte/transition';
  import { Button, Chip, EmptyState, IconButton, Notice, Skeleton, Spinner } from '@monos/ui';

  import PostCard from './PostCard.svelte';
  import Icon from './Icon.svelte';
  import { api } from '../lib/api.js';
  import { dayLabel, postKey } from '../lib/format.js';
  import { comesBefore, insertByTime } from '../lib/order.js';
  import {
    subs,
    reads,
    titles,
    avatars,
    markRead,
    markUnread,
    loadAvatars,
    showUndo,
    setSubscription,
    setShowRead,
    showRead,
    immersive,
    setImmersive,
    stateReady,
  } from '../lib/stores.js';
  import { feedChannels } from '../lib/feed.js';

  export let feedMode = 'feed';
  // Ключ экрана: оболочка по нему понимает, что место прокрутки можно вернуть.
  export let screenKey = '';
  // Колода: на телефоне лента показывается по одной карточке, без вертикальной
  // прокрутки. На широком экране остаётся обычный список.
  export let deck = false;
  // Запрос, с которым открылся раздел: чтобы лента не грузилась дважды.
  export let initialQuery = '';
  // Коллекция, по которой отфильтрована лента. Пусто — показываем все подписки.
  export let collection = null;

  const dispatch = createEventDispatcher();

  let posts = [];
  let errors = [];
  // Каналы, которых в кэше сервера ещё не было, догружаются в фоне. Ленту спрашиваем ещё раз,
  // пока они не придут (не больше LOADING_RETRIES подряд), иначе их посты ждут следующего открытия.
  const LOADING_RETRIES = 3;
  let loadingRetries = 0;
  let loadingTimer = null;
  onDestroy(() => {
    clearTimeout(loadingTimer);
    clearInterval(checkTimer);
  });

  // Новые публикации: лента сама спрашивает первую страницу раз в минуту и считает посты, которые
  // новее верхнего и ещё не прочитаны. Экран не трогает: новые приходят кнопкой, чтобы не сбить чтение.
  const CHECK_MS = 60000;
  let freshCount = 0;
  let checkTimer = null;

  async function checkNew() {
    if (document.hidden || loading || !started || feedMode !== 'feed') return;
    if (query.trim().length >= 2) return;
    const list = feedChannels(tab, collection, $subs);
    if (!list.length) return;
    const params = new URLSearchParams({ channels: list.join(','), q: '' });
    if (!$showRead) params.set('hide_read', '1');
    try {
      const answer = await api.feed(params);
      const top = posts[0];
      const have = new Set(posts.map(postKey));
      freshCount = (answer.posts || []).filter(
        (p) => !have.has(postKey(p)) && !$reads.has(postKey(p)) && (!top || comesBefore(p, top))
      ).length;
    } catch (err) {
      // Фоновая проверка молчит: лента на экране остаётся как есть.
    }
  }

  function applyFresh() {
    freshCount = 0;
    window.scrollTo({ top: 0, behavior: 'smooth' });
    load(false);
  }
  let hasMore = false;
  let cursor = null;
  let loading = false;
  let failed = false;
  let query = initialQuery;
  // Фильтр по каналу внутри ленты живёт в оболочке: на широком экране он
  // стоит в шапке под поиском, на телефоне — над лентой.
  export let tab = '';
  let savedPosts = new Set();
  let focusIndex = -1;
  let requestId = 0;
  let controller = null;
  let searchHint = '';
  let pullHeight = 0;
  let pulling = false;
  let lastTouch = 0;

  // The day separator: a post of another day starts a new group.
  $: groups = groupByDay(posts);

  // Имя действующего фильтра: коллекция или канал.
  $: filterName = collection ? collection.name : tab;

  function groupByDay(items) {
    const out = [];
    let day = null;
    for (const post of items) {
      const label = dayLabel(post.ts);
      if (label !== day) {
        day = label;
        out.push({ day: label, posts: [] });
      }
      out[out.length - 1].posts.push(post);
    }
    return out;
  }

  async function load(append = false) {
    // Новый запрос отменяет предыдущий, а не ждёт его: клик по фильтру во время
    // загрузки иначе терялся, и лента оставалась прежней.
    failed = false;
    if (!append) freshCount = 0;
    const id = ++requestId;
    if (controller) controller.abort();
    controller = new AbortController();
    const signal = controller.signal;
    loading = true;
    // One letter matches almost everything: the feed gave back 57 posts out of
    // 60 and the reader lost their place. Below two letters we search nothing.
    const typed = query.trim();
    const effectiveQuery = typed.length >= 2 ? typed : '';
    try {
      let answer;
      if (feedMode === 'saved') {
        const params = new URLSearchParams();
        if (effectiveQuery) params.set('q', effectiveQuery);
        if (append && savedCursor) {
          params.set('before_saved', savedCursor.saved_at);
          params.set('before_channel', savedCursor.channel);
          params.set('before_id', savedCursor.msg_id);
        }
        answer = await api.saved(params);
        savedCursor = answer.next || null;
      } else {
        // Список каналов считаем здесь, а не в реактивной переменной: реактивная
        // строка обновилась бы только после выхода из функции, и запрос ушёл бы
        // со старым списком — фильтр не сработал бы.
        const list = feedChannels(tab, collection, $subs);
        const params = new URLSearchParams({ channels: list.join(','), q: effectiveQuery });
        if (append && cursor) {
          params.set('before_ts', cursor.ts);
          params.set('before_channel', cursor.channel);
          params.set('before_id', cursor.msg_id);
        }
        if (!$showRead) params.set('hide_read', '1');
        answer = await api.feed(params);
        cursor = answer.next || null;
      }
      if (id !== requestId) return;
      posts = append ? posts.concat(answer.posts || []) : answer.posts || [];
      syncSaved(answer.posts || [], !append);
      hasMore = Boolean(answer.has_more);
      errors = answer.errors || [];
      if (append || !answer.loading || !answer.loading.length) {
        loadingRetries = 0;
      } else if (loadingRetries < LOADING_RETRIES) {
        loadingRetries += 1;
        clearTimeout(loadingTimer);
        loadingTimer = setTimeout(() => load(false), 4000);
      }
      if (answer.titles) titles.set(answer.titles);
      // Один знак ничего не ищет: лента показана без отбора, и об этом сказано.
      searchHint = effectiveQuery
        ? `По запросу «${effectiveQuery}»: ${answer.posts ? answer.posts.length : 0} постов`
        : typed.length === 1
          ? 'Введи хотя бы два символа, чтобы искать.'
          : '';
      loadAvatars(posts.map((p) => p.channel));
      await tick();
      dispatch('ready', { key: screenKey });
    } catch (err) {
      // Отменённый запрос — это не ошибка: его заменил следующий.
      if (id !== requestId || (err && err.name === 'AbortError')) return;
      failed = true;
      if (!append) posts = [];
    } finally {
      if (id === requestId) loading = false;
    }
  }

  let savedCursor = null;

  // Флажок «сохранено» приходит с каждым постом. Без него после перезагрузки
  // сохранённые посты показывали «Сохранить», и снять сохранение было нельзя.
  function syncSaved(list, replace) {
    const keys = list.filter((post) => post.saved).map((post) => postKey(post));
    savedPosts = new Set(replace ? keys : [...savedPosts, ...keys]);
  }

  // Сохранение на сервере и отметка на экране меняются одним путём.
  function setServerSaved(post, on) {
    api.setSaved(post.channel, post.msg_id, on).catch(() => {});
    const key = postKey(post);
    if (on) savedPosts.add(key);
    else savedPosts.delete(key);
    savedPosts = new Set(savedPosts);
  }

  function insertAt(list, index, post) {
    const key = postKey(post);
    const rest = list.filter((item) => postKey(item) !== key);
    const at = index < 0 ? 0 : Math.min(index, rest.length);
    return [...rest.slice(0, at), post, ...rest.slice(at)];
  }

  function more() {
    if (loading || !hasMore) return;
    load(true);
  }

  // --- колода: по одной карточке, без вертикальной прокрутки ---

  // Насколько верхнюю карточку сдвинули от 0 до 1: следующая поднимается за ней.
  // dragging — палец ещё держит карточку, и поднимать её надо без задержки.
  let lift = 0;
  let dragging = false;
  function onLift(event) {
    lift = event.detail;
    dragging = lift > 0 && lift < 1;
  }

  // Карточку уже увезла её собственная анимация: здесь её убирают и меняют
  // отметки. «Вернуть» отменяет отметки и ставит карточку первой в колоде.
  function deckSwipe(event) {
    const { direction, post, was } = event.detail;
    const key = postKey(post);
    posts = posts.filter((item) => postKey(item) !== key);
    // Новая верхняя карточка начинает с нуля: та, что поднялась, уже стала верхней.
    lift = 0;
    // Колода скоро кончится: заранее берём следующую страницу.
    if (posts.length <= 3 && hasMore && !loading) load(true);

    // Жест влево в ленте сохраняет, в сохранённом убирает из сохранённого.
    // Любой жест отмечает пост прочитанным: он уже был на экране.
    const wantSaved = direction === 'left' ? !inSaved : was.saved;
    const readChanged = !was.read;
    const saveChanged = wantSaved !== was.saved;
    if (!readChanged && !saveChanged) return;
    if (readChanged) markRead(key);
    if (saveChanged) setServerSaved(post, wantSaved);
    const text =
      direction === 'right'
        ? 'Публикация отмечена прочитанной.'
        : wantSaved
          ? 'Публикация сохранена.'
          : 'Публикация убрана из сохранённого.';
    showUndo(text, () => {
      if (readChanged) markUnread(key);
      if (saveChanged) setServerSaved(post, was.saved);
      // На место по времени, а не наверх: за время отмены лента могла обновиться.
      posts = insertByTime(posts, post);
    });
  }

  // Кнопки под колодой: команда уходит верхней карточке, как будто её сдвинули.
  let stack;
  function deckButton(direction) {
    const card = stack && stack.querySelector('.deck-card:not(.behind) .post');
    if (card) card.dispatchEvent(new CustomEvent('deckaction', { detail: direction }));
  }

  $: currentDay = posts.length ? dayLabel(posts[0].ts) : '';
  // В сохранённом жест влево убирает пост из сохранённого, в ленте — сохраняет.
  $: inSaved = feedMode === 'saved';
  $: leftLabel = inSaved ? 'Убрать из сохранённого' : 'Сохранить';
  // Пустая колода: то ли всё прочитано, то ли искать нечего.
  // В сохранённом пусто не оттого, что нет каналов: там сохраняют кнопкой.
  $: deckEmptyTitle = query
    ? 'Ничего не нашлось'
    : feedMode === 'saved'
      ? 'Сохранённых публикаций нет'
      : subs.length
        ? 'Ты всё прочитал'
        : 'Пока пусто';
  $: deckEmptyText = query
    ? 'Измени запрос или выбери канал.'
    : feedMode === 'saved'
      ? 'Публикацию сохраняет кнопка «Сохранить» у поста.'
      : subs.length
        ? 'Новых публикаций нет. Загляни позже или проверь сейчас.'
        : 'Добавь канал в каталоге — и публикации появятся здесь.';

  // The next page arrives when the reader comes close to the end of the column.
  let loadMoreSeen = false;
  function onScroll() {
    if (loading || !hasMore) return;
    const near = window.innerHeight + window.scrollY > document.body.offsetHeight - 900;
    if (near && !loadMoreSeen) {
      loadMoreSeen = true;
      more();
      setTimeout(() => (loadMoreSeen = false), 400);
    }
  }

  // Прокрутка только листает. Прочитанным пост становится по кнопке «Отметить прочитанным»,
  // по жесту вправо в колоде или по клавише u: отметка по прокрутке убрана.

  // Обновление ленты по событию: так же обновляет жест «потянуть вниз».
  function onRefresh() {
    pullRefresh();
  }

  function onSearch(event) {
    query = event.detail || '';
    load(false);
  }

  function onKey(event) {
    const key = event.detail;
    if (key === 'j') step(1);
    if (key === 'k') step(-1);
    if (key === 'n') jumpUnread();
    if (key === 'u') markFocused();
  }

  function step(delta) {
    if (!posts.length) return;
    const next = Math.min(Math.max(focusIndex + delta, 0), posts.length - 1);
    focusIndex = next;
    const node = document.querySelector(`.post[data-post="${cssEscape(posts[next])}"]`);
    if (node) node.scrollIntoView({ block: 'center' });
  }

  function jumpUnread() {
    const index = posts.findIndex((post) => !$reads.has(postKey(post)));
    if (index < 0) return;
    focusIndex = index;
    const node = document.querySelector(`.post[data-post="${cssEscape(posts[index])}"]`);
    if (node) node.scrollIntoView({ block: 'center' });
  }

  function markFocused() {
    if (focusIndex < 0 || !posts[focusIndex]) return;
    const key = postKey(posts[focusIndex]);
    if ($reads.has(key)) markUnread(key); // u снимает отметку
    else markRead(key);
  }

  // Ключ поста для селектора: сюда приходит сам пост, а не строка.
  function cssEscape(post) {
    return postKey(post).replace(/"/g, '\\"');
  }

  // Кнопка «Сохранено» у поста. В сохранённом снятие убирает пост из списка,
  // а «Вернуть» ставит его на прежнее место.
  function toggleSave(event) {
    const { post, saved } = event.detail;
    if (!saved) {
      setServerSaved(post, true);
      return;
    }
    const key = postKey(post);
    const index = posts.findIndex((item) => postKey(item) === key);
    const inSavedList = feedMode === 'saved';
    setServerSaved(post, false);
    if (inSavedList) posts = posts.filter((item) => postKey(item) !== key);
    showUndo('Публикация убрана из сохранённого.', () => {
      setServerSaved(post, true);
      if (inSavedList) posts = insertAt(posts, index, post);
    });
  }

  function toggleReadAll() {
    setShowRead(!$showRead);
  }

  // Клик по имени канала в посте — это фильтр ленты, а не переход на страницу
  // канала: второй клик снимает фильтр. Страница канала открывается из каталога.
  function pickChannel(channel) {
    dispatch('tab', channel);
  }

  // Фильтр по каналу и «скрыть прочитанные» приходят из оболочки: нажали там
  // — лента перечиталась здесь.
  let lastTab = tab;
  $: if (tab !== lastTab) {
    lastTab = tab;
    load(false);
  }

  let lastShowRead = $showRead;
  $: if ($showRead !== lastShowRead) {
    lastShowRead = $showRead;
    load(false);
  }

  // Смена коллекции — тоже смена состава ленты: без перезапроса посты остались
  // бы прежними, а подпись обещала коллекцию.
  let lastCollectionKey = '';
  // Ключ — с составом: изменили каналы коллекции, и лента должна перечитаться.
  $: collectionKey = collection
    ? String(collection.id) + ':' + ((collection.channels || []).join(','))
    : '';
  $: if (collectionKey !== lastCollectionKey) {
    lastCollectionKey = collectionKey;
    load(false);
  }

  // --- the gesture "pull to refresh" ---

  function onTouchStart(event) {
    // Жест только в самом верху ленты и одним пальцем, и не внутри окна:
    // в просмотрщике и в диалогах тянуть нечего. В колоде обновляет кнопка:
    // вертикальная протяжка там — прокрутка карточки, а не обновление.
    if (deck || window.scrollY > 0) return;
    if (event.touches.length !== 1) return;
    if (event.target && event.target.closest && event.target.closest('[role="dialog"]')) return;
    lastTouch = event.touches[0].clientY;
    pulling = true;
  }

  function onTouchMove(event) {
    if (!pulling) return;
    const distance = event.touches[0].clientY - lastTouch;
    if (distance <= 0) {
      pullHeight = 0;
      return;
    }
    pullHeight = Math.min(distance * 0.4, 70);
  }

  function onTouchEnd() {
    if (!pulling) return;
    pulling = false;
    if (pullHeight > 48) onRefresh();
    pullHeight = 0;
  }

  // Пока идёт обновление, индикатор виден: иначе жест выглядит как ничего
  // не сделавший.
  let refreshing = false;

  function pullRefresh() {
    refreshing = true;
    load(false).finally(() => (refreshing = false));
  }

  // Лента ждёт подписок: без них запрос ушёл бы без каналов, а сервер отдал
  // бы весь кэш — на экране мелькнули бы чужие каналы.
  let started = false;
  $: if ($stateReady && !started) {
    started = true;
    load(false);
  }

  onMount(() => {
    window.addEventListener('scroll', onScroll, { passive: true });
    window.addEventListener('koloda:search', onSearch);
    window.addEventListener('koloda:refresh', onRefresh);
    window.addEventListener('koloda:key', onKey);
    window.addEventListener('koloda:check', checkNew);
    checkTimer = setInterval(checkNew, CHECK_MS);
    return () => {
      clearInterval(checkTimer);
      window.removeEventListener('koloda:check', checkNew);
      window.removeEventListener('scroll', onScroll);
      window.removeEventListener('koloda:search', onSearch);
      window.removeEventListener('koloda:refresh', onRefresh);
      window.removeEventListener('koloda:key', onKey);
      if (controller) controller.abort();
    };
  });

  // Смена режима (лента ↔ сохранённое) перечитывает список. При первом
  // показе загрузку делает onMount: иначе запрос уходил бы дважды.
  let lastFeedMode = feedMode;
  $: if (feedMode !== lastFeedMode) {
    lastFeedMode = feedMode;
    load(false);
  }
</script>

<svelte:window
  on:touchstart={onTouchStart}
  on:touchmove|passive={onTouchMove}
  on:touchend={onTouchEnd}
  on:touchcancel={onTouchEnd}
/>

{#if deck}
  <!-- Колода: одна карточка, без вертикальной прокрутки. Вправо прочитано,
       влево сохранить. -->
  <div class="deck" id="deck">
    <div class="deck-top">
      <span class="deck-date">{currentDay}</span>
      <div class="deck-tools">
        {#if filterName}
          <Chip tone="accent" removable removeLabel="Снять фильтр" on:remove={() => dispatch('tab', '')}>
            {filterName}
          </Chip>
        {/if}
        {#if freshCount}
          <Button variant="secondary" size="sm" on:click={applyFresh}>Новые публикации: {freshCount}</Button>
        {/if}
        {#if feedMode === 'feed'}
          <span id="readToggleDeck" class="read-toggle">
            <Button variant="quiet" size="sm" on:click={toggleReadAll}>
              {$showRead ? 'Скрыть прочитанные' : 'Показать прочитанные'}
            </Button>
          </span>
        {/if}
        <!-- Обновить — значок: подпись остаётся в aria-label и в подсказке. Так строка колоды
             влезает в одну линию, как панель инструментов в Material 3. -->
        <IconButton label="Обновить" variant="ghost" size="sm" on:click={() => load(false)}>
          <Icon name="refresh" size={18} />
        </IconButton>
        <!-- Полный экран: шапка и разделы уходят, карточка занимает экран. Выход — этой же кнопкой. -->
        <Button variant="quiet" size="sm" on:click={() => setImmersive(!$immersive)} aria-pressed={$immersive}>
          {$immersive ? 'Обычный вид' : 'Полный экран'}
        </Button>
      </div>
      {#if searchHint}
        <p class="deck-hint" role="status">{searchHint}</p>
      {/if}
    </div>

    <div class="deck-stack" class:dragging bind:this={stack}>
      {#if loading && !posts.length}
        <div class="skeletons">
          <Skeleton height="8rem" />
          <Skeleton height="8rem" />
        </div>
      {:else if failed}
        <EmptyState title="Не удалось загрузить ленту" description="Проверь соединение и попробуй ещё раз.">
          <svelte:fragment slot="actions">
            <Button on:click={() => load(false)}>Попробовать снова</Button>
          </svelte:fragment>
        </EmptyState>
      {:else if !posts.length}
        <EmptyState title={deckEmptyTitle} description={deckEmptyText}>
          <svelte:fragment slot="actions">
            {#if feedMode !== 'saved'}
              <Button on:click={() => load(false)}>Проверить новые</Button>
            {/if}
          </svelte:fragment>
        </EmptyState>
      {:else}
        {#each posts.slice(0, 2) as post, i (postKey(post))}
          <div class="deck-card" class:behind={i === 1} style="--lift: {lift}">
            <PostCard
              {post}
              front={i === 0}
              {leftLabel}
              on:lift={onLift}
              read={$reads.has(postKey(post))}
              title={$titles[post.channel]}
              avatar={$avatars.get(post.channel) || ''}
              saved={savedPosts.has(postKey(post))}
              on:openChannel={(event) => pickChannel(event.detail)}
              on:lightbox={(event) => dispatch('lightbox', event.detail)}
              on:toggleSave={toggleSave}
              on:swipe={deckSwipe}
            />
          </div>
        {/each}
      {/if}
    </div>

    <!-- Кнопки делают то же, что жест: левая сохраняет, правая отмечает прочитанным. -->
    <div class="deck-actions">
      <button type="button" class="deck-btn" disabled={!posts.length} on:click={() => deckButton('left')}>
        <span class="deck-ring deck-save"><Icon name={inSaved ? 'close' : 'bookmark'} size={24} /></span>
        <span class="deck-label">{leftLabel}</span>
      </button>
      <button type="button" class="deck-btn" disabled={!posts.length} on:click={() => deckButton('right')}>
        <span class="deck-ring deck-read"><Icon name="check" size={26} /></span>
        <span class="deck-label">Прочитано</span>
      </button>
    </div>
  </div>
{:else}
<div
  class="pull"
  data-busy={refreshing ? '1' : null}
  data-drag={pullHeight ? '1' : null}
  style="height: {refreshing ? 40 : pullHeight}px"
>
  <Icon name="refresh" />
  <span>{refreshing ? 'Обновляю…' : 'Потяни, чтобы обновить'}</span>
</div>

<div class="column" id="feed">
  {#if feedMode === 'saved'}
    <h1 class="feed-title">Сохранённые публикации</h1>
    <p class="fresh">То, что ты отметил «Сохранить». Порядок — по времени сохранения.</p>
  {/if}

  {#if feedMode === 'feed'}
    <div class="feed-bar phone-only">
      <!-- Выбор канала и коллекции делается через папку: отдельного списка
           «Все каналы» не нужно, действие у них одно. Здесь только то, чем
           фильтр снять, и переключатель прочитанного. -->
      {#if filterName}
        <Chip tone="accent" removable removeLabel="Снять фильтр" on:remove={() => dispatch('tab', '')}>
          {filterName}
        </Chip>
      {/if}
      <span id="readToggle" class="read-toggle">
        <Button variant="text" size="sm" on:click={toggleReadAll}>
          {$showRead ? 'Скрыть прочитанные' : 'Показать прочитанные'}
        </Button>
      </span>
    </div>
  {/if}

  {#if searchHint}
    <p class="fresh" role="status">{searchHint}</p>
  {/if}

  {#if errors.length}
    <Notice tone="warning" title="Часть каналов не обновилась">
      {errors.map((e) => e.channel).join(', ')}
    </Notice>
  {/if}

  <div id="postList">
  {#if loading && !posts.length}
    <div class="skeletons">
      <Skeleton height="8rem" />
      <Skeleton height="8rem" />
      <Skeleton height="8rem" />
    </div>
  {:else if failed}
    <EmptyState title="Не удалось загрузить ленту" description="Проверь соединение и попробуй ещё раз.">
      <svelte:fragment slot="actions">
        <Button on:click={() => load(false)}>Попробовать снова</Button>
      </svelte:fragment>
    </EmptyState>
  {:else if !posts.length}
    <EmptyState
      title={query
        ? 'Ничего не нашлось'
        : feedMode === 'saved'
          ? 'Сохранённых публикаций нет'
          : collection
            ? 'В коллекции пока нет каналов'
            : 'Пока пусто'}
      description={query
        ? 'Измени запрос или выбери канал.'
        : feedMode === 'saved'
          ? 'Публикацию сохраняет кнопка «Сохранить» у поста.'
          : collection
            ? 'Добавь каналы в коллекцию — из списка каналов или со страницы канала.'
            : 'Добавь канал в каталоге — и публикации появятся здесь.'}
    >
      <svelte:fragment slot="actions">
        {#if query}
          <!-- Поле поиска в шапке общее с лентой: сбрасываем его там, а не только здесь. -->
          <Button on:click={() => dispatch('clearSearch')}>Сбросить поиск</Button>
        {/if}
      </svelte:fragment>
    </EmptyState>
  {:else}
    {#if freshCount}
      <div class="fresh-new">
        <Button variant="secondary" size="sm" on:click={applyFresh}>Новые публикации: {freshCount}</Button>
      </div>
    {/if}
    {#each groups as group (group.day)}
      <p class="day-sep">{group.day}</p>
      {#each group.posts as post (postKey(post))}
        <PostCard
          {post}
          read={$reads.has(postKey(post))}
          title={$titles[post.channel]}
          avatar={$avatars.get(post.channel) || ''}
          saved={savedPosts.has(postKey(post))}
          on:openChannel={(event) => pickChannel(event.detail)}
          on:lightbox={(event) => dispatch('lightbox', event.detail)}
          on:toggleSave={toggleSave}
        />
      {/each}
    {/each}

    {#if hasMore}
      <Button class="load-more" variant="text" on:click={more} loading={loading}>
        {loading ? 'Загружаю…' : 'Загрузить ещё'}
      </Button>
    {:else if posts.length}
      <p class="feed-empty-more">
        {feedMode === 'saved'
          ? 'Это все сохранённые публикации.'
          : 'Это все публикации твоих каналов. Более старые посты каждого канала есть в Telegram.'}
      </p>
    {/if}
  {/if}
  </div>
</div>
{/if}

<style>
  .fresh-new {
    display: flex;
    justify-content: center;
    padding: var(--ui-space-base) 0;
  }

  /* --- колода: одна карточка, без вертикальной прокрутки --- */

  .deck {
    display: flex;
    flex-direction: column;
    gap: var(--ui-space-snug);
    height: 100%;
    padding: var(--ui-space-snug) var(--ui-space-comfortable) var(--ui-space-base);
  }

  .deck-top {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--ui-space-snug);
    flex-wrap: wrap;
  }

  .deck-date {
    color: var(--ui-ink-faint);
    font-size: 12px;
    font-weight: 600;
  }

  .deck-tools {
    display: flex;
    align-items: center;
    gap: var(--ui-space-snug);
    flex-wrap: wrap;
  }

  .deck-stack {
    position: relative;
    flex: 1 1 auto;
    min-height: 0;
    width: 100%;
    max-width: 460px;
    margin: 0 auto;
  }

  /* Карточка заполняет стопку. Следующая лежит под ней уже и на отступ ниже:
     полоска внизу показывает, что колода не кончилась. */
  .deck-card {
    position: absolute;
    inset: 0 0 var(--ui-space-base);
    z-index: 1;
    transition: transform var(--ui-duration-base) cubic-bezier(0.2, 0.7, 0.3, 1);
  }

  /* Следующая карточка поднимается вместе с протяжкой: при --lift 0 она чуть меньше
     и выглядывает снизу полоской, при 1 — во весь размер. Подъём идёт через transform,
     а не через inset: inset пересчитывал раскладку на каждом движении пальца, и карточка
     подлагивала на телефоне (замер в браузере: ~45 пересчётов раскладки на протяжку). */
  .deck-card.behind {
    inset: 0;
    z-index: 0;
    pointer-events: none;
    transform-origin: 50% 100%;
    transform: scale(calc(0.94 + 0.06 * var(--lift)));
    will-change: transform;
  }

  /* Пока палец тянет карточку, следующая идёт за ним без задержки. */
  .deck-stack.dragging .deck-card {
    transition: none;
  }

  /* Телефон в лежачем положении: высоты мало, и кнопки под колодой съели бы
     карточку (была 108 px). Кнопки уходят в правую колонку, карточка получает
     всю высоту под шапкой колоды. */
  @media (max-height: 500px) {
    .deck {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      grid-template-rows: auto minmax(0, 1fr);
    }

    .deck-top {
      grid-column: 1 / -1;
    }

    .deck-stack {
      grid-column: 1;
      grid-row: 2;
      width: auto;
      max-width: none;
      margin: 0;
    }

    .deck-actions {
      grid-column: 2;
      grid-row: 2;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      gap: var(--ui-space-base);
      padding-left: var(--ui-space-base);
    }
  }

  /* Кто просит меньше движения, тот не видит подъёма и полёта: карточка
     меняется сразу. Полёт остаётся, только без перехода. */
  @media (prefers-reduced-motion: reduce) {
    .deck-card,
    .deck-card :global(.post),
    .deck-card.behind :global(.post > *),
    .deck-ring {
      transition: none;
    }
  }

  /* Следующая карточка в покое — пустая рамка: её текст проступал бы в полоске
     под верхней. Текст проявляется по мере протяжки. */
  .deck-card.behind :global(.post > *) {
    opacity: var(--lift);
    transition: opacity var(--ui-duration-base);
  }

  .deck-stack.dragging .deck-card.behind :global(.post > *) {
    transition: none;
  }

  /* Фото — лицо карточки: первый кадр занимает всё, что осталось между текстом
     и действиями. Остальные кадры скрыты, счётчик в углу говорит, что они есть. */
  .deck-card :global(.galw) {
    display: flex;
    flex: 1 0 auto;
    min-height: 240px;
    margin: var(--ui-space-base) 0 0;
  }

  .deck-card :global(.gal) {
    flex: 1 1 auto;
    min-height: 0;
  }

  .deck-card :global(.galw .gal > *:not(:first-child)) {
    display: none;
  }

  .deck-card :global(.galw .gal > *) {
    flex: 1 1 auto;
    width: 100%;
    max-width: none;
    height: 100%;
    max-height: none;
    margin: 0;
  }

  .deck-card :global(.galw .gal-frame > img) {
    height: 100%;
    max-height: none;
    aspect-ratio: auto;
    object-fit: cover;
    margin: 0;
  }

  /* Сама карточка — это пост: поверхность, скругление и тень. Прокручивается
     внутри себя, и прокрутка не уходит в страницу. Скругление — четыре радиуса
     кита, чтобы оно оставалось на его шкале. */
  .deck-card :global(.post) {
    display: flex;
    flex-direction: column;
    height: 100%;
    margin: 0;
    padding: var(--ui-space-comfortable);
    overflow-y: auto;
    overscroll-behavior: contain;
    -webkit-overflow-scrolling: touch;
    border: 0;
    border-radius: calc(var(--ui-radius) * 4);
    background: var(--ui-surface);
    box-shadow: var(--ui-shadow-lift);
    content-visibility: visible;
  }

  /* Текстовый пост в ленте ограничен по ширине (34em), в колоде карточка
     занимает всю стопку: иначе на широком экране телефона следующая карточка
     выглядывала бы справа. */
  .deck-card :global(article.post) {
    max-width: none;
  }

  .deck-card :global(.txt) {
    font-size: 16px;
    line-height: 1.6;
  }

  /* Действия поста уходят вниз карточки: там палец их и ищет. «Сохранить» в
     колоде есть кнопкой под ней, здесь она лишняя. */
  .deck-card :global(.post-actions) {
    margin-top: auto;
    padding-top: var(--ui-space-base);
  }

  .deck-card :global(.act-save) {
    display: none;
  }

  /* Аватар канала в карточке круглый: как фото в приложениях для знакомств. */
  .deck-card :global(.chan-dot) {
    border-radius: 999px;
  }

  .deck-actions {
    display: flex;
    justify-content: center;
    gap: var(--ui-space-section);
  }

  /* Подпись кнопки может стать длинной («Убрать из сохранённого»): переносится. */
  .deck-label {
    max-width: 8em;
    text-align: center;
    line-height: 1.25;
  }

  .deck-hint {
    flex: 1 0 100%;
    margin: 0;
    color: var(--ui-ink-soft);
    font-size: 12.5px;
  }

  .deck-btn {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: var(--ui-space-hair);
    padding: 0;
    border: 0;
    background: none;
    color: var(--ui-ink-soft);
    font: inherit;
    font-size: 12px;
    cursor: pointer;
  }

  .deck-btn:disabled {
    opacity: 0.4;
    cursor: default;
  }

  /* Круг кнопки — полтора контрола кита. */
  .deck-ring {
    display: grid;
    place-items: center;
    width: calc(var(--ui-control-xl) * 1.5);
    height: calc(var(--ui-control-xl) * 1.5);
    border-radius: 999px;
    background: var(--ui-surface);
    border: 1px solid var(--ui-line);
    box-shadow: var(--ui-shadow-rest);
    transition: transform var(--ui-duration-fast);
  }

  .deck-btn:active .deck-ring {
    transform: scale(0.92);
  }

  .deck-save {
    color: var(--ui-accent-text);
  }

  .deck-read {
    color: var(--ui-success-text);
  }

  .feed-title {
    font-size: 20px;
    line-height: 1.25;
    font-weight: 700;
    letter-spacing: -0.02em;
    margin: var(--ui-space-base) 0 0;
  }

  .feed-bar {
    display: flex;
    align-items: center;
    gap: var(--ui-space-snug);
    flex-wrap: wrap;
    padding: 0 0 var(--ui-space-snug);
  }

  .skeletons {
    display: flex;
    flex-direction: column;
    gap: var(--ui-space-base);
  }

  .feed-empty-more {
    color: var(--ui-ink-faint);
    font-size: 12px;
    text-align: center;
    padding: var(--ui-space-comfortable) 0 var(--ui-space-roomy);
  }

  /* На широком экране фильтры стоят в шапке под поиском: там глаз идёт от
     поля к постам, а здесь они оторваны от поиска. */
  @media (min-width: 901px) and (pointer: fine) {
    .phone-only {
      display: none;
    }
  }
</style>

<script>
  /**
   * The feed: the column of posts, the marks, the next page and the search.
   * The saved screen is the same column with another source.
   */
  import { onMount, onDestroy, tick } from 'svelte';
  import { createEventDispatcher } from 'svelte';
  import { Button, EmptyState, Notice, Skeleton, Spinner } from '@monos/ui';

  import PostCard from './PostCard.svelte';
  import Icon from './Icon.svelte';
  import { api } from '../lib/api.js';
  import { dayLabel, postKey } from '../lib/format.js';
  import { subs, reads, titles, avatars, markRead, loadAvatars, showUndo, setSubscription } from '../lib/stores.js';
  import { feedChannels } from '../lib/feed.js';

  export let feedMode = 'feed';
  // Коллекция, по которой отфильтрована лента. Пусто — показываем все подписки.
  export let collection = null;

  const dispatch = createEventDispatcher();

  let posts = [];
  let errors = [];
  let hasMore = false;
  let cursor = null;
  let loading = false;
  let failed = false;
  let showRead = false;
  let query = '';
  let tab = ''; // the channel filter inside the feed
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
    const id = ++requestId;
    if (controller) controller.abort();
    controller = new AbortController();
    const signal = controller.signal;
    loading = true;
    // One letter matches almost everything: the feed gave back 57 posts out of
    // 60 and the reader lost their place. Below two letters we search nothing.
    const effectiveQuery = query.trim().length >= 2 ? query.trim() : '';
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
        if (!showRead) params.set('hide_read', '1');
        answer = await api.feed(params);
        cursor = answer.next || null;
      }
      if (id !== requestId) return;
      posts = append ? posts.concat(answer.posts || []) : answer.posts || [];
      hasMore = Boolean(answer.has_more);
      errors = answer.errors || [];
      if (answer.titles) titles.set(answer.titles);
      searchHint = effectiveQuery
        ? `По запросу «${effectiveQuery}»: ${answer.posts ? answer.posts.length : 0} постов`
        : '';
      loadAvatars(posts.map((p) => p.channel));
      await tick();
      markPassed();
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

  function more() {
    if (loading || !hasMore) return;
    load(true);
  }

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
    markPassed();
  }

  // A post is read when it goes over the top of the screen. A post that a
  // refresh put above the screen was never measured and stays unread.
  let lastBottom = new WeakMap();
  function markPassed() {
    for (const node of document.querySelectorAll('.post[data-post]')) {
      const bottom = node.getBoundingClientRect().bottom;
      if (lastBottom.get(node) > 0 && bottom <= 0) {
        const key = node.dataset.post;
        if (!$reads.has(key)) markRead(key);
      }
      lastBottom.set(node, bottom);
    }
  }

  // Обновление ленты по событию: так же обновляет жест «потянуть вниз».
  function onRefresh() {
    load(false);
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
    if ($reads.has(key)) markRead(key); // the mark is taken back
    else markUnread(key);
  }

  function cssEscape(post) {
    return (post.channel + '/' + post.msg_id).replace(/"/g, '\\"');
  }

  function toggleSave(event) {
    const { post, saved } = event.detail;
    api.setSaved(post.channel, post.msg_id, !saved).catch(() => {});
    if (saved) {
      savedPosts.delete(postKey(post));
      showUndo('Публикация убрана из сохранённого.', () => {
        api.setSaved(post.channel, post.msg_id, true).catch(() => {});
        savedPosts.add(postKey(post));
      });
    } else {
      savedPosts.add(postKey(post));
    }
    savedPosts = new Set(savedPosts);
  }

  function toggleReadAll() {
    showRead = !showRead;
    load(false);
  }

  // Клик по имени канала в посте — это фильтр ленты, а не переход на страницу
  // канала: второй клик снимает фильтр. Страница канала открывается из каталога.
  function pickChannel(channel) {
    tab = tab === channel ? '' : channel;
    load(false);
  }

  // --- the gesture "pull to refresh" ---

  function onTouchStart(event) {
    if (window.scrollY > 0) return;
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

  onMount(() => {
    window.addEventListener('scroll', onScroll, { passive: true });
    window.addEventListener('koloda:search', onSearch);
    window.addEventListener('koloda:refresh', onRefresh);
    window.addEventListener('koloda:key', onKey);
    load(false);
    return () => {
      window.removeEventListener('scroll', onScroll);
      window.removeEventListener('koloda:search', onSearch);
      window.removeEventListener('koloda:refresh', onRefresh);
      window.removeEventListener('koloda:key', onKey);
      if (controller) controller.abort();
    };
  });

  $: if (feedMode) load(false);
</script>

<div class="pull" data-drag={pullHeight ? '1' : null} style="height: {pullHeight}px">
  <Icon name="refresh" />
  <span>Потяни, чтобы обновить</span>
</div>

<div class="column" id="feed">
  <div class="tabs-wrap">
    <button type="button" class="tabs-toggle" on:click={() => dispatch('openChannels')}>
      <Icon name="chevron" size={16} />
      <span id="tabsToggleLabel">{collection ? collection.name : tab ? tab : 'Все каналы'}</span>
      <Icon name="chevron" size={16} />
    </button>
  </div>

  <div class="feed-bar">
    <span id="readToggle" class="read-toggle">
      <Button variant="text" size="sm" on:click={toggleReadAll} aria-pressed={!showRead}>
        {showRead ? 'Показать прочитанные' : 'Скрыть прочитанные'}
      </Button>
    </span>
    {#if tab || collection}
      <Button variant="text" size="sm" on:click={() => pickChannel('')}>Все каналы</Button>
    {/if}
  </div>

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
      <Skeleton class="h-32" />
      <Skeleton class="h-32" />
      <Skeleton class="h-32" />
    </div>
  {:else if failed}
    <EmptyState title="Не удалось загрузить ленту" description="Проверь соединение и попробуй ещё раз.">
      <svelte:fragment slot="actions">
        <Button on:click={() => load(false)}>Попробовать снова</Button>
      </svelte:fragment>
    </EmptyState>
  {:else if !posts.length}
    <EmptyState
      title={query ? 'Ничего не нашлось' : 'Пока пусто'}
      description={query
        ? 'Измени запрос или выбери канал.'
        : 'Добавь канал в каталоге — и публикации появятся здесь.'}
    >
      <svelte:fragment slot="actions">
        {#if query}
          <Button on:click={() => {
            query = '';
            load(false);
          }}>Сбросить поиск</Button>
        {/if}
      </svelte:fragment>
    </EmptyState>
  {:else}
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

<style>
  .tabs-wrap {
    position: relative;
    max-width: 748px;
    margin: 0 auto;
    padding: var(--ui-space-base) var(--ui-space-roomy) var(--ui-space-snug);
  }

  .tabs-toggle {
    display: flex;
    align-items: center;
    gap: var(--ui-space-snug);
    width: 100%;
    min-height: 44px;
    padding: var(--ui-space-base);
    font: inherit;
    font-size: 14px;
    text-align: left;
    cursor: pointer;
    background: none;
    border: 0;
    border-bottom: 1px solid var(--ui-line);
    border-radius: 0;
    color: var(--ui-ink);
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
</style>

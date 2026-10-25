<script>
  /**
   * One channel: its latest publications, the subscription and the link to
   * Telegram. The same card as in the feed, the same marks.
   */
  import { onMount, onDestroy, tick } from 'svelte';
  import { createEventDispatcher } from 'svelte';
  import { Button, EmptyState, Notice, Skeleton } from '@monos/ui';

  import PostCard from './PostCard.svelte';
  import Icon from './Icon.svelte';
  import { api } from '../lib/api.js';
  import { postKey } from '../lib/format.js';
  import {
    subs,
    reads,
    titles,
    avatars,
    collections,
    setSubscription,
    showUndo,
    loadAvatars,
    markRead,
  } from '../lib/stores.js';

  export let channelName = '';
  // Имя канала проверяется до запроса: такого имени в Telegram нет, сервер
  // отвечал бы 400, а экран предлагал бы подписаться. Правило то же, что на сервере.
  const CHANNEL_NAME = /^[A-Za-z0-9_]{3,32}$/;
  $: validName = CHANNEL_NAME.test(channelName);
  // Ключ экрана: по нему оболочка понимает, что посты готовы и место
  // прокрутки можно вернуть.
  export let screenKey = '';
  // Название, которое знает каталог: сервер на странице канала отдаёт его не сразу.
  export let initialTitle = '';
  // Заголовок живёт здесь: prop перезаписывается родителем при каждой его
  // перерисовке, и название канала терялось бы.
  let heading = initialTitle;
  $: if (initialTitle && initialTitle !== heading) heading = initialTitle;

  const dispatch = createEventDispatcher();

  let posts = [];
  let hasMore = false;
  let cursor = null;
  let loading = false;
  let failed = false;
  let requestId = 0;
  let saved = new Set();
  // Коллекции, в которых лежит канал: их видно и можно поправить отсюда.
  $: mine = $collections.filter((c) => (c.channels || []).includes(channelName));

  $: subscribed = $subs.includes(channelName);
  $: mark = $avatars.get(channelName) || '';

  async function load(append = false) {
    if (loading || !validName) return;
    loading = true;
    failed = false;
    const id = ++requestId;
    try {
      const params = new URLSearchParams({ c: channelName });
      if (append && cursor) {
        params.set('before_ts', cursor.ts);
        params.set('before_channel', cursor.channel);
        params.set('before_id', cursor.msg_id);
      }
      const answer = await api.preview(params);
      if (id !== requestId) return;
      posts = append ? posts.concat(answer.posts || []) : answer.posts || [];
      hasMore = Boolean(answer.has_more);
      cursor = answer.next || null;
      // Название приходит с первой страницей: по прямой ссылке его нет ни в каталоге, ни в ленте.
      if (answer.title && answer.title !== channelName) heading = answer.title;
      loadAvatars([channelName]);
      await tick();
      dispatch('ready', { key: screenKey });
    } catch (err) {
      if (id !== requestId) return;
      failed = true;
    } finally {
      if (id === requestId) loading = false;
    }
  }

  // Отмена возвращает прежнее состояние: после подписки — отписка, и наоборот,
  // как в каталоге.
  function toggle() {
    const on = !subscribed;
    setSubscription(channelName, on);
    if (on) {
      showUndo('Канал «' + channelName + '» добавлен в ленту.', () => setSubscription(channelName, false));
    } else {
      showUndo('Канал «' + channelName + '» убран из ленты.', () => setSubscription(channelName, true));
    }
  }

  function toggleSave(event) {
    const { post, saved: wasSaved } = event.detail;
    api.setSaved(post.channel, post.msg_id, !wasSaved).catch(() => {});
  }

  onMount(() => {
    if (validName) load(false);
    else dispatch('ready', { key: screenKey });
  });
</script>

<div class="column ch-page" id="viewChannel">
  {#if !validName}
    <EmptyState title="Такого канала не бывает" description="Имя канала — от 3 до 32 латинских букв, цифр и подчёркиваний. Проверь ссылку или выбери канал в каталоге." />
  {:else}
    <!-- По прямой ссылке название приходит с первой страницей канала: до тех пор
         берём его из кэша, а логин остаётся только там, где названия нет нигде. -->
    <h1 id="chTitle" class="phone-hidden-title">{heading || $titles[channelName] || channelName}</h1>
    <p class="ch-meta" id="chMeta">@{channelName}</p>

    <div class="ch-actions">
      <span id="chAction">
        <Button class="cta" on:click={toggle}>{subscribed ? 'Убрать из ленты' : 'Добавить в ленту'}</Button>
      </span>
      <span id="chColl" class="ch-coll">
        {#if subscribed}
          <Button variant="text" size="sm" on:click={() => dispatch('channelCollections', channelName)}>
            {mine.length ? 'В коллекциях: ' + mine.length : 'В коллекцию'}
          </Button>
          <span id="chCollState">
            {mine.length ? 'В коллекциях: ' + mine.map((c) => c.name).join(', ') : 'Пока ни в одной коллекции'}
          </span>
        {:else}
          <!-- Кнопки нет, пока канал не подписан: сказать об этом лучше, чем
               показать пустое место. -->
          <span id="chCollState" class="hint">
            Подпишись на канал — и его можно будет положить в коллекцию.
          </span>
        {/if}
      </span>
      <a class="ch-link" id="chTg" href={'https://t.me/' + channelName} target="_blank" rel="noopener">
        <Icon name="external" size={14} />
        Открыть в Telegram
      </a>
    </div>

    {#if loading && !posts.length}
      <div class="skeletons">
        <Skeleton class="h-32" />
        <Skeleton class="h-32" />
      </div>
    {:else if failed}
      <EmptyState title="Не удалось загрузить публикации" description="Попробуй позже.">
        <svelte:fragment slot="actions">
          <Button on:click={() => load(false)}>Попробовать снова</Button>
        </svelte:fragment>
      </EmptyState>
    {:else if !posts.length}
      <EmptyState title="Публикаций нет" description="Этот канал пока ничего не опубликовал." />
    {:else}
      {#each posts as post (postKey(post))}
        <PostCard
          {post}
          read={$reads.has(postKey(post))}
          title={$titles[post.channel] || heading}
          avatar={$avatars.get(post.channel) || ''}
          saved={saved.has(postKey(post))}
          on:lightbox={(event) => dispatch('lightbox', event.detail)}
          on:toggleSave={toggleSave}
        />
      {/each}

      {#if hasMore}
        <Button class="load-more" variant="text" on:click={() => load(true)} loading={loading}>
          {loading ? 'Загружаю…' : 'Загрузить ещё'}
        </Button>
      {:else}
        <p class="ch-end">
          Более старые посты есть в <a href={'https://t.me/' + channelName} target="_blank" rel="noopener">Telegram</a>.
        </p>
      {/if}
    {/if}
  {/if}
</div>

<style>
  .ch-page {
    padding-top: var(--ui-space-roomy);
  }

  h1 {
    font-size: 24px;
    line-height: 1.25;
    font-weight: 700;
    letter-spacing: -0.025em;
    margin: var(--ui-space-snug) 0;
  }

  .ch-coll {
    display: inline-flex;
    align-items: center;
    gap: var(--ui-space-snug);
  }

  .ch-link {
    display: inline-flex;
    align-items: center;
    gap: var(--ui-space-field);
    min-height: 36px;
    padding: 0 var(--ui-space-base);
    color: var(--ui-ink);
    text-decoration: none;
    font-size: 12px;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }

  @media (hover: hover) {
    .ch-link:hover {
      color: var(--ui-accent-text);
    }
  }

  .skeletons {
    display: flex;
    flex-direction: column;
    gap: var(--ui-space-base);
  }

  .ch-end {
    color: var(--ui-ink-faint);
    font-size: 12px;
    text-align: center;
    padding: var(--ui-space-comfortable) 0 var(--ui-space-roomy);
  }
</style>

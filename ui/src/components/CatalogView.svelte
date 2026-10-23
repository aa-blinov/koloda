<script>
  /**
   * The catalog of the public channels: the search, the categories, the sort
   * and the list itself. The list comes from the open catalog of TGMania.
   */
  import { onMount } from 'svelte';
  import { createEventDispatcher } from 'svelte';
  import { Button, EmptyState, Notice, SegmentedControl, Skeleton, TextField } from '@monos/ui';

  import Icon from './Icon.svelte';
  import { api } from '../lib/api.js';
  import { searchValue } from '../lib/search.js';
  import { catRu, kfmt } from '../lib/format.js';
  import { subs, avatars, setSubscription, showUndo, loadAvatars } from '../lib/stores.js';

  const dispatch = createEventDispatcher();

  // Ключ экрана: по нему оболочка понимает, что список готов и место
  // прокрутки можно вернуть.
  export let screenKey = '';
  // Запрос, с которым открылся каталог: чтобы список не грузился дважды.
  export let initialQuery = '';

  const sorts = [
    { value: 'subs', label: 'По подписчикам' },
    { value: 'fresh', label: 'По свежести' },
    { value: 'name', label: 'По названию' },
  ];

  let query = initialQuery;
  let category = 'all';
  let categories = [];
  let sort = 'subs';
  let channels = [];
  let names = new Map();
  let offset = 0;
  let hasMore = false;
  let loading = false;
  let failed = false;
  let requestId = 0;
  let queryKey = '';
  let link = '';
  let linkState = '';
  let linkError = '';
  let allCategories = false;
  // Фото канала лежит в кэше сервера неделю, а адрес на CDN Telegram к тому
  // времени умирает. На месте мёртвой картинки встаёт буква названия.
  let brokenAvatars = new Set();

  $: queryKey = query.trim() + '|' + category;
  // Категории по алфавиту: список читают глазами, а не ищут.
  $: sortedCategories = [...categories].sort((a, b) =>
    catRu(a.name).localeCompare(catRu(b.name), 'ru'));

  const compare = {
    subs: (a, b) => (b.subs - a.subs) || a.username.localeCompare(b.username),
    fresh: (a, b) => (b.last_ts - a.last_ts) || (b.subs - a.subs),
    name: (a, b) => a.name.localeCompare(b.name, 'ru'),
  };

  async function load(append = false) {
    loading = true;
    failed = false;
    const id = ++requestId;
    try {
      const answer = await api.catalog({ q: query.trim(), category, offset: append ? offset : 0 });
      if (id !== requestId) return;
      const found = answer.channels || [];
      for (const channel of found) names.set(channel.username, channel);
      names = new Map(names);
      // The list is built from everything that was loaded: a sort that changes
      // the order of one page only would leave the rest as it was.
      const merged = new Map(channels.map((c) => [c.username, c]));
      for (const channel of found) merged.set(channel.username, channel);
      channels = [...merged.values()].sort(compare[sort] || compare.subs);
      offset += found.length;
      hasMore = Boolean(answer.has_more);
      loadAvatars(channels.map((c) => c.username));
      dispatch('ready', { key: screenKey });
    } catch (err) {
      if (id !== requestId) return;
      failed = true;
    } finally {
      if (id === requestId) loading = false;
    }
  }

  let categoriesFailed = false;

  async function loadCategories() {
    try {
      const answer = await api.categories();
      categories = answer.categories || [];
      categoriesFailed = false;
    } catch (err) {
      // Категории не пришли: ряд остаётся на месте, чтобы можно было повторить.
      categoriesFailed = true;
    }
  }

  function pickCategory(slug) {
    category = slug;
    channels = [];
    offset = 0;
    load(false);
  }

  function pickSort(value) {
    sort = value;
    channels = [...channels].sort(compare[value] || compare.subs);
  }

  $: if (queryKey !== undefined) {
    // A new query or a new category: the list is built from scratch.
  }

  let lastQueryKey = '';
  $: if (queryKey !== lastQueryKey) {
    lastQueryKey = queryKey;
    channels = [];
    offset = 0;
    load(false);
  }

  async function addByLink(event) {
    event.preventDefault();
    linkError = '';
    linkState = 'Проверяю канал…';
    const name = link.trim().replace(/^https?:\/\/(www\.)?t\.me\//, '').replace(/^@/, '').split('/')[0];
    if (!name) {
      linkError = 'Напиши имя канала или ссылку на него.';
      linkState = '';
      return;
    }
    try {
      const answer = await api.check(name);
      setSubscription(name, true);
      linkState = 'Канал «' + (answer.title || name) + '» добавлен в ленту.';
      link = '';
      load(false);
    } catch (err) {
      linkError = err && err.message ? err.message : 'Канал не найден.';
      linkState = '';
    }
  }

  function toggle(channel) {
    const on = !$subs.includes(channel.username);
    setSubscription(channel.username, on);
    if (on) {
      showUndo('Канал «' + channel.username + '» убран из ленты.', () => {
        setSubscription(channel.username, true);
      });
    }
  }

  onMount(() => {
    loadCategories();
    load(false);
    const onSearch = (event) => {
      query = searchValue(event);
    };
    window.addEventListener('koloda:search', onSearch);
    return () => window.removeEventListener('koloda:search', onSearch);
  });
</script>

<div class="column catalog">
  <h1>Каталог каналов</h1>
  <p class="catalog-lead">
    Каталог собран из открытого списка Telegram-каналов. Подпишись на нужные — и их публикации
    появятся в ленте.
  </p>

  <form class="channel-form" on:submit|preventDefault={addByLink}>
    <TextField
      id="channelInput"
      label="Канал по ссылке"
      bind:value={link}
      placeholder="@username или t.me/username"
      autocomplete="off"
    />
    <span id="channelAdd">
      <Button type="submit">Добавить по ссылке</Button>
    </span>
  </form>

  <div id="channelState" role="status">
  {#if linkState}
    <Notice tone="success" title="Готово">{linkState}</Notice>
  {/if}
  {#if linkError}
    <Notice tone="danger" title="Не получилось">{linkError}</Notice>
  {/if}
  </div>

  {#if categoriesFailed}
    <div class="cats-error">
      <span>Категории не загрузились.</span>
      <span id="catRetry">
        <Button variant="text" size="sm" on:click={loadCategories}>Повторить</Button>
      </span>
    </div>
  {/if}

  {#if categories.length || categoriesFailed}
    <div class="filters" class:wrap={allCategories} id="cats" role="group" aria-label="Категории">
      <button
        type="button"
        class="chip"
        aria-pressed={category === 'all'}
        on:click={() => pickCategory('all')}>Все</button>
      {#each sortedCategories as item (item.slug)}
        <button
          type="button"
          class="chip"
          aria-pressed={category === item.slug}
          on:click={() => pickCategory(item.slug)}
        >
          {catRu(item.name)}
        </button>
      {/each}
    </div>
    {#if categories.length > 12}
      <div class="cats-more">
        <button type="button" class="chip" aria-pressed={allCategories} on:click={() => (allCategories = !allCategories)}>
          {allCategories ? 'Свернуть' : 'Все категории'}
        </button>
      </div>
    {/if}
  {/if}

  <div class="sort-row">
    <SegmentedControl options={sorts} value={sort} label="Сортировка каталога" on:change={(e) => pickSort(e.detail.value)} />
  </div>

  {#if loading && !channels.length}
    <div class="skeletons">
      <Skeleton class="h-16" />
      <Skeleton class="h-16" />
      <Skeleton class="h-16" />
    </div>
  {:else if failed}
    <EmptyState title="Каталог недоступен" description="Проверь соединение и попробуй ещё раз.">
      <svelte:fragment slot="actions">
        <Button on:click={() => load(false)}>Попробовать снова</Button>
      </svelte:fragment>
    </EmptyState>
  {:else if !channels.length}
    <EmptyState title="Ничего не нашлось" description="Измени запрос или выбери категорию." />
  {:else}
    {#each channels as channel (channel.username)}
      <article class="channel-entry">
        <button
          type="button"
          class="channel-open"
          on:click={() => dispatch('openChannel', { channel: channel.username, title: channel.name })}
        >
          <span class="channel-mark" aria-hidden="true">
            {#if $avatars.get(channel.username) && !brokenAvatars.has(channel.username)}
              <img
                src={$avatars.get(channel.username)}
                alt=""
                loading="lazy"
                referrerpolicy="no-referrer"
                on:error={() => {
                  brokenAvatars = new Set(brokenAvatars).add(channel.username);
                }}
              />
            {:else}
              {(channel.name || channel.username).trim()[0].toUpperCase()}
            {/if}
          </span>
          <span class="channel-copy">
            <span class="channel-name">{channel.name || channel.username}</span>
            <span class="channel-meta">@{channel.username}{channel.cat ? ' · ' + catRu(channel.cat) : ''}</span>
            {#if channel.desc}<span class="channel-desc">{channel.desc}</span>{/if}
          </span>
        </button>
        {#if channel.subs}
          <span class="channel-count">
            {kfmt(channel.subs)}
            <small>подписчиков</small>
          </span>
        {/if}
        <button
          type="button"
          class="channel-action"
          class:is-on={$subs.includes(channel.username)}
          aria-pressed={$subs.includes(channel.username)}
          on:click={() => toggle(channel)}
        >
          <Icon name={$subs.includes(channel.username) ? 'check' : 'plus'} size={14} />
          {$subs.includes(channel.username) ? 'Убрать' : 'Добавить'}
        </button>
      </article>
    {/each}

    {#if hasMore}
      <Button class="load-more" variant="text" on:click={() => load(true)} loading={loading}>
        {loading ? 'Загружаю…' : 'Показать ещё каналы'}
      </Button>
    {/if}
  {/if}
</div>

<style>
  .catalog {
    padding-top: var(--ui-space-roomy);
  }

  h1 {
    font-size: 24px;
    line-height: 1.25;
    font-weight: 700;
    letter-spacing: -0.025em;
    margin: 0 0 var(--ui-space-snug);
  }

  .catalog-lead {
    color: var(--ui-ink-soft);
    font-size: 14px;
    margin: 0 0 var(--ui-space-roomy);
    max-width: 60ch;
  }


  .channel-form {
    display: flex;
    align-items: flex-end;
    gap: var(--ui-space-base);
    margin-bottom: var(--ui-space-comfortable);
    flex-wrap: wrap;
  }

  .channel-form :global(.field),
  .channel-form :global(label) {
    flex: 1;
    min-width: 220px;
  }

  .filters {
    display: flex;
    gap: var(--ui-space-snug);
    overflow-x: auto;
    padding-bottom: var(--ui-space-snug);
    margin-bottom: var(--ui-space-base);
    scrollbar-width: thin;
  }

  /* «Все категории» разворачивает ряд: чипы переносятся, и все видны без
     прокрутки вбок. Без этого кнопка меняла только надпись. */
  .filters.wrap {
    flex-wrap: wrap;
    overflow-x: visible;
  }

  .chip {
    flex: 0 0 auto;
    min-height: 36px;
    padding: 0 var(--ui-space-base);
    border: 1px solid var(--ui-line);
    border-radius: var(--ui-radius);
    background: none;
    color: var(--ui-ink-soft);
    font: inherit;
    font-size: 12.5px;
    cursor: pointer;
    white-space: nowrap;
  }

  .chip[aria-pressed='true'] {
    background: var(--ui-surface-2);
    color: var(--ui-ink);
    border-color: var(--ui-ink-soft);
  }

  /* Палец: чип не ниже 44 px. На мыши остаётся компактным. */
  @media (pointer: coarse) {
    .chip {
      min-height: 44px;
    }
  }

  .sort-row {
    margin-bottom: var(--ui-space-base);
  }

  .skeletons {
    display: flex;
    flex-direction: column;
    gap: var(--ui-space-base);
  }
</style>

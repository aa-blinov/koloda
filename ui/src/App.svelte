<script>
  /**
   * The shell: the sections, the search, the overlays and the keyboard.
   * The screens themselves live in their own components.
   */
  import { onMount, tick } from 'svelte';
  import { SegmentedControl, IconButton, SearchField, Toaster } from '@monos/ui';

  import AuthScreen from './components/AuthScreen.svelte';
  import FeedView from './components/FeedView.svelte';
  import CatalogView from './components/CatalogView.svelte';
  import ChannelView from './components/ChannelView.svelte';
  import ProfileView from './components/ProfileView.svelte';
  import ChannelsDialog from './components/ChannelsDialog.svelte';
  import CollectionDialog from './components/CollectionDialog.svelte';
  import Lightbox from './components/Lightbox.svelte';

  import Icon from './components/Icon.svelte';
  import { api } from './lib/api.js';
  import {
    session,
    subs,
    setSubscription,
    showUndo,
    collections as collectionsList,
    settings,
    syncStatus,
    undo,
    clearUndo,
    drainStateOps,
    loadAccountState,
    pullState,
    applySettings,
  } from './lib/stores.js';

  // What is on the screen: the feed, the catalog, one channel or the profile.
  export let view = 'feed';
  export let feedMode = 'feed';
  export let channelName = '';

  let searchOpen = false;
  let search = '';
  let channelsOpen = false;
  let collection = null;      // редактируемая коллекция
  let collectionChannel = ''; // канал, чьи коллекции правятся
  let collectionFilter = null; // коллекция, по которой отфильтрована лента
  let lightbox = null;
  let booting = true;
  let bootError = '';

  const sections = [
    { value: 'feed', label: 'Лента' },
    { value: 'catalog', label: 'Каталог' },
    { value: 'saved', label: 'Сохранённое' },
  ];

  $: section = feedMode === 'saved' ? 'saved' : view === 'catalog' ? 'catalog' : 'feed';

  function pickSection(value) {
    searchOpen = false;
    if (value === 'catalog') {
      view = 'catalog';
      feedMode = 'feed';
    } else if (value === 'saved') {
      view = 'feed';
      feedMode = 'saved';
    } else {
      view = 'feed';
      feedMode = 'feed';
    }
    writeHash();
  }

  function writeHash() {
    const hash =
      view === 'catalog' ? '#/cat' : view === 'channel' ? '#/channel/' + channelName : view === 'profile' ? '#/me' : '#/feed';
    if (location.hash !== hash) history.replaceState(null, '', hash);
  }

  function readHash() {
    const hash = location.hash || '';
    if (hash.startsWith('#/cat')) return { view: 'catalog', feedMode: 'feed', channelName: '' };
    if (hash.startsWith('#/channel/')) return { view: 'channel', feedMode: 'feed', channelName: hash.slice(9) };
    if (hash.startsWith('#/me')) return { view: 'profile', feedMode: 'feed', channelName: '' };
    return { view: 'feed', feedMode: 'feed', channelName: '' };
  }

  // --- the profile ---

  async function boot() {
    booting = true;
    bootError = '';
    try {
      const answer = await api.me();
      const me = answer && answer.account;
      if (me && me.username) {
        session.set(me);
        await loadAccountState({ migrate: true });
        const start = readHash();
        view = start.view;
        feedMode = start.feedMode;
        channelName = start.channelName;
        writeHash();
      } else {
        session.set(null);
      }
    } catch (err) {
      bootError = 'Сервис не отвечает. Попробуй позже.';
    } finally {
      booting = false;
    }
  }

  onMount(() => {
    boot();
    const onHash = () => {
      const next = readHash();
      view = next.view;
      feedMode = next.feedMode;
      channelName = next.channelName;
    };
    window.addEventListener('hashchange', onHash);
    const timer = setInterval(() => pullState(), 15000);
    const onFocus = () => pullState();
    window.addEventListener('focus', onFocus);
    const onVisibility = () => pullState();
    document.addEventListener('visibilitychange', onVisibility);
    return () => {
      window.removeEventListener('hashchange', onHash);
      window.removeEventListener('focus', onFocus);
      document.removeEventListener('visibilitychange', onVisibility);
      clearInterval(timer);
    };
  });

  // --- the keyboard ---

  function typing(node) {
    return node && (node.tagName === 'INPUT' || node.tagName === 'TEXTAREA' || node.isContentEditable);
  }

  function onKeydown(event) {
    if (typing(event.target) || event.metaKey || event.ctrlKey || event.altKey) return;
    if (event.key === 'Escape') {
      if (lightbox) return; // the viewer closes itself
      if (channelsOpen) {
        channelsOpen = false;
        return;
      }
      return;
    }
    if (lightbox || channelsOpen) return;
    const key = event.key.toLowerCase();
    if (key === '/') {
      const field = document.querySelector('#koloda-search input');
      // The search is hidden on the screens of the profile and the channel:
      // a focus in a hidden field does nothing, and the key would vanish.
      if (!field) return;
      event.preventDefault();
      searchOpen = true;
      tick().then(() => field.focus());
      return;
    }
    if (key === '?') {
      event.preventDefault();
      view = 'profile';
      writeHash();
      tick().then(() => window.dispatchEvent(new CustomEvent('koloda:help')));
      return;
    }
    if (view !== 'feed') return;
    if (key === 'j' || key === 'k' || key === 'n' || key === 'u') {
      event.preventDefault();
      window.dispatchEvent(new CustomEvent('koloda:key', { detail: key }));
    }
  }

  function onSearchInput(event) {
    search = event.detail.value;
    window.dispatchEvent(new CustomEvent('koloda:search', { detail: search }));
  }

  function onUndo() {
    const action = undo?.action;
    clearUndo();
    if (action) action();
  }
</script>

<svelte:window on:keydown={onKeydown} />

{#if booting}
  <main class="boot">
    <p class="text-ink-soft text-sm">Загружаю ленту…</p>
  </main>
{:else if !$session}
  <AuthScreen {bootError} on:done={(event) => boot()} />
{:else}
  <header class="sticky top-0 z-20 border-b border-line bg-canvas">
    <div class="bar">
      <IconButton
        label="Поиск"
        variant="ghost"
        size="lg"
        pressed={searchOpen}
        on:click={() => (searchOpen = !searchOpen)}
      >
        <Icon name="search" />
      </IconButton>

      <SegmentedControl
        options={sections}
        value={section}
        label="Разделы"
        on:change={(event) => pickSection(event.detail.value)}
      />

      {#if searchOpen}
        <div id="koloda-search" class="search">
          <SearchField
            value={search}
            label="Поиск"
            placeholder="Поиск по постам"
            clearLabel="Очистить"
            on:input={onSearchInput}
          />
        </div>
      {/if}

      <div class="ml-auto flex items-center gap-2">
        <IconButton
          label="Каналы и коллекции"
          variant="ghost"
          size="lg"
          on:click={() => (channelsOpen = true)}
        >
          <Icon name="list" />
        </IconButton>
        <IconButton
          label="Профиль"
          variant="ghost"
          size="lg"
          on:click={() => {
            view = 'profile';
            writeHash();
          }}
        >
          <Icon name="user" />
        </IconButton>
      </div>
    </div>

    {#if $syncStatus}
      <p class="sync-status" role="status">
        {$syncStatus.message}
        {#if $syncStatus.retry}
          <button type="button" class="underline" on:click={() => drainStateOps()}>Повторить</button>
        {/if}
      </p>
    {/if}

    {#if $undo}
      <p class="undo-bar" id="undoBar" role="status">
        {$undo.text}
        <button type="button" class="underline" on:click={onUndo}>Вернуть</button>
      </p>
    {/if}
  </header>

  {#if view === 'feed'}
    <FeedView
      {feedMode}
      collection={collectionFilter}
      on:lightbox={(event) => (lightbox = event.detail)}
    on:channelCollections={(event) => {
      collectionChannel = event.detail;
      collection = null;
    }}
    />
  {:else if view === 'catalog'}
    <CatalogView
      on:openChannel={(event) => {
        channelName = event.detail;
        view = 'channel';
        writeHash();
      }}
    />
  {:else if view === 'channel'}
    <ChannelView
      {channelName}
      on:back={() => {
        view = 'catalog';
        writeHash();
      }}
      on:lightbox={(event) => (lightbox = event.detail)}
    on:channelCollections={(event) => {
      collectionChannel = event.detail;
      collection = null;
    }}
    />
  {:else if view === 'profile'}
    <ProfileView on:done={() => {}} />
  {/if}

  <ChannelsDialog
    open={channelsOpen}
    on:close={() => (channelsOpen = false)}
    on:openChannel={(event) => {
      channelName = event.detail;
      view = 'channel';
      channelsOpen = false;
      writeHash();
    }}
    on:openCollection={(event) => {
      // Клик по названию коллекции показывает её в ленте: каналы коллекции
      // фильтром, остальные подписки — рядом.
      const found = ($collectionsList || []).find((c) => String(c.id) === String(event.detail));
      collectionFilter = found || null;
      view = 'feed';
      feedMode = 'feed';
      channelsOpen = false;
      writeHash();
    }}
    on:editCollection={(event) => {
      collection = event.detail;
    }}
    on:channelCollections={(event) => {
      collectionChannel = event.detail;
      collection = null;
    }}
    on:unsubscribe={(event) => {
      const channel = event.detail;
      setSubscription(channel, false);
      showUndo('Канал «' + channel + '» убран из ленты.', () => setSubscription(channel, true));
    }}
  />

  <CollectionDialog
    open={Boolean(collection) || Boolean(collectionChannel)}
    {collection}
    channel={collectionChannel}
    on:close={() => {
      collection = null;
      collectionChannel = '';
    }}
  />

  <Lightbox items={lightbox} on:close={() => (lightbox = null)} />

  <Toaster position="bottom-right" dismissLabel="Закрыть" />
{/if}

<style>
  .boot {
    display: grid;
    place-items: center;
    min-height: 60vh;
  }

  .bar {
    max-width: 1120px;
    margin: 0 auto;
    display: flex;
    gap: var(--ui-space-base);
    align-items: center;
    padding: var(--ui-space-snug) var(--ui-space-comfortable);
    padding-top: calc(var(--ui-space-snug) + env(safe-area-inset-top));
  }

  .search {
    flex: 1;
    min-width: 0;
    display: flex;
    align-items: center;
  }

  .search :global(.w-full) {
    width: 100%;
  }

  .sync-status,
  .undo-bar {
    max-width: 1120px;
    margin: 0 auto;
    padding: var(--ui-space-tight) var(--ui-space-comfortable) var(--ui-space-snug);
    color: var(--ui-ink-soft);
    font-size: 12.5px;
  }

  .undo-bar {
    color: var(--ui-ink);
  }

  .underline {
    background: none;
    border: 0;
    padding: 0;
    font: inherit;
    color: var(--ui-accent-text);
    text-decoration: underline;
    cursor: pointer;
  }
</style>

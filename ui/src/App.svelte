<script>
  /**
   * The shell: the screens, the navigation between them and the overlays.
   *
   * Navigation rules:
   * - three main screens: the feed, the catalog and the saved posts; they are
   *   always one tap away (a bottom bar on a phone, a switch in the header on
   *   a desktop);
   * - two sub-screens: the channel and the profile; they open from the main
   *   screens and close with the back arrow in the header, to where you came
   *   from;
   * - the browser's back button works the same way: every screen goes into the
   *   history, and the hash follows the screen.
   */
  import { onMount, tick } from 'svelte';
  import { NavItem, Button, Chip, IconButton, SearchField, Toaster } from '@monos/ui';

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
  import { phone } from './lib/layout.js';
  import { searchValue } from './lib/search.js';
  import {
    session,
    subs,
    syncStatus,
    undo,
    setSubscription,
    setShowRead,
    showRead,
    immersive,
    showUndo,
    clearUndo,
    collections as collectionsList,
    flushPending,
    loadAccountState,
    pullState,
  } from './lib/stores.js';

  // Главные экраны. Значение — раздел, а не экран: лента и сохранённое
  // показываются одним и тем же списком.
  const MAIN = [
    { value: 'feed', label: 'Лента', icon: 'feed' },
    { value: 'catalog', label: 'Каталог', icon: 'grid' },
    { value: 'saved', label: 'Сохранённое', icon: 'bookmark' },
  ];

  export let view = 'feed';
  export let feedMode = 'feed';
  export let channelName = '';

  let searchOpen = false;
  let search = '';
  // У каждого раздела своя строка поиска: в ленте ищутся посты, в каталоге —
  // каналы. Запрос раздела помнится, пока читатель ходит по соседним.
  const queries = { feed: '', saved: '', catalog: '' };
  let channelsOpen = false;
  let lightbox = null;
  let booting = true;
  let bootError = '';
  let collection = null;
  let collectionChannel = '';
  let collectionFilterId = null;
  // Фильтр по каналу внутри ленты. Он в оболочке, потому что его видно и в
  // шапке под поиском, и над лентой на телефоне — и нажимает его одно и то же.
  let feedTab = '';
  let anim = 0;
  // Глубина внутри приложения: сколько переходов сделано от корня. По ней
  // «назад» знает, есть ли куда вернуться, и не выбрасывает читателя с сайта.
  let depth = 0;
  // Куда смотрит переход: вперёд или назад. От этого зависит анимация.
  let direction = 'forward';
  // Адрес экрана, который сейчас открыт: по нему видно, что адрес уже сменился.
  let current = '';
  // Место прокрутки каждого экрана: вернувшись из каталога, читатель
  // ожидает найти ленту там же, где остановился.
  const scrolls = new Map();
  // Экран, которому ещё предстоит вернуть прокрутку: ждём, пока он наполнится.
  let restoring = '';

  $: isSub = view === 'channel' || view === 'profile';
  $: section = sectionOf({ view, feedMode });
  $: screenKey = keyOf({ view, feedMode, channelName });
  $: collectionFilter = collectionFilterId
    ? $collectionsList.find((c) => String(c.id) === String(collectionFilterId)) || null
    : null;
  // Имя действующего фильтра ленты: коллекция или канал.
  $: filterName = collectionFilter ? collectionFilter.name : feedTab;

  // Фильтр снимается целиком: и коллекция, и канал.
  function clearFilter() {
    feedTab = '';
    collectionFilterId = null;
  }

  // --- экраны и адреса ---

  function keyOf(next) {
    return next.view + ':' + next.feedMode + ':' + (next.channelName || '');
  }

  // Раздел экрана: лента, сохранённое или каталог.
  function sectionOf(next) {
    if (next.view === 'catalog') return 'catalog';
    return next.feedMode === 'saved' ? 'saved' : 'feed';
  }

  function screenOf(value) {
    if (value === 'catalog') return { view: 'catalog', feedMode: 'feed', channelName: '' };
    if (value === 'saved') return { view: 'feed', feedMode: 'saved', channelName: '' };
    return { view: 'feed', feedMode: 'feed', channelName: '' };
  }

  function hashOf(next) {
    if (next.view === 'catalog') return '#/cat';
    if (next.view === 'channel') return '#/channel/' + next.channelName;
    if (next.view === 'profile') return '#/me';
    return next.feedMode === 'saved' ? '#/saved' : '#/feed';
  }

  function screenFromHash() {
    const hash = location.hash || '';
    if (hash.startsWith('#/cat')) return { view: 'catalog', feedMode: 'feed', channelName: '' };
    if (hash.startsWith('#/channel/')) return { view: 'channel', feedMode: 'feed', channelName: hash.slice(10) };
    if (hash.startsWith('#/me')) return { view: 'profile', feedMode: 'feed', channelName: '' };
    if (hash.startsWith('#/saved')) return { view: 'feed', feedMode: 'saved', channelName: '' };
    return { view: 'feed', feedMode: 'feed', channelName: '' };
  }

  function apply(next, dir = 'forward') {
    view = next.view;
    feedMode = next.feedMode;
    channelName = next.channelName || '';
    direction = dir;
    current = hashOf(next);
    stopRestore();
    // На подэкранах поиска нет: там он и не работает. В разделах поле
    // показывает запрос именно этого раздела.
    search = next.view === 'channel' || next.view === 'profile' ? '' : queries[sectionOf(next)] || '';
    anim += 1;
  }

  function go(next, replace = false) {
    const target = { view: 'feed', feedMode: 'feed', channelName: '', ...next };
    scrolls.set(screenKey, window.scrollY);
    if (replace) {
      history.replaceState({ koloda: true, depth }, '', hashOf(target));
    } else {
      depth += 1;
      history.pushState({ koloda: true, depth }, '', hashOf(target));
    }
    apply(target, 'forward');
    restoring = keyOf(target);
    restoreScroll(keyOf(target));
  }

  function goBack() {
    // Кнопка «назад» ведёт туда, откуда пришли, — как кнопка браузера.
    // На корне возвращаться некуда: тогда идём в ленту, а не с сайта.
    if (depth > 0) history.back();
    else go({ view: 'feed' });
  }

  // Адрес сменился: это либо кнопка браузера, либо ручная правка адреса.
  function onLocation(event) {
    const next = screenFromHash();
    if (hashOf(next) === current) return;
    // Окно поверх экрана не переживает смену экрана: иначе оно висит над лентой
    // и перехватывает нажатия, а «Назад» листает экраны под ним.
    collection = null;
    collectionChannel = '';
    channelsOpen = false;
    scrolls.set(screenKey, window.scrollY);
    const d = event && event.state && typeof event.state.depth === 'number' ? event.state.depth : 0;
    direction = d < depth ? 'back' : 'forward';
    depth = d;
    apply(next, direction);
    restoring = keyOf(next);
    restoreScroll(keyOf(next));
  }

  // Возврат прокрутки: сначала пробуем сразу, а когда экран наполнится —
  // ещё раз, уже по его сигналу «готов».
  function restoreScroll(key) {
    tick().then(() => restoreTo(key));
  }

  // Картинки в постах догружаются позже поста и сдвигают текст вниз: после
  // возврата читатель оказывался на две сотни строк ниже нужного места.
  // Пока он сам не тронул страницу, место возвращаем ещё раз.
  let restoreTimer = null;

  function stopRestore() {
    if (!restoreTimer) return;
    clearInterval(restoreTimer);
    restoreTimer = null;
  }

  function restoreTo(key) {
    const y = scrolls.get(key) || 0;
    window.scrollTo(0, y);
    stopRestore();
    let tries = 0;
    restoreTimer = setInterval(() => {
      if (key !== screenKey || ++tries > 14) {
        stopRestore();
        return;
      }
      window.scrollTo(0, y);
    }, 120);
  }

  // Экран сообщает, что он готов: только теперь можно вернуть прокрутку.
  function onReady(event) {
    const key = (event.detail && event.detail.key) || screenKey;
    if (key !== screenKey || restoring !== key) return;
    restoring = '';
    restoreTo(key);
  }

  function pickSection(value) {
    const target = screenOf(value);
    if (target.view === view && target.feedMode === feedMode) {
      // Повторный выбор раздела возвращает наверх — как в телефоне.
      window.scrollTo({ top: 0, behavior: 'smooth' });
      return;
    }
    go(target);
  }

  function openChannel(value) {
    const channel = typeof value === 'string' ? value : value.channel;
    const title = typeof value === 'string' ? '' : value.title || '';
    // Название ставим до перехода: иначе экран канала откроется без него.
    channelTitle = title;
    go({ view: 'channel', feedMode: 'feed', channelName: channel }, false);
  }

  let channelTitle = '';

  function openProfile() {
    go({ view: 'profile' });
  }

  // --- профиль ---

  // Ссылка из письма: /verify и /reset. Её открывают и вошедшие, поэтому экран
  // ссылки показываем при любой сессии. Когда экран закрыт, путь сбрасываем.
  let letterLink = /^\/(verify|reset)$/.test(location.pathname);
  function finishLetter() {
    letterLink = false;
    history.replaceState(null, '', '/#/feed');
    boot();
  }

  async function boot() {
    booting = true;
    bootError = '';
    try {
      const answer = await api.me();
      const me = answer && answer.account;
      if (me && me.username) {
        session.set(me);
        await loadAccountState({ migrate: true });
        const start = screenFromHash();
        apply(start);
        history.replaceState({ koloda: true, depth: 0 }, '', hashOf(start));
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
    // Своё возвращение прокрутки: браузерное пригодилось бы только там, где
    // экрана нет вовсе, а у нас каждый экран помнит свой.
    if ('scrollRestoration' in history) history.scrollRestoration = 'manual';
    // Кнопка браузера и ручная правка адреса ведут себя одинаково.
    window.addEventListener('popstate', onLocation);
    window.addEventListener('hashchange', onLocation);
    // Читатель тронул страницу — место прокрутки больше не поправляем.
    window.addEventListener('wheel', stopRestore, { passive: true });
    window.addEventListener('touchmove', stopRestore, { passive: true });
    window.addEventListener('keydown', stopRestore);
    // Очередь отметок и подписок уходит сама: по таймеру и когда вернулась сеть,
    // без кнопки «Повторить».
    const timer = setInterval(() => {
      pullState();
      flushPending();
    }, 15000);
    const onFocus = () => pullState();
    window.addEventListener('focus', onFocus);
    const onVisibility = () => pullState();
    document.addEventListener('visibilitychange', onVisibility);
    const onOnline = () => flushPending();
    window.addEventListener('online', onOnline);
    return () => {
      window.removeEventListener('online', onOnline);
      window.removeEventListener('popstate', onLocation);
      window.removeEventListener('hashchange', onLocation);
      window.removeEventListener('wheel', stopRestore);
      window.removeEventListener('touchmove', stopRestore);
      window.removeEventListener('keydown', stopRestore);
      window.removeEventListener('focus', onFocus);
      document.removeEventListener('visibilitychange', onVisibility);
      clearInterval(timer);
      stopRestore();
    };
  });

  // --- клавиатура ---

  function typing(node) {
    return node && (node.tagName === 'INPUT' || node.tagName === 'TEXTAREA' || node.isContentEditable);
  }

  function onKeydown(event) {
    if (typing(event.target) || event.metaKey || event.ctrlKey || event.altKey) return;
    if (event.key === 'Escape') {
      if (lightbox) return;
      if (channelsOpen) {
        channelsOpen = false;
        return;
      }
      if (isSub) {
        goBack();
        return;
      }
      return;
    }
    if (lightbox || channelsOpen) return;
    const key = event.key.toLowerCase();
    if (key === '/') {
      const field = document.querySelector('#koloda-search input');
      if (!field) return;
      event.preventDefault();
      searchOpen = true;
      tick().then(() => {
        field.focus();
        field.select();
      });
      return;
    }
    if (key === '?') {
      event.preventDefault();
      openProfile();
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
    search = searchValue(event);
    queries[section] = search;
    window.dispatchEvent(new CustomEvent('koloda:search', { detail: search }));
  }

  // Очистка поля — из крестика, из Esc и из кнопки «Сбросить поиск» в ленте:
  // поле и лента всегда показывают один запрос.
  function clearSearch() {
    search = '';
    queries[section] = '';
    window.dispatchEvent(new CustomEvent('koloda:search', { detail: '' }));
  }

  // Esc в пустом поле: на телефоне закрывает поиск, везде снимает фокус.
  // Непустое поле Esc очищает раньше, чем доходит сюда (см. SearchField).
  function onSearchKey(event) {
    if (event.key !== 'Escape') return;
    if (event.target) event.target.blur();
    if ($phone && searchOpen) toggleSearch();
  }

  // Лупа на телефоне: открывает поле, а в открытом поле — крестик, который
  // его закрывает и убирает запрос.
  function toggleSearch() {
    searchOpen = !searchOpen;
    if (!searchOpen) {
      if (search) {
        search = '';
        queries[section] = '';
        window.dispatchEvent(new CustomEvent('koloda:search', { detail: '' }));
      }
      return;
    }
    tick().then(() => {
      const field = document.querySelector('#koloda-search input');
      if (field) field.focus();
    });
  }

  function onUndo() {
    const action = $undo && $undo.action;
    clearUndo();
    if (action) action();
  }
</script>

<svelte:window on:keydown={onKeydown} />

{#if booting}
  <main class="boot">
    <p class="text-ink-soft text-sm">Загружаю ленту…</p>
  </main>
{:else if !$session || letterLink}
  <AuthScreen {bootError} signedIn={Boolean($session)} on:done={finishLetter} />
{:else}
  <!-- Боковая панель: на широком экране палец не нужен, места много, и все
       назначения видны сразу и названы. На телефоне её заменяет нижняя
       панель: там до верха экрана тянуться не надо. -->
  <aside class="side" aria-label="Навигация">
    <div class="side-brand">
      <Icon name="send" size={20} />
      <span>Колода</span>
    </div>

    <nav class="side-nav" aria-label="Разделы">
      {#each MAIN as item (item.value)}
        <NavItem
          label={item.label}
          active={section === item.value}
          on:click={() => pickSection(item.value)}
        >
          <svelte:fragment slot="icon"><Icon name={item.icon} /></svelte:fragment>
        </NavItem>
      {/each}
    </nav>

    <nav class="side-foot" aria-label="Ещё">
      <NavItem label="Каналы и коллекции" on:click={() => (channelsOpen = true)}>
        <svelte:fragment slot="icon"><Icon name="folder" /></svelte:fragment>
      </NavItem>
      <NavItem label="Профиль" on:click={openProfile}>
        <svelte:fragment slot="icon"><Icon name="user" /></svelte:fragment>
      </NavItem>
    </nav>
  </aside>

  <!-- Колода — только на ленте: там страница не прокручивается. Остальные экраны
       прокручиваются целиком, и под панелью разделов им нужен запас. -->
  <div
    class="shell"
    class:deck-shell={$phone && view === 'feed'}
    class:immersive={$phone && view === 'feed' && $immersive}
    class:with-tabs={$phone && view !== 'feed' && !isSub}
  >
    <header class="topbar">
      <div class="bar" class:searching={searchOpen}>
        <!-- Имя сервиса: на телефоне боковой панели нет, и шапка с одним
             значком слева выглядит пустой. -->
        <span class="brand">
          <Icon name="send" size={18} />
          <span>Колода</span>
        </span>

        {#if isSub}
          <!-- Подэкран: назад туда, откуда пришли. -->
          <button type="button" class="back" on:click={goBack} aria-label="Назад">
            <Icon name="back" size={18} />
            <span>Назад</span>
          </button>
        {/if}

        <!-- Поле поиска: на широком экране оно всегда на виду, на телефоне
             открывается лупой — иначе шапка не помещается. На подэкранах поиска
             нет: там он и не работает, и молчаливое поле сбивает с толку. -->
        {#if !isSub}
          <div class="bar-right">
            <div id="koloda-search" class="search" class:open={searchOpen}>
              <SearchField
                bind:value={search}
                label="Поиск"
                placeholder={view === 'catalog' ? 'Поиск по каналу' : 'Поиск по постам'}
                clearLabel="Очистить"
                on:input={onSearchInput}
                on:clear={clearSearch}
                on:keydown={(event) => onSearchKey(event.detail)}
              />
            </div>
            <span class="search-toggle">
              <IconButton
                label={searchOpen ? 'Закрыть поиск' : 'Поиск'}
                variant="ghost"
                size="lg"
                on:click={toggleSearch}
              >
                <Icon name={searchOpen ? 'close' : 'search'} />
              </IconButton>
            </span>

            <!-- На широком экране каналы и профиль живут в боковой панели: в
                 шапке им места нет, а на телефоне боковой панели нет. -->
            <div class="head-icons">
              <IconButton
                label="Каналы и коллекции"
                variant="ghost"
                size="lg"
                on:click={() => (channelsOpen = true)}
              >
                <Icon name="folder" />
              </IconButton>
              <IconButton
                label="Профиль"
                variant="ghost"
                size="lg"
                on:click={openProfile}
              >
                <Icon name="user" />
              </IconButton>
            </div>

            <!-- Фильтры ленты: на широком экране они под поиском, справа от
                 него; на телефоне — над лентой, там шапка для них тесна.
                 Выбор канала и коллекции делается через папку: отдельного
                 списка «Все каналы» не нужно, действие у них одно. -->
            {#if feedMode === 'feed' && view === 'feed'}
              <div class="filters">
                {#if filterName}
                  <Chip tone="accent" removable removeLabel="Снять фильтр" on:remove={clearFilter}>
                    {filterName}
                  </Chip>
                {/if}
                <Button variant="text" size="sm" on:click={() => setShowRead(!$showRead)}>
                  {$showRead ? 'Скрыть прочитанные' : 'Показать прочитанные'}
                </Button>
              </div>
            {/if}
          </div>
        {/if}
      </div>

      {#if $syncStatus}
        <p class="sync-status" role="status">
          {$syncStatus.message}
          {#if $syncStatus.retry}
            <button type="button" class="underline" on:click={() => flushPending()}>Повторить</button>
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

    <main class="screens">
    <!-- На экране один раздел: пустой канал или профиль не должны грузиться
         зря. Место прокрутки каждого экрана запоминается, чтобы возврат
         в ленту не терял читателя. -->
    {#if view === 'feed'}
      <section class="screen" aria-label="Лента">
        <div
          class="screen-in"
          class:anim-1={anim % 2 === 0}
          class:anim-2={anim % 2 === 1}
          class:dir-back={direction === 'back'}
        >
          <FeedView
            {feedMode}
            {screenKey}
            deck={$phone}
            tab={feedTab}
            initialQuery={search}
            collection={collectionFilter}
            on:ready={onReady}
            on:clearSearch={clearSearch}
            on:clearCollection={() => (collectionFilterId = null)}
            on:tab={(event) => {
              // Клик по имени канала в посте — это фильтр, второй клик снимает
              // его. Коллекция при этом снимается: два фильтра сразу не живут.
              feedTab = feedTab === event.detail ? '' : event.detail;
              if (!feedTab && collectionFilterId) collectionFilterId = null;
            }}
            on:lightbox={(event) => (lightbox = event.detail)}
            on:channelCollections={(event) => {
              collectionChannel = event.detail;
              collection = null;
            }}
          />
        </div>
      </section>
    {:else if view === 'catalog'}
      <section class="screen" aria-label="Каталог">
        <div
          class="screen-in"
          class:anim-1={anim % 2 === 0}
          class:anim-2={anim % 2 === 1}
          class:dir-back={direction === 'back'}
        >
          <CatalogView
            {screenKey}
            initialQuery={search}
            on:openChannel={(event) => openChannel(event.detail)}
            on:ready={onReady}
          />
        </div>
      </section>
    {:else if view === 'channel'}
      <section class="screen" aria-label="Канал">
        <div
          class="screen-in"
          class:anim-1={anim % 2 === 0}
          class:anim-2={anim % 2 === 1}
          class:dir-back={direction === 'back'}
        >
          <ChannelView
            {channelName}
            {screenKey}
            initialTitle={channelTitle}
            on:ready={onReady}
            on:lightbox={(event) => (lightbox = event.detail)}
            on:channelCollections={(event) => {
              collectionChannel = event.detail;
              collection = null;
            }}
          />
        </div>
      </section>
    {:else if view === 'profile'}
      <section class="screen" aria-label="Профиль">
        <div
          class="screen-in"
          class:anim-1={anim % 2 === 0}
          class:anim-2={anim % 2 === 1}
          class:dir-back={direction === 'back'}
        >
          <ProfileView />
        </div>
      </section>
    {/if}
    </main>
  </div>

  <!-- Нижняя панель: на телефоне главные экраны должны быть под пальцем,
       и на подэкранах её сменяет стрелка «назад» в шапке. -->
  <nav class="tabs" class:hidden={isSub} class:immersive={$phone && view === 'feed' && $immersive} aria-label="Разделы">
    {#each MAIN as item (item.value)}
      <button
        type="button"
        class="tab"
        aria-current={section === item.value ? 'page' : undefined}
        on:click={() => pickSection(item.value)}
      >
        <Icon name={item.icon} size={20} />
        <span>{item.label}</span>
      </button>
    {/each}
  </nav>

  <ChannelsDialog
    open={channelsOpen}
    on:close={() => (channelsOpen = false)}
    on:unsubscribe={(event) => {
      // Отписка из списка: как на странице канала, с отменой на несколько секунд.
      // Диалог закрываем: панель «Вернуть» стоит в шапке под ним и без этого не нажимается.
      const channel = event.detail;
      channelsOpen = false;
      setSubscription(channel, false);
      showUndo('Канал «' + channel + '» убран из ленты.', () => setSubscription(channel, true));
    }}
    on:openChannel={(event) => {
      channelsOpen = false;
      openChannel(event.detail);
    }}
    on:openCollection={(event) => {
      const found = ($collectionsList || []).find((c) => String(c.id) === String(event.detail));
      collectionFilterId = found ? found.id : null;
      channelsOpen = false;
      go({ view: 'feed', feedMode: 'feed' });
    }}
    on:editCollection={(event) => {
      collection = event.detail;
    }}
    on:channelCollections={(event) => {
      collectionChannel = event.detail;
      collection = null;
    }}
  />

  <CollectionDialog
    open={Boolean(collection) || Boolean(collectionChannel)}
    {collection}
    channel={collectionChannel}
    on:deleted={(event) => {
      if (collectionFilterId && String(collectionFilterId) === String(event.detail)) {
        collectionFilterId = null;
      }
    }}
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
    flex-wrap: wrap;
    gap: var(--ui-space-base);
    align-items: center;
    padding: var(--ui-space-snug) var(--ui-space-comfortable);
    padding-top: calc(var(--ui-space-snug) + var(--ui-safe-top));
  }

  .topbar {
    position: sticky;
    top: 0;
    z-index: 20;
    border-bottom: 1px solid var(--ui-line);
    background: var(--ui-bg);
  }

  /* Боковая панель — только на широком экране. На телефоне её заменяет
     нижняя: до верха экрана палец не дотягивается. */
  .side {
    display: none;
  }

  .head-icons {
    display: flex;
    align-items: center;
    gap: var(--ui-space-field);
  }

  /* Правая часть шапки: поиск и фильтры. На телефоне это один ряд, на широком
     экране — столбик у самого края окна. */
  .bar-right {
    display: flex;
    align-items: center;
    gap: var(--ui-space-base);
    margin-left: auto;
  }

  /* Фильтры ленты в шапке: на телефоне их здесь нет, они стоят над лентой. */
  .filters {
    display: none;
    align-items: center;
    gap: var(--ui-space-base);
  }


  .brand {
    display: inline-flex;
    align-items: center;
    gap: var(--ui-space-snug);
    color: var(--ui-ink);
    font-size: 14px;
    font-weight: 700;
    letter-spacing: -0.01em;
  }

  .back {
    display: inline-flex;
    align-items: center;
    gap: var(--ui-space-tight);
    min-height: 44px;
    padding: 0 var(--ui-space-base) 0 var(--ui-space-tight);
    background: none;
    border: 0;
    border-radius: var(--ui-radius);
    color: var(--ui-ink);
    font: inherit;
    font-size: 13px;
    cursor: pointer;
  }

  @media (hover: hover) {
    .back:hover {
      background: color-mix(in srgb, var(--ui-ink) 8%, transparent);
    }
  }

  .search {
    flex: 1 1 220px;
    min-width: 0;
    display: none;
    align-items: center;
  }

  .search.open {
    display: flex;
  }

  /* Поле поиска гасит обводку кита (outline-none), и кольца фокуса не было.
     Кольцо то же, что у остальных контролов. */
  .search :global(input:focus-visible) {
    outline: 2px solid var(--ui-focus);
    outline-offset: 2px;
  }

  .search-toggle {
    display: none;
  }

  .screens {
    min-height: 60vh;
  }

  /* Колода: страница не прокручивается, карточка занимает всё, что осталось
     от шапки и нижней панели. */
  .deck-shell {
    display: flex;
    flex-direction: column;
    height: calc(100vh - var(--koloda-tabs) - var(--ui-safe-bottom));
    height: calc(100dvh - var(--koloda-tabs) - var(--ui-safe-bottom));
    overflow: hidden;
  }

  /* Полный экран колоды: шапка с поиском и панель разделов уходят, карточка занимает экран.
     Шапка остаётся тонкой: в ней панель «Вернуть» (висит поверх колоды), и отмена жеста
     должна быть под рукой. Строка колоды с переключателем тоже остаётся, чтобы выйти. */
  .shell.immersive {
    --koloda-tabs: 0px;
  }

  .shell.immersive .bar,
  .tabs.immersive {
    display: none;
  }

  /* Запас под панель разделов: только там, где она видна. */
  .shell.with-tabs {
    padding-bottom: calc(var(--koloda-tabs) + var(--ui-safe-bottom));
  }

  .deck-shell .topbar {
    flex: 0 0 auto;
    position: relative;
  }

  /* Строка «Вернуть» висит поверх колоды: в потоке она сдвигала бы карточку
     вниз на каждом жесте. */
  .deck-shell .undo-bar {
    position: absolute;
    top: 100%;
    left: 0;
    right: 0;
    z-index: 4;
    background: var(--ui-bg);
    border-bottom: 1px solid var(--ui-line);
  }

  .deck-shell .screens,
  .deck-shell .screen,
  .deck-shell .screen-in {
    flex: 1 1 auto;
    min-height: 0;
    display: flex;
    flex-direction: column;
  }

  /* Появление экрана: вперёд — из-под низа, назад — сверху. Два одинаковых
     набора кадров нужны для перезапуска: имя анимации должно меняться, иначе
     при переходе между разделами она не начнётся заново. */
  .screen-in.anim-1 {
    animation: enter-forward 220ms cubic-bezier(0.2, 0.7, 0.3, 1);
  }

  .screen-in.anim-2 {
    animation: enter-forward 220ms cubic-bezier(0.2, 0.7, 0.3, 1);
  }

  .screen-in.dir-back.anim-1 {
    animation: enter-back 220ms cubic-bezier(0.2, 0.7, 0.3, 1);
  }

  .screen-in.dir-back.anim-2 {
    animation: enter-back 220ms cubic-bezier(0.2, 0.7, 0.3, 1);
  }

  @keyframes enter-forward {
    from {
      opacity: 0;
      transform: translate(10px, 6px);
    }
    to {
      opacity: 1;
      transform: none;
    }
  }

  @keyframes enter-back {
    from {
      opacity: 0;
      transform: translate(-10px, -6px);
    }
    to {
      opacity: 1;
      transform: none;
    }
  }

  @media (prefers-reduced-motion: reduce) {
    .screen-in.anim-1,
    .screen-in.anim-2,
    .screen-in.dir-back.anim-1,
    .screen-in.dir-back.anim-2 {
      animation: none;
    }
  }

  /* Нижняя панель — только на телефоне: на широком экране разделы стоят
     в шапке, и палец до низа экрана не дотягивается. */
  .tabs {
    display: none;
  }

  .tab {
    flex: 1;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 2px;
    padding: 6px 4px calc(6px + var(--ui-safe-bottom));
    background: none;
    border: 0;
    /* Подпись панели — текст, поэтому роль текстовая: ink-muted на фоне
       не проходит 4.5:1 для 12 px. */
    color: var(--ui-ink-soft);
    font: inherit;
    font-size: 12px;
    cursor: pointer;
  }

  .tab[aria-current='page'] {
    color: var(--ui-accent-text);
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

  /* Телефон: узкий экран или палец вместо мыши. Телефон в лежачем положении
     шире 900 px, и там боковая панель была бы сбоку — а палец до низа не
     дотягивается. */
  @media (max-width: 900px), (pointer: coarse) {
    .search-toggle {
      display: inline-flex;
      margin-left: auto;
    }

    /* Поле поиска заняло всю шапку: имя уступает ему место. */
    .bar.searching .brand {
      display: none;
    }

    .tabs {
      position: fixed;
      left: 0;
      right: 0;
      bottom: 0;
      z-index: 30;
      display: flex;
      height: calc(var(--koloda-tabs) + var(--ui-safe-bottom));
      background: var(--ui-bg);
      border-top: 1px solid var(--ui-line);
    }

    .tabs.hidden {
      display: none;
    }

    .bar {
      padding-bottom: var(--ui-space-tight);
    }

    /* Кнопка «Назад» задаёт свою высоту в базовом стиле: здесь перебиваем её
       на палец, в том же блоке и с той же специфичностью. */
    .back {
      min-height: 48px;
    }

    /* Полноэкранный телефон: шапка не должна уйти под часы и вырез. */
    .topbar {
      padding-top: var(--ui-safe-top);
    }
  }

  @media (min-width: 901px) and (pointer: fine) {
    /* На широком экране разделы стоят в боковой панели: все назначения видны
       сразу и названы, а место занимает то, что на телефоне занимает палец. */
    .side {
      display: flex;
      flex-direction: column;
      position: fixed;
      top: 0;
      left: 0;
      bottom: 0;
      z-index: 25;
      width: var(--koloda-side);
      padding: var(--ui-space-comfortable) var(--ui-space-base);
      border-right: 1px solid var(--ui-line);
      background: var(--ui-bg);
    }

    .shell {
      padding-left: var(--koloda-side);
    }

    .side-brand {
      display: flex;
      align-items: center;
      gap: var(--ui-space-snug);
      padding: 0 var(--ui-space-field);
      margin-bottom: var(--ui-space-roomy);
      color: var(--ui-ink);
      font-size: 15px;
      font-weight: 700;
      letter-spacing: -0.01em;
    }

    .side-nav {
      display: flex;
      flex-direction: column;
      gap: 2px;
    }

    .side-foot {
      display: flex;
      flex-direction: column;
      gap: 2px;
      margin-top: auto;
      padding-top: var(--ui-space-base);
      border-top: 1px solid var(--ui-line);
    }

    /* Поиск стоит в правом верхнем углу окна, а фильтры ленты — под ним, с
       тем же правым краем: глаз идёт от поля к постам без поворота. В колонке
       flex-basis становится высотой, поэтому ширину задаём прямо. */
    .search {
      display: flex;
      flex: 0 0 auto;
      width: 360px;
    }

    .bar {
      max-width: none;
    }

    .bar-right {
      flex-direction: column;
      align-items: flex-end;
      gap: var(--ui-space-snug);
    }

    .filters {
      display: flex;
    }

    .brand {
      display: none;
    }

    .head-icons {
      display: none;
    }
  }
</style>

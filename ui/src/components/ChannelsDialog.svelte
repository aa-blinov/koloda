<script>
  /**
   * Окно каналов: подписки с состоянием их истории и коллекции профиля.
   * Канал выбирают в списке, и родитель открывает его в ленте.
   */
  import { createEventDispatcher } from 'svelte';
  import { Button, Chip, EmptyState, IconButton, Modal, Notice, Separator, Skeleton, TextField } from '@monos/ui';

  import Icon from './Icon.svelte';
  import { api } from '../lib/api.js';
  import { historyDetail, historyPercent } from '../lib/history.js';
  import { subs, titles, avatars, collections, setSubscription, loadAvatars } from '../lib/stores.js';

  export let open = false;

  const dispatch = createEventDispatcher();

  // Мёртвое фото канала уступает место букве: адрес на CDN Telegram живёт недолго.
  let broken = new Set();
  let history = null;
  let loading = false;
  let requestId = 0;

  let link = '';
  let linkState = '';
  let linkError = '';
  let adding = false;

  let collectionName = '';
  let collectionError = '';
  let creating = false;
  let removing = null; // коллекция, которую читатель собирается удалить
  let deleting = false;

  $: historyBy = new Map(
    (history && history.channels ? history.channels : []).map((item) => [item.channel, item])
  );
  $: cap = history && history.cap ? history.cap : 0;

  // Данные читаем, когда окно открывают, и обновляем при каждом открытии:
  // подписки и состояние истории меняются в ленте, а на каждый ввод ходить на
  // сервер незачем.
  let loaded = false;
  $: watchOpen(open);

  function watchOpen(isOpen) {
    if (isOpen && !loaded) {
      loaded = true;
      load();
    } else if (!isOpen) {
      loaded = false;
    }
  }

  async function load() {
    const id = ++requestId;
    loading = true;
    // Обе записи идут разом: список каналов и коллекции открываются вместе,
    // а по очереди окно на миг показывало бы половину.
    const answers = await Promise.allSettled([api.history(), api.collections()]);
    if (id !== requestId) return;
    const [historyAnswer, collectionsAnswer] = answers;
    // Без истории список каналов всё равно полезен: она придёт в следующий раз.
    history = historyAnswer.status === 'fulfilled' ? historyAnswer.value || null : null;
    if (collectionsAnswer.status === 'fulfilled') {
      collections.set((collectionsAnswer.value && collectionsAnswer.value.collections) || []);
    }
    loadAvatars($subs);
    loading = false;
  }

  async function refreshCollections() {
    try {
      const answer = await api.collections();
      collections.set((answer && answer.collections) || []);
    } catch (err) {
      // Список останется прежним: он уже на экране.
    }
  }


  const letter = (name) => (String(name || '').trim()[0] || '?').toUpperCase();

  // Коллекции, в которых лежит канал: их число показывает кнопка в строке.
  const collectionsOf = (name) => $collections.filter((c) => (c.channels || []).includes(name));

  function pickChannel(channel) {
    dispatch('openChannel', channel);
  }

  async function addByLink(event) {
    event.preventDefault();
    if (adding) return;
    linkState = '';
    linkError = '';
    const name = link
      .trim()
      .replace(/^https?:\/\/(www\.)?t\.me\//, '')
      .replace(/^@/, '')
      .split('/')[0];
    if (!name) {
      linkError = 'Напиши имя канала или ссылку на него.';
      return;
    }
    adding = true;
    try {
      const answer = await api.check(name);
      setSubscription(name, true);
      if (answer && answer.title) titles.set({ ...$titles, [name]: answer.title });
      linkState = 'Канал «' + (answer.title || name) + '» добавлен в ленту.';
      link = '';
      loadAvatars([name]);
      await load(); // у нового канала своя строка истории
    } catch (err) {
      linkError = (err && err.message) || 'Канал не найден.';
    } finally {
      adding = false;
    }
  }

  async function createCollection(event) {
    event.preventDefault();
    if (creating) return;
    const name = collectionName.trim();
    if (!name) return;
    creating = true;
    collectionError = '';
    try {
      // Каналы в коллекцию отбирает родитель: здесь только сама коллекция.
      await api.saveCollection({ name, channels: [] });
      collectionName = '';
      await refreshCollections();
    } catch (err) {
      collectionError = (err && err.message) || 'Не удалось создать коллекцию.';
    } finally {
      creating = false;
    }
  }

  async function removeCollection() {
    if (!removing || deleting) return;
    deleting = true;
    collectionError = '';
    const id = removing.id;
    try {
      await api.deleteCollection(id);
      removing = null;
      await refreshCollections();
    } catch (err) {
      collectionError = (err && err.message) || 'Не удалось удалить коллекцию.';
      removing = null;
    } finally {
      deleting = false;
    }
  }
</script>

<div id="chanList">
<Modal
  {open}
  title="Каналы и коллекции"
  size="lg"
  closeLabel="Закрыть"
  on:close={() => dispatch('close')}
>
  <form class="add" on:submit|preventDefault={addByLink}>
    <TextField
      label="Канал по ссылке"
      bind:value={link}
      placeholder="@username или t.me/username"
      autocomplete="off"
      variant="boxed"
    />
    <Button type="submit" loading={adding}>Добавить</Button>
  </form>

  {#if linkState}
    <Notice tone="success" title="Готово">{linkState}</Notice>
  {/if}
  {#if linkError}
    <Notice tone="danger" title="Не получилось">{linkError}</Notice>
  {/if}

  <Separator class="my-6" />

  <section class="section">
    <h3 class="title">Каналы</h3>
    <p class="hint">Состояние истории: что уже загружено, а что ждёт очереди.</p>

    {#if loading && !$subs.length}
      <Skeleton class="h-12" />
    {:else if !$subs.length}
      <EmptyState
        title="Каналов пока нет"
        description="Открой каталог, выбери нужные, и они появятся здесь."
      />
    {:else}
      <div class="list">
        {#each $subs as channel (channel)}
          {@const item = historyBy.get(channel)}
          {@const name = $titles[channel] || channel}
          <div class="row">
          <button type="button" class="chan" on:click={() => pickChannel(channel)}>
            <span class="photo" aria-hidden="true">
              {#if $avatars.get(channel) && !broken.has(channel)}
                <img
                  src={$avatars.get(channel)}
                  alt=""
                  loading="lazy"
                  referrerpolicy="no-referrer"
                  on:error={() => (broken = new Set(broken).add(channel))}
                />
              {:else}
                {letter(name)}
              {/if}
            </span>
            <span class="copy">
              <span class="name">{name}</span>
              {#if item}
                <span class="state">{historyDetail(item, cap)}</span>
              {/if}
            </span>
            {#if item}
              <span class="bar" aria-hidden="true">
                <span style="width: {historyPercent(item, cap)}%"></span>
              </span>
            {/if}
          </button>
          <!-- Быстрое «в коллекцию»: канал и его коллекции, без похода в каталог. -->
          <button
            type="button"
            class="quiet-btn unsub-btn"
            on:click={() => dispatch('unsubscribe', channel)}
          >Отписаться</button>
          <button
            type="button"
            class="quiet-btn"
            on:click={() => dispatch('channelCollections', channel)}
          >
            {collectionsOf(channel).length
              ? 'В коллекциях: ' + collectionsOf(channel).length
              : 'В коллекцию'}
          </button>
          </div>
        {/each}
      </div>
    {/if}
  </section>

  <Separator class="my-6" />

  <section class="section">
    <h3 class="title">Коллекции</h3>
    <p class="hint">Группы каналов из твоих подписок.</p>

    {#if loading && !$collections.length}
      <Skeleton class="h-12" />
    {:else if !$collections.length}
      <p class="hint">Коллекций пока нет. Создай первую ниже.</p>
    {:else}
      <div class="list">
        {#each $collections as coll (coll.id)}
          <div class="coll">
            <div class="coll-head">
              <button
                type="button"
                class="coll-name"
                on:click={() => dispatch('openCollection', coll.id)}
              >
                <span class="name">{coll.name}</span>
                <span class="count">{coll.channels.length ? coll.channels.length : 'нет каналов'}</span>
              </button>
              <!-- Правка коллекции — в родителе: там список подписок и галочки. -->
              <IconButton
                label="Изменить коллекцию {coll.name}"
                variant="ghost"
                size="sm"
                on:click={() => dispatch('editCollection', coll)}
              >
                <Icon name="settings" size={16} />
              </IconButton>
              <IconButton
                label="Удалить коллекцию {coll.name}"
                variant="ghost"
                size="sm"
                on:click={() => (removing = coll)}
              >
                <Icon name="trash" size={16} />
              </IconButton>
            </div>

            {#if coll.channels.length}
              <div class="chips">
                {#each coll.channels as name (name)}
                  <button type="button" class="tag" on:click={() => pickChannel(name)}>
                    <Chip>{$titles[name] || name}</Chip>
                  </button>
                {/each}
              </div>
            {/if}

            {#if removing && removing.id === coll.id}
              <div class="confirm">
                <span>Удалить коллекцию «{coll.name}»? Каналы останутся в ленте.</span>
                <Button size="sm" variant="danger" loading={deleting} on:click={removeCollection}>
                  Удалить
                </Button>
                <Button size="sm" variant="secondary" on:click={() => (removing = null)}>Отмена</Button>
              </div>
            {/if}
          </div>
        {/each}
      </div>
    {/if}

    {#if !$subs.length}
      <p class="hint">Подписок нет: сначала подпишись на канал, потом собирай коллекции.</p>
    {/if}
    <form class="coll-new" hidden={!$subs.length} on:submit|preventDefault={createCollection}>
      <TextField
        label="Новая коллекция"
        bind:value={collectionName}
        placeholder="Название"
        autocomplete="off"
        variant="boxed"
        size="sm"
      />
      <Button type="submit" size="sm" loading={creating} disabled={!collectionName.trim()}>
        Создать
      </Button>
    </form>

    {#if collectionError}
      <div id="collListMsg">
        <Notice tone="danger" title="Не получилось">{collectionError}</Notice>
      </div>
    {/if}
  </section>

  <svelte:fragment slot="footer">
    <Button variant="secondary" on:click={() => dispatch('close')}>Закрыть</Button>
  </svelte:fragment>
</Modal>
</div>

<style>
  /* Сетка, а не флекс: поле и кнопка — это корни двух разных компонентов,
     и селектор по первому ребёнку к ним не применить. */
  .add {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    align-items: end;
    gap: 12px;
  }

  .section {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .title {
    margin: 0;
    font-size: 13px;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--ui-ink);
  }

  .hint {
    margin: 0;
    font-size: 12px;
    color: var(--ui-ink-faint);
  }

  .list {
    display: flex;
    flex-direction: column;
    gap: 2px;
    margin-top: 4px;
  }

  .row {
    display: flex;
    align-items: center;
    gap: var(--ui-space-snug);
  }

  .quiet-btn {
    flex: 0 0 auto;
    min-height: var(--ui-control-touch);
    padding: 0 var(--ui-space-base);
    background: none;
    border: 0;
    border-radius: var(--ui-radius);
    color: var(--ui-ink-soft);
    font: inherit;
    font-size: 12px;
    cursor: pointer;
    white-space: nowrap;
  }

  .quiet-btn:hover {
    color: var(--ui-ink);
    background: color-mix(in srgb, var(--ui-ink) 8%, transparent);
  }

  .chan {
    display: grid;
    grid-template-columns: auto minmax(0, 1fr);
    align-items: center;
    gap: 2px 12px;
    width: 100%;
    padding: 8px;
    font: inherit;
    text-align: left;
    color: var(--ui-ink);
    background: none;
    border: 0;
    border-radius: var(--ui-radius);
    cursor: pointer;
  }

  .chan:hover {
    background: var(--ui-surface);
  }

  .photo {
    grid-row: 1;
    display: grid;
    place-items: center;
    width: 36px;
    height: 36px;
    overflow: hidden;
    border-radius: var(--ui-radius);
    background: var(--ui-surface-2);
    font-size: 15px;
    font-weight: 500;
  }

  .photo img {
    display: block;
    width: 100%;
    height: 100%;
    object-fit: cover;
  }

  .copy {
    grid-column: 2;
    display: flex;
    flex-direction: column;
    gap: 2px;
    min-width: 0;
  }

  .name {
    overflow: hidden;
    font-size: 14px;
    font-weight: 700;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .state {
    /* Состояние истории длинное: обрезанное «дальше т…» ничего не говорит,
       поэтому строка переносится, а имя канала остаётся в одну строку. */
    font-size: 12px;
    color: var(--ui-ink-faint);
    line-height: 1.45;
  }

  .bar {
    grid-column: 2;
    height: 4px;
    overflow: hidden;
    border-radius: var(--ui-radius);
    background: var(--ui-surface-2);
  }

  .bar span {
    display: block;
    height: 100%;
    background: var(--ui-accent);
  }

  .coll {
    display: flex;
    flex-direction: column;
    gap: 8px;
    padding: 8px;
    border: 1px solid var(--ui-line);
    border-radius: var(--ui-radius);
  }

  .coll-head {
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .coll-name {
    display: flex;
    align-items: baseline;
    gap: 8px;
    flex: 1 1 auto;
    min-width: 0;
    padding: 0;
    font: inherit;
    text-align: left;
    color: var(--ui-ink);
    background: none;
    border: 0;
    cursor: pointer;
  }

  .coll-name:hover .name {
    color: var(--ui-accent-text);
  }

  .count {
    flex: 0 0 auto;
    font-size: 12px;
    color: var(--ui-ink-faint);
  }

  .chips {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
  }

  .tag {
    padding: 0;
    background: none;
    border: 0;
    cursor: pointer;
  }

  .confirm {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 8px;
    font-size: 12px;
    color: var(--ui-ink-soft);
  }

  .coll-new {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    align-items: end;
    gap: 12px;
    margin-top: 4px;
  }
</style>

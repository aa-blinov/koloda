<script>
  /**
   * The editor of a collection: the name and the channels of it. The channels
   * can only be from the subscriptions — the server checks that too.
   *
   * The same dialog serves the other direction: the collections a channel
   * belongs to. That is the quick "into a collection" from the list of the
   * channels, and it saves at once.
   */
  import { createEventDispatcher } from 'svelte';
  import { Button, Modal, Notice, TextField } from '@monos/ui';

  import Icon from './Icon.svelte';
  import { api } from '../lib/api.js';
  import { subs, titles, collections } from '../lib/stores.js';

  export let open = false;
  export let collection = null;
  // Канал, чьи коллекции правятся. Пусто — правится сама коллекция.
  export let channel = '';

  const dispatch = createEventDispatcher();

  let name = '';
  let picked = [];
  let busy = false;
  let problem = '';

  $: if (open) {
    name = collection ? collection.name : '';
    picked = collection ? [...(collection.channels || [])] : [];
    problem = '';
  }

  $: available = $subs.filter((name) => !picked.includes(name));

  function toggle(channelName) {
    picked = picked.includes(channelName)
      ? picked.filter((c) => c !== channelName)
      : [...picked, channelName];
  }

  // Галочка коллекции сохраняется сразу: человек не ждёт кнопки «Сохранить»
  // после каждой галочки.
  async function toggleInCollection(coll, checked) {
    if (busy) return;
    busy = true;
    problem = '';
    try {
      const channels = checked
        ? [...new Set([...(coll.channels || []), channel])]
        : (coll.channels || []).filter((c) => c !== channel);
      const answer = await api.saveCollection({ id: coll.id, name: coll.name, channels });
      const saved = answer.collection || answer;
      collections.update((list) => list.map((c) => (c.id === saved.id ? saved : c)));
    } catch (err) {
      problem = err && err.message ? err.message : 'Не удалось сохранить коллекцию.';
    } finally {
      busy = false;
    }
  }

  async function save() {
    if (busy) return;
    busy = true;
    problem = '';
    try {
      const body = { name: name.trim(), channels: picked };
      if (collection && collection.id) body.id = collection.id;
      const answer = await api.saveCollection(body);
      const saved = answer.collection || answer;
      collections.update((list) => {
        const rest = list.filter((c) => c.id !== saved.id);
        return [...rest, saved];
      });
      dispatch('close');
    } catch (err) {
      problem = err && err.message ? err.message : 'Не удалось сохранить коллекцию.';
    } finally {
      busy = false;
    }
  }

  async function remove() {
    if (!collection || !collection.id || busy) return;
    busy = true;
    problem = '';
    try {
      await api.deleteCollection(collection.id);
      collections.update((list) => list.filter((c) => c.id !== collection.id));
      dispatch('close');
    } catch (err) {
      problem = err && err.message ? err.message : 'Не удалось удалить коллекцию.';
    } finally {
      busy = false;
    }
  }
</script>

<div id="collOvl">
<Modal
  {open}
  title={channel ? 'Коллекции канала' : collection && collection.id ? 'Коллекция' : 'Новая коллекция'}
  description={channel
    ? 'Отметь коллекции, в которых лежит канал. Галочка сохраняется сразу.'
    : 'Каналы берутся из подписок. Название — до 32 символов.'}
  closeLabel="Закрыть"
  on:close={() => dispatch('close')}
>
  {#if channel}
    {#if !$collections.length}
      <p class="hint">Коллекций пока нет. Создай первую в списке каналов.</p>
    {:else}
      <ul class="picks" id="chanCollList">
        {#each $collections as coll (coll.id)}
          <li>
            <label>
              <input
                type="checkbox"
                value={coll.id}
                checked={(coll.channels || []).includes(channel)}
                on:change={(event) => toggleInCollection(coll, event.currentTarget.checked)}
              />
              <span>{coll.name}</span>
            </label>
          </li>
        {/each}
      </ul>
    {/if}
  {:else}
    <TextField label="Название" bind:value={name} placeholder="Новости" />

    <div class="channels">
      {#if !available.length}
        <p class="hint">Все каналы уже в коллекции. Добавь подписку в каталоге.</p>
      {:else}
        <ul>
          {#each available as name (name)}
            <li>
              <button type="button" class="chan" on:click={() => toggle(name)}>
                <Icon name="plus" size={14} />
                {($titles && $titles[name]) || name}
              </button>
            </li>
          {/each}
        </ul>
      {/if}
    </div>

    {#if picked.length}
      <p class="hint">В коллекции: {picked.map((c) => ($titles && $titles[c]) || c).join(', ')}</p>
    {/if}
  {/if}

  {#if problem}
    <Notice tone="danger" title="Не получилось">{problem}</Notice>
  {/if}

  <svelte:fragment slot="footer">
    {#if collection && collection.id && !channel}
      <Button variant="danger" on:click={remove} disabled={busy}>Удалить</Button>
    {/if}
    <Button variant="secondary" on:click={() => dispatch('close')}>Отмена</Button>
    {#if !channel}
      <Button on:click={save} loading={busy}>Сохранить</Button>
    {/if}
  </svelte:fragment>
</Modal>
</div>

<style>
  .channels {
    margin: var(--ui-space-base) 0;
  }

  .channels ul {
    list-style: none;
    padding: 0;
    margin: 0;
    max-height: 40vh;
    overflow-y: auto;
  }

  .channels li {
    border-bottom: 1px solid var(--ui-line);
  }

  .chan {
    display: flex;
    align-items: center;
    gap: var(--ui-space-snug);
    width: 100%;
    min-height: var(--ui-control-touch);
    padding: 0;
    background: none;
    border: 0;
    font: inherit;
    color: var(--ui-ink);
    cursor: pointer;
    text-align: left;
  }

  .chan:hover {
    color: var(--ui-accent-text);
  }

  .picks {
    list-style: none;
    padding: 0;
    margin: 0;
  }

  .picks li {
    border-bottom: 1px solid var(--ui-line);
  }

  .picks label {
    display: flex;
    align-items: center;
    gap: var(--ui-space-snug);
    min-height: var(--ui-control-touch);
    cursor: pointer;
  }

  .hint {
    color: var(--ui-ink-faint);
    font-size: 12.5px;
    margin: 0;
  }
</style>

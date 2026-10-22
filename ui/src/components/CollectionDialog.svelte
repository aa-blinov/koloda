<script>
  /**
   * The editor of a collection: the name and the channels of it. The channels
   * can only be from the subscriptions — the server checks that too.
   */
  import { createEventDispatcher } from 'svelte';
  import { Button, Modal, Notice, TextField } from '@monos/ui';

  import Icon from './Icon.svelte';
  import { api } from '../lib/api.js';
  import { subs, titles, collections } from '../lib/stores.js';

  export let open = false;
  export let collection = null;

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

  $: available = $subs.filter((channel) => !picked.includes(channel));

  function toggle(channel) {
    picked = picked.includes(channel)
      ? picked.filter((c) => c !== channel)
      : [...picked, channel];
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

<Modal
  {open}
  title={collection && collection.id ? 'Коллекция' : 'Новая коллекция'}
  description="Каналы берутся из подписок. Название — до 32 символов."
  closeLabel="Закрыть"
  on:close={() => dispatch('close')}
>
  <TextField label="Название" bind:value={name} placeholder="Новости" />

  <div class="channels">
    {#if !available.length}
      <p class="hint">Все каналы уже в коллекции. Добавь подписку в каталоге.</p>
    {:else}
      <ul>
        {#each available as channel (channel)}
          <li>
            <button type="button" class="chan" on:click={() => toggle(channel)}>
              <Icon name="plus" size={14} />
              {($titles && $titles[channel]) || channel}
            </button>
          </li>
        {/each}
      </ul>
    {/if}
  </div>

  {#if picked.length}
    <p class="hint">В коллекции: {picked.map((c) => ($titles && $titles[c]) || c).join(', ')}</p>
  {/if}

  {#if problem}
    <Notice tone="danger" title="Не получилось">{problem}</Notice>
  {/if}

  <svelte:fragment slot="footer">
    {#if collection && collection.id}
      <Button variant="danger" on:click={remove} disabled={busy}>Удалить</Button>
    {/if}
    <Button variant="secondary" on:click={() => dispatch('close')}>Отмена</Button>
    <Button on:click={save} loading={busy}>Сохранить</Button>
  </svelte:fragment>
</Modal>

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

  .hint {
    color: var(--ui-ink-faint);
    font-size: 12.5px;
    margin: 0;
  }
</style>

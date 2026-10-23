<script>
  /**
   * The profile: the account, the data, the look and the help. The Telegram
   * panel lives here too — it is the way to the private channels.
   */
  import { onMount } from 'svelte';
  import { createEventDispatcher } from 'svelte';
  import { Button, DescriptionList, Kbd, Notice, SegmentedControl, Select, Separator, Tabs, TextField } from '@monos/ui';

  import Icon from './Icon.svelte';
  import { api } from '../lib/api.js';
  import { session, settings, saveSettings, THEMES, MODES, FONTS, setSubscription } from '../lib/stores.js';

  const dispatch = createEventDispatcher();

  // У палитр кита есть имена: «gruvbox» в списке выглядит как служебный код.
  const THEME_NAMES = {
    gruvbox: 'Gruvbox',
    github: 'GitHub',
    dracula: 'Dracula',
    nord: 'Nord',
    solarized: 'Solarized',
    catppuccin: 'Catppuccin',
  };
  const themeOptions = THEMES.map((name) => ({ value: name, label: THEME_NAMES[name] || name }));
  const modeOptions = [
    { value: 'dark', label: 'Тёмная' },
    { value: 'light', label: 'Светлая' },
    { value: 'auto', label: 'Как в системе' },
  ];
  const fontOptions = [
    { value: 'mono', label: 'Моно' },
    { value: 'sans', label: 'Без засечек' },
    { value: 'serif', label: 'С засечками' },
  ];

  const keys = [
    { key: 'j', what: 'следующий пост' },
    { key: 'k', what: 'предыдущий пост' },
    { key: 'n', what: 'следующий непрочитанный' },
    { key: 'u', what: 'отметить прочитанным или наоборот' },
    { key: '/', what: 'поиск' },
    { key: '?', what: 'эта справка' },
  ];

  let tab = 'account';
  let email = '';
  let password = '';
  let emailMessage = '';
  let emailProblem = '';
  let busy = false;

  let tgInstalled = false;
  let tgLinked = false;
  let tgTitle = '';
  let tgPhone = '';
  let tgChannels = [];
  let tgToken = '';
  let tgQr = '';
  let tgState = '';
  let tgProblem = '';

  $: if ($session && !$session.email && !email) email = '';

  onMount(() => {
    loadTelegram();
    const onHelp = () => (tab = 'help');
    window.addEventListener('koloda:help', onHelp);
    return () => window.removeEventListener('koloda:help', onHelp);
  });

  async function loadTelegram() {
    try {
      const status = await api.tgStatus();
      tgInstalled = Boolean(status.installed);
      tgLinked = Boolean(status.linked);
      tgTitle = status.title || '';
      tgPhone = status.phone || '';
      if (tgLinked) {
        const answer = await api.tgChannels();
        tgChannels = answer.channels || [];
      }
    } catch (err) {
      tgInstalled = false;
    }
  }

  async function saveEmail(event) {
    event.preventDefault();
    emailProblem = '';
    emailMessage = '';
    busy = true;
    try {
      await api.setEmail(password, email.trim());
      emailMessage = 'Почта сохранена. Подтверди её по ссылке из письма.';
      password = '';
    } catch (err) {
      emailProblem = err && err.message ? err.message : 'Не удалось сохранить почту.';
    } finally {
      busy = false;
    }
  }

  let logoutArmed = false;
  let logoutTimer = null;

  // Выход просит подтверждения: одно нажатие не должно выкидывать из профиля.
  function askLogout() {
    if (logoutArmed) {
      clearTimeout(logoutTimer);
      doLogout();
      return;
    }
    logoutArmed = true;
    logoutTimer = setTimeout(() => (logoutArmed = false), 6000);
  }

  async function doLogout() {
    try {
      await api.logout();
    } catch (err) {
      // The session is gone anyway: the server will not accept the old cookie.
    }
    location.reload();
  }

  async function tgStart() {
    tgProblem = '';
    tgState = 'Жду подтверждения в Telegram…';
    try {
      const answer = await api.tgLinkStart();
      if (answer.state === 'done') {
        tgState = 'Telegram уже подключён';
        await loadTelegram();
        return;
      }
      tgToken = answer.token || '';
      tgQr = answer.qr || '';
      tgState = 'Открой Telegram и подтверди вход по коду.';
      poll();
    } catch (err) {
      tgProblem = err && err.message ? err.message : 'Не удалось начать вход.';
      tgState = '';
    }
  }

  async function poll() {
    if (!tgToken) return;
    try {
      const answer = await api.tgLinkPoll(tgToken);
      if (answer.state === 'done') {
        tgToken = '';
        tgQr = '';
        tgState = 'Готово, Telegram подключён';
        await loadTelegram();
        return;
      }
      if (answer.state === 'expired') {
        tgToken = '';
        tgQr = '';
        tgState = answer.error || 'Ссылка протухла, начни заново';
        return;
      }
      if (answer.qr) tgQr = answer.qr;
      setTimeout(poll, 1500);
    } catch (err) {
      tgProblem = err && err.message ? err.message : 'Вход прерван.';
      tgState = '';
    }
  }

  async function tgCancel() {
    if (tgToken) {
      try {
        await api.tgLinkCancel(tgToken);
      } catch (err) {
        // The token is dead anyway: the link expires on its own.
      }
    }
    tgToken = '';
    tgQr = '';
    tgState = '';
  }

  async function tgAction(action) {
    tgProblem = '';
    try {
      if (action === 'sync') {
        const answer = await api.tgSync();
        tgState = answer && answer.found ? 'Каналов найдено: ' + answer.found : 'Приватных каналов не найдено';
      } else if (action === 'discover') {
        const answer = await api.tgDiscover();
        tgState = 'Каналов найдено: ' + answer.found;
      } else if (action === 'unlink') {
        await api.tgUnlink();
        tgState = 'Telegram отключён';
        tgToken = '';
        tgQr = '';
        await loadTelegram();
        return;
      }
      await loadTelegram();
    } catch (err) {
      tgProblem = err && err.message ? err.message : 'Не получилось.';
    }
  }
</script>

<div class="column profile">
  <h1>Профиль</h1>

  <Tabs
    items={[
      { id: 'account', label: 'Аккаунт' },
      { id: 'telegram', label: 'Telegram' },
      { id: 'data', label: 'Данные' },
      { id: 'view', label: 'Вид' },
      { id: 'help', label: 'Справка' },
    ]}
    bind:value={tab}
    label="Разделы настроек"
  >
    <svelte:fragment slot="panel" let:active>
      {#if active === 'account'}
        <section class="panel">
          <p class="account-name">@{($session && $session.username) || ''}</p>
          <p class="hint">
            {$session && $session.email
              ? $session.email_verified
                ? 'Почта подтверждена. Её можно использовать для восстановления пароля.'
                : 'Подтверди почту по ссылке из письма.'
              : 'Добавь почту, чтобы восстановить пароль, если забудешь его.'}
          </p>

          <form class="email-form" on:submit|preventDefault={saveEmail}>
            <TextField type="email" label="Почта" bind:value={email} placeholder="you@example.com" />
            <TextField type="password" label="Пароль профиля" bind:value={password} autocomplete="current-password" />
            <Button type="submit" loading={busy}>Сохранить почту</Button>
          </form>

          {#if emailMessage}
            <Notice tone="success" title="Готово">{emailMessage}</Notice>
          {/if}
          {#if emailProblem}
            <Notice tone="danger" title="Не получилось">{emailProblem}</Notice>
          {/if}

          <Separator />

          <h2>Частные каналы</h2>
          <p class="hint">Вход по QR в Telegram открывает и приватные каналы. Он живёт на отдельной вкладке.</p>
          <Button variant="text" on:click={() => (tab = 'telegram')}>К Telegram</Button>

          <Separator />

          <Button variant="danger" on:click={askLogout}>
            {logoutArmed ? 'Точно выйти?' : 'Выйти из профиля'}
          </Button>
        </section>
      {:else if active === 'telegram'}
        <section class="panel">
          <h2>Частные каналы</h2>
          {#if !tgInstalled}
            <p class="hint">Приватные каналы сейчас недоступны: в образе нет Telethon.</p>
          {:else if !tgLinked}
            <p class="hint">
              Публичные каналы работают и без этого. Подключишь Telegram — появятся ещё и приватные,
              где ты участник.
            </p>
            <Button on:click={tgStart}>Подключить Telegram</Button>
          {:else}
            <p class="hint">
              Твой Telegram подключён{tgTitle ? ': ' + tgTitle : ''}{tgPhone ? ' (' + tgPhone + ')' : ''}.
            </p>
            <div class="tg-actions">
              <Button variant="text" size="sm" on:click={() => tgAction('sync')}>Обновить список</Button>
              <Button variant="text" size="sm" on:click={() => tgAction('discover')}>Найти каналы</Button>
              <Button variant="text" size="sm" on:click={() => tgAction('unlink')}>Отключить</Button>
            </div>
            {#if tgChannels.length}
              <ul class="tg-list">
                {#each tgChannels as channel (channel.channel)}
                  <li>
                    <span>{channel.title || channel.channel}</span>
                    <Button
                      variant="text"
                      size="sm"
                      on:click={() => api.tgSubscribe(channel.channel, !channel.subscribed).then(loadTelegram)}
                    >
                      {channel.subscribed ? 'Убрать из ленты' : 'Добавить в ленту'}
                    </Button>
                  </li>
                {/each}
              </ul>
            {/if}
          {/if}

          {#if tgState}
            <p class="hint" role="status">{tgState}</p>
          {/if}
          {#if tgProblem}
            <Notice tone="danger" title="Не получилось">{tgProblem}</Notice>
          {/if}
          {#if tgQr}
            <div class="qr">
              {@html tgQr}
              <Button variant="text" size="sm" on:click={tgCancel}>Отменить</Button>
            </div>
          {/if}

        </section>
      {:else if active === 'data'}
        <section class="panel">
          <h2>Резервная копия</h2>
          <p class="hint">
            Файл со списком каналов и отметок о прочитанном. В нём всё, что сервис знает о профиле.
          </p>
          <a class="export" href="/api/state/export" download="koloda-lenta.jsonl">
            <Icon name="download" size={14} />
            Скачать koloda-lenta.jsonl
          </a>
        </section>
      {:else if active === 'view'}
        <section class="panel">
          <div class="view-row">
            <Select
              options={themeOptions}
              value={$settings.theme}
              label="Палитра"
              on:change={(event) => saveSettings({ theme: event.detail.value })}
            />
          </div>

          <h2>Светлота</h2>
          <SegmentedControl
            options={modeOptions}
            value={$settings.mode}
            label="Светлота"
            on:change={(event) => saveSettings({ mode: event.detail.value })}
          />

          <h2>Шрифт</h2>
          <SegmentedControl
            options={fontOptions}
            value={$settings.font}
            label="Шрифт"
            on:change={(event) => saveSettings({ font: event.detail.value })}
          />
        </section>
      {:else if active === 'help'}
        <section class="panel">
          <h2>Клавиши</h2>
          <p class="hint">Работают, когда курсор не в поле ввода.</p>
          <DescriptionList
            items={keys.map((item) => ({ term: item.key, description: item.what }))}
            layout="inline"
          />
          <h2>Что где</h2>
          <DescriptionList
            items={[
              { term: 'Лента', description: 'Публикации каналов, на которые ты подписан.' },
              { term: 'Каталог', description: 'Каталог публичных каналов Telegram.' },
              { term: 'Сохранённое', description: 'Публикации, которые ты сохранил на потом.' },
              { term: 'Профиль', description: 'Аккаунт, резервная копия, вид и справка.' },
            ]}
            layout="inline"
          />
        </section>
      {/if}
    </svelte:fragment>
  </Tabs>
</div>

<style>
  .profile {
    padding-top: var(--ui-space-roomy);
  }

  h1 {
    font-size: 24px;
    line-height: 1.25;
    font-weight: 700;
    letter-spacing: -0.025em;
    margin: 0 0 var(--ui-space-comfortable);
  }

  h2 {
    font-size: 14px;
    font-weight: 700;
    margin: var(--ui-space-roomy) 0 var(--ui-space-snug);
  }

  .panel {
    padding-top: var(--ui-space-base);
  }

  .account-name {
    font-size: 18px;
    font-weight: 700;
  }

  .hint {
    color: var(--ui-ink-soft);
    font-size: 13px;
    margin: 0 0 var(--ui-space-base);
    max-width: 60ch;
  }

  .email-form {
    display: flex;
    flex-direction: column;
    gap: var(--ui-space-base);
    align-items: flex-start;
    margin-bottom: var(--ui-space-base);
  }

  .email-form :global(.field),
  .email-form :global(label) {
    width: 100%;
    max-width: 420px;
  }

  .export {
    display: inline-flex;
    align-items: center;
    gap: var(--ui-space-field);
    min-height: 36px;
    color: var(--ui-ink);
    text-decoration: none;
    font-size: 12px;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }

  @media (hover: hover) {
    .export:hover {
      color: var(--ui-accent-text);
    }
  }

  .tg-actions {
    display: flex;
    gap: var(--ui-space-base);
    flex-wrap: wrap;
    margin-bottom: var(--ui-space-snug);
  }

  .tg-list {
    list-style: none;
    padding: 0;
    margin: 0 0 var(--ui-space-base);
  }

  .tg-list li {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--ui-space-base);
    padding: var(--ui-space-snug) 0;
    border-bottom: 1px solid var(--ui-line);
  }

  .qr {
    display: flex;
    align-items: center;
    gap: var(--ui-space-comfortable);
    margin: var(--ui-space-base) 0;
  }

  .qr :global(svg) {
    width: 180px;
    height: 180px;
    background: var(--ui-bg);
    border: 1px solid var(--ui-line);
    border-radius: var(--ui-radius);
    padding: var(--ui-space-snug);
  }

  .view-row {
    max-width: 320px;
  }
</style>

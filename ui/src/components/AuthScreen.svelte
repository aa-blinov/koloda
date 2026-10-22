<script>
  /**
   * The entry to the profile: the sign in, the registration and the recovery of
   * the password. The mode decides which fields are on the screen.
   */
  import { createEventDispatcher } from 'svelte';
  import { Button, Card, Notice, SegmentedControl, TextField } from '@monos/ui';

  import Icon from './Icon.svelte';
  import { api } from '../lib/api.js';

  export let bootError = '';

  const dispatch = createEventDispatcher();

  let mode = 'login'; // login | register | forgot | reset | verify | sent
  let email = '';
  let username = '';
  let password = '';
  let confirm = '';
  let token = '';
  let message = '';
  let problem = '';
  let busy = false;

  $: isRegister = mode === 'register';
  $: showEmail = mode === 'register' || mode === 'forgot';
  $: showUsername = mode === 'login' || mode === 'register';
  $: showPassword = mode === 'login' || mode === 'register' || mode === 'reset';
  $: showConfirm = mode === 'register' || mode === 'reset';

  const help = {
    login: 'Введи имя и пароль своего профиля.',
    register: 'Имя профиля — это имя, а не почта. Пароль можно восстановить по почте.',
    forgot: 'Напиши почту профиля: на неё придёт письмо со ссылкой для восстановления.',
    reset: 'Придумай новый пароль для профиля.',
  };

  function setMode(next) {
    mode = next;
    message = '';
    problem = '';
    if (next === 'reset') {
      const params = new URLSearchParams(location.search);
      token = params.get('token') || '';
      if (!token) {
        problem = 'Ссылка для восстановления неполная. Запроси письмо ещё раз.';
        mode = 'forgot';
      }
    }
  }

  async function submit() {
    if (busy) return;
    problem = '';
    message = '';
    if (isRegister && password !== confirm) {
      problem = 'Пароли не совпадают.';
      return;
    }
    busy = true;
    try {
      if (mode === 'login') {
        await api.login(username.trim(), password);
        await afterLogin();
        return;
      }
      if (mode === 'register') {
        const answer = await api.register(username.trim(), password, email.trim());
        if (answer && answer.pending_verification) {
          setMode('sent');
          message = 'Проверь почту: в письме ссылка, которая подтверждает адрес.';
          return;
        }
        await afterLogin();
        return;
      }
      if (mode === 'forgot') {
        await api.forgot(email.trim());
        setMode('sent');
        message = 'Письмо со ссылкой отправлено на ' + email.trim() + '.';
        return;
      }
      if (mode === 'reset') {
        await api.resetPassword(token, password);
        setMode('login');
        message = 'Пароль изменён. Теперь войди с новым паролем.';
        return;
      }
    } catch (err) {
      problem = err && err.message ? err.message : 'Не удалось выполнить вход.';
    } finally {
      busy = false;
    }
  }

  async function afterLogin() {
    // Профиль читает оболочка: она же ставит сессию и грузит состояние.
    // Если сделать это здесь, а потом ещё раз там, лента mounts дважды и
    // запрос уходит дважды.
    await api.me();
    dispatch('done');
  }

  async function resend() {
    problem = '';
    message = '';
    try {
      await api.resendVerification(email.trim());
      message = 'Письмо отправлено ещё раз.';
    } catch (err) {
      problem = err && err.message ? err.message : 'Не удалось отправить письмо.';
    }
  }
</script>

<main class="auth-screen" id="authScreen">
  <div class="auth-card">
    <Card>
    <div class="auth-brand">
      <Icon name="send" size={20} />
      <span>Колода</span>
    </div>
    <h1>Своя лента</h1>
    <p class="auth-lead">
      Выбирай каналы и читай их публикации в одном месте. У каждого профиля своя подборка.
    </p>

    <!-- Переключатель, а не две кнопки: так видно, где ты сейчас. -->
    <div class="auth-modes">
      <span id="authRegisterMode">
        <SegmentedControl
          options={[
            { value: 'login', label: 'Уже есть профиль' },
            { value: 'register', label: 'Создать профиль' },
          ]}
          value={mode === 'register' ? 'register' : 'login'}
          label="Действие с профилем"
          on:change={(event) => setMode(event.detail.value)}
        />
      </span>
    </div>

    <form on:submit|preventDefault={submit}>
      {#if showEmail}
        <TextField
          id="authEmail"
          type="email"
          label="Почта"
          bind:value={email}
          placeholder="you@example.com"
          autocomplete="email"
          size="lg"
        />
      {/if}

      {#if showUsername}
        <TextField
          id="authUsername"
          label="Имя профиля"
          bind:value={username}
          autocomplete="username"
          placeholder="имя"
          size="lg"
        />
      {/if}

      {#if showPassword}
        <TextField
          id="authPassword"
          type="password"
          label="Пароль"
          bind:value={password}
          autocomplete={isRegister ? 'new-password' : 'current-password'}
          size="lg"
        />
      {/if}

      {#if showConfirm}
        <TextField
          id="authConfirm"
          type="password"
          label="Повтори пароль"
          bind:value={confirm}
          autocomplete="new-password"
          size="lg"
        />
      {/if}

      <p class="auth-help">{help[mode] || ''}</p>

      <div id="authMessage" role="status">
      {#if problem}
        <Notice tone="danger" title="Не получилось">{problem}</Notice>
      {/if}
      {#if message}
        <Notice tone="success" title="Готово">{message}</Notice>
      {/if}
      </div>
      {#if bootError}
        <Notice tone="warning" title="Сервис">{bootError}</Notice>
      {/if}

      <span id="authSubmit" class="submit">
      <Button type="submit" full loading={busy} size="lg">
        {mode === 'register' ? 'Создать профиль' : mode === 'forgot' ? 'Отправить письмо' : mode === 'reset' ? 'Сохранить пароль' : 'Войти'}
      </Button>
      </span>

      <div class="auth-links">
        {#if mode === 'login'}
          <Button variant="text" size="sm" on:click={() => setMode('forgot')}>Забыл пароль?</Button>
        {:else}
          <Button variant="text" size="sm" on:click={() => setMode('login')}>Вернуться ко входу</Button>
        {/if}
        {#if mode === 'sent'}
          <Button variant="text" size="sm" on:click={resend}>Отправить письмо ещё раз</Button>
        {/if}
      </div>
    </form>

    <p class="auth-footnote">
      Для ленты не нужен аккаунт Telegram. Восстановить пароль можно по подтверждённой почте.
    </p>
    </Card>
  </div>
</main>

<style>
  .auth-screen {
    display: grid;
    place-items: center;
    min-height: 100vh;
    padding: var(--ui-space-roomy) var(--ui-space-comfortable);
  }

  .auth-card {
    width: 100%;
    max-width: 420px;
  }

  .auth-brand {
    display: flex;
    align-items: center;
    gap: var(--ui-space-snug);
    color: var(--ui-ink-soft);
    font-size: 13px;
    margin-bottom: var(--ui-space-comfortable);
  }

  h1 {
    font-size: 24px;
    line-height: 1.25;
    font-weight: 700;
    letter-spacing: -0.025em;
    margin: 0 0 var(--ui-space-snug);
  }

  .auth-lead {
    color: var(--ui-ink-soft);
    font-size: 14px;
    margin: 0 0 var(--ui-space-roomy);
    max-width: 42ch;
  }

  .auth-modes {
    display: flex;
    gap: var(--ui-space-base);
    margin-bottom: var(--ui-space-comfortable);
  }

  form {
    display: flex;
    flex-direction: column;
    gap: var(--ui-space-base);
  }

  .auth-help {
    color: var(--ui-ink-faint);
    font-size: 12.5px;
  }

  .auth-links {
    display: flex;
    gap: var(--ui-space-base);
    flex-wrap: wrap;
  }

  .auth-footnote {
    margin-top: var(--ui-space-roomy);
    padding-top: var(--ui-space-comfortable);
    border-top: 1px solid var(--ui-line);
    color: var(--ui-ink-faint);
    font-size: 12px;
  }
</style>

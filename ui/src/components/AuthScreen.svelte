<script>
  /**
   * The entry to the profile: the sign in, the registration, the confirmation of
   * the address by a letter and the recovery of the password. One mode is one
   * screen: a form, or a short message with one action. The links from the
   * letters open /verify?token=… and /reset?token=… straight into their mode.
   */
  import { createEventDispatcher, onDestroy, onMount } from 'svelte';
  import { Button, Card, Notice, SegmentedControl, TextField } from '@monos/ui';

  import Icon from './Icon.svelte';
  import { api } from '../lib/api.js';

  export let bootError = '';
  // Вошедший открыл ссылку из письма: после подтверждения или сброса выходим в ленту,
  // а не в форму входа, которая для него бессмысленна.
  export let signedIn = false;

  const dispatch = createEventDispatcher();

  // Как на сервере: письмо повторно не уходит чаще раза в минуту.
  const RESEND_SECONDS = 60;
  // Сервер отвечает одинаково, уйдёт письмо или нет (лимит, неизвестный адрес), поэтому текст не утверждает отправку.
  const NEUTRAL_NOTE = 'Если адрес можно использовать, письмо придёт. Перейди по ссылке из него.';

  const formModes = ['login', 'register', 'forgot', 'reset'];
  const resendModes = ['check', 'verify-failed', 'forgot-sent'];

  const titles = {
    login: 'Своя лента',
    register: 'Своя лента',
    forgot: 'Восстановление пароля',
    reset: 'Новый пароль',
    check: 'Проверь почту',
    verify: 'Подтверждение почты',
    verified: 'Почта подтверждена',
    'verify-failed': 'Ссылка не сработала',
    'forgot-sent': 'Проверь почту',
    'reset-done': 'Пароль изменён',
    'reset-failed': 'Ссылка не сработала',
  };

  const help = {
    login: 'Введи имя и пароль своего профиля.',
    register: 'Имя нужно, чтобы входить. Почта нужна, чтобы подтвердить профиль и восстановить пароль.',
    forgot: 'Напиши почту профиля: на неё придёт письмо со ссылкой для восстановления.',
    reset: 'Придумай новый пароль: не меньше 8 символов.',
  };

  const labels = {
    login: 'Войти',
    register: 'Создать профиль',
    forgot: 'Отправить письмо',
    reset: 'Сохранить пароль',
    verified: 'Войти',
    'reset-done': 'Войти',
    'reset-failed': 'Запросить новую ссылку',
  };

  let mode = 'login';
  let email = '';
  let sentTo = ''; // куда ушло письмо в этой вкладке; пусто, если адрес неизвестен
  let username = '';
  let password = '';
  let confirm = '';
  let token = '';
  let status = null; // { tone, title, text }
  let busy = false;
  let cooldown = 0;
  let timer = null;

  $: formMode = formModes.includes(mode);
  $: resendMode = resendModes.includes(mode);
  $: blocked = resendMode && cooldown > 0;
  $: resendLabel = cooldown > 0 ? `Отправить ещё раз через ${cooldown} с` : 'Отправить письмо ещё раз';
  $: label = resendMode ? resendLabel : labels[mode] || '';

  const isEmail = (value) => /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value.trim());
  const cleanAddress = () => history.replaceState(history.state, '', '/');

  function fail(text) {
    status = { tone: 'danger', title: 'Не получилось', text };
  }

  function show(next) {
    mode = next;
    status = null;
    // Токен в адресе нужен только его экрану; со всех остальных адрес возвращается к корню.
    if (next !== 'reset' && location.search) cleanAddress();
  }

  function passwordProblem() {
    const length = [...password].length;
    if (length < 8 || length > 128) return 'Пароль должен быть от 8 до 128 символов.';
    if (password !== confirm) return 'Пароли не совпадают.';
    return '';
  }

  function startCooldown() {
    clearInterval(timer);
    cooldown = RESEND_SECONDS;
    timer = setInterval(() => {
      cooldown -= 1;
      if (cooldown <= 0) clearInterval(timer);
    }, 1000);
  }

  onDestroy(() => clearInterval(timer));

  onMount(() => {
    token = new URLSearchParams(location.search).get('token') || '';
    if (location.pathname === '/verify') {
      cleanAddress();
      if (token) confirmEmail();
      else mode = 'verify-failed';
    } else if (location.pathname === '/reset') {
      cleanAddress();
      mode = token ? 'reset' : 'reset-failed';
    }
  });

  async function confirmEmail() {
    mode = 'verify';
    busy = true;
    try {
      await api.verifyEmail(token);
      token = '';
      show('verified');
    } catch (err) {
      show('verify-failed');
      // Истёкшая или уже использованная ссылка объясняется текстом экрана, остальное показываем ошибкой.
      if (err.status !== 400) fail(err.message);
    } finally {
      busy = false;
    }
  }

  async function submit() {
    if (busy) return;
    if (mode === 'verified' || mode === 'reset-done') return signedIn ? dispatch('done') : show('login');
    if (mode === 'reset-failed') return show('forgot');
    if (resendMode) return resend();
    status = null;
    if (mode === 'register' || mode === 'reset') {
      const problem = passwordProblem();
      if (problem) return fail(problem);
    }
    if ((mode === 'register' || mode === 'forgot') && !isEmail(email)) {
      return fail('Введи действующий адрес почты.');
    }
    busy = true;
    try {
      if (mode === 'login') {
        await api.login(username.trim(), password);
        await afterLogin();
      } else if (mode === 'register') {
        const answer = await api.register(username.trim(), password, email.trim());
        if (answer && answer.verification_required) {
          sentTo = email.trim();
          show('check');
          startCooldown();
        } else {
          await afterLogin();
        }
      } else if (mode === 'forgot') {
        await api.forgot(email.trim());
        sentTo = email.trim();
        show('forgot-sent');
        startCooldown();
      } else if (mode === 'reset') {
        await api.resetPassword(token, password);
        token = '';
        show('reset-done');
      }
    } catch (err) {
      if (mode === 'login' && err.status === 403) {
        // Пароль верный, а почта ещё не подтверждена: сервер так и говорит.
        sentTo = '';
        show('check');
        status = { tone: 'warning', title: 'Почта не подтверждена', text: err.message };
      } else if (mode === 'reset' && err.status === 400) {
        show('reset-failed');
      } else {
        fail(err.message || 'Не удалось выполнить вход.');
      }
    } finally {
      busy = false;
    }
  }

  async function resend() {
    if (busy || cooldown > 0) return;
    const address = (sentTo || email).trim();
    if (!isEmail(address)) return fail('Введи почту, на которую создан профиль.');
    busy = true;
    status = null;
    try {
      if (mode === 'forgot-sent') await api.forgot(address);
      else await api.resendVerification(address);
      status = { tone: 'info', title: 'Проверь почту', text: NEUTRAL_NOTE };
      startCooldown();
    } catch (err) {
      fail(err.message);
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
</script>

<main class="auth-screen" id="authScreen">
  <div class="auth-card">
    <Card>
    <div class="auth-brand">
      <Icon name="send" size={20} />
      <span>Колода</span>
    </div>
    <h1>{titles[mode]}</h1>

    {#if mode === 'login' || mode === 'register'}
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
            value={mode}
            label="Действие с профилем"
            on:change={(event) => show(event.detail.value)}
          />
        </span>
      </div>
    {/if}

    <form novalidate on:submit|preventDefault={submit}>
      {#if mode === 'register' || mode === 'forgot'}
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

      {#if mode === 'login' || mode === 'register'}
        <TextField
          id="authUsername"
          label="Имя профиля"
          bind:value={username}
          autocomplete="username"
          placeholder="имя"
          size="lg"
        />
      {/if}

      {#if mode === 'login' || mode === 'register' || mode === 'reset'}
        <TextField
          id="authPassword"
          type="password"
          label={mode === 'reset' ? 'Новый пароль' : 'Пароль'}
          bind:value={password}
          autocomplete={mode === 'login' ? 'current-password' : 'new-password'}
          size="lg"
        />
      {/if}

      {#if mode === 'register' || mode === 'reset'}
        <TextField
          id="authConfirm"
          type="password"
          label="Повтори пароль"
          bind:value={confirm}
          autocomplete="new-password"
          size="lg"
        />
      {/if}

      {#if formMode}
        <p class="auth-help">{help[mode]}</p>
      {:else if mode === 'check' && sentTo}
        <p class="auth-text">
          Адрес: <strong>{sentTo}</strong>. {NEUTRAL_NOTE} Ссылка подтверждает почту и действует 24 часа.
          Если письма не будет, проверь папку «Спам».
        </p>
      {:else if mode === 'check'}
        <p class="auth-text">
          Профиль ещё ждёт подтверждения почты. Введи почту профиля. {NEUTRAL_NOTE}
        </p>
        <TextField
          id="authEmail"
          type="email"
          label="Почта"
          bind:value={email}
          hint="Почта, на которую создан профиль"
          autocomplete="email"
          size="lg"
        />
      {:else if mode === 'verify'}
        <p class="auth-text">Проверяем ссылку…</p>
      {:else if mode === 'verified'}
        <p class="auth-text">Теперь можно войти в профиль с именем и паролем.</p>
      {:else if mode === 'verify-failed'}
        <p class="auth-text">
          Ссылка устарела, уже использована или неполная. Если почта уже подтверждена, просто войди.
          Иначе введи почту профиля. {NEUTRAL_NOTE}
        </p>
        <TextField
          id="authEmail"
          type="email"
          label="Почта"
          bind:value={email}
          hint="Почта, на которую создан профиль"
          autocomplete="email"
          size="lg"
        />
      {:else if mode === 'forgot-sent'}
        <p class="auth-text">
          {NEUTRAL_NOTE} Ссылка для нового пароля действует 30 минут.
        </p>
      {:else if mode === 'reset-done'}
        <p class="auth-text">Теперь войди с новым паролем.</p>
      {:else if mode === 'reset-failed'}
        <p class="auth-text">
          Ссылка устарела, уже использована или неполная. Запроси новую: письмо придёт на почту профиля.
        </p>
      {/if}

      <div id="authMessage" role="status">
        {#if status}
          <Notice tone={status.tone} title={status.title}>{status.text}</Notice>
        {/if}
      </div>
      {#if bootError}
        <Notice tone="warning" title="Сервис">{bootError}</Notice>
      {/if}

      {#if label}
        <span id="authSubmit" class="submit">
          <Button type="submit" full loading={busy} disabled={blocked} size="lg">{label}</Button>
        </span>
      {/if}

      <div class="auth-links">
        {#if mode === 'login'}
          <Button variant="text" size="sm" on:click={() => show('forgot')}>Забыл пароль?</Button>
        {:else if mode !== 'verify' && mode !== 'verified' && mode !== 'reset-done'}
          <Button variant="text" size="sm" on:click={() => show('login')}>Вернуться ко входу</Button>
        {/if}
      </div>
    </form>

    {#if mode === 'login' || mode === 'register'}
      <p class="auth-footnote">
        Для ленты не нужен аккаунт Telegram. Восстановить пароль можно по подтверждённой почте.
      </p>
    {/if}
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

  .auth-text {
    color: var(--ui-ink-soft);
    font-size: 14px;
    line-height: 1.5;
    margin: 0;
  }

  .auth-text strong {
    color: var(--ui-ink);
    font-weight: 700;
    overflow-wrap: anywhere;
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

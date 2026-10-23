<script>
  /**
   * One post. The text comes from the server already cleaned: the links and the
   * marks are allowed, nothing else.
   */
  import { createEventDispatcher } from 'svelte';
  import { Button } from '@monos/ui';

  import Icon from './Icon.svelte';
  import { api } from '../lib/api.js';
  import { when, nfmt, viewsText, postKey, postLink } from '../lib/format.js';
  import { markRead, markUnread, showUndo } from '../lib/stores.js';

  export let post;
  export let read = false;
  export let title = '';
  export let avatar = '';
  export let saved = false;

  const dispatch = createEventDispatcher();

  let media = post.media || [];
  let loading = false;
  let fresh = false; // the addresses were asked for once already
  let avatarFailed = false; // the photo of the channel died: the letter stands in

  $: media = post.media || [];

  const letter = (String(title || post.channel).trim()[0] || '?').toUpperCase();

  function channelLabel() {
    if (post.channel.startsWith('private:-100')) return 'Приватный канал';
    return title || post.channel;
  }

  function openChannel() {
    dispatch('openChannel', post.channel);
  }

  // The frames of an album are loaded when they come near the screen: a long
  // feed with ten frames in every post would pull tens of megabytes at once.
  function lazy(node) {
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (!entry.isIntersecting) continue;
          const src = node.dataset.src;
          if (src) {
            node.src = src;
            node.classList.add('got');
          }
          observer.disconnect();
        }
      },
      { rootMargin: '400px 0px' }
    );
    observer.observe(node);
    return { destroy: () => observer.disconnect() };
  }

  // A frame that did not open: Telegram changes the addresses of the files.
  // The service gives fresh ones, once per post.
  async function reload(event) {
    if (fresh || loading) return;
    fresh = true;
    loading = true;
    // Битый кадр убираем сразу: пока он в разметке, браузер рисует заглушку
    // сломанной картинки, и она мигает до прихода свежей ссылки.
    if (event && event.target) event.target.removeAttribute('src');
    try {
      const answer = await api.media(postKey(post));
      if (answer && answer.media) {
        media = answer.media;
        post = { ...post, media: answer.media };
      }
    } catch (err) {
      // The frame stays broken: the link below it opens the post in Telegram.
    } finally {
      loading = false;
    }
  }

  function openLightbox(index) {
    const items = media
      .map((m) => (m.k === 'v' ? { type: 'video', url: m.u } : { type: 'image', url: m.u }))
      .filter((m) => m.url);
    dispatch('lightbox', { items, index });
  }

  function share() {
    const url = postLink(post);
    if (navigator.clipboard) navigator.clipboard.writeText(url);
    else window.prompt('Ссылка на публикацию', url);
  }

  // --- жест по карточке, как в приложениях для знакомств ---
  //
  // Вправо — отметить прочитанным, влево — сохранить. Вертикаль остаётся
  // прокрутке ленты: жест признаётся горизонтальным только когда он явно
  // горизонтальный, иначе лента едет мимо.
  let swipeX = 0;
  let swiping = false;
  let startX = 0;
  let startY = 0;

  const SWIPE_START = 14; // с какого смещения жест считается горизонтальным
  const SWIPE_TRIGGER = 92; // с какого смещения действие срабатывает

  function onTouchStart(event) {
    if (event.touches.length !== 1) return;
    startX = event.touches[0].clientX;
    startY = event.touches[0].clientY;
    swipeX = 0;
    swiping = false;
  }

  function onTouchMove(event) {
    if (event.touches.length !== 1) return;
    const dx = event.touches[0].clientX - startX;
    const dy = event.touches[0].clientY - startY;
    if (!swiping) {
      if (Math.abs(dx) < SWIPE_START) return;
      if (Math.abs(dx) < Math.abs(dy) * 1.5) return;
      swiping = true;
    }
    swipeX = dx;
  }

  function onTouchEnd() {
    if (!swiping) {
      swipeX = 0;
      return;
    }
    swiping = false;
    const dx = swipeX;
    swipeX = 0;
    if (dx >= SWIPE_TRIGGER) swipeRead();
    else if (dx <= -SWIPE_TRIGGER) swipeSave();
  }

  function swipeRead() {
    if (read) return;
    markRead(postKey(post));
    showUndo('Публикация отмечена прочитанной.', () => markUnread(postKey(post)));
  }

  function swipeSave() {
    if (saved) return;
    dispatch('save', { post });
  }

  $: tilt = Math.max(-9, Math.min(9, swipeX / 26));

  function toggleSave() {
    dispatch('toggleSave', { post, saved });
  }

  function toggleRead() {
    if (read) markUnread(postKey(post));
    else markRead(postKey(post));
  }
</script>

<article
  class="post"
  class:read
  class:swiping
  data-post={postKey(post)}
  style="--swipe: {swipeX}px; --tilt: {tilt}deg"
  on:touchstart={onTouchStart}
  on:touchmove|passive={onTouchMove}
  on:touchend={onTouchEnd}
  on:touchcancel={onTouchEnd}
>
  <!-- Штамп с той стороны, которая остаётся на виду: карточка уезжает
       в другую, и надпись была бы за краем экрана. -->
  {#if swiping && swipeX > 8}
    <span class="stamp stamp-left">Прочитано</span>
  {:else if swiping && swipeX < -8}
    <span class="stamp stamp-right">Сохранить</span>
  {/if}
  <div class="meta">
    <!-- Непрочитанность стоит рядом с кнопкой, а не внутри неё: иначе
         скринридер читает «Не прочитано Pavel Zloi» вместо названия канала. -->
    {#if !read}
      <span class="dot" aria-hidden="true"></span>
      <span class="sr-only">Не прочитано</span>
    {/if}
    <button type="button" class="who" title="Показать только этот канал" on:click={openChannel}>
      <span class="chan-dot" aria-hidden="true">
        {#if avatar && !avatarFailed}
          <img src={avatar} alt="" loading="lazy" referrerpolicy="no-referrer"
               on:error={() => (avatarFailed = true)} />
        {:else}
          {letter}
        {/if}
      </span>
      {channelLabel()}
    </button>

    <span class="meta-end">
      {#if post.views}
        <span class="stat">
          <Icon name="eye" size={14} />
          {viewsText(post.views)}
        </span>
      {/if}
      <time datetime={new Date(post.ts * 1000).toISOString()}>{when(post.ts)}</time>
    </span>
  </div>

  {#if post.fwd}
    <p class="orig">
      <Icon name="send" size={14} />
      <span>
        Переслано от
        {#if post.fwd.url}
          <a href={post.fwd.url} target="_blank" rel="noopener">{post.fwd.name || 'Telegram'}</a>
        {:else}
          {post.fwd.name || 'Telegram'}
        {/if}
      </span>
    </p>
  {/if}

  {#if post.quote}
    <div class="quote">
      {#if post.quote.author}
        <span class="who">{post.quote.author}</span>
      {/if}
      {#if post.quote.text}
        <div class="line">{post.quote.text}</div>
      {/if}
    </div>
  {/if}

  {#if post.text}
    <div class="txt">{@html post.text}</div>
  {/if}

  {#if media.length}
    <div class="galw">
      <div class="gal" class:one={media.length === 1}>
        {#each media as item, index (item.u)}
          {#if item.k === 'v'}
            <!-- svelte-ignore a11y-media-has-caption -->
            <video
              controls
              preload="none"
              playsinline
              src={item.u}
              poster={item.p || ''}
              style="--ar: {item.ar || '1/1'}"
            ></video>
          {:else}
            <button type="button" class="gal-frame" on:click={() => openLightbox(index)}>
              <img
                alt="Кадр из публикации"
                loading="lazy"
                data-src={item.u}
                on:error={reload}
                use:lazy
              />
            </button>
          {/if}
        {/each}
      </div>
      {#if media.length > 1}
        <span class="gal-n">{media.length}</span>
      {/if}
    </div>
  {/if}

  {#if post.sticker}
    <img class="sticker" loading="lazy" src={post.sticker} alt="Стикер из публикации" />
  {/if}

  {#if post.doc}
    <a class="doc" href={post.doc.url} target="_blank" rel="noopener">
      <Icon name="file" size={20} />
      <span>
        <span class="nm">{post.doc.title || 'Файл'}</span>
        {#if post.doc.meta}<span class="mt">{post.doc.meta}</span>{/if}
      </span>
    </a>
  {/if}

  {#if post.preview}
    <a class="preview" href={post.preview.url} target="_blank" rel="noopener">
      {#if post.preview.image}
        <img loading="lazy" src={post.preview.image} alt="" />
      {/if}
      <span>
        {#if post.preview.site}<span class="pv-site">{post.preview.site}</span>{/if}
        {#if post.preview.title}<span class="pv-title">{post.preview.title}</span>{/if}
        {#if post.preview.desc}<span class="pv-desc">{post.preview.desc}</span>{/if}
      </span>
    </a>
  {/if}

  {#if post.reactions && post.reactions.length}
    <div class="rx">
      {#each post.reactions as reaction}
        <span>
          {#if reaction.emoji}<span>{reaction.emoji}</span>{/if}
          {nfmt(reaction.count)}
        </span>
      {/each}
    </div>
  {/if}

  <div class="post-actions">
    <Button variant="text" size="sm" on:click={toggleSave} aria-pressed={saved}>
      {saved ? 'Сохранено' : 'Сохранить'}
    </Button>
    <Button variant="text" size="sm" on:click={share}>Поделиться</Button>
    <Button variant="text" size="sm" on:click={toggleRead}>
      {read ? 'Отметить непрочитанным' : 'Отметить прочитанным'}
    </Button>
  </div>
</article>

<style>
  .post-actions {
    display: flex;
    flex-wrap: wrap;
    gap: var(--ui-space-snug);
    margin-top: var(--ui-space-base);
  }

  .sticker {
    width: auto !important;
    max-width: 160px !important;
    margin: var(--ui-space-tight) 0 0 !important;
    background: none !important;
    border: 0 !important;
  }
</style>

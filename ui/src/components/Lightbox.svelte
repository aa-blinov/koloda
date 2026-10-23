<script>
  /**
   * Просмотрщик кадра: одна картинка или видео на весь экран.
   * Оверлей, ловушку фокуса, блокировку прокрутки и Escape даёт модалка кита;
   * здесь только сам кадр, счётчик и листание.
   */
  import { createEventDispatcher } from 'svelte';
  import { IconButton, Modal } from '@monos/ui';

  import Icon from './Icon.svelte';

  export let items = null;

  const dispatch = createEventDispatcher();

  let index = 0;
  let open = false;
  let swipe = null;

  $: frames = (items && items.items) || [];
  $: total = frames.length;
  // Открыли другой пост — просмотрщик встаёт на тот кадр, с которого начали.
  $: if (items) index = Math.min(Math.max(items.index || 0, 0), Math.max(total - 1, 0));
  $: frame = frames[index];
  $: open = Boolean(items && total);

  function step(delta) {
    if (total < 2) return;
    index = (index + delta + total) % total;
  }

  function close() {
    dispatch('close');
  }

  function onKeydown(event) {
    if (!open) return;
    // По видео стрелки ведут сам плеер: листать и перематывать сразу нельзя.
    if (event.target && event.target.closest && event.target.closest('video')) return;
    if (event.key === 'ArrowLeft') {
      event.preventDefault();
      step(-1);
    } else if (event.key === 'ArrowRight') {
      event.preventDefault();
      step(1);
    }
  }

  // Свайп влево и вправо листает кадры. По видео не листаем: там водят ползунок.
  function onTouchStart(event) {
    const one = event.touches.length === 1;
    const onVideo = event.target && event.target.closest && event.target.closest('video');
    swipe = one && !onVideo ? [event.touches[0].clientX, event.touches[0].clientY] : null;
  }

  function onTouchEnd(event) {
    if (!swipe) return;
    const dx = event.changedTouches[0].clientX - swipe[0];
    const dy = event.changedTouches[0].clientY - swipe[1];
    swipe = null;
    if (total > 1 && Math.abs(dx) > 48 && Math.abs(dx) > Math.abs(dy) * 1.5) step(dx < 0 ? 1 : -1);
  }
</script>

<svelte:window on:keydown={onKeydown} />

<Modal
  bind:open
  title="Просмотр кадра"
  showClose={false}
  closeOnBackdrop={false}
  class="lb-panel"
  on:close={close}
>
  <div class="stage" on:touchstart|passive={onTouchStart} on:touchend|passive={onTouchEnd}>
    {#if frame}
      {#if frame.type === 'video'}
        <!-- Дорожки с субтитрами нет: ролик приходит из Telegram, а там их не бывает. -->
        <!-- svelte-ignore a11y-media-has-caption -->
        <video
          class="frame"
          controls
          preload="metadata"
          playsinline
          src={frame.url}
          aria-label="Видео из публикации"
        ></video>
      {:else}
        <img class="frame" src={frame.url} alt="Кадр из публикации" />
      {/if}
    {/if}

    <span class="chip prev">
      <IconButton
        label="Предыдущий кадр"
        variant="ghost"
        size="lg"
        disabled={total < 2}
        class="!text-ink hover:!bg-transparent"
        on:click={() => step(-1)}
      >
        <Icon name="back" />
      </IconButton>
    </span>

    <span class="chip next">
      <IconButton
        label="Следующий кадр"
        variant="ghost"
        size="lg"
        disabled={total < 2}
        class="!text-ink hover:!bg-transparent"
        on:click={() => step(1)}
      >
        <Icon name="forward" />
      </IconButton>
    </span>

    <span class="chip close">
      <IconButton
        label="Закрыть просмотр"
        variant="ghost"
        size="lg"
        class="!text-ink hover:!bg-transparent"
        on:click={close}
      >
        <Icon name="close" />
      </IconButton>
    </span>

    <span class="count" id="lbN" aria-live="polite">{total > 1 ? `${index + 1} из ${total}` : ''}</span>
  </div>
</Modal>

<style>
  /* Панель модалки кита — карточка с рамкой и полями, а просмотрщику нужна вся
     площадь экрана. Стили кита перебиваем: его разметка у нас внутри. */
  :global(.lb-panel) {
    width: 100vw !important;
    height: 100vh !important;
    max-width: none !important;
    max-height: none !important;
    padding: 0 !important;
    border: 0 !important;
    border-radius: 0 !important;
    overflow: visible !important;
    box-shadow: none !important;
  }

  /* Заголовок модалки нужен только как имя диалога: зритель его не видит. */
  :global(.lb-panel h2) {
    position: absolute;
    width: 1px;
    height: 1px;
    margin: -1px;
    padding: 0;
    overflow: hidden;
    clip-path: inset(50%);
    white-space: nowrap;
  }

  .stage {
    position: absolute;
    inset: 0;
    display: grid;
    place-items: center;
  }

  .frame {
    max-width: 94vw;
    max-height: 86vh;
    object-fit: contain;
    border-radius: var(--ui-radius);
    display: block;
  }

  /* Кнопки и счётчик лежат прямо на кадре: подложка тёмная и полупрозрачная,
     иначе светлая картинка проглатывает иконку. */
  .chip {
    position: absolute;
    display: inline-flex;
    border-radius: var(--ui-radius);
    background: color-mix(in srgb, var(--ui-bg) 62%, transparent);
    transition: background var(--ui-duration-fast);
  }

  @media (hover: hover) {
    .chip:hover {
      background: color-mix(in srgb, var(--ui-bg) 82%, transparent);
    }
  }

  .prev {
    left: 16px;
    top: 50%;
    transform: translateY(-50%);
  }

  .next {
    right: 16px;
    top: 50%;
    transform: translateY(-50%);
  }

  .close {
    top: 12px;
    right: 12px;
  }

  .count {
    position: absolute;
    left: 50%;
    bottom: 16px;
    transform: translateX(-50%);
    padding: var(--ui-space-tight) var(--ui-space-comfortable);
    border-radius: var(--ui-radius);
    background: color-mix(in srgb, var(--ui-bg) 62%, transparent);
    color: var(--ui-ink);
    font-size: 12.5px;
    font-variant-numeric: tabular-nums;
    pointer-events: none;
  }
</style>

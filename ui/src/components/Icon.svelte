<script>
  /**
   * The icon set of the interface. One component, a name and a path: the icons
   * are drawn the same way as the rest of the kit — a stroke of 1.7, no fill,
   * 16 pixels by default.
   */
  export let name = '';
  export let size = 16;

  const paths = {
    grid: '<rect x="3" y="3" width="7" height="7" rx="1.6"/><rect x="14" y="3" width="7" height="7" rx="1.6"/><rect x="3" y="14" width="7" height="7" rx="1.6"/><rect x="14" y="14" width="7" height="7" rx="1.6"/>',
    search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
    eye: '<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/>',
    check: '<path d="m4.5 12.5 5 5 10-11"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    alert: '<path d="M12 8v5"/><circle cx="12" cy="16.5" r="1.1" fill="currentColor" stroke="none"/><circle cx="12" cy="12" r="9"/>',
    file: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8Z"/><path d="M14 3v5h5"/>',
    audio: '<path d="M9 18V6l10-2v12"/><circle cx="6.5" cy="18" r="2.5"/><circle cx="16.5" cy="16" r="2.5"/>',
    list: '<path d="M4 6h16M4 12h16M4 18h16"/>',
    // Лента — строки постов, каталог — квадраты каналов: знаки не путаются.
    feed: '<path d="M4 6h16M4 10h16M4 14h11M4 18h14"/>',
    folder: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"/>',
    user: '<circle cx="12" cy="8" r="3.6"/><path d="M4.6 20a7.6 7.6 0 0 1 14.8 0"/>',
    video: '<rect x="2.5" y="5" width="14" height="14" rx="2"/><path d="m16.5 10.5 5-3v9l-5-3z"/>',
    link: '<path d="M10.5 13.5a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1.6 1.6"/><path d="M13.5 10.5a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1.6-1.6"/>',
    refresh: '<path d="M20 12a8 8 0 1 1-2.34-5.66"/><path d="M20 4v5h-5"/>',
    // Полный экран: четыре угла наружу и обратно внутрь.
    expand: '<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/>',
    collapse: '<path d="M9 4v5H4M15 4v5h5M9 20v-5H4M15 20v-5h5"/>',
    send: '<path d="m21 3-9.5 9.5M21 3l-6.5 18-3.5-7-7-3.5Z"/>',
    chevron: '<path d="m6 9 6 6 6-6"/>',
    bookmark: '<path d="M6.5 3.5h11v17L12 16.6l-5.5 4z"/>',
    share: '<circle cx="18" cy="5.5" r="2.5"/><circle cx="6" cy="12" r="2.5"/><circle cx="18" cy="18.5" r="2.5"/><path d="m8.2 10.8 7.6-4.1M8.2 13.2l7.6 4.1"/>',
    close: '<path d="m6 6 12 12M18 6 6 18"/>',
    back: '<path d="m14 6-6 6 6 6"/>',
    forward: '<path d="m10 6 6 6-6 6"/>',
    trash: '<path d="M4 7h16M9 7V5h6v2M6 7l1 13h10l1-13"/>',
    download: '<path d="M12 4v10m0 0 4-4m-4 4-4-4M5 19h14"/>',
    copy: '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M6 15H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v1"/>',
    external: '<path d="M14 4h6v6"/><path d="M20 4 11 13"/><path d="M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
    settings: '<circle cx="12" cy="12" r="3"/><path d="M12 3v2m0 14v2M3 12h2m14 0h2M5.6 5.6 7 7m10 10 1.4 1.4M18.4 5.6 17 7M7 17l-1.4 1.4"/>',
    filter: '<path d="M3 5h18l-7 8v6l-4 2v-8L3 5Z"/>',
    unread: '<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="3" fill="currentColor" stroke="none"/>',
    qr: '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><path d="M14 14h3v3h-3zM20 14v3M14 20h7"/>',
  };
</script>

<svg
  class="i"
  width={size}
  height={size}
  viewBox="0 0 24 24"
  fill="none"
  stroke="currentColor"
  stroke-width="1.7"
  stroke-linecap="round"
  stroke-linejoin="round"
  aria-hidden="true"
  focusable="false"
>
  {@html paths[name] || ''}
</svg>

<style>
  svg {
    flex: 0 0 auto;
  }
</style>

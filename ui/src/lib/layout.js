import { writable } from 'svelte/store';

/**
 * Телефон или широкий экран.
 *
 * Ширины мало: телефон в лежачем положении шире 900 px, и там указатель
 * важен больше ширины. Тот же запрос, что и в стилях, — иначе раскладка и
 * поведение разойдутся.
 */
const QUERY = '(max-width: 900px), (pointer: coarse)';

export const phone = writable(false);

if (typeof window !== 'undefined' && window.matchMedia) {
  const media = window.matchMedia(QUERY);
  phone.set(media.matches);
  const onChange = (event) => phone.set(event.matches);
  if (media.addEventListener) media.addEventListener('change', onChange);
  else media.addListener(onChange);
}

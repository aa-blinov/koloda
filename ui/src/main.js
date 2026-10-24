import App from './App.svelte';
// Сначала стили кита: токены, палитры и база. Стили приложения идут после
// них и опираются на те же токены.
import '@monos/ui/styles.css';
import './app.css';

// Профиль устройства кита: высоты полос и касания берутся от него. Касание —
// телефон или планшет, и тогда различаем iOS и Android по устройству.
const touch = window.matchMedia('(pointer: coarse)').matches;
const ios = /iPhone|iPad|iPod/.test(navigator.userAgent);
document.documentElement.dataset.profile = touch ? (ios ? 'iphone' : 'android') : 'desktop';

const app = new App({ target: document.getElementById('app') });

export default app;

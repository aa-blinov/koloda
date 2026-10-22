import App from './App.svelte';
// Сначала стили кита: токены, палитры и база. Стили приложения идут после
// них и опираются на те же токены.
import '@monos/ui/styles.css';
import './app.css';

const app = new App({ target: document.getElementById('app') });

export default app;

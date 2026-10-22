/**
 * The wording of the state of the history of a channel. The reader has to
 * understand from one line whether the channel waits in the queue or its
 * history is already here.
 */

export function historyDetail(item, cap) {
  if (!item) return '';
  if (item.state === 'loading') return 'загружаем: ' + item.fetched + ' из ' + cap;
  if (item.state === 'waiting') return 'в очереди';
  if (item.state === 'paused') return 't.me не отвечает, повторим позже';
  if (item.state === 'done') return 'вся история загружена';
  return 'загружено ' + item.fetched + ', дальше только в Telegram';
}

export function channelHistoryText(item, cap, pending) {
  if (!item) return '';
  if (item.state === 'loading') return 'Загружаем старые публикации канала: ' + item.fetched + ' из ' + cap;
  const detail = historyDetail(item, cap);
  return pending ? 'Старые публикации канала: ' + detail : detail;
}

export function historyPercent(item, cap) {
  if (!item) return 0;
  if (item.state === 'done' || item.state === 'capped') return 100;
  if (!cap) return 0;
  return Math.min(100, Math.round((100 * item.fetched) / cap));
}

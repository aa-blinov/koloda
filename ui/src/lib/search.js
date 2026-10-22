/**
 * The value of the search field, taken from the kit's event.
 *
 * The kit sends the native input event, and in a native event the value lives
 * in target, not in the event itself. Reading event.detail.value gave
 * undefined, the search silently found nothing, and the field looked alive:
 * the text was in it, the requests went out without the query.
 */
export function searchValue(event) {
  const detail = event && event.detail !== undefined ? event.detail : event;
  if (detail && detail.target && typeof detail.target.value === 'string') return detail.target.value;
  return typeof detail === 'string' ? detail : '';
}

/**
 * Which channels the feed shows.
 *
 * Three cases: the whole subscription list, one channel (the reader clicked its
 * name in a post) or one collection (the reader picked it in the list of the
 * channels). A collection shows only the channels that are still subscribed:
 * the collection may outlive the subscription.
 */
export function feedChannels(tab, collection, subs) {
  const list = Array.isArray(subs) ? subs : [];
  if (collection) {
    const channels = Array.isArray(collection.channels) ? collection.channels : [];
    return channels.filter((channel) => list.includes(channel));
  }
  if (tab) return list.includes(tab) ? [tab] : [];
  return list;
}

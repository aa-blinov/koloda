import { describe, it, expect } from 'vitest';
import { comesBefore, insertByTime } from './order.js';

const post = (channel, msg_id, ts) => ({ channel, msg_id, ts });
const keys = (list) => list.map((p) => p.channel + '/' + p.msg_id);

describe('insertByTime', () => {
  it('puts a post back where its time is, not on top, when newer posts arrived meanwhile', () => {
    // лента обновилась: сверху два новых поста; старый пост возвращаем на его место
    const list = [post('b', 2, 300), post('b', 1, 200), post('a', 9, 100)];
    const returned = post('c', 5, 250);
    expect(keys(insertByTime(list, returned))).toEqual(['b/2', 'c/5', 'b/1', 'a/9']);
  });

  it('keeps the top place when the post was the newest', () => {
    const list = [post('b', 1, 200), post('a', 9, 100)];
    expect(keys(insertByTime(list, post('c', 5, 300)))).toEqual(['c/5', 'b/1', 'a/9']);
  });

  it('goes to the end when it is the oldest', () => {
    const list = [post('b', 2, 300), post('b', 1, 200)];
    expect(keys(insertByTime(list, post('a', 1, 50)))).toEqual(['b/2', 'b/1', 'a/1']);
  });

  it('breaks a tie of time the way the server does: channel, then the newer message', () => {
    const list = [post('b', 7, 200)];
    expect(keys(insertByTime(list, post('a', 3, 200)))).toEqual(['a/3', 'b/7']);
    expect(keys(insertByTime([post('a', 9, 200)], post('a', 3, 200)))).toEqual(['a/9', 'a/3']);
  });

  it('does not duplicate a post that is already in the list', () => {
    const list = [post('b', 2, 300), post('a', 1, 100)];
    expect(keys(insertByTime(list, post('a', 1, 100)))).toEqual(['b/2', 'a/1']);
  });

  it('works on an empty list', () => {
    expect(keys(insertByTime([], post('a', 1, 100)))).toEqual(['a/1']);
  });
});

describe('comesBefore', () => {
  it('orders like the server: newer first', () => {
    expect(comesBefore(post('a', 1, 200), post('a', 1, 100))).toBe(true);
    expect(comesBefore(post('a', 1, 100), post('a', 1, 200))).toBe(false);
  });
});

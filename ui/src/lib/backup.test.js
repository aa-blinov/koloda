import { describe, it, expect } from 'vitest';

import { parseBackup, parseChannelList, buildChannelList } from './backup.js';

describe('parseBackup', () => {
  it('reads channels, read marks and collections', () => {
    const text = [
      '{"type":"channel","channel":"durov"}',
      '{"type":"collection","name":"Чтение","channels":["durov","hardcoded"]}',
      '{"type":"read","post":"durov/123"}',
      '',
    ].join('\n');
    expect(parseBackup(text)).toEqual({
      subs: ['durov'],
      reads: ['durov/123'],
      collections: [{ name: 'Чтение', channels: ['durov', 'hardcoded'] }],
    });
  });

  it('skips blank lines and unknown record types', () => {
    const text = [
      '',
      '{"type":"channel","channel":"a"}',
      '{"type":"folder","note":"из будущей версии"}',
      '{"type":"channel","channel":42}',
      '{"type":"collection","name":"Без состава","channels":"не список"}',
    ].join('\n');
    const parsed = parseBackup(text);
    expect(parsed.subs).toEqual(['a']);
    expect(parsed.reads).toEqual([]);
    expect(parsed.collections).toEqual([]);
  });

  it('names the damaged line and refuses the whole file', () => {
    const text = '{"type":"channel","channel":"a"}\n{"type":"read"';
    expect(() => parseBackup(text)).toThrow('Строка 2');
  });

  it('takes an empty file as an empty transfer', () => {
    expect(parseBackup('')).toEqual({ subs: [], reads: [], collections: [] });
  });
});

describe('parseChannelList', () => {
  it('takes @names and t.me links, skips bare words', () => {
    const text = [
      'https://t.me/durov',
      '@hardcoded, t.me/s/rybolos_channel',
      'https://t.me/durov/123',
      'просто слово',
      'durov',
    ].join('\n');
    expect(parseChannelList(text)).toEqual(['durov', 'hardcoded', 'rybolos_channel']);
  });

  it('drops names a channel cannot have', () => {
    expect(parseChannelList('@ab https://t.me/long_name_here_over_limit!!')).toEqual([]);
    expect(parseChannelList('@durov')).toEqual(['durov']);
  });

  it('takes an empty file as an empty list', () => {
    expect(parseChannelList('')).toEqual([]);
  });
});

describe('buildChannelList', () => {
  it('writes one link per line with a trailing newline', () => {
    expect(buildChannelList(['durov', 'hardcoded'])).toBe(
      'https://t.me/durov\nhttps://t.me/hardcoded\n'
    );
    expect(buildChannelList([])).toBe('\n');
  });
});

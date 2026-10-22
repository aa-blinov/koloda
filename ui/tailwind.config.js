import ui from '@monos/ui/tailwind-preset';

// The kit's own components are written with the utility names of this preset,
// so their directory has to be in the content: otherwise Tailwind does not see
// the classes inside them and they lose their colours.
export default {
  presets: [ui],
  content: [
    './index.html',
    './src/**/*.{svelte,js}',
    './node_modules/@monos/ui/src/**/*.svelte',
  ],
};

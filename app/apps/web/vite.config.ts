import tailwindcss from '@tailwindcss/postcss';
import vinext from 'vinext';
import { defineConfig } from 'vite';

// IRRADIUM runs on its bundled local Node server, without Workers bindings.
export default defineConfig({
    // Open result viewers load VTK chunks lazily, sometimes hours after the
    // page was opened. Keep their hashed assets available across rebuilds.
    build: { emptyOutDir: false },
    css: { postcss: { plugins: [tailwindcss()] } },
    plugins: [vinext()],
});

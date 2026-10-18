import {defineConfig} from 'vite';
import {existsSync, readFileSync} from 'node:fs';

const settings = new URL('./config.json', import.meta.url);
const config = existsSync(settings) ? JSON.parse(readFileSync(settings, 'utf8').replace(/^\uFEFF/, '')) : {};

export default defineConfig({
  server: {host: '127.0.0.1', port: config.site_port || 8765, strictPort: true},
});

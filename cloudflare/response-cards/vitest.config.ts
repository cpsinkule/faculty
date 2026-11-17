import {cloudflareTest, readD1Migrations} from '@cloudflare/vitest-plugin';
import {defineConfig} from 'vitest/config';
export default defineConfig(async()=>({
  plugins:[cloudflareTest({
    wrangler:{configPath:'./wrangler.jsonc'},
    miniflare:{bindings:{
      FACULTY_API_SECRET:'test-only-faculty-secret-0000000000000000',
      TURNSTILE_SECRET:'test-only-turnstile-secret',
      TURNSTILE_SITE_KEY:'test-site-key',
      TEST_MIGRATIONS:await readD1Migrations('./migrations'),
    }},
  })],
  test:{fileParallelism:false},
}));

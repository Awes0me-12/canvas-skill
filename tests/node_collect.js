// Test harness: run tools/fetch.js under Node against a Canvas-like server.
//   node node_collect.js <origin> <settings.json> [cookie]
const fs = require('fs');
const path = require('path');

const [origin, settingsFile, cookie] = process.argv.slice(2);
globalThis.window = globalThis;
globalThis.location = { origin };
const realFetch = globalThis.fetch;
globalThis.fetch = (url, opts = {}) => realFetch(url.startsWith('http') ? url : origin + url, {
  ...opts, headers: { ...(opts.headers || {}), ...(cookie ? { Cookie: cookie } : {}) },
});
(0, eval)(fs.readFileSync(path.join(__dirname, '..', 'tools', 'fetch.js'), 'utf8'));
globalThis.__ccCollect(JSON.parse(fs.readFileSync(settingsFile, 'utf8'))).then(
  (result) => process.stdout.write(JSON.stringify(result)),
  (err) => { console.error(err); process.exit(1); });

// Run with `node tests/test_tasks.cjs`; no Zotero, files or network are modified.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const current = 'native-layout-v6-line-numbers';
const old = 'native-layout-v5-native-only';
const code = fs.readFileSync(path.join(__dirname, '../addon/modules/tasks.js'), 'utf8');

async function check({ocrVersion, translationVersion, edited = false, snapshot = null, refresh}) {
  const attachment = {getFilePathAsync: async () => '/saved/index.html'};
  const ocr = {attachment, nativeParserVersion: ocrVersion, editRevision: edited ? 'user-edit' : null};
  const translation = {attachment, nativeParserVersion: translationVersion};
  const PL = {
    alive: true, config: {nativeParserVersion: current},
    Editing: {stale: () => false},
    Store: {saved: async (_, kind) => kind === 'ocr' ? ocr : translation, get: () => ({}), set: () => {}},
  };
  vm.runInNewContext(code, {PaperLoom: PL, PathUtils: {join: path.join, parent: path.dirname},
    IOUtils: {exists: async () => !!snapshot, readJSON: async () => ({document: {parser: snapshot}})}});
  PL.Tasks.fingerprint = async () => ({hash: 'pdf-hash'});
  PL.Tasks.ocrCore = async () => {throw new Error('REFRESH_SOURCE');};
  const run = () => PL.Tasks.translateCore({source: {}, language: 'zh'});
  if (refresh) await assert.rejects(run, /REFRESH_SOURCE/);
  else assert.equal(await run(), translation);
}

(async () => {
  await check({ocrVersion: old, refresh: true});
  await check({ocrVersion: current, refresh: true});
  await check({ocrVersion: current, translationVersion: old, refresh: true});
  await check({ocrVersion: current, translationVersion: current, refresh: false});
  await check({ocrVersion: old, edited: true, refresh: false});
  await check({snapshot: {name: 'pymupdf-layout', auto_parser_version: old}, refresh: true});
  await check({snapshot: {name: 'legacy-ocr'}, refresh: false});
  console.log('PASS: 7 saved-translation cache and manual-edit cases.');
})().catch(error => {console.error(error); process.exitCode = 1;});

// Regression contracts for grouping, migration, permissions and saved exports.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const moduleCode = fs.readFileSync(path.join(__dirname, '../addon/modules/attachment-groups.js'), 'utf8');
const artifactsCode = fs.readFileSync(path.join(__dirname, '../addon/modules/artifacts.js'), 'utf8');

function setup() {
  const items = new Map(), writes = [], failures = new Set();
  let nextID = 100;
  class Item {
    constructor(type = 'attachment', options = {}) {
      this.type = type; this.id = options.id || null; this.libraryID = options.libraryID || 1;
      this.key = options.key || (this.id ? `KEY${this.id}` : null);
      this._parentID = options.parentID || null; this._savedParent = this._parentID;
      this.title = options.title || 'Research paper.pdf'; this.deleted = !!options.deleted;
      this.tags = options.tags || []; this.collections = options.collections || [];
      this.attachmentContentType = options.contentType || 'application/pdf';
      this.file = options.file || `/storage/${this.key}/original.pdf`;
      if (this.id) items.set(this.id, this);
    }
    get parentItemID() { return this._parentID || false; }
    set parentID(id) { this._parentID = id; }
    isAttachment() { return this.type === 'attachment'; }
    isRegularItem() { return this.type !== 'attachment'; }
    getField(name) { return name === 'title' ? this.title : ''; }
    setField(name, value) { if (name === 'title') this.title = value; }
    getCollections() { return [...this.collections]; }
    getTags() { return this.tags.map(tag => ({tag})); }
    addTag(tag) { this.tags.push(tag); }
    async save() {
      if (failures.has(this.id)) throw new Error('Simulated save failure');
      if (!this.id) {this.id = ++nextID; this.key = `KEY${this.id}`; items.set(this.id, this);}
      // Zotero's documented reparenting behavior transfers collection membership.
      if (this._parentID && !this._savedParent) {
        const parent = items.get(this._parentID);
        parent.collections = [...new Set([...parent.collections, ...this.collections])];
        this.collections = [];
      }
      this._savedParent = this._parentID;
      return this.id;
    }
    async saveTx() { return this.save(); }
  }
  class Search {
    conditions = [];
    addCondition(...args) { this.conditions.push(args); }
    async search() {
      const tags = this.conditions.filter(c => c[0] === 'tag').map(c => c[2]);
      return [...items.values()].filter(item => (!this.libraryID || item.libraryID === this.libraryID)
        && tags.some(tag => item.tags.includes(tag))).map(item => item.id);
    }
  }
  const readOnly = new Set(), records = new Map();
  const PL = {alive: true, tags: item => item.tags,
    Store: {directory: '/profile/paperloom', ensure: source => records.set(source.id, source.parentItemID),
      flush: async () => {}, refresh: () => {}, set: () => {}}};
  const Zotero = {Item, Search, Libraries: {get: id => ({editable: !readOnly.has(id), filesEditable: !readOnly.has(id)})},
    Items: {getAsync: async ids => Array.isArray(ids) ? ids.map(id => items.get(id)) : items.get(ids),
      getByLibraryAndKeyAsync: async (libraryID, key) => [...items.values()].find(item => item.libraryID === libraryID && item.key === key)},
    DB: {executeTransaction: async callback => {
      const before = new Map([...items].map(([id, item]) => [id, structuredClone({...item})]));
      try {return await callback();}
      catch (error) {
        for (const [id, item] of items) {
          if (!before.has(id)) items.delete(id);
          else Object.assign(item, before.get(id));
        }
        throw error;
      }
    }}, logError: () => {}};
  const context = {PaperLoom: PL, Zotero, PathUtils: {join: path.join},
    IOUtils: {writeJSON: async (file, data) => writes.push({file, data: JSON.parse(JSON.stringify(data))})}};
  vm.runInNewContext(moduleCode, context);
  const pdf = options => new Item('attachment', options);
  const artifact = (id, source, role = 'paperloom:ocr', options = {}) => pdf({id, libraryID: source.libraryID,
    contentType: 'text/html', tags: [role, `paperloom:pdf:${source.key}`], ...options});
  return {PL, Zotero, context, items, writes, failures, readOnly, records, pdf, artifact, Item};
}

async function run() {
  {
    const h = setup(), source = h.pdf({id: 1, collections: [10], file: '/storage/source/original.pdf'});
    const a = h.artifact(2, source, 'paperloom:ocr', {collections: [20]});
    const b = h.artifact(3, source, 'paperloom:reader');
    const c = h.artifact(4, source, 'paperloom:markdown');
    const unrelated = h.pdf({id: 5});
    const before = [source, a, b, c].map(item => ({id: item.id, key: item.key, file: item.file}));
    const result = await h.PL.AttachmentGroups.migrateExisting();
    assert.equal(result.parentsCreated, 1); assert.equal(result.resultsMoved, 3);
    const parent = h.items.get(source.parentItemID);
    assert.equal(parent.type, 'document'); assert.equal(parent.title, 'Research paper');
    assert.deepEqual(parent.collections.sort(), [10, 20]);
    for (const item of [a, b, c]) assert.equal(item.parentItemID, parent.id);
    assert.deepEqual([source, a, b, c].map(item => ({id: item.id, key: item.key, file: item.file})), before);
    assert.equal(unrelated.parentItemID, false);
    assert.equal(h.writes[0].data.items[0].parentID, null);
    assert.equal(h.records.get(source.id), parent.id);
    const again = await h.PL.AttachmentGroups.migrateExisting();
    assert.equal(again.parentsCreated, 0); assert.equal(again.resultsMoved, 0);
    assert.equal(h.writes.length, 2); // Idempotent rerun needs no new backup.
  }
  {
    const h = setup(), parent = new h.Item('journalArticle', {id: 9, title: 'Existing metadata', collections: [10]});
    const source = h.pdf({id: 1, parentID: parent.id});
    const orphan = h.artifact(2, source, 'paperloom:reader', {collections: [20]});
    const placed = h.artifact(3, source, 'paperloom:ocr', {parentID: 8});
    const result = await h.PL.AttachmentGroups.ensure(source);
    assert.equal(result.created, false); assert.equal(result.parent, parent);
    assert.equal(orphan.parentItemID, parent.id); assert.equal(placed.parentItemID, 8);
    assert.deepEqual(parent.collections.sort(), [10, 20]);
    assert.equal(parent.title, 'Existing metadata');
  }
  {
    const h = setup(), a = h.pdf({id: 1, key: 'SAMEKEY', libraryID: 1});
    const b = h.pdf({id: 2, key: 'SAMEKEY', libraryID: 2});
    const aa = h.artifact(3, a), bb = h.artifact(4, b);
    await h.PL.AttachmentGroups.ensure(a);
    assert.equal(aa.parentItemID, a.parentItemID); assert.equal(bb.parentItemID, false);
    assert.equal(b.parentItemID, false);
  }
  {
    const h = setup(), source = h.pdf({id: 1}); h.artifact(2, source);
    const [a, b] = await Promise.all([h.PL.AttachmentGroups.ensure(source), h.PL.AttachmentGroups.ensure(source)]);
    assert.equal(a.parent.id, b.parent.id);
    assert.equal([...h.items.values()].filter(item => item.isRegularItem()).length, 1);
  }
  {
    const h = setup(), source = h.pdf({id: 1}); const orphan = h.artifact(2, source);
    h.readOnly.add(1);
    const result = await h.PL.AttachmentGroups.migrateExisting();
    assert.equal(result.skipped, 1); assert.equal(h.writes.length, 0);
    assert.equal(source.parentItemID, false); assert.equal(orphan.parentItemID, false);
    await assert.rejects(() => h.PL.AttachmentGroups.ensure(source), /权限/);
  }
  {
    const h = setup(), source = h.pdf({id: 1, deleted: true}); h.artifact(2, source);
    const missing = h.pdf({id: 3, tags: ['paperloom:reader', 'paperloom:pdf:MISSING']});
    const ambiguous = h.pdf({id: 4, tags: ['paperloom:ocr', 'paperloom:pdf:A', 'paperloom:pdf:B']});
    const result = await h.PL.AttachmentGroups.migrateExisting();
    assert.equal(result.skipped, 2);
    assert.equal(missing.parentItemID, false); assert.equal(ambiguous.parentItemID, false);
    assert.equal(h.items.size, 4);
  }
  {
    const h = setup(), source = h.pdf({id: 1, collections: [10]}); const orphan = h.artifact(2, source);
    h.failures.add(orphan.id);
    await assert.rejects(() => h.PL.AttachmentGroups.ensure(source), /Simulated/);
    assert.equal(source.parentItemID, false); assert.equal(orphan.parentItemID, false);
    assert.deepEqual(source.collections, [10]); assert.equal(h.items.size, 2);
    h.failures.clear();
    assert.equal((await h.PL.AttachmentGroups.ensure(source)).created, true);
  }
  {
    const h = setup(), source = h.pdf({id: 1}); const imports = [];
    h.Zotero.Attachments = {
      importSnapshotFromFile: async options => {imports.push(['html', options]); return h.pdf({id: 20, parentID: options.parentItemID});},
      importFromFile: async options => {imports.push(['md', options]); return h.pdf({id: 21, parentID: options.parentItemID});},
      getStorageDirectory: () => ({path: '/storage/output'}),
    };
    h.Zotero.File = {copyDirectory: async () => {}};
    vm.runInNewContext(artifactsCode, h.context);
    await h.PL.Artifacts.save(source, 'translate', 'hash', {id: 'export', document_id: 'doc'}, '/input', 'zh');
    assert.deepEqual(imports.map(([type]) => type), ['html', 'md']);
    for (const [, options] of imports) {assert.equal(options.parentItemID, source.parentItemID); assert.equal(options.collections, undefined);}
    assert.ok(h.items.get(20).tags.includes('paperloom:reader'));
    assert.ok(h.items.get(21).tags.includes('paperloom:markdown'));
  }
  console.log('PASS: 8 attachment hierarchy, migration, transaction and export cases.');
}
run().catch(error => {console.error(error); process.exitCode = 1;});

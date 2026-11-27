// Alignment uses the existing paired paragraph, never a new translation call.
export function sentences(text, locale) {
  const value = String(text || "");
  return Array.from(new Intl.Segmenter(locale, {granularity: "sentence"}).segment(value))
    .map(part => ({text: part.segment.trim(), start: part.index, end: part.index + part.segment.trimEnd().length}))
    .filter(part => part.text);
}

export function alignedSourceRange(source, target, start, end, original = false) {
  if (!Number.isInteger(start) || !Number.isInteger(end) || start < 0 || end <= start || end > target.length) return null;
  if (original) return {start, end, text: target.slice(start, end).trim()};
  const left = sentences(source, "en"), right = sentences(target, "zh");
  // Translations can merge/split sentences. Do not guess a sentence in that case.
  if (!left.length || left.length !== right.length) return null;
  const indexes = right.map((part, index) => part.end > start && part.start < end ? index : -1).filter(index => index >= 0);
  if (!indexes.length || indexes.length > 3) return null;
  const first = left[indexes[0]], last = left[indexes.at(-1)];
  return {start: first.start, end: last.end, text: source.slice(first.start, last.end).trim()};
}

export function alignSelection(source, target, start, end, original = false) {
  const match = alignedSourceRange(source, target, start, end, original);
  return match?.text.replace(/\s+/g, " ") || null;
}

export function textModel(root) {
  const entries = [], doc = root.ownerDocument;
  const walker = doc.createTreeWalker(root, 4);
  let text = "", node;
  while ((node = walker.nextNode())) {
    if (node.parentElement?.closest('button,.pl-block-tools,.pl-role,mjx-assistive-mml,[hidden]')) continue;
    const heading = node.parentElement?.closest("h2");
    if (heading && /^(?:abstract|摘要)$/i.test(heading.textContent.trim())) continue;
    entries.push({node, start: text.length, end: text.length + node.data.length});
    text += node.data;
  }
  return {text, entries};
}

export function selectedOffsets(model, range) {
  const first = model.entries.find(entry => entry.node === range.startContainer);
  const last = model.entries.find(entry => entry.node === range.endContainer);
  if (first && last) return [first.start + range.startOffset, last.start + range.endOffset];
  const selected = range.toString().trim(), start = model.text.indexOf(selected);
  return selected && start >= 0 && model.text.indexOf(selected, start + 1) < 0 ? [start, start + selected.length] : null;
}

export function normalizedText(text) {
  let value = "", index = 0;
  const offsets = [];
  for (const char of String(text || "")) {
    const part = char.normalize("NFKC").toLowerCase().replace(/[^\p{L}\p{N}]/gu, "");
    for (let i = 0; i < part.length; i++) offsets.push({start:index, end:index+char.length});
    value += part; index += char.length;
  }
  return {value, offsets};
}

export function sourceSelectionOffsets(source, selected) {
  const model = normalizedText(source), wanted = normalizedText(selected).value;
  if (wanted.length < 4) return null;
  const start = model.value.indexOf(wanted);
  if (start < 0 || model.value.indexOf(wanted, start + 1) >= 0) return null;
  return [model.offsets[start].start, model.offsets[start+wanted.length-1].end];
}

export function alignSourceSelection(source, target, start, end) {
  if (!Number.isInteger(start) || !Number.isInteger(end) || start < 0 || end <= start || end > source.length) return null;
  const left = sentences(source, "en"), right = sentences(target, "zh");
  if (!left.length || left.length !== right.length) return null;
  const indexes = left.map((part,index) => part.end > start && part.start < end ? index : -1).filter(index => index >= 0);
  if (!indexes.length || indexes.length > 3) return null;
  const first = right[indexes[0]], last = right[indexes.at(-1)];
  return {start:first.start, end:last.end, text:target.slice(first.start,last.end)};
}

export function modelRange(model, start, end) {
  const first = model.entries.find(entry => entry.start <= start && entry.end > start);
  const last = model.entries.find(entry => entry.start < end && entry.end >= end);
  if (!first || !last) return null;
  const range = first.node.ownerDocument.createRange();
  range.setStart(first.node, start-first.start); range.setEnd(last.node, end-last.start);
  return range;
}

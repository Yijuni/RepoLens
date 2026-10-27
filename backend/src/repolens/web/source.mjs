// Lightweight source coloring; tokens are always rendered as text, never HTML.
const KEYWORDS = new Set("False None True and as assert async await break class continue def del elif else except finally for from global if import in is lambda nonlocal not or pass raise return try while with yield match case".split(" "));
export function tokenizeLine(line, state = { triple: null }) {
  const tokens = [];
  const push = (text, kind = "") => { if (text) tokens.push({ text, kind }); };
  let i = 0;
  while (i < line.length) {
    if (state.triple) {
      const end = line.indexOf(state.triple, i);
      if (end < 0) { push(line.slice(i), "string"); break; }
      push(line.slice(i, end + 3), "string"); i = end + 3; state.triple = null; continue;
    }
    if (line[i] === "#") { push(line.slice(i), "comment"); break; }
    if (line[i] === "'" || line[i] === '"') {
      const quote = line[i];
      if (line.slice(i, i + 3) === quote.repeat(3)) { state.triple = quote.repeat(3); push(state.triple, "string"); i += 3; continue; }
      let end = i + 1;
      while (end < line.length) { if (line[end] === "\\") { end += 2; continue; } if (line[end++] === quote) break; }
      push(line.slice(i, end), "string"); i = end; continue;
    }
    const word = /^[A-Za-z_][A-Za-z_0-9]*/.exec(line.slice(i));
    if (word) { push(word[0], KEYWORDS.has(word[0]) ? "keyword" : ""); i += word[0].length; continue; }
    const number = /^\b\d+(?:\.\d+)?/.exec(line.slice(i));
    if (number) { push(number[0], "number"); i += number[0].length; continue; }
    push(line[i++]);
  }
  return tokens;
}

export function renderSource(container, source) {
  const fragment = document.createDocumentFragment(), state = { triple: null };
  const lines = source.content.split(/\r\n|\n|\r/);
  if (lines.length > 1 && lines.at(-1) === "") lines.pop();
  const { start_line: start, end_line: end } = source.anchor;
  lines.forEach((line, i) => {
    const row = document.createElement("div"); row.className = "code-line"; row.dataset.line = i + 1;
    row.classList.toggle("anchor-line", i + 1 >= start && i + 1 <= end);
    const number = document.createElement("span"); number.className = "line-number"; number.textContent = i + 1;
    const text = document.createElement("span"); text.className = "code-text";
    const tokens = source.path.endsWith(".py") ? tokenizeLine(line, state) : [{ text: line }];
    for (const token of tokens) { const span = document.createElement("span"); if (token.kind) span.className = "tok-" + token.kind; span.textContent = token.text; text.append(span); }
    if (!line) text.textContent = " ";
    row.append(number, text); fragment.append(row);
  });
  container.replaceChildren(fragment);
  requestAnimationFrame(() => {
    const row = container.querySelector(`[data-line="${start}"]`);
    container.scrollTop = row ? Math.max(0, row.offsetTop - container.offsetTop - 65) : 0;
    container.scrollLeft = 0;
  });
}

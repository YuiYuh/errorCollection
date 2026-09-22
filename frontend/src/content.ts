export type Segment = { type: 'text' | 'math' | 'display' | 'code' | 'inline-code'; value: string };
const CJK = /[\u2E80-\u9FFF\uF900-\uFAFF\uFF00-\uFFEF\u3000-\u303F]/;
const N_COMMAND = /^\\n(?:u|abla|eq|ot|ewline|i|oindent|mid|leq|geq|rightarrow|leftarrow|warrow)(?![A-Za-z])/;

function group(text: string, start: number): { value: string; end: number } | null {
  if (text[start] !== '{') return null;
  let depth = 1;
  for (let i = start + 1; i < text.length; i++) {
    if (text[i] === '\\' && /[{}\\]/.test(text[i + 1] || '')) { i++; continue; }
    if (text[i] === '{') depth++;
    if (text[i] === '}' && --depth === 0) return { value: text.slice(start + 1, i), end: i + 1 };
  }
  return null;
}

function environment(text: string, start: number) {
  const tokens = /\\(begin|end)\{([^}]+)\}/g;
  tokens.lastIndex = start;
  const stack: string[] = [];
  let name = '', bodyStart = start;
  for (let token = tokens.exec(text); token; token = tokens.exec(text)) {
    if (token[1] === 'begin') {
      if (!stack.length) { name = token[2]; bodyStart = tokens.lastIndex; }
      stack.push(token[2]);
    } else {
      if (stack.pop() !== token[2]) return null;
      if (!stack.length) return { name, body: text.slice(bodyStart, token.index), end: tokens.lastIndex };
    }
  }
  return null;
}

function proseLayout(body: string): string {
  let output = '', depth = 0;
  for (let i = 0; i < body.length; i++) {
    if (body[i] === '\\' && /[{}]/.test(body[i + 1] || '')) { output += body.slice(i, i + 2); i++; continue; }
    if (body[i] === '{') depth++;
    if (body[i] === '}') depth--;
    if (!depth) {
      const line = body.slice(i).match(/^(?:\\newline\b|\\\\)/);
      if (line) { output += '\n'; i += line[0].length - 1; continue; }
      if (body[i] === '&') continue;
    }
    output += body[i];
  }
  return output;
}

function scanMath(text: string, start: number): number {
  let index = start, depth = 0;
  while (index < text.length) {
    const char = text[index];
    if (!depth && text.startsWith('\\n', index) && !N_COMMAND.test(text.slice(index))) break;
    if (!depth && (CJK.test(char) || char === '\n' || char === '$' || char === '`')) break;
    if (!depth && index > start && /^\\(?:text\{|mbox\{|newline\b|begin\{|\\)/.test(text.slice(index))) break;
    if (char === ' ' && !depth) {
      const token = text.slice(index + 1).split(/\s/)[0] || '';
      if (!/[\\^_=]/.test(token)) break;
    }
    if (!depth && !/[A-Za-z0-9{}^_=+\-*/()<>.,;:!|&[\]'%~\\ ]/.test(char)) break;
    if (char === '\\' && /[{}]/.test(text[index + 1] || '')) { index += 2; continue; }
    if (char === '{') depth++;
    if (char === '}') { if (!depth) break; depth--; }
    index++;
  }
  while (index > start && /[.,;:\s、]/.test(text[index - 1])) index--;
  return index;
}

// Only presentation wrappers are removed. Formula tokens, uncertainty and original
// source stay intact; the database and editable source are never rewritten here.
export function splitSegments(raw: string): Segment[] {
  const segments: Segment[] = [];
  let buffer = '', index = 0;
  const flush = () => { if (buffer) { segments.push({ type: 'text', value: buffer }); buffer = ''; } };
  const addMath = (value: string, type: 'math' | 'display') => {
    // A whole paragraph wrapped in \\text is prose, even inside $$...$$.
    if (/^\s*\\(?:text|mbox)\{/.test(value)) {
      const start = value.indexOf('{'), parsed = group(value, start);
      if (parsed && !value.slice(parsed.end).trim()) { buffer += unescapeText(parsed.value); return; }
    }
    const longProse = /\\(?:text|mbox)\{[^{}]*[\u3400-\u9FFF][^{}]{12,}\}/.test(value);
    if (longProse) {
      const env = value.startsWith('\\begin{') ? environment(value, 0) : null;
      if (env && /^(?:aligned|align\*?|gathered|gather\*?)$/.test(env.name)) {
        flush(); segments.push(...splitSegments(proseLayout(env.body))); return;
      }
      if (!env) { flush(); segments.push(...splitSegments(value)); return; }
    }
    flush(); segments.push({ type, value });
  };
  while (index < raw.length) {
    const rest = raw.slice(index);
    if (rest.startsWith('```')) {
      const end = raw.indexOf('```', index + 3);
      if (end >= 0) {
        flush();
        const value = raw.slice(index + 3, end).replace(/^[\w+-]*\r?\n/, '');
        segments.push({ type: 'code', value }); index = end + 3; continue;
      }
    }
    if (rest.startsWith('`')) {
      const end = raw.indexOf('`', index + 1);
      if (end >= 0) { flush(); segments.push({ type: 'inline-code', value: raw.slice(index + 1, end) }); index = end + 1; continue; }
    }
    const delimiter = ([['$$', '$$', 'display'], ['\\[', '\\]', 'display'], ['\\(', '\\)', 'math'], ['$', '$', 'math']] as const).find(([open]) => rest.startsWith(open));
    if (delimiter) {
      const end = raw.indexOf(delimiter[1], index + delimiter[0].length);
      if (end >= 0) { addMath(raw.slice(index + delimiter[0].length, end), delimiter[2]); index = end + delimiter[1].length; continue; }
    }
    if (rest.startsWith('\\begin{')) {
      const env = environment(raw, index);
      if (env) { addMath(raw.slice(index, env.end).replace(/\\newline\b/g, '\\\\'), 'display'); index = env.end; continue; }
    }
    const textCommand = rest.match(/^\\(?:text|mbox)\{/);
    if (textCommand) {
      const parsed = group(raw, index + textCommand[0].length - 1);
      if (parsed) { buffer += unescapeText(parsed.value); index = parsed.end; continue; }
    }
    const newline = rest.match(/^(?:\\newline\b|\\\\)/);
    if (newline) { buffer += '\n'; index += newline[0].length; continue; }
    if (rest.startsWith('\\n') && !N_COMMAND.test(rest)) { buffer += '\n'; index += 2; continue; }
    if (rest.startsWith('\\ ')) { buffer += ' '; index += 2; continue; }
    const command = /^\\[A-Za-z]/.test(rest);
    const script = /^[A-Za-z0-9)}][\^_]/.test(rest);
    if (command || script) {
      const end = scanMath(raw, index);
      if (end > index) { flush(); segments.push({ type: 'math', value: raw.slice(index, end) }); index = end; continue; }
    }
    buffer += raw[index++];
  }
  flush();
  return segments;
}

function unescapeText(value: string): string {
  return value.replace(/\\([{}%&#_$])/g, '$1').replace(/~/g, ' ');
}

export function questionPresentation(question: { subject?: string; question_text?: string; question_latex?: string }) {
  const text = question.question_text?.trim(), latex = question.question_latex?.trim();
  // question_text is the complete OCR question. question_latex only carries
  // mathematical expressions, so it must never replace the readable full stem.
  if (text) return { main: text, supplemental: latex && splitSegments(latex).some((s) => s.type === 'math' || s.type === 'display') ? latex : '' };
  return { main: latex || text || '未识别到题目', supplemental: '' };
}

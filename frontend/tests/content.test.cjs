const assert = require('node:assert/strict');
const test = require('node:test');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const { splitSegments, questionPresentation } = require('../.test-build/content.js');
const { RichText, Formula, QuestionContent, ResultField, AnalysisContent } = require('../.test-build/RichContent.js');
const { analysisPresentation, availabilityOnly } = require('../.test-build/analysisContent.js');
const { groupByDate, DateArchive } = require('../.test-build/DateArchive.js');
const render = (text) => renderToStaticMarkup(React.createElement(RichText, { text }));
const screenshot = String.raw`\begin{aligned}\text{（1）}&\ \text{手写答案可辨认为“ARP”，并写有HTTP相关内容；}\newline\text{（2）}&\ \text{手写答案可辨认为 }\langle 00\text{-}11\text{-}22\text{-}33\text{-}44\text{-}cc,4\rangle\text{；}\newline\text{（3）}&\ \text{手写答案为ARP请求帧，目的MAC地址为 }FF\text{-}FF\text{-}FF\text{-}FF\text{-}FF\text{-}FF\text{，并标注数量为2。}\end{aligned}`;

test('screenshot paragraph becomes wrapping prose, preserving all three answers', () => {
  const segments = splitSegments(screenshot);
  assert.equal(segments.filter((s) => s.type === 'display').length, 0);
  const html = render(screenshot);
  for (const text of ['（1）', '（2）', '（3）', '手写答案可辨认为“ARP”', '并标注数量为2。', 'FF-FF-FF-FF-FF-FF']) assert.ok(html.includes(text), text);
  assert.ok(!html.includes('formula-error'), html);
  assert.ok(!html.includes('\\begin{aligned}') && !html.includes('\\newline'));
  assert.ok(segments.some((s) => s.type === 'text' && s.value.includes('\n')));
});

test('entire text wrapped in TeX stays normal text even inside delimiters', () => {
  for (const value of [String.raw`\text{题目涉及DNS解析、ARP地址解析。}`, String.raw`$$\text{题目涉及DNS解析、ARP地址解析。}$$`]) {
    const html = render(value);
    assert.ok(html.includes('题目涉及DNS解析、ARP地址解析。'));
    assert.ok(!html.includes('katex'));
  }
});

test('complete 408 question takes precedence over generated summary', () => {
  const question = { subject: 'cn', question_text: '完整题干：（1）请回答DNS封装协议。（2）写出交换表。', question_latex: String.raw`\text{题目涉及DNS解析。}` };
  assert.deepEqual(questionPresentation(question), { main: question.question_text, supplemental: '' });
  const html = renderToStaticMarkup(React.createElement(QuestionContent, { question }));
  assert.ok(html.includes('完整题干') && html.includes('（2）'));
  assert.ok(!html.includes('题目涉及'));
});

test('408 actual formulas remain available beside full question', () => {
  const result = questionPresentation({ subject: 'cn', question_text: '计算传输时间', question_latex: String.raw`T=\frac{L}{R}` });
  assert.equal(result.main, '计算传输时间');
  assert.equal(result.supplemental, String.raw`T=\frac{L}{R}`);
});

test('real aligned derivation is rendered as one complete display formula', () => {
  const formula = String.raw`\begin{aligned}f(x)&=\frac{x^2}{2}\\f'(x)&=x\end{aligned}`;
  assert.deepEqual(splitSegments(formula), [{ type: 'display', value: formula }]);
  const html = render(formula);
  assert.ok(html.includes('katex-display') && !html.includes('formula-error'));
});

test('nested cases in aligned environment remain intact', () => {
  const html = render(String.raw`\begin{aligned}f(x)&=\begin{cases}x&x>0\\0&x\leq0\end{cases}\end{aligned}`);
  assert.ok(html.includes('katex-display') && !html.includes('formula-error'));
});

test('Chinese text inside a formula stays inside balanced command braces', () => {
  const html = render(String.raw`概率为 \(P(\text{成功})=\frac{1}{2}\)，继续判断。`);
  assert.ok(html.includes('katex') && html.includes('继续判断'));
  assert.ok(!html.includes('formula-error'));
});

test('literal newlines do not corrupt nu nabla neq or TeX row breaks', () => {
  const html = render(String.raw`第一行\n第二行 \(\nu\neq0,\nabla f=0\)`);
  assert.ok(html.includes('第一行\n第二行'));
  assert.ok(!html.includes('formula-error'));
  assert.ok(render(String.raw`\begin{aligned}x&=1\newline y&=2\end{aligned}`).includes('katex-display'));
});

test('code and inline identifiers are never treated as formulas', () => {
  const code = 'for (i=0; i<n; i++) { a_i = "\\n"; }';
  const html = render('```c\n' + code + '\n```\n`a_i`');
  assert.ok(html.includes('<pre><code>') && html.includes('a_i'));
  assert.ok(!html.includes('katex'));
  assert.equal(splitSegments('```c\n' + code + '\n```')[0].value, code + '\n');
});

test('broken formula is explicitly flagged without losing source', () => {
  const html = render(String.raw`答案 \(\frac{1}{\) 待核对`);
  assert.ok(html.includes('formula-error') && html.includes('<pre>\\frac{1}{'));
  assert.ok(html.includes('待核对'));
});

test('result fields default to readable preview with source editor collapsed', () => {
  const html = renderToStaticMarkup(React.createElement(ResultField, { label: '我的过程', value: screenshot, onChange() {} }));
  assert.ok(!html.includes('<textarea'));
  assert.ok(html.includes('修改') && html.includes('目的MAC地址'));
});

test('HTML and untrusted TeX cannot inject executable markup', () => {
  const html = render('<img src=x onerror=alert(1)> ' + String.raw`\(\href{javascript:alert(1)}{链接}\)`);
  assert.ok(!html.includes('<img') && !html.includes('href="javascript:'));
});

test('math question keeps a whole expression in math mode', () => {
  for (const value of ['x+1=2', String.raw`\frac{x^2}{2}+C`]) {
    const html = renderToStaticMarkup(React.createElement(Formula, { value }));
    assert.ok(html.includes('katex-display') && !html.includes('formula-error'));
  }
});

test('old math solution with literal paragraph breaks does not absorb them into formula', () => {
  const html = render(String.raw`积分为 \iint_D x^2\,d\sigma=\pi\int_0^1r^3\,dr,\n\n令u=r^2。`);
  assert.ok(!html.includes('formula-error'));
  assert.ok(html.includes('\n\n令'));
});

test('missing-material paragraphs become one visible notice while originals remain accessible', () => {
  const result = {
    user_solution_latex: '未提供用户解题过程，无法逐字识别用户步骤，也无法定位用户计算中的具体错误。',
    reference_solution_latex: '图片中未提供参考答案，无法进行逐字识别和直接对照。以下纠正结论由题干独立推导得到。',
    first_error_step: '无法定位：当前只有题目图片，未提供用户解题过程和参考答案。',
    correct_approach: '先确定慢启动门限，再逐轮计算拥塞窗口。',
    uncertain_parts: ['未提供用户解题过程'],
  };
  const presentation = analysisPresentation(result);
  assert.equal(presentation.sections.length, 1);
  assert.equal(presentation.notices.length, 4);
  const html = renderToStaticMarkup(React.createElement(AnalysisContent, { result }));
  const visible = html.replace(/<details>.*?<\/details>/gs, '');
  assert.equal((visible.match(/缺少可核对的解题过程/g) || []).length, 1);
  assert.ok(!visible.includes('未提供') && !visible.includes('我的过程'));
  assert.ok(visible.includes('先确定慢启动门限'));
  assert.ok(html.includes(result.user_solution_latex));
  assert.ok(!html.includes('<details open'));
});

test('exact duplicate analysis is shown once and specific uncertainty stays visible', () => {
  const result = { first_error_step: '忽略拥塞避免阶段。', error_analysis: '忽略拥塞避免阶段。', uncertain_parts: ['末行符号无法辨认'] };
  const html = renderToStaticMarkup(React.createElement(AnalysisContent, { result }));
  assert.equal((html.match(/忽略拥塞避免阶段/g) || []).length, 1);
  assert.ok(html.includes('识别依据与限制') && html.includes('末行符号无法辨认'));
});

test('missing-material detection never collapses actual calculations or specific reasoning', () => {
  for (const value of [
    '未提供参考答案。正确结果为 x=2。',
    '无法判断拥塞窗口，因为题目缺少超时条件。',
    '未提供参考答案但独立推导表明应先使用慢启动。',
    '未提供用户解题过程。应先检查定义域。',
    String.raw`未提供参考答案，\(x=\frac{1}{2}\)。`,
  ]) assert.equal(availabilityOnly(value), false, value);
});

test('substantive solution and reference are folded without hiding the main correction', () => {
  const result = { user_solution_latex: '先求导再代入。', reference_solution_latex: '利用对称性积分。', first_error_step: '积分限取反。' };
  const html = renderToStaticMarkup(React.createElement(AnalysisContent, { result }));
  assert.ok(html.includes('<details><summary>我的过程</summary>'));
  assert.ok(html.includes('<details><summary>参考答案</summary>'));
  assert.ok(html.replace(/<details>.*?<\/details>/gs, '').includes('积分限取反'));
});

test('date archive groups by stored calendar day across years and preserves within-day order', () => {
  const items = [
    { id: 1, created_at: '2025-09-19T23:30:00+00:00' },
    { id: 2, created_at: '2026-09-19T01:00:00+00:00' },
    { id: 3, created_at: '2026-09-19T02:00:00+00:00' },
    { id: 4 },
  ];
  const groups = groupByDate(items);
  assert.deepEqual(groups.map(([date]) => date), ['2026-09-19', '2025-09-19', '']);
  assert.deepEqual(groups[0][1].map(q => q.id), [2, 3]);
  assert.deepEqual(groupByDate(items, true).map(([date]) => date), ['2025-09-19', '2026-09-19', '']);
  const html = renderToStaticMarkup(React.createElement(DateArchive, { items, renderItem: q => React.createElement('button', {key:q.id}, `题目${q.id}`) }));
  assert.equal((html.match(/<details/g) || []).length, 3);
  assert.ok(!html.includes(' open'));
  assert.ok(html.includes('2026年9月19日') && html.includes('2 题'));
  assert.equal((html.match(/<button/g) || []).length, 4);
});

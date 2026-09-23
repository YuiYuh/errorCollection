import React, { useMemo, useState } from 'react';
import { renderToString } from 'katex';
import { questionPresentation, splitSegments } from './content';
import { analysisPresentation } from './analysisContent';

function MathRun({ value, display }: { value: string; display: boolean }) {
  const html = useMemo(() => {
    try { return renderToString(value, { throwOnError: true, displayMode: display, trust: false, strict: 'ignore' }); }
    catch { return null; }
  }, [value, display]);
  if (html === null) return <details className="formula-error"><summary>此处公式格式异常，展开核对原文</summary><pre>{value}</pre></details>;
  return <span className={display ? 'formula' : 'mathrun'} dangerouslySetInnerHTML={{ __html: html }} />;
}

export function RichText({ text }: { text?: string }) {
  const segments = useMemo(() => text ? splitSegments(String(text)) : [], [text]);
  if (!segments.length) return null;
  return <div className="richtext">{segments.map((segment, index) => {
    if (segment.type === 'text') return <span key={index}>{segment.value}</span>;
    if (segment.type === 'code') return <pre key={index}><code>{segment.value}</code></pre>;
    if (segment.type === 'inline-code') return <code className="inline-code" key={index}>{segment.value}</code>;
    return <MathRun key={index} value={segment.value} display={segment.type === 'display'} />;
  })}</div>;
}

// Old records may mix prose and TeX even in fields named *_latex.
export function Formula({ value }: { value?: string }) {
  if (!value) return null;
  const pureMath = /[=^_\\]/.test(value) && !/[\u2E80-\u9FFF\uF900-\uFAFF`$]/.test(value)
    && !/\\(?:text\{|mbox\{|\(|\[)/.test(value);
  return pureMath ? <MathRun value={value.replace(/\\newline\b/g, '\\\\')} display /> : <RichText text={value} />;
}

export function QuestionContent({ question }: { question: { subject?: string; question_text?: string; question_latex?: string } }) {
  const { main, supplemental } = questionPresentation(question);
  const mainIsFormula = main === question.question_latex?.trim();
  return <>{mainIsFormula ? <Formula value={main} /> : <RichText text={main} />}{supplemental && <details><summary>补充公式</summary><Formula value={supplemental} /></details>}</>;
}

export function ResultField({ label, value, onChange }: { label: string; value?: string; onChange: (value: string) => void }) {
  const [editing, setEditing] = useState(false);
  return <div className="result-field">
    <div className="cardhead"><h3>{label}</h3><button className="ghost" onClick={() => setEditing(!editing)}>{editing ? '收起编辑' : '修改'}</button></div>
    {editing && <label className="result-source">编辑内容（公式支持 LaTeX）<textarea value={value || ''} onChange={(event) => onChange(event.target.value)} /></label>}
    {value ? <RichText text={value} /> : <p className="muted">暂无内容</p>}
  </div>;
}

export function AnalysisContent({ result }: { result: Parameters<typeof analysisPresentation>[0] }) {
  const { sections, notices, uncertainty, notice } = analysisPresentation(result);
  return <div className="analysis-content">
    {Boolean(result.needs_review) && <p className="review-flag">低置信度结果，已自动加入错题本，请稍后快速核对。</p>}
    {notice && <p className="analysis-notice">{notice}</p>}
    {sections.map(item => item.secondary
      ? <details key={item.field}><summary>{item.label}</summary><RichText text={item.value} /></details>
      : <div key={item.field}><h3>{item.label}</h3><RichText text={item.value} /></div>)}
    {(notices.length > 0 || uncertainty.length > 0) && <details><summary>识别依据与限制</summary>{notices.map((item, index) => <p key={`notice-${index}`}><span className="muted">{item.label}：</span>{item.value}</p>)}{uncertainty.map((item, index) => <p key={`uncertainty-${index}`}><span className="muted">不确定：</span>{item}</p>)}</details>}
  </div>;
}

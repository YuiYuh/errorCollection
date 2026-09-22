import { DateArchive } from './DateArchive';
import React, { lazy, memo, Suspense, useCallback, useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
// 公式渲染依赖 KaTeX，只在需要显示题目或分析结果时加载，缩短首页首屏脚本。
const Formula = lazy(() => import('./RichContent').then((module) => ({ default: module.Formula })));
const RichText = lazy(() => import('./RichContent').then((module) => ({ default: module.RichText })));
const QuestionContent = lazy(() => import('./RichContent').then((module) => ({ default: module.QuestionContent })));
const AnalysisContent = lazy(() => import('./RichContent').then((module) => ({ default: module.AnalysisContent })));
import 'katex/dist/katex.min.css';
import './style.css';
import { CropEditor, ImageCompare, type EditedImage } from './ImageTools';

type ImageItem = { id: number; image_type: string; mime_type: string; file_size: number; has_edit?: boolean };
type AiMeta = { model: string; elapsed_ms: number; attempts: number; status_code: number };
type SyllabusPoint = { chapter: string; name: string; count: number };
type Syllabus = Record<string, SyllabusPoint[]>;
type Task = {
  id: number; status: string; result?: any; images: ImageItem[]; error?: string;
  ai_meta?: AiMeta; question_label?: string; recognition_hint?: string; created_at?: string; updated_at?: string;
  multi_batch_id?: string | null; crop_ready?: number;
  duplicates?: any[];
  draft?: any;
};

const api = (path: string, options?: RequestInit) =>
  fetch('/api' + path, options).then(async (response) => {
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(describeError(data, response.status));
    return data;
  });

// FastAPI 校验失败时 detail 是数组，直接丢给 Error 会显示成 [object Object]
function describeError(data: any, status: number): string {
  const detail = data?.detail;
  if (typeof detail === 'string' && detail.trim()) return detail;
  if (Array.isArray(detail)) {
    const parts = detail.map((item: any) => {
      const field = Array.isArray(item?.loc) ? item.loc.filter((piece: any) => piece !== 'body').join('.') : '';
      const message = item?.msg || JSON.stringify(item);
      return [field, message].filter(Boolean).join('：');
    });
    if (parts.length) return '提交内容有误 → ' + parts.join('；');
  }
  if (detail) return typeof detail === 'string' ? detail : JSON.stringify(detail);
  return `请求失败 ${status}`;
}
const jsonPost = (path: string, body: unknown) =>
  api(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });

const IMAGE_LABELS: Record<string, string> = {
  question: '题目', solution: '解题过程', reference: '参考答案', other: '其他',
};
const SUBJECT_LABELS: Record<string, string> = {
  math_calculus: '高等数学',
  math_linear: '线性代数',
  math_probability: '概率论与数理统计',
  math1: '数学一（未细分）',
  ds: '数据结构',
  coa: '计算机组成原理',
  os: '操作系统',
  cn: '计算机网络',
  unknown: '未确认',
};
const SUBJECT_COLORS: Record<string, string> = {
  math_calculus: '#27272a',
  math_linear: '#3f3f46',
  math_probability: '#52525b',
  math1: '#71717a',
  ds: '#3f3f46',
  coa: '#52525b',
  os: '#71717a',
  cn: '#a1a1aa',
  unknown: '#a1a1aa',
};
const PALETTE = ['#3b82f6', '#8b5cf6', '#14b8a6', '#f59e0b', '#ec4899', '#06b6d4', '#f97316', '#84cc16'];
const DIFFICULTIES = [
  { value: 2, label: '简单', color: '#14b8a6' },
  { value: 3, label: '中等', color: '#3b82f6' },
  { value: 4, label: '难', color: '#f59e0b' },
  { value: 5, label: '很难', color: '#f43f5e' },
];
// Preserve historical scores while grouping levels 1 and 2 under “简单”.
const difficultyLevel = (value: unknown) => {
  const level = Number(value);
  return Number.isInteger(level) && level >= 1 && level <= 5 ? Math.max(2, level) : 3;
};
const difficultyName = (value: unknown) => DIFFICULTIES.find((item) => item.value === difficultyLevel(value))!.label;
const RATINGS: [string, string][] = [['correct', '正确'], ['partial', '部分正确'], ['wrong', '错误']];
const NAV_ITEMS: [string, string][] = [
  ['home', '今天'], ['upload', '上传'], ['list', '错题本'],
  ['review', '复习'], ['stats', '洞察'], ['status', '状态'],
];
const REVIEW_BUTTONS: [string, string][] = [['forgot', '忘记'], ['hard', '困难'], ['remembered', '记得'], ['mastered', '熟练']];
const ERROR_TYPE_OPTIONS = ['概念理解', '方法选择', '计算与符号', '条件与边界', '审题与信息提取', '推理与论证', '记忆与规范', '代码与实现'];
const SORTS: [string, string][] = [
  ['created_desc', '最近添加'], ['created_asc', '最早添加'], ['due_asc', '最该复习'],
  ['mastery_asc', '最不熟'], ['difficulty_desc', '最难'],
];
const ACTIVE_STATUSES = [
  'uploaded', 'pending', 'pending_recognition', 'recognizing',
  'pending_analysis', 'analyzing', 'recognition_completed', 'analysis_completed',
];
const TITLES: Record<string, string> = {
  home: '今天', upload: '上传', task: '识别中', confirm: '确认', list: '错题本',
  detail: '错题详情', review: '复习', stats: '洞察', status: '状态', practice: '独立重做', trash: '回收站', tasks: '任务中心',
};
const TASK_STATUS: Record<string, string> = {
  uploaded: '已保存，等待识别',
  pending: '排队中',
  pending_recognition: '排队中',
  recognizing: '识别中',
  pending_analysis: '分析中',
  analyzing: '分析中',
  recognition_completed: '识别完成',
  analysis_completed: '分析完成',
  waiting_confirmation: '等待确认',
  confirmed: '已确认',
  failed: '失败',
  cancelled: '已取消',
};
const BUSY_STATUSES = ['uploaded', 'pending', 'pending_recognition', 'recognizing', 'pending_analysis', 'analyzing', 'recognition_completed', 'analysis_completed'];
const statusText = (value?: string) => TASK_STATUS[value || ''] || value || '-';
const elapsed = (from?: string) => {
  if (!from) return '';
  const seconds = Math.max(0, Math.round((Date.now() - new Date(from).getTime()) / 1000));
  return seconds < 60 ? `${seconds} 秒前` : `${Math.round(seconds / 60)} 分钟前`;
};
const duration = (from?: string, now = Date.now()) => {
  if (!from) return '';
  const seconds = Math.max(0, Math.round((now - new Date(from).getTime()) / 1000));
  return seconds < 60 ? `${seconds} 秒` : `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`;
};

const joinList = (list?: string[]) => (list || []).join('、');
const splitList = (text: string) => text.split(/[，,、;；\n]/).map((item) => item.trim()).filter(Boolean);
const joinBlocks = (blocks?: string[]) => (blocks || []).join('\n\n---\n\n');
const splitBlocks = (text: string) => text.split(/\n\s*---\s*\n/).map((item) => item.trim()).filter(Boolean);
const primaryErrorType = (types?: string[]) => ERROR_TYPE_OPTIONS.includes(types?.[0] || '') ? types![0] : '方法选择';
const EMPTY_FILTER = { q: '', subject: '', chapter: '', knowledge_point: '', error_type: '', date_from: '', date_to: '', sort: 'created_desc' };

function KnowledgeSelect({ value, catalog, onChange }: { value: any; catalog: Syllabus; onChange: (next: any) => void }) {
  const entries = catalog[value.subject] || [];
  const point = entries.some((entry) => entry.name === value.knowledge_points?.[0]) ? value.knowledge_points[0] : '待确认';
  return <label>考纲知识点
    <select value={point} onChange={(event) => {
      const selected = entries.find((entry) => entry.name === event.target.value);
      onChange({ ...value, knowledge_points: [event.target.value], chapter: selected?.chapter || '' });
    }}>
      <option value="待确认">请选择考纲知识点</option>
      {[...new Set(entries.map((entry) => entry.chapter))].map((chapter) => <optgroup key={chapter} label={chapter}>
        {entries.filter((entry) => entry.chapter === chapter).map((entry) => <option key={entry.name} value={entry.name}>{entry.name}</option>)}
      </optgroup>)}
    </select>
  </label>;
}

function TagEditor({ value, onChange }: { value: any; onChange: (next: any) => void }) {
  const [input, setInput] = useState('');
  const tags: string[] = value.tags || [];
  const add = () => {
    const next = input.trim().replace(/^#/, '').slice(0, 24);
    if (next && !tags.includes(next)) onChange({ ...value, tags: [...tags, next].slice(0, 8) });
    setInput('');
  };
  return <label>辅助标签（AI 自动生成，可手动添加或删除）
    <div className="tageditor">
      <div className="rowmeta">{tags.map((tag) => <span className="chip" key={tag}>#{tag}<button type="button" className="chipremove" onClick={() => onChange({ ...value, tags: tags.filter((item) => item !== tag) })}>×</button></span>)}</div>
      <div className="taginput"><input value={input} onChange={(event) => setInput(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ',') { event.preventDefault(); add(); } }} placeholder="输入标签后回车" /><button type="button" onClick={add}>添加</button></div>
    </div>
  </label>;
}
const subjectName = (value?: string) => SUBJECT_LABELS[value || ''] || value || '未分类';
const subjectColor = (value?: string) => SUBJECT_COLORS[value || ''] || '#8e8e93';
function compactTitle(question: any) {
  const [year, month, day] = String(question.created_at || '').slice(0, 10).split('-');
  const dailyNumber = Number(question.daily_number);
  if (month && day && dailyNumber > 0) return `${Number(month)}月${Number(day)}日 · 当天第${dailyNumber}题`;
  return question.question_label?.trim() || '未标注题目';
}
const confidenceClass = (value: number) => (value >= 0.8 ? 'high' : value >= 0.6 ? 'mid' : 'low');
const FIELD_CONFIDENCE_LABELS: [string, string][] = [
  ['question', '题目文字'], ['formula', '公式'], ['solution', '解题过程'],
  ['reference', '参考答案'], ['classification', '科目 / 知识点'],
];
const fieldConfidenceValue = (result: any, field: string) => {
  const value = result?.field_confidence?.[field];
  if (typeof value === 'number' && Number.isFinite(value)) return Math.max(0, Math.min(1, value));
  const recognition = Number(result?.recognition_confidence ?? result?.confidence ?? 0);
  const correction = Number(result?.correction_confidence ?? result?.confidence ?? 0);
  if (field === 'question') return result?.question_text || result?.question_latex ? recognition : 0;
  if (field === 'formula') return result?.question_latex || result?.user_solution_latex || result?.reference_solution_latex ? recognition : 0;
  if (field === 'solution') return result?.user_solution_latex || result?.first_error_step || result?.error_analysis ? correction : 0;
  if (field === 'reference') return result?.reference_solution_latex ? correction : 0;
  return result?.subject && !['unknown', 'math1'].includes(result.subject) && result?.knowledge_points?.length ? Math.min(recognition, correction) : 0;
};
const fieldNeedsReview = (result: any, field: string) => (result?.uncertain_parts || []).some((part: unknown) => {
  const text = String(part);
  const markers: Record<string, string[]> = {
    question: ['题', '识别', '图片', '看不清'], formula: ['公式', '算式'],
    solution: ['解题', '错误', '纠错'], reference: ['参考答案', '答案'],
    classification: ['科目', '知识点', '考纲'],
  };
  return (markers[field] || []).some((marker) => text.includes(marker));
});
const localKey = (date: Date) =>
  `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;

const ICONS: Record<string, string> = {
  home: 'M3 10.6 12 3l9 7.6V21a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z',
  upload: 'M12 16V4m0 0L7 9m5-5 5 5M4 20h16',
  list: 'M4 6h16M4 12h16M4 18h10',
  review: 'M20.5 12a8.5 8.5 0 1 1-2.5-6M20.5 4v4h-4',
  stats: 'M4 20V10m5 10V4m5 16v-7m5 7V9',
  status: 'M12 3v2m0 14v2M4.2 4.2l1.5 1.5M18.3 18.3l1.5 1.5M3 12h2m14 0h2M4.2 19.8l1.5-1.5M18.3 5.7l1.5-1.5',
};

const NavIcon = memo(function NavIcon({ name }: { name: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={ICONS[name] || ICONS.home} />
    </svg>
  );
});

function LocalImage({ file }: { file: File }) {
  const [src, setSrc] = useState('');
  useEffect(() => {
    const objectUrl = URL.createObjectURL(file);
    setSrc(objectUrl);
    return () => URL.revokeObjectURL(objectUrl);
  }, [file]);
  return <img src={src} alt={file.name} />;
}

function CodeBlocks({ blocks }: { blocks?: string[] }) {
  const [copied, setCopied] = useState<number | null>(null);
  if (!blocks?.length) return null;
  return (
    <div>
      <h3 style={{ marginTop: 18 }}>代码 / 结构图</h3>
      <p className="muted">按原文保留，未转成 LaTeX。</p>
      {blocks.map((block, index) => (
        <div key={index}>
          <div className="codehead">
            <span>第 {index + 1} 段</span>
            <button
              className="ghost"
              onClick={() => {
                navigator.clipboard?.writeText(block).catch(() => {});
                setCopied(index);
                window.setTimeout(() => setCopied(null), 1500);
              }}
            >{copied === index ? '已复制' : '复制'}</button>
          </div>
          <pre>{block}</pre>
        </div>
      ))}
    </div>
  );
}

function Empty({ emoji, text }: { emoji: string; text: string }) {
  return <div className="empty"><span className="emoji">{emoji}</span>{text}</div>;
}

function Donut({ items, center }: { items: { name: string; count: number; color: string }[]; center?: string }) {
  const size = 216;
  const thickness = 34;
  const radius = (size - thickness) / 2;
  const circumference = 2 * Math.PI * radius;
  const total = items.reduce((sum, item) => sum + item.count, 0);
  let offset = 0;
  return (
    <div className="donutwrap">
      <svg className="donut" width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
        <g transform={`rotate(-90 ${size / 2} ${size / 2})`}>
          <circle cx={size / 2} cy={size / 2} r={radius} fill="none" stroke="var(--surface-3)" strokeWidth={thickness} />
          {total > 0 && items.filter((item) => item.count > 0).map((item) => {
            const dash = (item.count / total) * circumference;
            const node = (
              <circle
                key={item.name}
                cx={size / 2}
                cy={size / 2}
                r={radius}
                fill="none"
                stroke={item.color}
                strokeWidth={thickness}
                strokeDasharray={`${dash} ${circumference - dash}`}
                strokeDashoffset={-offset}
                strokeLinecap="butt"
              />
            );
            offset += dash;
            return node;
          })}
        </g>
        <text x="50%" y="47%" textAnchor="middle" fill="var(--muted)" fontSize="13">共</text>
        <text x="50%" y="60%" textAnchor="middle" fill="var(--text)" fontSize="28" fontWeight="800">{total}</text>
      </svg>
      {center && <p className="muted">{center}</p>}
    </div>
  );
}

function Legend({ items, unit }: { items: { name: string; count: number; color: string }[]; unit: string }) {
  const total = items.reduce((sum, item) => sum + item.count, 0);
  if (!items.length) return <Empty emoji="📊" text="还没有数据" />;
  return (
    <div className="legend">
      {items.map((item) => {
        const percent = total ? (item.count / total) * 100 : 0;
        return (
          <div className="statrow" key={item.name}>
            <div className="top">
              <span className="pct">{percent.toFixed(percent >= 10 ? 1 : 2)}%</span>
              <span className="label">{item.name}</span>
              <span className="value">{item.count} {unit}</span>
            </div>
            <div className="track"><div className="fill" style={{ width: `${percent}%`, background: item.color }} /></div>
          </div>
        );
      })}
    </div>
  );
}

function withColors<T extends { name: string | number; count: number }>(items: T[] | undefined) {
  return (items || []).map((item, index) => ({ name: String(item.name), count: item.count, color: PALETTE[index % PALETTE.length] }));
}

function TrendBars({ counts }: { counts: number[] }) {
  const max = Math.max(1, ...counts);
  if (!counts.some((value) => value > 0)) return <Empty emoji="📈" text="最近 30 天没有新增错题" />;
  return (
    <div className="trend">
      {counts.map((count, index) => (
        count > 0
          ? <div className="tbar" key={index} style={{ height: `${Math.max(8, (count / max) * 100)}%` }} title={`${count} 题`} />
          : <div className="tbar is-empty" key={index} />
      ))}
    </div>
  );
}

function App() {
  const [tab, setTab] = useState('home');
  const [statsTab, setStatsTab] = useState('dist');
  const [syllabusSubject, setSyllabusSubject] = useState('math_calculus');
  const [files, setFiles] = useState<File[]>([]);
  const [imageEdits, setImageEdits] = useState<Map<File, EditedImage>>(new Map());
  const [cropFile, setCropFile] = useState<File | null>(null);
  const [uploadProgress, setUploadProgress] = useState<number | null>(null);
  const [centerTasks, setCenterTasks] = useState<any[]>([]);
  const [centerFilter, setCenterFilter] = useState(() => {
    const saved = window.localStorage.getItem('wrongbook-task-filter');
    return ['unresolved', '', 'active', 'waiting_confirmation', 'failed', 'confirmed', 'cancelled'].includes(saved || '') ? (saved || 'unresolved') : 'unresolved';
  });
  const [centerPage, setCenterPage] = useState(0);
  const [centerSelected, setCenterSelected] = useState<Set<number>>(new Set());
  const [centerTotal, setCenterTotal] = useState(0);
  const [centerLoading, setCenterLoading] = useState(false);
  const [centerError, setCenterError] = useState('');
  const [types, setTypes] = useState<string[]>([]);
  const [questionLabel, setQuestionLabel] = useState('');
  const [multiQuestion, setMultiQuestion] = useState(false);
  const [questionLabels, setQuestionLabels] = useState('');
  const [taskLabel, setTaskLabel] = useState('');
  const [recognitionHint, setRecognitionHint] = useState('');
  const [taskList, setTaskList] = useState<any[]>([]);
  const [archivedTasks, setArchivedTasks] = useState<any[]>([]);
  const [taskTotal, setTaskTotal] = useState(0);
  const [archiveTotal, setArchiveTotal] = useState(0);
  const [taskLimit, setTaskLimit] = useState(20);
  const [archiveLimit, setArchiveLimit] = useState(20);
  const [task, setTask] = useState<Task | null>(null);
  const [draft, setDraft] = useState<any>(null);
  const [questions, setQuestions] = useState<any[]>([]);
  const [questionTotal, setQuestionTotal] = useState(0);
  const [questionLimit, setQuestionLimit] = useState(100);
  const [selected, setSelected] = useState<any | null>(null);
  const [editing, setEditing] = useState<any | null>(null);
  const [status, setStatus] = useState<any>();
  const [overview, setOverview] = useState<any>();
  const [accuracy, setAccuracy] = useState<any>();
  const [distribution, setDistribution] = useState<any>();
  const [filters, setFilters] = useState<any>({ chapters: [], knowledge_points: [], error_types: [] });
  const [filter, setFilter] = useState(EMPTY_FILTER);
  const [reviewScope, setReviewScope] = useState('today');
  const [reviews, setReviews] = useState<any[]>([]);
  const [backups, setBackups] = useState<any[]>([]);
  const [selfTest, setSelfTest] = useState<{ running: boolean; data?: any }>({ running: false });
  const [feedback, setFeedback] = useState({ recognition: '', correction: '' });
  const [theme, setTheme] = useState<string>(() => window.localStorage.getItem('wrongbook-theme') || 'light');
  const [busy, setBusy] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [practiceQuestion, setPracticeQuestion] = useState<any | null>(null);
  const [practiceFiles, setPracticeFiles] = useState<File[]>([]);
  const [practiceTask, setPracticeTask] = useState<any | null>(null);
  const [practiceHistory, setPracticeHistory] = useState<any[]>([]);
  const [attemptGrade, setAttemptGrade] = useState({ outcome: 'uncertain', same_error: 'unknown' });
  const [weekOffset, setWeekOffset] = useState(0);
  const [weekly, setWeekly] = useState<any | null>(null);
  const [trash, setTrash] = useState<any[]>([]);
  const [showAllRepeatKnowledge, setShowAllRepeatKnowledge] = useState(false);

  const renderUploadTask = (item: any) => (
                  <div className="rowitem" key={item.id} style={{ alignItems: 'flex-start' }}>
                    <span className={`ring ${BUSY_STATUSES.includes(item.status) ? 'busy' : ''}`} style={{ '--ring-color': item.status === 'failed' ? 'var(--danger)' : item.status === 'waiting_confirmation' ? 'var(--success)' : 'var(--primary)' } as React.CSSProperties} />
                    <span className="rowmain">
                      <span className="rowtitle">
                        {item.question_label ? `${item.question_label} · ` : ''}
                        {statusText(item.status)}
                      </span>
                      <span className="rowmeta">
                        <span className="chip">任务 #{item.id}</span>
                        {item.attempts > 0 && <span className="chip">尝试 {item.attempts} 次</span>}
                        <span className="chip">{elapsed(item.updated_at || item.created_at)}</span>
                        {item.images?.length > 0 && <span className="chip">{item.images.length} 张图</span>}
                      </span>
                      {item.error && <span className="rowmeta"><span className="chip color" style={{ '--chip-color': 'var(--danger)' } as React.CSSProperties}>{String(item.error).slice(0, 80)}</span></span>}
                      <span className="rowmeta">
                        {item.status === 'confirmed' && item.question_id
                          ? <button className="ghost" onClick={() => openQuestion(item.question_id)}>查看错题</button>
                          : <button className="ghost" onClick={() => openTask(item.id)}>{item.status === 'waiting_confirmation' ? '查看结果' : '查看'}</button>}
                        {['failed', 'cancelled'].includes(item.status) && (
                          <button className="ghost" onClick={async () => {
                            try { await api(`/tasks/${item.id}/retry`, { method: 'POST' }); loadTasks(); setNotice(`任务 #${item.id} 已重新排队`); }
                            catch (err: any) { setError(err.message); }
                          }}>重试</button>
                        )}
                        {BUSY_STATUSES.includes(item.status) && (
                          <button className="ghost" onClick={async () => {
                            try { await api('/tasks/' + item.id, { method: 'DELETE' }); loadTasks(); setTask(null); setNotice(`任务 #${item.id} 已取消`); }
                            catch (err: any) { setError(err.message); }
                          }}>取消</button>
                        )}
                      </span>
                    </span>
                  </div>
  );

  useEffect(() => {
    window.localStorage.setItem('wrongbook-task-filter', centerFilter);
  }, [centerFilter]);

  const loadQuestions = useCallback(() => {
    const query = new URLSearchParams();
    Object.entries(filter).forEach(([key, value]) => { if (value) query.set(key, String(value)); });
    // Fetch pages so old matching questions remain reachable beyond the first 100.
    return Promise.all(Array.from({ length: Math.ceil(questionLimit / 100) }, (_, page) =>
      api('/questions?' + query.toString() + `&limit=100&offset=${page * 100}`)))
      .then((pages) => { setQuestions(pages.flatMap((data) => data.items || [])); setQuestionTotal(pages[0]?.total || 0); })
      .catch((err: Error) => setError(err.message));
  }, [filter, questionLimit]);

  const loadReviews = useCallback((scope: string) => {
    const path = scope === 'overdue' ? '/reviews/overdue' : '/reviews/today';
    return api(path).then((data) => setReviews(data.items || [])).catch((err: Error) => setError(err.message));
  }, []);

  const loadDistribution = useCallback(() => api('/stats/distribution').then(setDistribution).catch(() => {}), []);
  const loadBackups = useCallback(() => api('/system/backups').then((data) => setBackups(data.items || [])).catch(() => {}), []);
  const loadWeekly = useCallback(() => api('/learning/weekly?offset=' + weekOffset).then(setWeekly).catch((err: Error) => setError(err.message)), [weekOffset]);
  const loadTrash = useCallback(() => api('/learning/trash').then((data) => setTrash(data.items || [])).catch((err: Error) => setError(err.message)), []);

  const loadTasks = useCallback(() => {
    const fetchPages = (status: string, limit: number) => Promise.all(
      Array.from({ length: Math.ceil(limit / 20) }, (_, page) => api(`/tasks?status=${status}&limit=20&offset=${page * 20}`))
    );
    return Promise.all([fetchPages('unresolved', taskLimit), fetchPages('archived', archiveLimit)])
      .then(([pending, archived]) => {
        setTaskList(pending.flatMap(page => page.items));
        setTaskTotal(pending[0].total);
        setArchivedTasks(archived.flatMap(page => page.items));
        setArchiveTotal(archived[0].total);
      })
      .catch((err: Error) => setError(err.message));
  }, [taskLimit, archiveLimit]);

  useEffect(() => {
    if (tab !== 'tasks') return;
    let alive = true;
    const refresh = async () => {
      if (document.hidden) return;
      try {
        const data = await api(`/tasks?include_practice=true&limit=20&offset=${centerPage * 20}&status=${centerFilter}`);
        if (alive) {
          setCenterTasks(data.items);
          setCenterTotal(data.total);
          setCenterSelected((current) => new Set([...current].filter((id) => data.items.some((item: any) => item.id === id))));
          setCenterError('');
        }
      } catch (err: any) { if (alive) setCenterError(err.message); }
      finally { if (alive) setCenterLoading(false); }
    };
    setCenterLoading(true); refresh();
    const timer = window.setInterval(refresh, 3000);
    return () => { alive = false; window.clearInterval(timer); };
  }, [tab, centerFilter, centerPage]);

  const batchTaskAction = useCallback(async (action: 'retry' | 'cancel') => {
    const ids = [...centerSelected];
    if (!ids.length) return;
    try {
      const result = await jsonPost(`/tasks/batch-${action}`, { task_ids: ids });
      setCenterSelected(new Set());
      setNotice(`${action === 'retry' ? '已重新排队' : '已取消'} ${result.count || 0} 个任务${result.skipped?.length ? `，${result.skipped.length} 个未操作` : ''}`);
      const data = await api(`/tasks?include_practice=true&limit=20&offset=${centerPage * 20}&status=${centerFilter}`);
      setCenterTasks(data.items || []); setCenterTotal(data.total || 0);
    } catch (err: any) { setError(err.message); }
  }, [centerSelected, centerPage, centerFilter]);

  const openTask = useCallback(async (taskId: number) => {
    try {
      const detail = await api('/tasks/' + taskId);
      setTask(detail);
      window.localStorage.setItem('wrongbook-last-task', String(taskId));
      if (detail.status === 'waiting_confirmation') {
        setDraft({ ...(detail.draft || detail.result) });
        setTab('confirm');
      } else {
        setTab('task');
      }
    } catch (err: any) {
      setError(err.message);
    }
  }, []);

  useEffect(() => {
    const root = document.documentElement;
    if (theme === 'auto') root.removeAttribute('data-theme');
    else root.setAttribute('data-theme', theme);
    window.localStorage.setItem('wrongbook-theme', theme);
  }, [theme]);

  useEffect(() => {
    api('/system/status').then(setStatus).catch((err: Error) => setError(err.message));
    api('/stats/overview').then(setOverview).catch(() => {});
    api('/stats/ai-accuracy').then(setAccuracy).catch(() => {});
    api('/questions/filters').then(setFilters).catch(() => {});
    loadDistribution();
    loadBackups();
  }, [loadDistribution]);

  // The错题本 is paginated and can be large; defer its first query until the
  // user opens that tab instead of blocking the homepage startup.
  useEffect(() => { if (tab === 'list') loadQuestions(); }, [tab, filter, loadQuestions]);
  useEffect(() => { setQuestionLimit(100); }, [filter]);

  useEffect(() => { setTaskLabel(task?.question_label || ''); }, [task?.id, task?.question_label]);
  useEffect(() => { setRecognitionHint(task?.recognition_hint || ''); }, [task?.id, task?.recognition_hint]);

  useEffect(() => {
    if (!task || tab !== 'task') return;
    if (task.status === 'waiting_confirmation') {
      setDraft({ ...(task.draft || task.result) });
      setTab('confirm');
      loadTasks();
      return;
    }
    if (!ACTIVE_STATUSES.includes(task.status)) return;
    const timer = window.setInterval(() => {
      if (document.hidden) return;
      api('/tasks/' + task.id)
        .then((next) => {
          setTask(next);
          if (!ACTIVE_STATUSES.includes(next.status)) loadTasks();
        })
        .catch(() => {});
    }, 2000);
    return () => window.clearInterval(timer);
  }, [task?.id, task?.status, tab, loadTasks]);

  useEffect(() => {
    if (!task || !ACTIVE_STATUSES.includes(task.status)) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [task?.status, task?.id]);

  useEffect(() => {
    if (!practiceTask || !ACTIVE_STATUSES.includes(practiceTask.status)) return;
    const timer = window.setInterval(() => {
      if (document.hidden) return;
      api('/tasks/' + practiceTask.id).then(setPracticeTask).catch(() => {});
    }, 2000);
    return () => window.clearInterval(timer);
  }, [practiceTask?.id, practiceTask?.status]);

  useEffect(() => {
    setAttemptGrade({ outcome: practiceTask?.result?.outcome || 'uncertain', same_error: practiceTask?.result?.same_error == null ? 'unknown' : String(practiceTask.result.same_error) });
  }, [practiceTask?.id, practiceTask?.status]);
  useEffect(() => { if (tab === 'stats') loadWeekly(); }, [tab, loadWeekly]);

  function go(next: string) {
    setTab(next);
    if (next === 'upload' || next === 'tasks') loadTasks();
    if (next === 'review') loadReviews(reviewScope);
    if (next === 'stats') { loadDistribution(); loadWeekly(); api('/stats/overview').then(setOverview).catch(() => {}); api('/questions/filters').then(setFilters).catch(() => {}); }
    if (next === 'trash') loadTrash();
    if (next === 'status') { loadBackups(); api('/system/status').then(setStatus).catch(() => {}); }
  }

  function showKnowledge(name: string, subject = '') {
    setFilter({ ...EMPTY_FILTER, knowledge_point: name, subject });
    go('list');
  }

  async function openPractice(id: number) {
    try {
      const data = await api('/learning/practice/' + id);
      setPracticeQuestion(data.question); setPracticeHistory(data.attempts);
      setPracticeTask(null); setPracticeFiles([]); setTab('practice');
      const pending = data.attempts.find((item: any) => item.source === 'practice' && !item.confirmed_at && item.status !== 'cancelled');
      if (pending) setPracticeTask(await api('/tasks/' + pending.task_id));
    } catch (err: any) { setError(err.message); }
  }

  async function uploadPractice() {
    if (!practiceQuestion || !practiceFiles.length) return;
    setBusy(true); setError('');
    try {
      const form = new FormData();
      practiceFiles.forEach((file) => { form.append('files', file); form.append('image_types', 'solution'); });
      form.append('image_edits', JSON.stringify(practiceFiles.map((file) => imageEdits.get(file)?.edit || null)));
      form.append('question_label', '重做'); form.append('practice_question_id', String(practiceQuestion.id));
      const created = await uploadImages(form);
      const saved = await api('/tasks/' + created.id).catch(() => ({ ...created, images: [] }));
      setPracticeTask(saved); setPracticeFiles([]);
      setImageEdits((current) => { const next = new Map(current); practiceFiles.forEach((file) => next.delete(file)); return next; });
    } catch (err: any) { setError(err.message); } finally { setBusy(false); setUploadProgress(null); }
  }

  async function confirmPractice(attempt: any, rating: string) {
    setBusy(true);
    try {
      await jsonPost(`/learning/attempts/${attempt.id}/confirm`, { outcome: attemptGrade.outcome, same_error: attemptGrade.same_error === 'unknown' ? null : attemptGrade.same_error === 'true', rating });
      setNotice('重做已记录，复习计划已更新');
      if (practiceQuestion) await openPractice(practiceQuestion.id);
    } catch (err: any) { setError(err.message); } finally { setBusy(false); }
  }

  function choose(event: React.ChangeEvent<HTMLInputElement>) {
    const picked = Array.from(event.target.files || []);
    setFiles((current) => [...current, ...picked]);
    setTypes((current) => [...current, ...picked.map(() => 'question')]);
    event.target.value = '';
  }

  function removeFile(index: number) {
    setImageEdits((current) => { const next = new Map(current); next.delete(files[index]); return next; });
    setFiles((current) => current.filter((_, position) => position !== index));
    setTypes((current) => current.filter((_, position) => position !== index));
  }

  function uploadImages(form: FormData): Promise<any> {
    setUploadProgress(0);
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open('POST', '/api/tasks/upload');
      xhr.timeout = 180000;
      xhr.upload.onprogress = (event) => { if (event.lengthComputable) setUploadProgress(Math.round(event.loaded * 100 / event.total)); };
      xhr.onload = () => {
        let data: any;
        try { data = JSON.parse(xhr.responseText); } catch { reject(new Error('上传响应异常，请先到任务中心确认是否已保存')); return; }
        if (xhr.status >= 200 && xhr.status < 300) resolve(data);
        else reject(new Error(describeError(data, xhr.status)));
      };
      xhr.onerror = () => reject(new Error('上传连接中断，照片仍在本页；请先查看任务中心是否已保存，再决定重试。'));
      xhr.ontimeout = () => reject(new Error('上传超时，照片仍在本页；请先查看任务中心是否已保存。'));
      xhr.send(form);
    });
  }

  async function upload() {
    if (!files.length) return;
    setBusy(true); setError('');
    try {
      const form = new FormData();
      files.forEach((file, index) => {
        form.append('files', file);
        form.append('image_types', types[index] || 'other');
      });
      form.append('question_label', questionLabel.trim());
      form.append('multi_question', String(multiQuestion));
      if (multiQuestion) form.append('question_labels', questionLabels.trim());
      form.append('image_edits', JSON.stringify(files.map((file) => imageEdits.get(file)?.edit || null)));
      const created = await uploadImages(form);
      // Fetch the complete task immediately so the saved originals appear
      // before the background worker finishes. This prevents the photo preview
      // from looking as if it disappeared after tapping upload.
      const saved = await api('/tasks/' + created.id).catch(() => ({ ...created, images: [] }));
      setTask(saved);
      setFiles([]); setTypes([]); setQuestionLabel(''); setMultiQuestion(false); setQuestionLabels('');
      setImageEdits(new Map());
      window.localStorage.setItem('wrongbook-last-task', String(created.id));
      if ((created.task_count || created.ids?.length || 1) > 1) {
        setNotice(`已创建 ${created.task_count || created.ids.length} 个识别任务，正在分别分析你选择的题目`);
        go('tasks');
      } else {
        setTab('task');
      }
      loadTasks();
    } catch (err: any) {
      setError(err.message);
    } finally {
      setBusy(false);
      setUploadProgress(null);
    }
  }

  async function retry() {
    if (!task) return;
    try {
      setTask(await api(`/tasks/${task.id}/retry`, { method: 'POST' }));
      setError('');
    } catch (err: any) {
      setError(err.message);
    }
  }

  async function retryWithLabel() {
    if (!task) return;
    setBusy(true); setError('');
    try {
      await jsonPost(`/tasks/${task.id}/retry`, {
        question_label: taskLabel.trim(),
        recognition_hint: recognitionHint.trim(),
      });
      window.localStorage.setItem('wrongbook-last-task', String(task.id));
      setTask(await api('/tasks/' + task.id));
      setTab('task');
      setNotice(recognitionHint.trim() ? '已带着你的纠正提示重新识别' : taskLabel.trim() ? `已按「${taskLabel.trim()}」重新识别` : '已重新识别');
      loadTasks();
    } catch (err: any) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function saveConfirmed(withFeedback: boolean) {
    if (!task || !draft) return;
    if (withFeedback && (!feedback.recognition || !feedback.correction)) {
      setError('请先评价识别与纠错是否准确，再确认保存');
      return;
    }
    setBusy(true); setError('');
    try {
      if (withFeedback) {
        await jsonPost(`/tasks/${task.id}/feedback`, {
          recognition_rating: feedback.recognition,
          correction_rating: feedback.correction,
        });
      }
      const payload: Record<string, unknown> = {};
      Object.entries(draft).forEach(([key, value]) => { payload[key] = value === null ? '' : value; });
      payload.task_id = task.id;
      const question = await jsonPost(`/questions/${task.id}/confirm`, payload);
      setSelected(question); setTab('detail');
      loadTasks();
      setOverview(await api('/stats/overview'));
      setAccuracy(await api('/stats/ai-accuracy'));
      loadDistribution();
      api('/questions/filters').then(setFilters).catch(() => {});
    } catch (err: any) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function openQuestion(id: number) {
    try {
      setSelected(await api('/questions/' + id));
      setEditing(null); setTab('detail');
    } catch (err: any) {
      setError(err.message);
    }
  }

  async function saveQuestion() {
    if (!editing) return;
    setBusy(true); setError('');
    try {
      const updated = await api(`/questions/${editing.id}`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(editing),
      });
      setSelected(updated); setEditing(null); loadQuestions();
      api('/questions/filters').then(setFilters).catch(() => {});
      setNotice('已保存修改');
    } catch (err: any) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function removeQuestion(id: number) {
    if (!window.confirm('删除这道错题？图片和备份不会被删除。')) return;
    try {
      await api('/questions/' + id, { method: 'DELETE' });
      setSelected(null); go('list'); loadQuestions();
    } catch (err: any) {
      setError(err.message);
    }
  }

  async function review(questionId: number, rating: string) {
    try {
      await jsonPost(`/questions/${questionId}/review`, { rating });
      loadReviews(reviewScope);
      api('/stats/overview').then(setOverview).catch(() => {});
    } catch (err: any) {
      setError(err.message);
    }
  }

  async function runSelfTest() {
    setSelfTest({ running: true }); setError('');
    try {
      setSelfTest({ running: false, data: await api('/system/ai-selftest?cases=3', { method: 'POST' }) });
    } catch (err: any) {
      setSelfTest({ running: false }); setError(err.message);
    }
  }

  const trendMap = new Map<string, number>((distribution?.trend || []).map((item: any) => [item.day, item.count]));
  const week = Array.from({ length: 7 }, (_, index) => {
    const date = new Date();
    date.setDate(date.getDate() - (6 - index));
    return {
      weekday: '日一二三四五六'[date.getDay()],
      day: date.getDate(),
      today: index === 6,
      count: trendMap.get(localKey(date)) ?? 0,
    };
  });
  const last30 = Array.from({ length: 30 }, (_, index) => {
    const date = new Date();
    date.setDate(date.getDate() - (29 - index));
    return trendMap.get(localKey(date)) ?? 0;
  });
  const subjectItems = withColors((distribution?.subjects || []).map((item: any) => ({
    name: subjectName(item.name), count: item.count,
  })));
  const errorItems = withColors(distribution?.error_types);
  const knowledgeItems = withColors(distribution?.knowledge_points);
  const syllabus: Syllabus = filters.syllabus || {};
  const syllabusEntries = syllabus[syllabusSubject] || [];
  const filterSyllabus = Object.entries(syllabus).filter(([subject]) => !filter.subject || subject === filter.subject
    || (filter.subject === 'math1' && subject.startsWith('math_')) || (filter.subject === '408' && ['ds', 'coa', 'os', 'cn'].includes(subject)));
  const difficultyItems = DIFFICULTIES.map((level) => ({
    name: level.label, color: level.color,
    count: (distribution?.difficulty || []).reduce((total: number, item: any) =>
      total + (difficultyLevel(item.name) === level.value ? item.count : 0), 0),
  })).filter((item) => item.count > 0);
  const repeatQuestions: any[] = distribution?.repeat_questions || [];
  const subjectLabels = new Set(Object.values(SUBJECT_LABELS));
  const repeatKnowledgePoints: any[] = (distribution?.repeat_syllabus_points || distribution?.repeat_knowledge_points || (() => {
    const groups: Record<string, any> = {};
    repeatQuestions.forEach((item: any) => {
      const rawName = item.knowledge_points?.[0] || '待确认';
      const name = subjectLabels.has(rawName) ? '待确认' : rawName;
      const key = `${item.subject || ''}-${name}`;
      const group = groups[key] || (groups[key] = {
        name, subject: item.subject, count: 0, repeat_count: 0,
        mastery: 0, questions: [],
      });
      const repeats = Number(item.failures || 0) + Number(item.lapses || 0);
      group.count += 1;
      group.repeat_count += repeats;
      group.mastery += Number(item.mastery || 0);
      group.questions.push(item);
    });
    return Object.values(groups).map((group: any) => ({
      ...group,
      mastery: group.count ? group.mastery / group.count : 0,
      questions: group.questions.slice(0, 5),
    })).sort((a: any, b: any) => b.repeat_count - a.repeat_count || a.mastery - b.mastery);
  })()).filter((point: any) => point.name !== '待确认');
  const repeatGroupMap = new Map(repeatKnowledgePoints.map((point) => [`${point.subject || ''}-${point.name}`, point]));
  // Use the same canonical catalog and counts as 洞察 -> 标签 -> 考纲知识点.
  // Repeat statistics are merged in when available, while a point with many
  // wrong questions remains visible even before it has a recorded repeat.
  const syllabusOrder = new Map<string, number>();
  let syllabusIndex = 0;
  Object.entries(syllabus).forEach(([subject, entries]) => (entries as any[]).forEach((entry) => {
    syllabusOrder.set(`${subject}-${entry.name}`, syllabusIndex++);
  }));
  const catalogPoints = Object.entries(syllabus).flatMap(([subject, entries]) => (entries as any[])
    .filter((entry) => Number(entry.count || 0) > 0)
    .map((entry) => {
      const group = repeatGroupMap.get(`${subject}-${entry.name}`) || {};
      return {
        ...group,
        name: entry.name,
        subject,
        count: Number(entry.count || 0),
        question_count: Number(entry.count || 0),
        repeat_count: Number(group.repeat_count || 0),
        questions: group.questions || [],
        has_repeat_stats: Boolean(repeatGroupMap.has(`${subject}-${entry.name}`)),
      };
    }));
  const repeatSyllabusPoints: any[] = (catalogPoints.length ? catalogPoints : [...repeatKnowledgePoints]).sort((left, right) => {
    const leftCount = Number(left.question_count ?? left.count ?? 0);
    const rightCount = Number(right.question_count ?? right.count ?? 0);
    if (leftCount !== rightCount) return rightCount - leftCount;
    const leftOrder = syllabusOrder.get(`${left.subject || ''}-${left.name}`);
    const rightOrder = syllabusOrder.get(`${right.subject || ''}-${right.name}`);
    if (leftOrder != null && rightOrder != null && leftOrder !== rightOrder) return leftOrder - rightOrder;
    if (leftOrder != null && rightOrder == null) return -1;
    if (leftOrder == null && rightOrder != null) return 1;
    return Number(right.repeat_count || 0) - Number(left.repeat_count || 0)
      || Number(left.mastery || 0) - Number(right.mastery || 0)
      || String(left.name).localeCompare(String(right.name));
  });
  const visibleRepeatKnowledge = showAllRepeatKnowledge ? repeatSyllabusPoints : repeatSyllabusPoints.slice(0, 3);
  const subjectOptions: { value: string; label: string }[] = filters.subject_options?.length
    ? filters.subject_options
    : Object.entries(SUBJECT_LABELS).filter(([key]) => key !== 'unknown').map(([value, label]) => ({ value, label }));
  const subjectEditOptions: { value: string; label: string }[] = filters.subject_edit_options?.length
    ? filters.subject_edit_options
    : Object.entries(SUBJECT_LABELS).filter(([key]) => key !== 'unknown').map(([value, label]) => ({ value, label }));

  return (
    <main>
      <header>
        <div className="headmain">
          <h1>{tab === 'task'
            ? (task?.status === 'confirmed'
              ? '已保存'
              : task?.status === 'waiting_confirmation'
                ? '确认结果'
                : task?.status === 'failed'
                  ? '识别失败'
                  : '识别中')
            : (TITLES[tab] || '错题本')}</h1>
          {tab === 'home' && <p className="sub">数学一 · 408 · 拍照识别与间隔复习</p>}
        </div>
        <div className="headside">
          <span className={'aipill ' + (status?.api_key_configured ? 'ok' : 'warn')}>
            {status?.api_key_configured ? (status?.ai_model || 'AI 就绪') : 'AI 未配置'}
          </span>
          <button
            className="iconbtn"
            onClick={() => setTheme(theme === 'dark' ? 'light' : theme === 'light' ? 'auto' : 'dark')}
            title="切换主题：深色 / 浅色 / 跟随系统"
          >{theme === 'dark' ? '🌙' : theme === 'light' ? '☀️' : '🌗'}</button>
        </div>
      </header>

      {tab === 'home' && (
        <div className="week">
          {week.map((item, index) => (
            <div key={index} className={item.today ? 'today' : ''}>
              {item.weekday}
              <b>{item.day}</b>
              {item.count > 0 && <span className="dot" />}
            </div>
          ))}
        </div>
      )}

      {error && <div className="error">{error}<button onClick={() => setError('')}>关闭</button></div>}
      {notice && <div className="notice">{notice}<button onClick={() => setNotice('')}>关闭</button></div>}
      {uploadProgress != null && <div className="upload-progress" role="status"><b>{uploadProgress < 100 ? `照片传输中 · ${uploadProgress}%` : '传输完成，正在保存到电脑…'}</b><progress max="100" value={uploadProgress} /><p className="muted">上传完成前请保持页面打开。</p></div>}
      {['home', 'upload', 'review'].includes(tab) && <button className="wide task-entry" onClick={() => go('tasks')}>任务中心 · 查看上传、重做和待确认结果 ›</button>}
      {cropFile && <CropEditor file={cropFile} initial={imageEdits.get(cropFile)} onClose={() => setCropFile(null)} onSave={(value) => { setImageEdits((current) => { const next = new Map(current); if (value) next.set(cropFile, value); else next.delete(cropFile); return next; }); setCropFile(null); }} />}

      {tab === 'tasks' && <section>
        <div className="cardhead"><h3>上传与分析进度</h3><button onClick={() => go('upload')}>添加照片</button></div>
        <p className="muted">每 3 秒更新。原图保存后会进入 AI 队列，分析失败可直接重试。</p>
        <div className="task-center-toolbar">
          <label>任务状态<select value={centerFilter} onChange={(event) => { setCenterFilter(event.target.value); setCenterPage(0); setCenterSelected(new Set()); }}>{[['unresolved', '未处理完'], ['', '全部'], ['active', '排队与分析中'], ['waiting_confirmation', '待确认'], ['failed', '失败'], ['confirmed', '已完成'], ['cancelled', '已取消']].map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
          <label className="task-select-all"><input type="checkbox" checked={centerTasks.length > 0 && centerTasks.every((item) => centerSelected.has(item.id))} onChange={(event) => setCenterSelected(event.target.checked ? new Set(centerTasks.map((item) => item.id)) : new Set())} /> 全选本页</label>
          {!!centerSelected.size && <span className="chip">已选 {centerSelected.size} 项</span>}
          {[...centerSelected].some((id) => centerTasks.some((item) => item.id === id && ['failed', 'cancelled'].includes(item.status))) && <button onClick={() => batchTaskAction('retry')}>批量重试</button>}
          {[...centerSelected].some((id) => centerTasks.some((item) => item.id === id && BUSY_STATUSES.includes(item.status))) && <button onClick={() => batchTaskAction('cancel')}>批量取消</button>}
        </div>
        {centerError && <p className="error" role="alert">任务列表更新失败：{centerError}，正在自动重试。</p>}
        {centerLoading ? <p role="status">正在加载任务…</p> : centerTasks.length ? centerTasks.map((item) => <article className="task-card" key={item.id}>
          <div className="cardhead"><label className="task-select"><input type="checkbox" checked={centerSelected.has(item.id)} onChange={(event) => setCenterSelected((current) => { const next = new Set(current); if (event.target.checked) next.add(item.id); else next.delete(item.id); return next; })} aria-label={`选择任务 ${item.id}`} /><b>#{item.id} · {item.practice_question_id ? '重做' : '上传'}{item.question_label ? ' · ' + item.question_label : ''}</b></label><span className="chip">{statusText(item.status)}</span></div>
          <p className="muted">{new Date(item.created_at).toLocaleString()} · {item.images.length} 张原图已保存</p>
          <ol className="task-steps" aria-label="任务进度">{['原图已保存', 'AI 分析', '等待确认', '完成'].map((label, i) => <li key={label} className={(item.status === 'confirmed' || i === 0 || (i === 1 && ['recognizing', 'pending_analysis', 'analyzing', 'recognition_completed', 'analysis_completed', 'waiting_confirmation'].includes(item.status)) || (i === 2 && item.status === 'waiting_confirmation')) ? 'done' : ''}>{label}</li>)}</ol>
          {item.error && <p className="error">{item.error}</p>}
          <div className="practice-actions">
            <button onClick={async () => { if (item.practice_question_id) { await openPractice(item.practice_question_id); try { setPracticeTask(await api('/tasks/' + item.id)); } catch (err: any) { setError(err.message); } } else if (item.status === 'confirmed' && item.question_id) openQuestion(item.question_id); else openTask(item.id); }}>{item.status === 'waiting_confirmation' ? '核对结果' : item.status === 'confirmed' ? '查看记录' : '查看详情'}</button>
            {['failed', 'cancelled'].includes(item.status) && <button onClick={async () => { try { const next = await jsonPost(`/tasks/${item.id}/retry`, {}); setCenterTasks((items) => items.map((t) => t.id === item.id ? { ...t, status: next.status, error: null } : t)); } catch (err: any) { setError(err.message); } }}>重试分析</button>}
          </div>
        </article>) : <Empty emoji="📋" text="这个状态下没有任务" />}
        <div className="practice-actions"><button disabled={!centerPage} onClick={() => setCenterPage((p) => p - 1)}>上一页</button><span>第 {centerPage + 1} 页 · 共 {centerTotal} 项</span><button disabled={(centerPage + 1) * 20 >= centerTotal} onClick={() => setCenterPage((p) => p + 1)}>下一页</button></div>
      </section>}

      {tab === 'home' && (
        <>
          <section>
            <h3>☀️ 今日待复习 {overview?.today_due ?? overview?.due ?? 0}</h3>
            <p className="muted">逾期 {overview?.overdue ?? 0} 题 · 到期的错题会出现在复习页，按忘记 / 困难 / 记得 / 熟练评价。</p>
            <button className="primary wide" onClick={() => go('review')}>开始复习</button>
          </section>

          <section>
            <h3>🔁 高频错题考纲知识点 {repeatSyllabusPoints.length}</h3>
            {repeatSyllabusPoints.length ? (
              <>
                <div className="repeat-points">
                  {visibleRepeatKnowledge.map((point) => (
                    <details className="repeat-point" key={`${point.subject}-${point.name}`}>
                      <summary>
                        <span className="repeat-summary-main">
                            <span className="rowtitle">{point.name}</span>
                            <span className="rowmeta">
                              <span className="chip color" style={{ '--chip-color': subjectColor(point.subject) } as React.CSSProperties}>{subjectName(point.subject)}</span>
                            <span className="chip">{point.question_count ?? point.count} 道错题</span>
                            {point.has_repeat_stats && <span className="chip">重复 {point.repeat_count} 次</span>}
                            {point.has_repeat_stats && <span className="chip">平均掌握度 {Math.round((point.mastery || 0) * 100)}%</span>}
                          </span>
                        </span>
                      </summary>
                      <div className="repeat-point-body">
                        <div className="rows">
                          {(point.questions || []).map((item: any) => (
                            <button className="rowitem" key={item.id} onClick={() => openQuestion(item.id)}>
                              <span className="ring" style={{ '--ring-color': subjectColor(item.subject) } as React.CSSProperties} />
                              <span className="rowmain">
                                <span className="rowtitle">{compactTitle(item)}</span>
                                <span className="rowmeta">
                                  <span className="chip">{item.chapter || '未分类'}</span>
                                  <span className="chip">重复 {(item.failures || 0) + (item.lapses || 0)} 次</span>
                                  <span className="chip">掌握度 {Math.round((item.mastery || 0) * 100)}%</span>
                                </span>
                              </span>
                              <span className="chev">›</span>
                            </button>
                          ))}
                        </div>
                        <button className="ghost repeat-view" onClick={() => showKnowledge(point.name, point.subject)}>查看该知识点全部错题 ›</button>
                      </div>
                    </details>
                  ))}
                </div>
                {repeatSyllabusPoints.length > 3 && <button className="wide ghost repeat-toggle" onClick={() => setShowAllRepeatKnowledge((value) => !value)}>
                  {showAllRepeatKnowledge ? '收起其余考纲知识点' : `展开其余 ${repeatSyllabusPoints.length - 3} 个考纲知识点`}
                </button>}
              </>
            ) : repeatQuestions.length ? (
              <p className="muted">正在整理反复出错知识点，请刷新后查看。</p>
            ) : <Empty emoji="🌱" text="还没有反复出错的知识点" />}
          </section>

          <section>
            <h3>📊 AI 准确率</h3>
            <div className="metrics">
              <div className="metric"><b>{Math.round((accuracy?.recognition?.rate ?? 0) * 100)}%</b><span>图像识别正确率</span></div>
              <div className="metric"><b>{Math.round((accuracy?.correction?.rate ?? 0) * 100)}%</b><span>第一处错误判断正确率</span></div>
            </div>
            <p className="muted">共评价 {accuracy?.reviewed_tasks ?? 0} 次，AI 已分析 {accuracy?.analyzed_tasks ?? 0} 次。</p>
          </section>
        </>
      )}

      {tab === 'upload' && (
        <>
          <section>
            <div className="upload-pickers">
              <label className="file-picker"><span>📷 拍照</span><input disabled={busy} type="file" accept="image/*" capture="environment" onChange={choose} /></label>
              <label className="file-picker"><span>🖼️ 相册多选</span><input disabled={busy} type="file" accept="image/*" multiple onChange={choose} /></label>
            </div>
            <label className="inline-choice upload-mode-choice">
              <input type="checkbox" checked={multiQuestion} onChange={(event) => setMultiQuestion(event.target.checked)} />
              <span>多题纠错（同一页选择多道题）</span>
            </label>
            {multiQuestion ? (
              <label>选择要纠错的题号（逗号分隔，也支持范围）
                <input
                  value={questionLabels}
                  onChange={(event) => setQuestionLabels(event.target.value)}
                  placeholder="例如：1, 3, 5-8"
                  inputMode="text"
                />
                <small className="muted">每道题会先从整页照片中定位并裁剪，再独立识别、确认和收纳，最多 20 道。</small>
              </label>
            ) : (
              <label>这是第几题（照片里有多道题时只分析这一道）
                <input
                  value={questionLabel}
                  onChange={(event) => setQuestionLabel(event.target.value)}
                  placeholder="例如：第3题 / 3"
                  inputMode="text"
                />
              </label>
            )}
            {files.length > 0 && (
              <div className="thumbs">
                {files.map((file, index) => (
                  <div className="thumb" key={index}>
                    {imageEdits.has(file) ? <img src={imageEdits.get(file)!.preview} alt="已选识别范围" /> : <LocalImage file={file} />}
                    <select value={types[index]} onChange={(event) => setTypes((current) => current.map((value, position) => position === index ? event.target.value : value))}>
                      {Object.entries(IMAGE_LABELS).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
                    </select>
                    <button disabled={busy} onClick={() => setCropFile(file)}>裁剪 / 旋转</button>
                    <button disabled={busy} onClick={() => removeFile(index)}>删除</button>
                  </div>
                ))}
              </div>
            )}
            <p className="muted">{files.length ? `已选择 ${files.length} 张，可继续添加` : '选择题目、解题过程或参考答案图片，原图会先保存到电脑'}</p>
            <button className="primary wide" disabled={busy || !files.length} onClick={upload}>
              {busy ? '保存中…' : '保存原图并开始分析'}
            </button>
          </section>

          <section>
            <div className="cardhead">
              <h3>待处理 {taskTotal}</h3>
              <button className="ghost" onClick={() => loadTasks()}>刷新</button>
            </div>
            {taskList.length ? (
              <div className="rows">
                {taskList.map(renderUploadTask)}
              </div>
             ) : <p className="muted">暂无待处理任务</p>}
            {taskList.length < taskTotal && <button className="wide" onClick={() => setTaskLimit(limit => limit + 20)}>加载更多待处理任务</button>}
            {archiveTotal > 0 && <details className="archive-group">
              <summary><span>已归档</span><span className="archive-count">{archiveTotal} 项</span></summary>
              <div className="rows">{archivedTasks.map(renderUploadTask)}</div>
              {archivedTasks.length < archiveTotal && <button className="wide" onClick={() => setArchiveLimit(limit => limit + 20)}>加载更多已归档任务</button>}
            </details>}
          </section>
        </>
      )}

      {tab === 'task' && task && (
        <section>
          <label>{task.multi_batch_id ? '本批次题号（每道题已独立裁剪）' : '只分析哪一道题（照片里有多道题时填这里）'}
            <input
              value={taskLabel}
              disabled={Boolean(task.multi_batch_id)}
              onChange={(event) => setTaskLabel(event.target.value)}
              placeholder={task.multi_batch_id ? '多题任务的题号不能单独修改' : '例如：第3题 / 3 / 3-5，留空表示整页都识别'}
            />
          </label>
          <label>指出上次识别哪里错了（可选）
            <textarea
              value={recognitionHint}
              onChange={(event) => setRecognitionHint(event.target.value)}
              placeholder="例如：题干第二行的 2π 识别成了 π；小问（2）的条件是 x>0"
              rows={3}
            />
          </label>
          <button className="primary wide" disabled={busy || ACTIVE_STATUSES.includes(task.status)} onClick={retryWithLabel}>
            {ACTIVE_STATUSES.includes(task.status) ? '识别中…' : recognitionHint.trim() ? '按提示重新识别' : '重新识别'}
          </button>
          <div className="statusrow"><span>状态</span><b>{statusText(task.status)}</b></div>
          <div className="statusrow"><span>原图</span><b>{task.images?.length || 0} 张已保存</b></div>
          {task.ai_meta && (
            <>
              <div className="statusrow"><span>模型</span><b>{task.ai_meta.model}</b></div>
              <div className="statusrow"><span>耗时</span><b>{task.ai_meta.elapsed_ms} ms · 第 {task.ai_meta.attempts} 次</b></div>
            </>
          )}
          {task.images?.length > 0 && (
            <div className="thumbs">
              {task.images.map((image) => (
                <div className="thumb saved" key={image.id}>
                  <img src={'/api/tasks/images/' + image.id + (image.has_edit ? '?edited=true' : '')} alt={image.has_edit ? '本题裁剪图' : '原图'} />
                  <small>{IMAGE_LABELS[image.image_type] || image.image_type}</small>
                </div>
              ))}
            </div>
          )}
          {task.error && <div className="error">{task.error}</div>}
          <button onClick={() => api('/tasks/' + task.id).then(setTask).catch((err: Error) => setError(err.message))}>刷新</button>
          {task.status === 'failed' && <button className="primary" onClick={retry}>重试分析</button>}
          {ACTIVE_STATUSES.includes(task.status) && (
            <p className="muted">
              已用时 {duration(task.created_at, now)}，页面会自动刷新。
              整页试卷照片识别通常需要 1-3 分钟，可以离开这个页面，稍后从「上传 → 待处理」回来查看。
            </p>
          )}
        </section>
      )}

      {tab === 'confirm' && draft && task && (
        <section>
          <div className="confidence">
            <b className={confidenceClass(draft.recognition_confidence ?? draft.confidence ?? 0)}>
              识别 {Math.round((draft.recognition_confidence ?? draft.confidence ?? 0) * 100)}%
            </b>
            <b className={confidenceClass(draft.correction_confidence ?? draft.confidence ?? 0)}>
              纠错 {Math.round((draft.correction_confidence ?? draft.confidence ?? 0) * 100)}%
            </b>
          </div>
          <div className="field-confidence" aria-label="各字段可信度">
            {FIELD_CONFIDENCE_LABELS.map(([field, label]) => {
              const value = fieldConfidenceValue(draft, field);
              return <span className={`field-confidence-item ${confidenceClass(value)}`} key={field}>
                <span>{label}</span><b>{Math.round(value * 100)}%</b>{fieldNeedsReview(draft, field) && <em>需核对</em>}
              </span>;
            })}
          </div>

          <div className="rowmeta">
            <span className="chip">{subjectName(draft.subject)}</span>
            {draft.chapter && <span className="chip">{draft.chapter}</span>}
            {draft.question_label && <span className="chip">题号 {draft.question_label}</span>}
          </div>
          <details className="compare-panel" open>
            <summary>原题图片（以照片为准）</summary>
            <ImageCompare key={task.id} images={task.images || []} preferEdited={Boolean(task.multi_batch_id)} />
          </details>

          <h3 style={{ marginTop: 18 }}>题目</h3>
          <QuestionContent question={draft} />

          <AnalysisContent result={draft} />
          <CodeBlocks blocks={draft.code_blocks} />

          <div className="rowmeta" style={{ marginTop: 14 }}>
            {(draft.knowledge_points || []).map((name: string) => <span className="chip" key={name}>#{name}</span>)}
            {(draft.error_types || []).map((name: string) => <span className="chip" key={name}>{name}</span>)}
            {(draft.tags || []).map((name: string) => <span className="chip" key={`tag-${name}`}>#{name}</span>)}
          </div>

          {!!task.duplicates?.length && <div className="feedback"><p><b>可能是已存在的错题</b></p><p className="muted">请核对题号与条件，追加会保留本次原图和分析，不计入重做正确率。</p>{task.duplicates.map((item: any) => <div className="duplicateitem" key={item.id}><b>{compactTitle(item)}</b><p className="muted">{item.reason}</p><RichText text={item.question_text} /><button onClick={async () => { try { await jsonPost(`/learning/duplicates/${task.id}/attach`, { question_id: item.id }); setNotice('已追加到原错题'); loadTasks(); openQuestion(item.id); } catch (err: any) { setError(err.message); } }}>追加到此题</button></div>)}</div>}

          <details>
            <summary>编辑识别结果</summary>
            <div className="filtergrid">
              <label>题号<input value={draft.question_label || ''} onChange={(event) => setDraft({ ...draft, question_label: event.target.value })} placeholder="例如：第3题" /></label>
              <label>科目
                <select value={draft.subject || 'unknown'} onChange={(event) => setDraft({ ...draft, subject: event.target.value })}>
                  {subjectEditOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                </select>
              </label>
              <label>题型<input value={draft.question_type || ''} onChange={(event) => setDraft({ ...draft, question_type: event.target.value })} /></label>
              <label>难度
                <select value={difficultyLevel(draft.difficulty)} onChange={(event) => setDraft({ ...draft, difficulty: Number(event.target.value) })}>
                  {DIFFICULTIES.map((level) => <option key={level.value} value={level.value}>{level.label}</option>)}
                </select>
              </label>
            </div>
            <label>题目文字<textarea value={draft.question_text || ''} onChange={(event) => setDraft({ ...draft, question_text: event.target.value })} /></label>
            <label>题目 LaTeX<textarea value={draft.question_latex || ''} onChange={(event) => setDraft({ ...draft, question_latex: event.target.value })} /></label>
            <label>我的解题过程 LaTeX<textarea value={draft.user_solution_latex || ''} onChange={(event) => setDraft({ ...draft, user_solution_latex: event.target.value })} /></label>
            <label>参考答案 LaTeX<textarea value={draft.reference_solution_latex || ''} onChange={(event) => setDraft({ ...draft, reference_solution_latex: event.target.value })} /></label>
            <label>第一处实质性错误<textarea value={draft.first_error_step || ''} onChange={(event) => setDraft({ ...draft, first_error_step: event.target.value })} /></label>
            <label>错误原因<textarea value={draft.error_analysis || ''} onChange={(event) => setDraft({ ...draft, error_analysis: event.target.value })} /></label>
            <label>正确思路<textarea value={draft.correct_approach || ''} onChange={(event) => setDraft({ ...draft, correct_approach: event.target.value })} /></label>
            <KnowledgeSelect value={draft} catalog={syllabus} onChange={setDraft} />
            <TagEditor value={draft} onChange={setDraft} />
            <label>错误大类
              <select value={primaryErrorType(draft.error_types)} onChange={(event) => setDraft({ ...draft, error_types: [event.target.value] })}>
                {ERROR_TYPE_OPTIONS.map((name) => <option key={name} value={name}>{name}</option>)}
              </select>
            </label>
            <label>复习提示<textarea value={draft.review_prompt || ''} onChange={(event) => setDraft({ ...draft, review_prompt: event.target.value })} /></label>
            <label>408 代码 / 结构图（多段用一行 --- 分隔）
              <textarea className="codeinput" value={joinBlocks(draft.code_blocks)} onChange={(event) => setDraft({ ...draft, code_blocks: splitBlocks(event.target.value) })} />
            </label>
            <button onClick={async () => { try { await api(`/tasks/${task.id}/draft`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ result: draft, task_version: task.updated_at }) }); setNotice('修改已保存为草稿，尚未加入错题集。'); } catch (err: any) { setError(err.message); } }}>保存修改草稿</button>
          </details>

          <div className="feedback">
            <p><b>识别准确吗？</b></p>
            <div className="review-actions">
              {RATINGS.map(([key, label]) => (
                <button key={key} className={feedback.recognition === key ? 'active' : ''} onClick={() => setFeedback((current) => ({ ...current, recognition: key }))}>{label}</button>
              ))}
            </div>
            <p><b>纠错准确吗？</b></p>
            <div className="review-actions">
              {RATINGS.map(([key, label]) => (
                <button key={key} className={feedback.correction === key ? 'active' : ''} onClick={() => setFeedback((current) => ({ ...current, correction: key }))}>{label}</button>
              ))}
            </div>
          </div>
          <button className="primary wide" disabled={busy || !feedback.recognition || !feedback.correction} onClick={() => saveConfirmed(true)}>
            确认后加入错题集
          </button>
          <button className="wide" disabled={busy} onClick={() => saveConfirmed(false)}>跳过评价直接保存</button>



          <details>
            <summary>只保留其中一道题（重新识别）</summary>
            <label>题号<input value={taskLabel} onChange={(event) => setTaskLabel(event.target.value)} placeholder="例如：第3题 / 3 / 3-5" /></label>
            <label>指出上次识别哪里错了（可选）
              <textarea value={recognitionHint} onChange={(event) => setRecognitionHint(event.target.value)} placeholder="例如：第一小问的积分上限应为 1，不是 2" rows={3} />
            </label>
            <button className="wide" disabled={busy} onClick={retryWithLabel}>{recognitionHint.trim() ? '按提示重新识别' : '重新识别'}</button>
            <p className="muted">会重新读取原图并参考你的提示，原图会保留，当前识别结果会更新。</p>
          </details>
        </section>
      )}

      {tab === 'list' && (
        <>
          <section>
            <div className="cardhead"><h3>我的错题</h3><button className="ghost" onClick={() => go('trash')}>回收站</button></div>
            <input placeholder="搜索题目、公式、错误原因" value={filter.q} onChange={(event) => setFilter({ ...filter, q: event.target.value })} />
            <details>
              <summary>筛选与排序（{questions.length} 条）</summary>
              <div className="filtergrid">
                <label>科目
                  <select value={filter.subject} onChange={(event) => setFilter({ ...filter, subject: event.target.value })}>
                    <option value="">全部</option>
                    {subjectOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                  </select>
                </label>
                <label>章节
                  <select value={filter.chapter} onChange={(event) => setFilter({ ...filter, chapter: event.target.value })}>
                    <option value="">全部</option>
                    {Array.from(new Set(filterSyllabus.flatMap(([, entries]) => entries.map((entry) => entry.chapter)))).map((name) => <option key={name} value={name}>{name}</option>)}
                  </select>
                </label>
                <label>知识点
                  <select value={filter.knowledge_point} onChange={(event) => setFilter({ ...filter, knowledge_point: event.target.value })}>
                    <option value="">全部</option>
                    {filterSyllabus.map(([subject, entries]) => <optgroup key={subject} label={subjectName(subject)}>
                      {entries.map((entry) => <option key={entry.name} value={entry.name}>{entry.chapter} · {entry.name}{entry.count ? ` (${entry.count})` : ''}</option>)}
                    </optgroup>)}
                    {filterSyllabus.length === 0 && filters.knowledge_points?.map((name: string) => <option key={name} value={name}>{name}</option>)}
                  </select>
                </label>
                <label>错误类型
                  <select value={filter.error_type} onChange={(event) => setFilter({ ...filter, error_type: event.target.value })}>
                    <option value="">全部</option>
                    {filters.error_types?.map((name: string) => <option key={name} value={name}>{name}</option>)}
                  </select>
                </label>
                <label>起始日期<input type="date" value={filter.date_from} onChange={(event) => setFilter({ ...filter, date_from: event.target.value })} /></label>
                <label>结束日期<input type="date" value={filter.date_to} onChange={(event) => setFilter({ ...filter, date_to: event.target.value })} /></label>
                <label>排序
                  <select value={filter.sort} onChange={(event) => setFilter({ ...filter, sort: event.target.value })}>
                    {SORTS.map(([key, label]) => <option key={key} value={key}>{label}</option>)}
                  </select>
                </label>
              </div>
              <button onClick={() => setFilter(EMPTY_FILTER)}>清空筛选</button>
            </details>
          </section>
          <section>
            {questions.length ? (
              <DateArchive items={questions} ascending={filter.sort === 'created_asc'} renderItem={(question) => (
                  <button className="rowitem" key={question.id} onClick={() => openQuestion(question.id)}>
                    <span className="ring" style={{ '--ring-color': subjectColor(question.subject) } as React.CSSProperties} />
                      <span className="rowmain">
                      <span className="rowtitle">{question.daily_number ? `第${question.daily_number}题` : compactTitle(question)}</span>
                      <span className="rowmeta">
                        <span className="chip color" style={{ '--chip-color': subjectColor(question.subject) } as React.CSSProperties}>{subjectName(question.subject)}</span>
                        <span className="chip">{question.chapter || '未分类'}</span>
                        <span className="chip">掌握度 {Math.round((question.mastery || 0) * 100)}%</span>
                        <span className="chip">{difficultyName(question.difficulty)}</span>
                      </span>
                    </span>
                    <span className="chev">›</span>
                  </button>
                )} />
            ) : <Empty emoji="🔍" text="没有符合条件的错题" />}
            {questions.length < questionTotal && <button className="wide" onClick={() => setQuestionLimit((limit) => limit + 100)}>继续加载（已显示 {questions.length} / {questionTotal}）</button>}
          </section>
        </>
      )}

      {tab === 'detail' && selected && (
        <section>
          <div className="cardhead">
            <button className="ghost" onClick={() => go('list')}>‹ 返回</button>
            {!editing && <button className="ghost" onClick={() => setEditing({ ...selected })}>编辑</button>}
          </div>
          {!editing && (
            <>
              <span className="chip color" style={{ '--chip-color': subjectColor(selected.subject) } as React.CSSProperties}>{subjectName(selected.subject)}</span>
              <span className="chip" style={{ marginLeft: 8 }}>{difficultyName(selected.difficulty)}</span>
              {selected.question_label && <span className="chip" style={{ marginLeft: 8 }}>{selected.question_label}</span>}
              <h2 style={{ marginTop: 10 }}>{selected.chapter || '未分类'}</h2>
              <QuestionContent question={selected} />
              <AnalysisContent result={selected} />
              <CodeBlocks blocks={selected.code_blocks} />
              {selected.review_prompt && <><h3 style={{ marginTop: 18 }}>复习提示</h3><RichText text={selected.review_prompt} /></>}
              <div className="rowmeta" style={{ marginTop: 14 }}>
                {(selected.knowledge_points || []).map((name: string) => <span className="chip" key={name}>#{name}</span>)}
                {(selected.error_types || []).map((name: string) => <span className="chip" key={name}>{name}</span>)}
                {(selected.tags || []).map((name: string) => <span className="chip" key={`tag-${name}`}>#{name}</span>)}
              </div>
              <p className="muted" style={{ marginTop: 14 }}>
                掌握度 {Math.round((selected.mastery || 0) * 100)}% · 复习 {selected.review_count || 0} 次 · 下次 {selected.next_review_at ? new Date(selected.next_review_at).toLocaleDateString() : '未安排'}
              </p>
              <button className="primary wide" onClick={() => openPractice(selected.id)}>独立重做这道题</button>
              <div className="thumbs">
                {selected.images?.map((image: any) => (
                  <div className="thumb saved" key={image.id}>
                    <img src={'/api/tasks/images/' + image.id + (image.edit_json ? '?edited=true' : '')} alt={image.edit_json ? '本题裁剪图' : '原图'} />
                    <small>{IMAGE_LABELS[image.image_type] || image.image_type}</small>
                  </div>
                ))}
              </div>
              <button className="danger wide" onClick={() => removeQuestion(selected.id)}>删除这道错题</button>
            </>
          )}
          {editing && (
            <>
              <div className="filtergrid">
                <label>科目
                  <select value={editing.subject || 'unknown'} onChange={(event) => setEditing({ ...editing, subject: event.target.value })}>
                    {subjectEditOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                  </select>
                </label>
                <label>难度
                  <select value={difficultyLevel(editing.difficulty)} onChange={(event) => setEditing({ ...editing, difficulty: Number(event.target.value) })}>
                    {DIFFICULTIES.map((level) => <option key={level.value} value={level.value}>{level.label}</option>)}
                  </select>
                </label>
              </div>
              <label>题目<textarea value={editing.question_text || ''} onChange={(event) => setEditing({ ...editing, question_text: event.target.value })} /></label>
              <label>题目 LaTeX<textarea value={editing.question_latex || ''} onChange={(event) => setEditing({ ...editing, question_latex: event.target.value })} /></label>
              <label>参考答案 LaTeX<textarea value={editing.reference_solution_latex || ''} onChange={(event) => setEditing({ ...editing, reference_solution_latex: event.target.value })} /></label>
              <label>第一处错误<textarea value={editing.first_error_step || ''} onChange={(event) => setEditing({ ...editing, first_error_step: event.target.value })} /></label>
              <label>错误原因<textarea value={editing.error_analysis || ''} onChange={(event) => setEditing({ ...editing, error_analysis: event.target.value })} /></label>
              <label>正确思路<textarea value={editing.correct_approach || ''} onChange={(event) => setEditing({ ...editing, correct_approach: event.target.value })} /></label>
              <KnowledgeSelect value={editing} catalog={syllabus} onChange={setEditing} />
              <TagEditor value={editing} onChange={setEditing} />
              <label>错误大类
                <select value={primaryErrorType(editing.error_types)} onChange={(event) => setEditing({ ...editing, error_types: [event.target.value] })}>
                  {ERROR_TYPE_OPTIONS.map((name) => <option key={name} value={name}>{name}</option>)}
                </select>
              </label>
              <label>代码 / 结构图<textarea className="codeinput" value={joinBlocks(editing.code_blocks)} onChange={(event) => setEditing({ ...editing, code_blocks: splitBlocks(event.target.value) })} /></label>
              <button className="primary wide" disabled={busy} onClick={saveQuestion}>保存修改</button>
              <button className="wide" onClick={() => setEditing(null)}>取消</button>
            </>
          )}
        </section>
      )}

      {tab === 'review' && (
        <>
          <div className="segmented">
            <button className={reviewScope === 'today' ? 'active' : ''} onClick={() => { setReviewScope('today'); loadReviews('today'); }}>今日</button>
            <button className={reviewScope === 'overdue' ? 'active' : ''} onClick={() => { setReviewScope('overdue'); loadReviews('overdue'); }}>逾期</button>
          </div>
          <section>
            {reviews.length ? (
              <div className="rows">
                {reviews.map((question) => (
                  <div key={question.id} style={{ padding: '16px 0', borderBottom: '1px solid var(--border)' }}>
                    <div className="row" style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                      <span className="ring" style={{ '--ring-color': subjectColor(question.subject) } as React.CSSProperties} />
                      <span className="rowmain">
                        <span className="rowtitle">{compactTitle(question)}</span>
                        <span className="rowmeta">
                          <span className="chip">{subjectName(question.subject)}</span>
                          <span className="chip">{question.chapter || '未分类'}</span>
                        </span>
                      </span>
                    </div>
                    <QuestionContent question={question} />
                    {question.review_prompt && <RichText text={'提示：' + question.review_prompt} />}
                    <div className="review-actions">
                      {REVIEW_BUTTONS.map(([rating, label]) => (
                        <button key={rating} onClick={() => review(question.id, rating)}>{label}</button>
                      ))}
                    </div>
                    <button className="ghost" onClick={() => openQuestion(question.id)}>查看详情</button>
                  </div>
                ))}
              </div>
            ) : <Empty emoji="✅" text={reviewScope === 'overdue' ? '没有逾期任务' : '今天没有要复习的题目'} />}
          </section>
        </>
      )}

      {tab === 'practice' && practiceQuestion && (
        <section>
          <div className="cardhead"><h3>独立重做</h3><button className="ghost" onClick={() => openQuestion(practiceQuestion.id)}>返回详情</button></div>
          <p className="muted">先自己完成，再拍照上传。AI 会对照历史错误判断这次是否重犯。</p>
          <QuestionContent question={practiceQuestion} />
          {!!practiceQuestion.images?.length && <details><summary>查看原题图片（可能包含答案）</summary><div className="practice-previews">{practiceQuestion.images.map((item: any) => <a key={item.id} href={'/api/tasks/images/' + item.id} target="_blank" rel="noreferrer"><img src={'/api/tasks/images/' + item.id} alt="原题" /></a>)}</div></details>}
          <div className="practice-actions">
            <label className="file-picker"><span>📷 拍照</span><input type="file" accept="image/*" capture="environment" onChange={(event) => { const picked = Array.from(event.target.files || []); setPracticeFiles((current) => [...current, ...picked]); event.target.value = ''; }} /></label>
            <label className="file-picker"><span>🖼️ 相册多选</span><input type="file" accept="image/*" multiple onChange={(event) => { const picked = Array.from(event.target.files || []); setPracticeFiles((current) => [...current, ...picked]); event.target.value = ''; }} /></label>
          </div>
          <div className="practice-previews">{practiceFiles.map((file, index) => <div key={index}>{imageEdits.has(file) ? <img src={imageEdits.get(file)!.preview} alt="已选识别范围" /> : <LocalImage file={file} />}<button disabled={busy} className="ghost" onClick={() => setCropFile(file)}>裁剪 / 旋转</button><button disabled={busy} className="ghost" onClick={() => setPracticeFiles((items) => items.filter((_, i) => i !== index))}>移除</button></div>)}</div>
          <button className="primary wide" disabled={busy || !practiceFiles.length || !!practiceTask} onClick={uploadPractice}>提交本次重做</button>
          {practiceTask && <div className="statusrow"><span>AI 状态</span><b>{statusText(practiceTask.status)}</b></div>}
          {practiceTask?.error && <div className="error">{practiceTask.error}</div>}
          {practiceTask?.status === 'failed' && <button onClick={async () => { try { setPracticeTask(await jsonPost('/tasks/' + practiceTask.id + '/retry', {})); } catch (err: any) { setError(err.message); } }}>重试分析（原图已保存）</button>}
          {practiceTask?.result && <div className="feedback"><h3>本次纠错</h3><Formula value={practiceTask.result.user_solution_latex} /><RichText text={practiceTask.result.first_error_step} /><RichText text={practiceTask.result.error_analysis} /><RichText text={practiceTask.result.correct_approach} /><Formula value={practiceTask.result.reference_solution_latex} />{practiceTask.result.code_blocks?.map((code: string, i: number) => <pre key={i}><code>{code}</code></pre>)}{!!practiceTask.result.uncertain_parts?.length && <p className="uncertain">不确定：{joinList(practiceTask.result.uncertain_parts)}</p>}</div>}
          {practiceTask?.practice_attempt && practiceTask.status === 'waiting_confirmation' && <div className="feedback">
            <p className="muted">核对 AI 判断，可手动修正，再选择复习结果保存。</p>
            <label>本次作答<select value={attemptGrade.outcome} onChange={(e) => setAttemptGrade({ ...attemptGrade, outcome: e.target.value })}>{[['correct', '完整正确'], ['partial', '部分正确'], ['wrong', '错误'], ['uncertain', '不确定']].map(([v, label]) => <option key={v} value={v}>{label}</option>)}</select></label>
            <label>是否重复历史同一处错误<select value={attemptGrade.same_error} onChange={(e) => setAttemptGrade({ ...attemptGrade, same_error: e.target.value })}><option value="unknown">不确定</option><option value="true">是</option><option value="false">否</option></select></label>
            <div className="review-actions">{REVIEW_BUTTONS.map(([rating, label]) => <button disabled={busy} key={rating} onClick={() => confirmPractice(practiceTask.practice_attempt, rating)}>{label}</button>)}</div>
          </div>}
          {practiceTask?.status === 'confirmed' && <button onClick={() => setPracticeTask(null)}>开始新的重做</button>}
          {!!practiceHistory.length && <details><summary>历史作答与追加资料 · {practiceHistory.length} 次</summary>{practiceHistory.map((item) => <div className="statusrow" key={item.id}><span>{new Date(item.created_at).toLocaleString()}<br /><small className="muted">{item.source === 'duplicate' ? '追加资料' : ({ correct: '正确', partial: '部分正确', wrong: '错误', uncertain: '不确定' } as Record<string, string>)[item.outcome] || statusText(item.status)}</small></span><button onClick={async () => { try { setPracticeTask(await api('/tasks/' + item.task_id)); } catch (err: any) { setError(err.message); } }}>查看</button></div>)}</details>}
        </section>
      )}

      {tab === 'trash' && (
        <section>
          <div className="cardhead"><h3>回收站</h3><button className="ghost" onClick={() => go('list')}>返回错题本</button></div>
          <p className="muted">删除的错题会保留原图和复习记录，恢复后可继续使用。</p>
          {trash.length ? trash.map((item) => <div className="statusrow" key={item.id}><span>{subjectName(item.subject)} · {item.chapter || '未分类'}<br /><small className="muted">删除于 {new Date(item.deleted_at).toLocaleString()}</small></span><button onClick={async () => { try { await api(`/learning/trash/${item.id}/restore`, { method: 'POST' }); loadTrash(); loadQuestions(); setNotice('错题已恢复'); } catch (err: any) { setError(err.message); } }}>恢复</button></div>) : <Empty emoji="🗂" text="回收站是空的" />}
        </section>
      )}

      {tab === 'stats' && (
        <>
          <div className="tabs">
            {[['dist', '分布'], ['tags', '标签'], ['trend', '趋势'], ['weekly', '周报']].map(([key, label]) => (
              <button key={key} className={statsTab === key ? 'active' : ''} onClick={() => setStatsTab(key)}>{label}</button>
            ))}
          </div>
          <div className="metrics" style={{ marginBottom: 16 }}>
            <div className="metric"><b>{overview?.total ?? 0}</b><span>错题总数</span></div>
            <div className="metric"><b>{overview?.due ?? 0}</b><span>待复习</span></div>
            <div className="metric"><b>{overview?.overdue ?? 0}</b><span>逾期</span></div>
            <div className="metric"><b>{Math.round((overview?.average_mastery ?? 0) * 100)}%</b><span>平均掌握度</span></div>
            <div className="metric"><b>{Math.round((accuracy?.recognition?.rate ?? 0) * 100)}%</b><span>AI 识别正确率</span></div>
          </div>

          {statsTab === 'dist' && (
            <>
              <section>
                <h3>科目分布</h3>
                <p className="muted">不同科目的错题占比。</p>
                <Donut items={subjectItems} />
                <Legend items={subjectItems} unit="题" />
              </section>
              <section>
                <h3>错误类型分布</h3>
                <p className="muted">按错误标签统计的分布情况。</p>
                {errorItems.length ? <><Donut items={errorItems} /><Legend items={errorItems} unit="次" /></> : <Empty emoji="🏷" text="还没有错误标签" />}
              </section>
              <section>
                <h3>难度分布</h3>
                <Legend items={difficultyItems} unit="题" />
              </section>
            </>
          )}

          {statsTab === 'tags' && (
            <>
              <section>
                <h3>考纲知识点</h3>
                <select value={syllabusSubject} onChange={(event) => setSyllabusSubject(event.target.value)}>
                  {subjectEditOptions.filter((option) => syllabus[option.value]).map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                </select>
                <div className="rows" style={{ marginTop: 12 }}>
                  {[...syllabusEntries].sort((left, right) => right.count - left.count || left.name.localeCompare(right.name)).map((item) => (
                    <button className="rowitem" key={item.name} onClick={() => showKnowledge(item.name, syllabusSubject)}>
                      <span className="rowmain">
                        <span className="rowtitle">{item.name}</span>
                        <span className="rowmeta"><span className="chip">{item.chapter}</span><span className="chip">{item.count} 道错题</span></span>
                      </span>
                      <span className="chev">›</span>
                    </button>
                    
                  ))}
                </div>
              </section>
              <section>
                <h3>已记录知识点</h3>
                {knowledgeItems.length ? knowledgeItems.map((item) => (
                  <div className="tagrow" key={item.name} style={{ '--tag-color': item.color } as React.CSSProperties}>
                    <span className="hash">#</span>
                    <span className="tagname">{item.name}</span>
                    <span className="tagdot" />
                    <span className="tagnum">{item.count}</span>
                    <span className="tagright">
                      <button className="ghost" onClick={() => showKnowledge(item.name)}>查看 ›</button>
                    </span>
                  </div>
                )) : <Empty emoji="🏷" text="还没有知识点数据" />}
              </section>
              <section>
                <h3>错误标签</h3>
                {errorItems.length ? errorItems.map((item) => (
                  <div className="tagrow" key={item.name} style={{ '--tag-color': item.color } as React.CSSProperties}>
                    <span className="hash">#</span>
                    <span className="tagname">{item.name}</span>
                    <span className="tagdot" />
                    <span className="tagnum">{item.count}</span>
                    <span className="tagright">
                      <button className="ghost" onClick={() => { setFilter({ ...filter, error_type: item.name }); go('list'); }}>查看 ›</button>
                    </span>
                  </div>
                )) : <Empty emoji="🏷" text="还没有错误标签" />}
              </section>
            </>
          )}

          {statsTab === 'trend' && (
            <section>
              <h3>活跃趋势</h3>
              <p className="muted">最近 30 天新增的错题数量。</p>
              <TrendBars counts={last30} />
              <div className="trendaxis"><span>30 天前</span><span>今天</span></div>
            </section>
          )}
          {statsTab === 'weekly' && weekly && (
            <>
              <section><h3>{weekOffset ? '历史' : '本周'}薄弱点报告</h3><div className="practice-actions"><button disabled={weekOffset >= 52} onClick={() => setWeekOffset((v) => v + 1)}>上一周</button><button disabled={!weekOffset} onClick={() => setWeekOffset((v) => v - 1)}>下一周</button></div><p className="muted">{weekly.start} 至 {weekly.end}</p><div className="metrics"><div className="metric"><b>{weekly.new_questions}</b><span>新增错题</span></div><div className="metric"><b>{weekly.review_count}</b><span>复习次数</span></div><div className="metric"><b>{weekly.improved_questions}</b><span>重做改善</span></div><div className="metric"><b>{weekly.repeated_errors}</b><span>重复错误</span></div></div></section>
              <section><h3>优先复习知识点</h3>{weekly.knowledge?.length ? weekly.knowledge.slice(0, 8).map((item: any) => <div className="statusrow" key={item.subject + item.name}><span>{subjectName(item.subject)} · {item.name}</span><b>{item.count} 题</b></div>) : <Empty emoji="📈" text="本周还没有形成薄弱点数据" />}</section>
              <section><h3>重做结果</h3><div className="statusrow"><span>可判定 / 已确认重做</span><b>{weekly.practice?.graded || 0} / {weekly.practice?.total || 0}</b></div><div className="statusrow"><span>正确率</span><b>{weekly.practice?.accuracy == null ? '暂无' : Math.round(weekly.practice.accuracy * 100) + '%'}</b></div><div className="statusrow"><span>前一周正确率</span><b>{weekly.previous_practice?.accuracy == null ? '暂无' : Math.round(weekly.previous_practice.accuracy * 100) + '%'}</b></div><p className="muted">{weekly.basis}</p></section>
              <section><h3>常见错误</h3>{weekly.errors?.length ? weekly.errors.map((item: any) => <div className="statusrow" key={item.name}><span>{item.name}</span><b>{item.count} 次</b></div>) : <p className="muted">暂无错误记录</p>}</section>
            </>
          )}
        </>
      )}

      {tab === 'status' && (
        <>
          <section>
            <h3>运行状态</h3>
            <div className="statusrow"><span>后端</span><b className={status?.backend ? 'good' : 'bad'}>{status?.backend ? '正常' : '异常'}</b></div>
            <div className="statusrow"><span>数据库</span><b className={status?.database ? 'good' : 'bad'}>{status?.database ? '正常' : '异常'}</b></div>
            <div className="statusrow"><span>磁盘空间</span><b className={status?.disk_ok ? 'good' : 'bad'}>{status?.disk_ok ? `${Math.round((status?.disk_free_bytes || 0) / 1024 / 1024 / 1024)} GB 可用` : '不足'}</b></div>
            <div className="statusrow"><span>Tailscale</span><b className={status?.tailscale_running ? 'good' : 'bad'}>{status?.tailscale_running ? '已连接' : '未连接'}</b></div>
            {status?.tailscale_https && status?.tailscale_message && <a className="statuslink" href={status.tailscale_message.replace('HTTPS 已配置：', '')}>手机 HTTPS 地址</a>}
            <div className="statusrow"><span>AI 模型</span><b>{status?.ai_model || '-'}</b></div>
            <div className="statusrow"><span>自动备份</span><b>{status?.auto_backup_hours ? `每 ${status.auto_backup_hours} 小时` : '已关闭'}</b></div>
          </section>
          {status?.disk_message && <div className="error">{status.disk_message}</div>}
          {!status?.tailscale_running && status?.tailscale_message && <div className="error">{status.tailscale_message}</div>}

          <section>
            <h3>AI 视觉自检</h3>
            <p className="muted">现场生成已知答案的算式图片，让模型识别后自动比对。</p>
            <button className="primary wide" disabled={selfTest?.running || !status?.api_key_configured} onClick={runSelfTest}>
              {selfTest?.running ? '正在测试…' : '测试 AI 识别能力'}
            </button>
            {selfTest?.data && (
              <>
                <div className="statusrow"><span>本次正确率</span><b className={selfTest.data.accuracy >= 0.8 ? 'good' : 'bad'}>{Math.round((selfTest.data.accuracy || 0) * 100)}%</b></div>
                <div className="statusrow"><span>通过</span><b>{selfTest.data.passed} / {selfTest.data.cases}</b></div>
                {selfTest.data.results?.map((item: any, index: number) => (
                  <p className="muted" key={index}>第 {index + 1} 题：{item.ok ? '正确' : '失败'} · 期望 {item.expression}={item.expected} · 返回 {item.answer || item.error}</p>
                ))}
              </>
            )}
          </section>

          <section>
            <h3>数据与备份</h3>
            <button className="primary wide" onClick={async () => {
              try {
                const created = await api('/system/backup', { method: 'POST' });
                setNotice('备份已创建：' + created.path);
                loadBackups();
              } catch (err: any) { setError(err.message); }
            }}>立即备份</button>
            <button className="wide" disabled={busy} onClick={async () => {
              setBusy(true); setError('');
              try {
                const result = await api('/system/export-bundle', { method: 'POST' });
                setNotice(`已导出 ${result.questions} 道错题、${result.images} 张原图：${result.path}`);
              } catch (err: any) { setError(err.message); } finally { setBusy(false); }
            }}>导出数据与原图（zip）</button>
            <button className="wide" onClick={async () => {
              try {
                const result = await api('/system/integrity-check');
                setNotice(result.ok ? '完整性检查通过' : result.issues.join('；'));
              } catch (err: any) { setError(err.message); }
            }}>完整性检查</button>
            <p className="muted">最近一次备份：{status?.last_backup_at ? new Date(status.last_backup_at).toLocaleString() : '还没有备份'}</p>
            {backups.slice(0, 5).map((item) => (
              <div className="statusrow" key={item.name}>
                <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{item.name}</span>
                <b>{(item.size / 1024).toFixed(0)} KB</b>
              </div>
            ))}
            <details>
              <summary>原始状态数据</summary>
              <pre>{JSON.stringify(status, null, 2)}</pre>
            </details>
          </section>
        </>
      )}

      {['home', 'list'].includes(tab) && (
        <button className="fab" onClick={() => go('upload')} title="上传错题">+</button>
      )}

      <nav>
        {NAV_ITEMS.map(([key, label]) => (
          <button
            key={key}
            className={tab === key || (key === 'upload' && (tab === 'task' || tab === 'practice')) || (key === 'list' && (tab === 'detail' || tab === 'trash')) ? 'active' : ''}
            onClick={() => go(key)}
          ><NavIcon name={key} /><span>{label}</span></button>
        ))}
      </nav>
    </main>
  );
}

createRoot(document.getElementById('root')!).render(
  <Suspense fallback={<main className="app-shell"><p className="muted">正在加载公式渲染器…</p></main>}>
    <App />
  </Suspense>,
);


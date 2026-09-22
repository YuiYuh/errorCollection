import React from 'react';

// Match the stored date used by daily numbering and the date filters.
export function groupByDate<T extends { created_at?: string }>(items: T[], ascending = false) {
  const groups = new Map<string, T[]>();
  for (const item of items) {
    const date = item.created_at?.match(/^\d{4}-\d{2}-\d{2}/)?.[0] || '';
    if (!groups.has(date)) groups.set(date, []);
    groups.get(date)!.push(item);
  }
  return [...groups].sort(([a], [b]) => !a ? 1 : !b ? -1 : ascending ? a.localeCompare(b) : b.localeCompare(a));
}

export function DateArchive<T extends { created_at?: string }>({ items, ascending, renderItem }: {
  items: T[]; ascending?: boolean; renderItem: (item: T) => React.ReactNode;
}) {
  return <div className="date-archive">{groupByDate(items, ascending).map(([date, questions]) => (
    <details className="archive-group" key={date}>
      <summary><span>{date ? date.replace(/^(\d+)-(\d+)-(\d+)$/, (_, y, m, d) => `${y}年${Number(m)}月${Number(d)}日`) : '日期未知'}</span><span className="archive-count">{questions.length} 题</span></summary>
      <div className="rows">{questions.map(renderItem)}</div>
    </details>
  ))}</div>;
}

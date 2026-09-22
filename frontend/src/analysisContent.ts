type Analysis = { user_solution_latex?: string; reference_solution_latex?: string; first_error_step?: string; error_analysis?: string; correct_approach?: string; uncertain_parts?: string[]; needs_review?: boolean };

// Be conservative: only collapse paragraphs consisting entirely of source-availability
// notices. Any calculation, specific error or uncertainty remains in the main content.
export function availabilityOnly(text?: string): boolean {
  if (!text?.trim() || /[0-9\\=]/.test(text)) return false;
  const clauses = text.split(/[，。；：\n]/).map(s => s.trim()).filter(Boolean);
  return clauses.length > 0 && clauses.every(clause => [
    /^(?:图片中|当前|本次)?(?:未|没有)(?:提供|上传|看到)(?:用户|单独的?|明确的?)?(?:解题过程|作答|参考答案|答案|步骤)(?:和参考答案)?$/,
    /^(?:当前|目前)?(?:仅有|只有|仅提供了?)(?:题目|题干|原题)(?:图片|照片)?$/,
    /^(?:也|因此|故|目前|暂时)?(?:无法|不能)(?:逐字识别(?:用户步骤|用户过程)?|进行逐字识别和直接对照|直接对照|定位(?:用户计算中的具体错误|具体错误|第一处实质性错误)?)$/,
    /^以下(?:纠正结论|纠错结论|解答|结论)由题干独立推导得到$/,
  ].some(pattern => pattern.test(clause)));
}

export function analysisPresentation(result: Analysis) {
  const labels = [
    ['first_error_step', '第一处错误'], ['error_analysis', '错误原因'],
    ['correct_approach', '正确思路'], ['user_solution_latex', '我的过程'],
    ['reference_solution_latex', '参考答案'],
  ] as const;
  const seen = new Set<string>();
  const sections: { field: string; label: string; value: string; secondary: boolean }[] = [];
  const notices: { label: string; value: string }[] = [];
  const uncertainty: string[] = [];
  for (const [field, label] of labels) {
    const value = result[field]?.trim();
    if (!value) continue;
    const key = value.replace(/\s+/g, '');
    if (seen.has(key)) continue;
    seen.add(key);
    if (availabilityOnly(value)) { notices.push({ label, value }); continue; }
    sections.push({ field, label, value, secondary: field === 'user_solution_latex' || field === 'reference_solution_latex' });
  }
  for (const value of result.uncertain_parts || []) {
    if (typeof value !== 'string' || !value.trim()) continue;
    const key = value.replace(/\s+/g, '');
    if (seen.has(key)) continue;
    seen.add(key);
    if (availabilityOnly(value)) notices.push({ label: '材料限制', value });
    else uncertainty.push(value);
  }
  const missingProcess = notices.some(item => /(?:未|没有)提供(?:用户)?解题过程|无法定位/.test(item.value));
  return { sections, notices, uncertainty, notice: notices.length ? (missingProcess ? '缺少可核对的解题过程，暂不能定位具体错误。' : '部分材料未提供，分析依据与限制见下方说明。') : '' };
}

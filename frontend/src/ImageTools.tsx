import React, { useEffect, useRef, useState } from 'react';

export type ImageEdit = { rotation: number; x: number; y: number; width: number; height: number };
export type EditedImage = { edit: ImageEdit; preview: string };
const full = { x: 0, y: 0, width: 1, height: 1 };

export function CropEditor({ file, initial, onSave, onClose }: {
  file: File; initial?: EditedImage; onSave: (value: EditedImage | null) => void; onClose: () => void;
}) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const anchor = useRef<{ x: number; y: number } | null>(null);
  const [source, setSource] = useState<HTMLImageElement | null>(null);
  const [rotation, setRotation] = useState(initial?.edit.rotation || 0);
  const [crop, setCrop] = useState(initial?.edit || full);
  const [error, setError] = useState('');
  const closeButton = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    closeButton.current?.focus();
    const url = URL.createObjectURL(file);
    const image = new Image();
    image.onload = () => setSource(image);
    image.onerror = () => setError('浏览器无法预览此格式。可直接上传原图，或改用 JPEG / PNG。');
    image.src = url;
    return () => { image.onload = null; image.onerror = null; URL.revokeObjectURL(url); };
  }, [file]);
  useEffect(() => {
    if (!source || !canvas.current) return;
    const c = canvas.current;
    const scale = Math.min(1, 1600 / Math.max(source.naturalWidth, source.naturalHeight));
    const w = Math.round(source.naturalWidth * scale), h = Math.round(source.naturalHeight * scale);
    c.width = rotation % 180 ? h : w; c.height = rotation % 180 ? w : h;
    const ctx = c.getContext('2d')!;
    ctx.translate(c.width / 2, c.height / 2); ctx.rotate(rotation * Math.PI / 180);
    ctx.drawImage(source, -w / 2, -h / 2, w, h);
  }, [source, rotation]);
  const point = (event: React.PointerEvent) => {
    const rect = event.currentTarget.getBoundingClientRect();
    return { x: Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width)), y: Math.min(1, Math.max(0, (event.clientY - rect.top) / rect.height)) };
  };
  const save = () => {
    if (!canvas.current || crop.width < .01 || crop.height < .01) { setError('请框选更大的区域'); return; }
    const c = canvas.current, preview = document.createElement('canvas');
    preview.width = Math.max(1, Math.round(c.width * crop.width)); preview.height = Math.max(1, Math.round(c.height * crop.height));
    preview.getContext('2d')!.drawImage(c, crop.x * c.width, crop.y * c.height, crop.width * c.width, crop.height * c.height, 0, 0, preview.width, preview.height);
    onSave({ edit: { ...crop, rotation }, preview: preview.toDataURL('image/jpeg', .85) });
  };
  return <div className="image-modal" role="dialog" aria-modal="true" aria-label="裁剪与旋转" onKeyDown={(event) => { if (event.key === 'Escape') onClose(); }}>
    <div className="image-modal-card">
      <div className="cardhead"><h2>裁剪与旋转</h2><button ref={closeButton} onClick={onClose}>关闭</button></div>
      <p className="muted">在图片上拖动框选题目。旋转后请重新框选；原图始终保留。</p>
      {error && <p role="alert" className="error">{error}</p>}
      {!source && !error && <p>正在加载图片…</p>}
      <div className="crop-stage" style={{ display: source ? 'block' : 'none' }}
        onPointerDown={(event) => { event.currentTarget.setPointerCapture(event.pointerId); anchor.current = point(event); }}
        onPointerMove={(event) => { if (!anchor.current) return; const end = point(event), start = anchor.current; setCrop({ x: Math.min(start.x, end.x), y: Math.min(start.y, end.y), width: Math.abs(end.x - start.x), height: Math.abs(end.y - start.y) }); }}
        onPointerUp={() => { anchor.current = null; }} onPointerCancel={() => { anchor.current = null; }}>
        <canvas ref={canvas} />
        <div className="crop-selection" style={{ left: crop.x * 100 + '%', top: crop.y * 100 + '%', width: crop.width * 100 + '%', height: crop.height * 100 + '%' }} />
      </div>
      {source && <>
        <div className="practice-actions"><button onClick={() => { setRotation((rotation + 270) % 360); setCrop(full); }}>向左旋转</button><button onClick={() => { setRotation((rotation + 90) % 360); setCrop(full); }}>向右旋转</button><button onClick={() => setCrop(full)}>选择整张</button></div>
        <details><summary>精确调整范围</summary>{(['x', 'y', 'width', 'height'] as const).map((key) => <label key={key}>{({ x: '左边位置', y: '顶部位置', width: '宽度', height: '高度' })[key]} · {Math.round(crop[key] * 100)}%
          <input type="range" min={key === 'x' || key === 'y' ? 0 : .01} max={key === 'x' ? 1 - crop.width : key === 'y' ? 1 - crop.height : key === 'width' ? 1 - crop.x : 1 - crop.y} step="0.001" value={crop[key]} onChange={(event) => setCrop({ ...crop, [key]: Number(event.target.value) })} />
        </label>)}</details>
        <button className="primary" onClick={save}>使用这个范围</button>
      </>}
      <button onClick={() => onSave(null)}>恢复原图</button>
    </div>
  </div>;
}

export function ImageCompare({ images, children, preferEdited = false }: { images: { id: number; image_type: string; has_edit?: boolean }[]; children?: React.ReactNode; preferEdited?: boolean }) {
  const [index, setIndex] = useState(0);
  // Show the actual captured photo first. The cropped recognition range stays
  // available as an optional comparison, so the source is never mistaken for
  // an AI-generated summary.
  const [edited, setEdited] = useState(preferEdited);
  const [zoom, setZoom] = useState(1);
  const [imageError, setImageError] = useState(false);
  const current = images[Math.min(index, images.length - 1)];
  const url = current ? `/api/tasks/images/${current.id}${edited && current.has_edit ? '?edited=true' : ''}` : '';
  useEffect(() => { setImageError(false); }, [url]);
  useEffect(() => { setEdited(preferEdited); }, [preferEdited, images.length]);
  return <div className={children ? 'compare-grid' : 'image-only'}><div className="compare-source">
    <label>对照图片<select value={index} onChange={(event) => { setIndex(Number(event.target.value)); setZoom(1); }}>{images.map((image, i) => <option key={image.id} value={i}>第 {i + 1} 张 · {({ question: '题目', solution: '解题过程', reference: '参考答案' } as Record<string, string>)[image.image_type] || '其他'}</option>)}</select></label>
    {current?.has_edit && <label className="inline-choice"><input type="checkbox" checked={edited} onChange={(event) => setEdited(event.target.checked)} />显示裁剪后的识别范围</label>}
    <label>图片放大 · {zoom} 倍<input type="range" min="1" max="4" step="0.5" value={zoom} onChange={(event) => setZoom(Number(event.target.value))} /></label>
    <div className="compare-image">{url && <img src={url} alt="对照原图" onError={() => setImageError(true)} style={{ width: zoom * 100 + '%', maxWidth: 'none' }} />}</div>
    {imageError && <p role="alert">图片预览失败，请切换原图查看。</p>}
    {url && <a href={url} target="_blank" rel="noreferrer">单独打开图片</a>}
  </div>{children && <div className="compare-result">{children}</div>}</div>;
}

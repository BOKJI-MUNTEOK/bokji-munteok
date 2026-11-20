/* Exact-file preview. All dependencies are bundled; no telemetry or storage. */
const MAX_BYTES = 20 * 1024 * 1024;
const MAX_PAGES = 40;
const MAX_SVG_CHARS = 16 * 1024 * 1024;
const pages = document.querySelector('#pages');
const viewport = document.querySelector('#viewport');
const status = document.querySelector('#status');
const previous = document.querySelector('#previous');
const next = document.querySelector('#next');
const zoom = document.querySelector('#zoom');
const print = document.querySelector('#print');
const counter = document.querySelector('#page-counter');
const openWindow = document.querySelector('#open-window');
let activeId = '', generation = 0, currentPage = 0, pageNodes = [], hwp = null, pdf = null;
let hwpRuntime = null;
let nativePdfFrame = null, nativePdfUrl = null, nativePdfLoaded = null;
let focusTexts = [], wantedPages = [];
const printRules = document.createElement('style');
document.head.append(printRules);

function message(type, details = {}) {
  window.parent.postMessage({isStreamlitMessage: true, type, ...details}, '*');
}
function value(args, state) {
  message('streamlit:setComponentValue', {value: {request_id: args.request_id, format: args.format, ...state}, dataType: 'json'});
}
function frameHeight() { message('streamlit:setFrameHeight', {height: document.body.scrollHeight + 8}); }
function updatePage() {
  pageNodes.forEach((node, i) => node.classList.toggle('current', i === currentPage));
  counter.textContent = pageNodes.length ? `${currentPage + 1} / ${pageNodes.length}쪽` : '서류 여는 중';
  previous.disabled = currentPage <= 0;
  next.disabled = currentPage >= pageNodes.length - 1;
  const width = Math.max(240, viewport.clientWidth - (window.innerWidth < 520 ? 14 : 30));
  for (const node of pageNodes) {
    node.style.width = `${zoom.value === 'fit' ? width : Number(node.dataset.width) * Number(zoom.value)}px`;
  }
  viewport.scrollTo(0, 0);
}
previous.addEventListener('click', () => {currentPage--; updatePage();});
next.addEventListener('click', () => {currentPage++; updatePage();});
zoom.addEventListener('change', updatePage);
print.addEventListener('click', async () => {
  if (!pageNodes.length) return;
  if (!nativePdfFrame) { window.print(); return; }
  // Print the original PDF. Screen canvases are deliberately bounded previews,
  // and are never substituted for the PDF's fonts, vectors or paper geometry.
  document.querySelector('#print-fallback').hidden = false;
  try {
    await Promise.race([nativePdfLoaded, new Promise((_, reject) => setTimeout(() => reject(new Error('print_timeout')), 8000))]);
    nativePdfFrame.contentWindow.focus();
    nativePdfFrame.contentWindow.print();
  } catch (_) {
    status.textContent = '이 브라우저에서 인쇄 창을 열지 못했습니다. 아래 링크로 PDF를 저장해서 인쇄해 주세요.';
  }
  frameHeight();
});
// 작은 미리보기 대신 새 탭에서 모든 쪽을 크게 본다. PDF는 브라우저 PDF 화면을 쓴다.
openWindow.addEventListener('click', () => {
  if (!pageNodes.length) return;
  if (nativePdfUrl) { window.open(nativePdfUrl, '_blank'); return; }
  const clone = pages.cloneNode(true);
  for (const node of clone.querySelectorAll('.page')) {
    node.classList.add('current');
    node.style.width = `${Math.round(Number(node.dataset.width) * 1.25)}px`;
  }
  const base = new URL('.', location.href).href;
  const html = `<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">`
    + `<base href="${base}"><link rel="stylesheet" href="viewer.css"><title>작성한 서류 크게 보기</title>`
    + `<style>body{background:#e9edf2;margin:0;padding:16px}.bar{position:sticky;top:0;display:flex;gap:12px;align-items:center;justify-content:space-between;`
    + `background:#f4f7fc;border:1px solid #d1d8e0;border-radius:10px;padding:10px 14px;margin:0 auto 16px;max-width:1100px;z-index:1}`
    + `.bar p{margin:0;color:#464c53}#pages{display:block}.page{display:block!important;max-width:100%;margin:0 auto 18px}`
    + `@media print{.bar{display:none}body{padding:0;background:#fff}.page{margin:0;width:100%!important}}</style></head>`
    + `<body><div class="bar"><p>모든 쪽을 크게 보여 드립니다. 이름·주소·연락처가 맞는지 확인해 주세요.</p>`
    + `<button class="primary" type="button" onclick="window.print()">인쇄하기</button></div>`
    + `<div id="pages">${clone.innerHTML}</div></body></html>`;
  const url = URL.createObjectURL(new Blob([html], {type: 'text/html'}));
  window.open(url, '_blank');
  setTimeout(() => URL.revokeObjectURL(url), 60000);
});
window.addEventListener('resize', () => {updatePage(); frameHeight();});

function sanitizeSvg(source) {
  if (source.length > MAX_SVG_CHARS) throw new Error('page_limit');
  const clean = DOMPurify.sanitize(source, {
    USE_PROFILES: {svg: true, svgFilters: true}, RETURN_DOM_FRAGMENT: true,
    FORBID_TAGS: ['foreignObject', 'script', 'iframe', 'object', 'embed', 'animate', 'set'],
    FORBID_ATTR: ['tabindex'],
  });
  for (const el of clean.querySelectorAll('*')) {
    for (const attr of [...el.attributes]) {
      const name = attr.name.toLowerCase(), v = attr.value.trim();
      if (name === 'href' || name.endsWith(':href')) {
        if (!(v.startsWith('#') || /^data:image\/(png|jpeg|gif|webp);base64,[a-z0-9+/=\s]+$/i.test(v))) el.removeAttribute(attr.name);
      }
      if (name === 'style') {
        el.setAttribute(attr.name, v.replace(/@import[^;]*;?/gi, '').replace(/url\(\s*(['"]?)(?!#)[^)]*\)/gi, 'none'));
      }
    }
  }
  for (const style of clean.querySelectorAll('style')) {
    style.textContent = style.textContent.replace(/@import[^;]*;?/gi, '').replace(/url\(\s*(['"]?)(?!#)[^)]*\)/gi, 'none');
  }
  const svg = clean.querySelector('svg');
  if (!svg) throw new Error('invalid_svg');
  return svg;
}
function normalized(text) {return String(text).replace(/[\s\u200b\u200c\u200d()-]/g,'');}
function addPage(content, width, height, text = '') {
  if (!Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0 || width > 20000 || height > 20000) throw new Error('page_size');
  const page = document.createElement('section');
  page.className = 'page'; page.dataset.width = width; page.dataset.height = height;
  page.dataset.number = String(pageNodes.length + 1);
  const searchable = normalized(text);
  page.dataset.matches = String(focusTexts.filter(marker => searchable.includes(marker)).length);
  page.setAttribute('aria-label', `${pageNodes.length + 1}쪽`);
  page.append(content); pages.append(page); pageNodes.push(page);
  // SVG is generated at an explicit 96dpi. Preserve the physical page size
  // when printing instead of stretching every page to the printer default.
  const mmWidth = (width * 25.4 / 96).toFixed(3), mmHeight = (height * 25.4 / 96).toFixed(3);
  const name = `document_page_${pageNodes.length}`;
  printRules.textContent += `@page ${name}{size:${mmWidth}mm ${mmHeight}mm;margin:0}\n@media print{.page[data-number="${pageNodes.length}"]{page:${name};width:${mmWidth}mm!important;height:${mmHeight}mm!important;max-width:none!important}}\n`;
}
async function renderPdf(data, epoch, filename) {
  nativePdfUrl = URL.createObjectURL(new Blob([data], {type:'application/pdf'}));
  nativePdfFrame = document.createElement('iframe');
  nativePdfFrame.className = 'native-print-frame'; nativePdfFrame.title = '인쇄용 PDF';
  nativePdfFrame.setAttribute('aria-hidden','true'); nativePdfFrame.tabIndex = -1;
  nativePdfLoaded = new Promise(resolve => nativePdfFrame.addEventListener('load',resolve,{once:true}));
  nativePdfFrame.src = nativePdfUrl; document.body.append(nativePdfFrame);
  const fallback = document.querySelector('#print-download');
  fallback.href = nativePdfUrl; fallback.download = filename || '작성한_서류.pdf';
  const pdfjs = await import('./vendor/pdf.mjs');
  pdfjs.GlobalWorkerOptions.workerSrc = new URL('./vendor/pdf.worker.mjs', import.meta.url).href;
  pdf = await pdfjs.getDocument({data, isEvalSupported:false, useSystemFonts:false,
    cMapUrl:new URL('./vendor/cmaps/', import.meta.url).href, cMapPacked:true,
    standardFontDataUrl:new URL('./vendor/standard_fonts/', import.meta.url).href,
    wasmUrl:new URL('./vendor/wasm/', import.meta.url).href,
  }).promise;
  // 긴 PDF는 신청서가 있는 쪽만 그린다. 인쇄·새 창은 원본 PDF 전체를 쓴다.
  const subset = pdf.numPages > MAX_PAGES ? wantedPages.filter(n => n >= 1 && n <= pdf.numPages) : [];
  if (pdf.numPages < 1 || (pdf.numPages > MAX_PAGES && !subset.length)) throw new Error('page_limit');
  const order = subset.length ? subset : Array.from({length: pdf.numPages}, (_, k) => k + 1);
  for (const i of order) {
    if (epoch !== generation) return;
    status.textContent = `서류를 여는 중입니다. ${i} / ${pdf.numPages}쪽`;
    const page = await pdf.getPage(i), natural = page.getViewport({scale:1});
    // Bound total raster memory while retaining useful reading/printing quality.
    const scale = Math.min(1.8, Math.sqrt(30_000_000 / pdf.numPages / (natural.width * natural.height)));
    const view = page.getViewport({scale}), canvas = document.createElement('canvas');
    canvas.width = Math.ceil(view.width); canvas.height = Math.ceil(view.height);
    canvas.setAttribute('role', 'img'); canvas.setAttribute('aria-label', `서류 ${i}쪽`);
    await page.render({canvasContext:canvas.getContext('2d'),viewport:view}).promise;
    if (epoch !== generation) return;
    // PDF units are points (72dpi); convert to CSS units for physical sizing.
    const text = focusTexts.length ? (await page.getTextContent()).items.map(item => item.str || '').join('') : '';
    addPage(canvas,natural.width*96/72,natural.height*96/72,text); page.cleanup();
  }
}
async function renderHwp(data, epoch) {
  const context = document.createElement('canvas').getContext('2d');
  globalThis.measureTextWidth = (font, text) => { context.font = font; return context.measureText(text).width; };
  if (!hwpRuntime) {
    hwpRuntime = import('./vendor/rhwp.js').then(async module => {
      await module.default({module_or_path:new URL('./vendor/rhwp_bg.wasm',import.meta.url).href});
      return module;
    }).catch(error => {hwpRuntime=null; throw error;});
  }
  const module = await hwpRuntime;
  if (epoch !== generation) return;
  hwp = new module.HwpDocument(data);
  hwp.setDpi(96);
  const count = hwp.pageCount();
  if (count < 1 || count > MAX_PAGES) throw new Error('page_limit');
  for (let i=0; i<count; i++) {
    if (epoch !== generation) return;
    status.textContent = `서류를 여는 중입니다. ${i+1} / ${count}쪽`;
    const svg = sanitizeSvg(hwp.renderPageSvg(i));
    const vb = (svg.getAttribute('viewBox') || '').split(/[ ,]+/).map(Number);
    const width = vb.length===4 ? vb[2] : parseFloat(svg.getAttribute('width'));
    const height = vb.length===4 ? vb[3] : parseFloat(svg.getAttribute('height'));
    addPage(svg,width,height,svg.textContent);
    await new Promise(resolve => requestAnimationFrame(resolve));
  }
}
async function render(args, theme) {
  if (!args || typeof args.request_id !== 'string') return;
  document.body.dataset.dark = theme?.base==='dark' || /^#(?:0|1|2)/i.test(theme?.backgroundColor || '') ? 'true' : 'false';
  if (activeId === args.request_id) {requestAnimationFrame(() => {updatePage(); frameHeight();}); return;}
  activeId = args.request_id; const epoch = ++generation;
  focusTexts = (Array.isArray(args.focus_texts) ? args.focus_texts : []).slice(0,4).map(s => normalized(String(s).slice(0,200))).filter(s => s.length>=2);
  wantedPages = (Array.isArray(args.pages) ? args.pages : []).map(Number).filter(Number.isInteger).slice(0, MAX_PAGES);
  nativePdfFrame?.remove(); nativePdfFrame=null; nativePdfLoaded=null;
  if (nativePdfUrl) URL.revokeObjectURL(nativePdfUrl); nativePdfUrl=null;
  document.querySelector('#print-fallback').hidden=true;
  document.querySelector('#print-download').removeAttribute('href');
  printRules.textContent='';
  try {hwp?.free();} catch (_) {} hwp=null;
  try {await pdf?.destroy();} catch (_) {} pdf=null;
  if (epoch !== generation) return;
  pageNodes=[]; currentPage=0; pages.replaceChildren();
  previous.disabled=next.disabled=zoom.disabled=print.disabled=openWindow.disabled=true;
  status.className=''; status.textContent='서류를 열고 있습니다.';
  document.querySelector('#compatibility').hidden = !['hwp','hwpx'].includes(args.format);
  value(args,{status:'loading',page_count:0}); frameHeight();
  try {
    if (!['pdf','hwp','hwpx'].includes(args.format)) throw new Error('unsupported');
    if (typeof args.data !== 'string' || args.data.length > Math.ceil(MAX_BYTES*4/3)+4) throw new Error('size_limit');
    const binary=atob(args.data);
    if (!binary.length || binary.length>MAX_BYTES) throw new Error('size_limit');
    const data=Uint8Array.from(binary,c=>c.charCodeAt(0));
    await document.fonts.load('16px ViewerGothic');
    await document.fonts.load('16px "Nanum Gothic"');
    await document.fonts.load('16px "Noto Sans KR"');
    await document.fonts.load('16px "Noto Serif KR"');
    await document.fonts.ready;
    if (epoch!==generation) return;
    if (args.format==='pdf') await renderPdf(data,epoch,args.filename); else await renderHwp(data,epoch);
    if (epoch!==generation) return;
    if (!pageNodes.length) throw new Error('empty_document');
    const bestMatch = Math.max(...pageNodes.map(node => Number(node.dataset.matches)));
    if (bestMatch>0) currentPage=pageNodes.findIndex(node => Number(node.dataset.matches)===bestMatch);
    zoom.disabled=print.disabled=openWindow.disabled=false; updatePage();
    status.textContent=(bestMatch>0 ? `작성한 내용이 있는 ${currentPage+1}쪽부터 보여 드립니다. ` : `${pageNodes.length}쪽을 열었습니다. `)+'이름과 주소가 맞고 글씨가 겹치지 않는지 확인해 주세요.'
      +(pdf && pdf.numPages>pageNodes.length ? ` 긴 서류라 신청서가 있는 ${pageNodes.length}쪽만 보여 드립니다(전체 ${pdf.numPages}쪽은 ‘새 창에서 크게 보기’).` : '');
    value(args,{status:'ready',page_count:pageNodes.length,focused_page:bestMatch>0?currentPage+1:null}); frameHeight();
  } catch (error) {
    if (epoch!==generation) return;
    pageNodes=[]; pages.replaceChildren();
    nativePdfFrame?.remove(); nativePdfFrame=null;
    if(nativePdfUrl) URL.revokeObjectURL(nativePdfUrl); nativePdfUrl=null;
    document.querySelector('#print-fallback').hidden=true;
    status.className='error';
    status.textContent=['page_limit','size_limit','page_size'].includes(error?.message)
      ? '서류가 크거나 쪽수가 많아 화면에 표시할 수 없습니다. 파일을 저장해서 확인해 주세요.'
      : '이 서류는 화면에 표시하지 못했습니다. 파일을 저장해서 한글 또는 PDF 프로그램으로 확인해 주세요.';
    counter.textContent='미리보기 불가';
    value(args,{status:error?.message==='unsupported'?'unsupported':'error',page_count:0,reason:'render_failed'}); frameHeight();
  }
}
window.addEventListener('message', event => {
  if (event.source!==window.parent || event.data?.type!=='streamlit:render') return;
  render(event.data.args,event.data.theme);
});
message('streamlit:componentReady',{apiVersion:1}); frameHeight();

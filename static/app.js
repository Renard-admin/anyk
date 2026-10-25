'use strict';

const uploadZone        = document.getElementById('uploadZone');
const fileInput         = document.getElementById('fileInput');
const cameraBtn         = document.getElementById('cameraBtn');
const cameraInput       = document.getElementById('cameraInput');
const uploadPlaceholder = document.getElementById('uploadPlaceholder');
const uploadPreview     = document.getElementById('uploadPreview');
const previewImg        = document.getElementById('previewImg');
const pdfPreviewIcon    = document.getElementById('pdfPreviewIcon');
const fileName          = document.getElementById('fileName');
const removeBtn         = document.getElementById('removeBtn');
const submitBtn         = document.getElementById('submitBtn');
const formSection       = document.getElementById('formSection');
const progressScreen    = document.getElementById('progressScreen');
const analyzeForm       = document.getElementById('analyzeForm');
const uploadError       = document.getElementById('uploadError');
const pasteHint         = document.getElementById('pasteHint');
const mobileCaptureHint = document.getElementById('mobileCaptureHint');

let selectedFile = null;

// ── Helpers ──────────────────────────────────────────────────────────────────

function showError(msg) {
  uploadError.textContent = msg;
  uploadError.hidden = false;
}

function clearError() {
  uploadError.textContent = '';
  uploadError.hidden = true;
}

function isPdf(file) {
  return (
    file.type === 'application/pdf' ||
    file.name.toLowerCase().endsWith('.pdf')
  );
}

function isAcceptedType(file) {
  const allowed = ['image/png', 'image/jpeg', 'image/webp', 'image/bmp', 'image/tiff', 'application/pdf'];
  if (allowed.includes(file.type)) return true;
  // Accept empty / generic MIME when extension identifies the format
  // (common when dragging from Windows Explorer or other windows)
  const ext = file.name.toLowerCase().split('.').pop();
  if (ext === 'pdf') return true;
  return ['png', 'jpg', 'jpeg', 'webp', 'bmp', 'tiff'].includes(ext);
}

// ── Show/hide mobile camera hint ─────────────────────────────────────────────

function isMobileDevice() {
  return /Mobi|Android|iPhone|iPad/i.test(navigator.userAgent) ||
         (window.innerWidth <= 768 && ('ontouchstart' in window));
}

if (mobileCaptureHint) {
  mobileCaptureHint.style.display = isMobileDevice() ? 'flex' : 'none';
}

// ── Paste hint: show Cmd+V on macOS ──────────────────────────────────────────

if (pasteHint) {
  const isMac = /Mac|iPhone|iPad/.test(navigator.platform || '');
  const kbdEl = pasteHint.querySelector('kbd');
  if (kbdEl && isMac) kbdEl.textContent = '⌘+V';
}

// ── File selection ────────────────────────────────────────────────────────────

function setFile(file) {
  clearError();

  if (!isAcceptedType(file)) {
    showError('Формат не поддерживается. Загрузите PNG, JPG, WEBP или PDF.');
    return;
  }
  if (file.size > 20 * 1024 * 1024) {
    showError('Файл слишком большой. Максимум 20 МБ.');
    return;
  }

  selectedFile = file;

  if (isPdf(file)) {
    // PDF preview: show icon, not image
    previewImg.hidden = true;
    pdfPreviewIcon.hidden = false;
  } else {
    // Image preview
    const reader = new FileReader();
    reader.onload = (e) => {
      previewImg.src = e.target.result;
    };
    reader.readAsDataURL(file);
    previewImg.hidden = false;
    pdfPreviewIcon.hidden = true;
  }

  fileName.textContent = file.name;
  uploadPlaceholder.style.display = 'none';
  uploadPreview.classList.add('visible');
  uploadZone.classList.add('has-file');
  // Prevent file input from intercepting pointer events on preview buttons
  fileInput.style.pointerEvents = 'none';
  submitBtn.disabled = false;
}

function clearFile() {
  selectedFile = null;
  fileInput.value = '';
  previewImg.src = '';
  previewImg.hidden = true;
  pdfPreviewIcon.hidden = true;
  fileName.textContent = '';
  uploadPreview.classList.remove('visible');
  uploadPlaceholder.style.display = '';
  uploadZone.classList.remove('has-file');
  fileInput.style.pointerEvents = '';
  submitBtn.disabled = true;
  clearError();
}

if (fileInput) fileInput.addEventListener('change', () => {
  if (fileInput.files[0]) setFile(fileInput.files[0]);
});

if (removeBtn) removeBtn.addEventListener('click', (e) => {
  e.preventDefault();
  e.stopPropagation();
  clearFile();
});

// ── Keyboard navigation for upload zone ──────────────────────────────────────

if (uploadZone) uploadZone.addEventListener('keydown', (e) => {
  if ((e.key === 'Enter' || e.key === ' ') && !uploadZone.classList.contains('has-file')) {
    e.preventDefault();
    if (fileInput) fileInput.click();
  }
});

if (uploadZone) uploadZone.addEventListener('click', (e) => {
  // Don't open file dialog when clicking preview buttons
  if (removeBtn && (e.target === removeBtn || removeBtn.contains(e.target))) return;
  if (uploadZone.classList.contains('has-file')) return;
  if (fileInput) fileInput.click();
});

// ── Drag & drop ───────────────────────────────────────────────────────────────

if (uploadZone) {
  uploadZone.addEventListener('dragover', (e) => {
    e.preventDefault();
    uploadZone.classList.add('drag-over');
  });
  uploadZone.addEventListener('dragleave', (e) => {
    if (!uploadZone.contains(e.relatedTarget)) {
      uploadZone.classList.remove('drag-over');
    }
  });
  uploadZone.addEventListener('drop', (e) => {
    e.preventDefault();
    uploadZone.classList.remove('drag-over');
    const file = e.dataTransfer?.files?.[0];
    if (file) setFile(file);
  });
}

// ── Clipboard paste (Ctrl+V / Cmd+V) ─────────────────────────────────────────

function _handlePasteItems(clipboardItems) {
  for (const item of clipboardItems) {
    if (item.kind === 'file') {
      const file = item.getAsFile();
      if (file && isAcceptedType(file)) {
        const ext = file.type === 'application/pdf' ? '.pdf'
                  : file.type === 'image/png'       ? '.png'
                  : file.type === 'image/jpeg'       ? '.jpg'
                  : '.png';
        const named = new File([file], `pasted_screenshot${ext}`, { type: file.type });
        setFile(named);
        return true;
      }
    }
  }
  return false;
}

function _onPasteEvent(e) {
  const target = e?.target;
  if (!target) return;
  if (
    target.tagName === 'TEXTAREA' ||
    target.tagName === 'INPUT' ||
    target.isContentEditable
  ) return;

  const items = e.clipboardData && e.clipboardData.items;
  if (!items) return;
  if (_handlePasteItems(Array.from(items))) {
    e.preventDefault();
  }
}

// Listen on document AND uploadZone so paste works regardless of focus state
document.addEventListener('paste', _onPasteEvent);
if (uploadZone) uploadZone.addEventListener('paste', _onPasteEvent);

// Paste button — uses Clipboard API for browsers where paste event needs a click
const pasteBtn = document.getElementById('pasteBtn');
if (pasteBtn) {
  pasteBtn.addEventListener('click', async () => {
    if (!navigator.clipboard || !navigator.clipboard.read) {
      uploadZone.focus();
      return;
    }
    try {
      const items = await navigator.clipboard.read();
      for (const item of items) {
        const imageType = item.types.find(t => t.startsWith('image/'));
        if (imageType) {
          const blob = await item.getType(imageType);
          const ext = imageType === 'image/png' ? '.png' : '.jpg';
          const file = new File([blob], `pasted_screenshot${ext}`, { type: imageType });
          setFile(file);
          return;
        }
      }
      showError('Буфер обмена не содержит изображения. Скопируйте скриншот и попробуйте снова.');
    } catch {
      uploadZone.focus();
    }
  });
}

// ── Mobile camera ─────────────────────────────────────────────────────────────

if (cameraBtn && cameraInput) {
  cameraBtn.addEventListener('click', () => { cameraInput.click(); });
  cameraInput.addEventListener('change', () => {
    if (cameraInput.files?.[0]) setFile(cameraInput.files[0]);
  });
}

// ── Progress animation ────────────────────────────────────────────────────────

const STEPS = ['step1', 'step2', 'step3', 'step4'];
let stepInterval = null;
let currentStep = 0;

function startProgress() {
  currentStep = 0;
  STEPS.forEach(id => {
    const el = document.getElementById(id);
    el.classList.remove('active', 'done');
  });
  document.getElementById(STEPS[0]).classList.add('active');

  stepInterval = setInterval(() => {
    if (currentStep < STEPS.length - 1) {
      document.getElementById(STEPS[currentStep]).classList.remove('active');
      document.getElementById(STEPS[currentStep]).classList.add('done');
      currentStep++;
      document.getElementById(STEPS[currentStep]).classList.add('active');
    }
  }, 5000);
}

function stopProgress() {
  if (stepInterval) { clearInterval(stepInterval); stepInterval = null; }
  STEPS.forEach(id => {
    document.getElementById(id)?.classList.remove('active');
  });
}

// ── Form submit ───────────────────────────────────────────────────────────────

if (analyzeForm) analyzeForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  if (!selectedFile) return;

  clearError();
  if (formSection) formSection.style.display = 'none';
  if (progressScreen) progressScreen.hidden = false;
  startProgress();

  const formData = new FormData();
  // Force a filename so mobile browsers (iOS/Android) don't send an empty or
  // 'blob' name, which causes the backend to fail extension detection.
  const safeName = (selectedFile.name && selectedFile.name !== 'blob')
    ? selectedFile.name
    : (selectedFile.type === 'application/pdf' ? 'mobile_receipt.pdf' : 'mobile_receipt.jpg');
  formData.append('file', selectedFile, safeName);
  const context = document.getElementById('contextInput')?.value || '';
  formData.append('context', context);

  try {
    const res = await fetch('/analyze', { method: 'POST', body: formData });

    // Guard against empty body (OOM kill / 502 gateway)
    const text = await res.text();
    if (!text || !text.trim()) {
      throw new Error(
        'Сервер временно перегружен или обрабатывает документ слишком долго. ' +
        'Попробуйте загрузить файл в формате PDF или повторите через несколько секунд.'
      );
    }

    let data;
    try {
      data = JSON.parse(text);
    } catch {
      throw new Error(
        'Сервер вернул неожиданный ответ. ' +
        'Попробуйте загрузить файл в формате PDF или повторите через несколько секунд.'
      );
    }

    if (res.status === 422 || res.status === 400) {
      const msg = data?.message || 'Ошибка формата файла. Попробуйте сделать обычный скриншот или загрузить PDF.';
      throw new Error(msg);
    }
    if (!res.ok || data.error) {
      throw new Error(data.message || `Ошибка сервера: ${res.status}`);
    }

    stopProgress();
    window.location.href = `/report/${data.report_id}`;
  } catch (err) {
    stopProgress();
    if (progressScreen) progressScreen.hidden = true;
    if (formSection) formSection.style.display = '';
    showError(`Ошибка анализа: ${err.message}`);
  }
});

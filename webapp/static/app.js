/* DripLink dashboard — local assets only; model outputs are candidates, not truth. */
(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const SVG_NS = 'http://www.w3.org/2000/svg';
  const PAGE_MODE = document.body.getAttribute('data-mode') === 'user' ? 'user' : 'admin';
  const uiRole = () => (PAGE_MODE === 'user' ? 'user' : (state.role || 'admin'));
  const state = {
    recordOffset: 0, nextOffset: null, meter: 'demo', status: null, csrf: '', file: null, previewUrl: null,
    record: null, records: [], imageMode: 'original', view: 'monitor',
    uploading: false, mutating: false, deleteRecord: null,
    recordRequest: 0, analyticsRequest: 0, statusRequest: 0, toastTimer: null,
    me: null, role: 'admin', authenticated: false, meterObjects: [], showLogin: false
  };
  const viewInfo = {
    monitor: ['设备与预警', '设备与预警', '集中监视设备在线状态与用水异常。'],
    workbench: ['识别工作台', '让每一滴水，清晰可见', '从一张水表照片开始，识读、核验，再看见用水变化。'],
    history: ['历史记录', '每一次识读，都有迹可循', '区分测试、待审核与已确认，始终保留真实的数据边界。'],
    analytics: ['用水分析', '用真实读数，理解用水', '只分析人工确认后的数据，不用预测替代事实。'],
    devices: ['设备接入', '从边缘设备，到数据工作台', '为后续树莓派采集预留接口，在可信网络内安全连接。']
  };
  const ROLE_VIEWS = { admin: ['monitor', 'workbench', 'history', 'analytics', 'devices'], user: ['monitor', 'analytics'] };

  function text(id, value) { $(id).textContent = value == null ? '—' : String(value); }
  function empty(node) { node.replaceChildren(); }
  function create(tag, className, content) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (content !== undefined) node.textContent = String(content);
    return node;
  }
  function plain(value) {
    if (value == null) return '';
    if (typeof value === 'string' || typeof value === 'number') return String(value);
    if (typeof value === 'object') {
      const message = value.message ?? value.error ?? value.detail ?? value.title;
      if (message !== undefined && message !== value) return plain(message);
      try { return JSON.stringify(value); } catch (_) { return '返回了无法显示的信息'; }
    }
    return String(value);
  }
  function numeric(value) {
    if (value === null || value === undefined || value === '' || typeof value === 'boolean') return null;
    if (typeof value === 'object') return numeric(value.reading ?? value.value ?? value.predicted_reading);
    if (typeof value === 'string' && !value.trim()) return null;
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
  }
  function reading(value) { const number = numeric(value); return number === null ? '—' : number.toFixed(4); }
  function dateTime(value) {
    if (!value) return '未提供时间';
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString('zh-CN', { hour12: false });
  }
  function sourceLabel(source) {
    return ({ sample: 'sample · 测试样本', manual: 'manual · 手动上传（测试）', device: 'device · 设备自动计入' })[source] || (source ? String(source) : '未提供来源');
  }
  function isSample(record) { return record && (record.status === 'sample' || record.source === 'manual' || record.source === 'sample'); }
  function statusInfo(record) {
    if (isSample(record)) return ['测试样本 · 不统计', 'sample'];
    if (record.status === 'confirmed') return ['已计入统计', 'confirmed'];
    return ['设备记录 · 已计入统计', 'confirmed'];
  }
  function toast(message, danger = false) {
    clearTimeout(state.toastTimer);
    $('toast').textContent = message;
    $('toast').className = danger ? 'toast toast-error' : 'toast';
    $('toast').hidden = false;
    state.toastTimer = setTimeout(() => { $('toast').hidden = true; }, 6500);
  }
  function setMessage(id, message, type = 'neutral') {
    const node = $(id);
    node.textContent = message;
    node.className = `banner ${type}`;
    node.hidden = false;
  }

  async function api(path, options = {}) {
    const method = (options.method || 'GET').toUpperCase();
    const headers = new Headers(options.headers || {});
    headers.set('Accept', 'application/json');
    if (method === 'POST') {
      if (!state.csrf) throw new Error('未取得安全校验令牌，请先点击顶部刷新按钮重新连接服务。');
      headers.set('X-CSRF-Token', state.csrf);
    }
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), path === '/api/predict' ? 180000 : 30000);
    try {
      const response = await fetch(path, { ...options, method, headers, credentials: 'same-origin', cache: 'no-store', signal: controller.signal });
      let data;
      try { data = await response.json(); } catch (_) {
        throw new Error(response.ok ? '服务返回的内容不是有效 JSON，请检查 API 配置。' : `请求失败（HTTP ${response.status}），服务未返回 JSON 错误信息。`);
      }
      if (!response.ok) {
        const detail = data && typeof data === 'object' ? plain(data.error ?? data.message ?? data.detail) : '';
        const suffix = response.status === 403 ? ' 请刷新安全状态后重试。' : '';
        throw new Error((detail || `请求失败（HTTP ${response.status}）`) + suffix);
      }
      if (!data || typeof data !== 'object' || Array.isArray(data)) throw new Error('服务返回的数据结构不符合约定。');
      return data;
    } catch (error) {
      if (error.name === 'AbortError') throw new Error('请求超时，服务可能仍在处理。请先刷新记录确认是否已入库，避免重复提交。');
      if (error instanceof TypeError) throw new Error('无法连接服务，请检查网络与后端是否启动。');
      throw error;
    } finally { clearTimeout(timer); }
  }

  function updateActions() {
    const ready = state.status && state.status.model_ready === true;
    $('predict-button').disabled = !state.file || !ready || !state.csrf || state.uploading || state.mutating;
    $('predict-button-text').textContent = state.uploading ? '正在识读，请稍候…' : '开始识读';
    $('upload-form').setAttribute('aria-busy', String(state.uploading));
    $('file-input').disabled = state.uploading;
    $('choose-file').disabled = state.uploading;
    $('replace-file').disabled = state.uploading;
    $('meter-id').disabled = state.uploading || state.mutating;
    $('meter-form').querySelector('button').disabled = state.uploading || state.mutating;
    $('delete-submit').disabled = !state.csrf || state.mutating;
    for (const button of document.querySelectorAll('[data-mutation]')) button.disabled = !state.csrf || state.mutating || state.uploading;
    let hint = '手动上传为测试样本，不计入统计；设备读数自动计入统计。';
    if (state.uploading) hint = '正在上传并推理。请保持页面开启，勿重复提交。';
    else if (!state.status) hint = '服务尚未连接，暂时无法上传。可点击顶部刷新重试。';
    else if (!ready) hint = '模型未就绪，无法识读。请查看顶部原因并检查服务端配置。';
    else if (!state.csrf) hint = '未取得安全校验令牌，所有写入操作已禁用。';
    else if (!state.file) hint = '请选择一张清晰的水表照片，再开始识读。';
    text('upload-hint', hint);
  }

  async function loadStatus() {
    const request = ++state.statusRequest;
    try {
      const data = await api('/api/status');
      if (request !== state.statusRequest) return;
      state.status = data;
      state.csrf = typeof data.csrf_token === 'string' ? data.csrf_token : '';
      state.me = (data && typeof data.me === 'object' && data.me) ? data.me : null;
      state.authenticated = !!state.me;
      state.role = state.me ? (state.me.role || 'admin') : null;
      const ready = data.model_ready === true;
      $('model-pill').className = `status-pill ${ready ? 'ready' : 'failed'}`;
      text('model-pill-text', ready ? '模型已就绪' : '模型未就绪');
      const model = plain(data.model_name) || '未提供模型名称';
      const device = plain(data.device) || '未提供运行设备';
      if (!ready) setMessage('service-banner', `模型未就绪 · ${plain(data.model_error) || '服务端未报告详细原因，请检查模型权重与运行环境。'}`, 'error');
      else if (!state.csrf) setMessage('service-banner', `模型已就绪 · ${model} / ${device}。但缺少 CSRF 安全令牌，写入操作已禁用。`, 'warning');
      else setMessage('service-banner', `模型已就绪 · ${model} / ${device}。设备读数自动计入统计，手动上传为测试样本。`, 'success');
      empty($('meter-options'));
      if (Array.isArray(data.meters)) {
        for (const meter of data.meters) {
          const id = typeof meter === 'string' ? meter : meter && (meter.meter_id ?? meter.id);
          if (id === null || id === undefined) continue;
          const option = create('option');
          option.value = String(id);
          $('meter-options').append(option);
        }
      }
      if (state.role === 'user') {
        const visible = Array.isArray(data.meters) ? data.meters : [];
        if (visible.length && !visible.includes(state.meter)) {
          state.meter = String(visible[0]);
          $('meter-id').value = state.meter;
        } else if (!visible.length) {
          state.meter = '';
          $('meter-id').value = '';
        }
      }
      applyRole();
      text('device-auth', data.device_upload_auth_required === true ? '需要设备鉴权凭证（本页面不显示 token）' : '服务未声明强制鉴权，请先检查服务端安全配置');
    } catch (error) {
      if (request !== state.statusRequest) return;
      state.status = null;
      state.csrf = '';
      $('model-pill').className = 'status-pill failed';
      text('model-pill-text', '服务连接失败');
      setMessage('service-banner', `服务连接失败 · ${error.message}`, 'error');
      text('device-auth', '未能确认设备鉴权配置，请勿对外开放接口');
    } finally { updateActions(); }
  }

  function applyRole() {
    const isRealUser = !!(state.me && !state.me.legacy);
    const forcedLogin = PAGE_MODE === 'user' ? !isRealUser : (!state.authenticated && state.role === null);
    const showLogin = forcedLogin || state.showLogin;
    $('login-screen').hidden = !showLogin;
    document.querySelector('.sidebar').hidden = showLogin;
    document.querySelector('.app-shell').hidden = showLogin;
    $('login-cancel').hidden = forcedLogin;
    $('user-pill').hidden = !state.me;
    $('login-button').hidden = PAGE_MODE === 'user' || !(state.me && state.me.legacy);
    $('logout-button').hidden = !isRealUser;
    if (state.me) {
      text('user-pill-text', state.me.legacy ? '本机管理员' : (state.me.display_name || state.me.username));
    }
    const allowed = uiRole() === 'user' ? ROLE_VIEWS.user : ROLE_VIEWS.admin;
    for (const link of document.querySelectorAll('[data-view]')) {
      const visible = allowed.includes(link.dataset.view);
      link.hidden = !visible;
    }
    if (showLogin) { $('login-submit').disabled = !state.csrf; return; }
    if (!allowed.includes(state.view)) state.view = 'monitor';
    switchView(state.view, false);
  }

  function switchView(name, updateHash = true) {
    if (!state.authenticated) { name = 'monitor'; }
    if (!viewInfo[name]) name = 'monitor';
    const allowed = uiRole() === 'user' ? ROLE_VIEWS.user : ROLE_VIEWS.admin;
    if (!allowed.includes(name)) name = allowed[0] || 'monitor';
    state.view = name;
    for (const key of Object.keys(viewInfo)) $('view-' + key).hidden = key !== name;
    for (const link of document.querySelectorAll('[data-view]')) {
      const active = link.dataset.view === name;
      link.classList.toggle('active', active);
      if (active) link.setAttribute('aria-current', 'page'); else link.removeAttribute('aria-current');
    }
    const [label, title, description] = viewInfo[name];
    text('current-view-label', label);
    $('page-title').replaceChildren(document.createTextNode(uiRole() === 'user' && name === 'monitor' ? '我的用水，一目了然' : title), create('span', 'title-dot', '.'));
    text('page-description', uiRole() === 'user' && name === 'monitor' ? '只看属于你的设备，了解自己的用水与节水建议。' : description);
    if (name === 'monitor') loadMonitor();
    if (updateHash && location.hash !== '#' + name) history.replaceState(null, '', '#' + name);
  }

  function chooseFile(file) {
    if (!file || state.uploading) return;
    if (!file.type.startsWith('image/')) { toast('请选择图片文件，不支持此文件类型。', true); return; }
    if (!file.size) { toast('这张图片是空文件，请重新选择。', true); return; }
    if (state.previewUrl) URL.revokeObjectURL(state.previewUrl);
    state.file = file;
    state.previewUrl = URL.createObjectURL(file);
    $('upload-preview').src = state.previewUrl;
    $('upload-preview').hidden = false;
    $('upload-placeholder').hidden = true;
    $('replace-file').hidden = false;
    $('drop-zone').classList.add('has-image');
    text('file-name', file.name);
    text('file-size', file.size < 1024 * 1024 ? `${(file.size / 1024).toFixed(1)} KB` : `${(file.size / 1024 / 1024).toFixed(2)} MB`);
    updateActions();
  }
  $('upload-preview').addEventListener('error', () => {
    if (!state.file) return;
    state.file = null;
    $('upload-preview').hidden = true;
    $('upload-placeholder').hidden = false;
    $('replace-file').hidden = true;
    $('drop-zone').classList.remove('has-image');
    text('file-name', '图片无法预览，请换用 JPEG、PNG 或 WebP');
    text('file-size', '未提交');
    if (state.previewUrl) URL.revokeObjectURL(state.previewUrl);
    state.previewUrl = null;
    updateActions();
    toast('浏览器无法解码这张图片，请换用常见格式。', true);
  });

  // Only server-returned same-origin HTTP(S) image addresses are displayed.
  // Local upload previews use a separately created blob URL, never API-provided data URLs.
  function imageUrl(raw) {
    if (typeof raw !== 'string' || !raw.trim()) return null;
    try {
      const url = new URL(raw, location.origin);
      if (!['http:', 'https:'].includes(url.protocol) || url.origin !== location.origin || url.username || url.password) return null;
      return url.href;
    } catch (_) { return null; }
  }
  function showImage(mode) {
    state.imageMode = mode;
    $('show-original').setAttribute('aria-pressed', String(mode === 'original'));
    $('show-result').setAttribute('aria-pressed', String(mode === 'result'));
    const record = state.record;
    const url = record && imageUrl(mode === 'original' ? record.original_url : record.result_url);
    const image = $('result-image');
    image.hidden = !url;
    $('result-image-empty').hidden = !!url;
    if (url) { image.src = url; image.alt = mode === 'original' ? '服务端保存的水表原图，请人工核对读数' : '模型标注结果图，标注不代表识读正确'; }
    else { image.removeAttribute('src'); text('result-image-empty', mode === 'result' ? '服务未提供可用的同源标注图' : '服务未提供可用的同源原图'); }
  }
  $('result-image').addEventListener('error', () => {
    $('result-image').hidden = true;
    $('result-image-empty').hidden = false;
    text('result-image-empty', '图片加载失败，请刷新记录或检查服务端图片地址');
  });

  function renderResult(record) {
    state.record = record;
    $('result-empty').hidden = !!record;
    $('result-content').hidden = !record;
    if (!record) { text('result-state', '等待上传'); $('result-state').className = 'tag subtle'; $('result-image').removeAttribute('src'); return; }
    const [label, className] = statusInfo(record);
    text('result-state', label);
    $('result-state').className = `tag ${className}`;
    if (record.quality === 'incomplete' || record.quality === 'ambiguous') {
      text('result-state', record.quality === 'incomplete' ? '模型漏检 · 不可采信' : '识读不稳 · 需核对');
      $('result-state').className = 'tag danger';
      $('result-content').classList.add('quality-bad');
    } else if (Array.isArray(record.image_flags) && record.image_flags.length) {
      text('result-state', '图像质量差 · 需核对');
      $('result-state').className = 'tag danger';
      $('result-content').classList.add('quality-bad');
    } else { $('result-content').classList.remove('quality-bad'); }
    text('predicted-reading', reading(record.predicted_reading));
    const number = numeric(record.predicted_reading);
    text('integer-reading', number === null ? '—' : Math.trunc(number));
    text('context-reading', reading(record.context_reading));
    text('independent-reading', reading(record.independent_reading));
    text('result-meter', record.meter_id);
    text('result-time', dateTime(record.captured_at));
    text('result-source', sourceLabel(record.source));
    const regions = record.detected_regions;
    text('result-regions', Array.isArray(regions) ? `${regions.length} 个区域` : (numeric(regions) !== null ? `${numeric(regions)} 个区域` : '服务未提供'));
    empty($('result-warnings'));
    const warnings = Array.isArray(record.warnings) ? record.warnings : (record.warnings ? [record.warnings] : []);
    if (warnings.length) for (const warning of warnings) $('result-warnings').append(create('li', '', plain(warning)));
    else $('result-warnings').append(create('li', '', '服务未返回额外警告；无警告不代表识读正确，请仍对照原图核验。'));
    if (isSample(record)) {
      text('review-state-label', '测试样本 · 不计入统计');
      text('review-state-help', '手动上传仅用于检验识别模型，不参与用水统计。');
    } else {
      text('review-state-label', `已自动计入统计 · ${reading(record.confirmed_reading)} m³`);
      text('review-state-help', '设备读数已自动计入统计；如读数有误，可删除该记录后重新采集。');
    }
    showImage('original');
    updateActions();
  }

  function validRecord(data) {
    if (!data.record || typeof data.record !== 'object' || Array.isArray(data.record) || data.record.id == null) throw new Error('服务未返回有效的 record，请刷新历史记录确认处理结果。');
    return data.record;
  }
  async function submitPrediction(event) {
    event.preventDefault();
    if (state.uploading || state.mutating || !state.file || $('predict-button').disabled) return;
    if ($('meter-id').value.trim() !== state.meter) { toast('请先点击“切换”应用水表编号，再开始识读。', true); return; }
    const data = new FormData();
    data.append('file', state.file);
    data.append('meter_id', state.meter);
    state.uploading = true;
    updateActions();
    try {
      const result = await api('/api/predict', { method: 'POST', body: data });
      renderResult(validRecord(result));
      toast('测试识读已保存，不计入用水统计。');
      await Promise.all([loadRecords(), loadAnalytics()]);
    } catch (error) { toast(error.message, true); }
    finally { state.uploading = false; updateActions(); }
  }

  function actionButton(label, handler, className = 'text-button', mutation = false) {
    const button = create('button', className, label);
    button.type = 'button';
    if (mutation) button.dataset.mutation = 'true';
    button.addEventListener('click', handler);
    return button;
  }
  function renderRecords() {
    const filter = $('record-filter').value;
    const records = state.records.filter((record) => filter === 'all' || (filter === 'sample' ? isSample(record) : !isSample(record) && record.status === filter));
    text('record-count', `${records.length} 条`);
    empty($('records-body'));
    $('records-table-wrap').hidden = records.length === 0;
    $('records-message').hidden = records.length !== 0;
    if (!records.length) text('records-message', state.records.length ? '当前筛选下暂无记录，请切换记录类型。' : `水表 ${state.meter} 尚无记录。上传第一张照片后，在这里查看与审核。`);
    for (const record of records) {
      const row = create('tr');
      const time = create('td');
      time.append(create('strong', 'table-time', dateTime(record.captured_at)), create('small', 'record-id', `#${record.id}`));
      row.append(time, create('td', 'source-cell', sourceLabel(record.source)), create('td', 'tabular', reading(record.predicted_reading)), create('td', 'tabular', reading(record.confirmed_reading)));
      const status = create('td');
      const [label, className] = statusInfo(record);
      status.append(create('span', `tag ${className}`, label));
      const actions = create('td', 'record-actions');
      actions.append(actionButton('查看', () => { renderResult(record); switchView('workbench'); $('result-heading').scrollIntoView({ behavior: 'smooth', block: 'start' }); }));
      actions.append(actionButton('删除', () => openDelete(record), 'text-button text-danger', true));
      row.append(status, actions);
      $('records-body').append(row);
    }
    updateActions();
  }
  async function loadRecords(offset = 0) {
    if (typeof offset !== 'number') offset = 0;
    state.recordOffset = offset;
    const request = ++state.recordRequest;
    const meter = state.meter;
    text('records-message', '正在读取记录…');
    $('records-message').hidden = false;
    $('records-table-wrap').hidden = true;
    text('record-count', '—');
    try {
      const data = await api(`/api/records?meter_id=${encodeURIComponent(meter)}&offset=${offset}&limit=100`);
      if (request !== state.recordRequest || meter !== state.meter) return;
      if (!Array.isArray(data.records)) throw new Error('记录接口未返回 records 列表。');
      state.nextOffset = data.next_offset ?? null;
      $('older-records').disabled = state.nextOffset === null;
      $('newer-records').disabled = offset === 0;
      $('page-info').textContent = `第 ${Math.floor(offset / 100) + 1} 页，共 ${data.total ?? data.records.length} 条（筛选仅当前页）`;
      state.records = data.records.filter((record) => record && typeof record === 'object' && record.id != null);
      renderRecords();
    } catch (error) {
      if (request !== state.recordRequest || meter !== state.meter) return;
      state.records = [];
      empty($('records-body'));
      text('records-message', `记录读取失败 · ${error.message}`);
      $('records-message').hidden = false;
      $('records-table-wrap').hidden = true;
    }
  }

  function showDialog(dialog) {
    if (typeof dialog.showModal === 'function') dialog.showModal();
    else { toast('当前浏览器不支持安全审核对话框，请使用较新版本浏览器。', true); }
  }
  function openDelete(record) {
    if (state.mutating || state.uploading) return;
    if (!state.csrf) { toast('安全状态尚未就绪，请先刷新。', true); return; }
    state.deleteRecord = record;
    text('delete-description', `将删除水表 ${record.meter_id} 的记录 #${record.id}（${dateTime(record.captured_at)}）。`);
    $('delete-error').hidden = true;
    showDialog($('delete-dialog'));
  }
  async function submitDeletion(event) {
    event.preventDefault();
    if (state.mutating || !state.deleteRecord) return;
    const record = state.deleteRecord;
    state.mutating = true;
    updateActions();
    $('delete-error').hidden = true;
    try {
      const data = await api(`/api/records/${encodeURIComponent(record.id)}/delete`, { method: 'POST' });
      if (data.ok !== true) throw new Error('服务未确认删除成功，请刷新记录核对。');
      if (state.record && String(state.record.id) === String(record.id)) renderResult(null);
      $('delete-dialog').close();
      state.deleteRecord = null;
      toast('记录已删除，相关统计已重新读取。');
      await Promise.all([loadRecords(), loadAnalytics()]);
    } catch (error) { text('delete-error', error.message); $('delete-error').hidden = false; }
    finally { state.mutating = false; updateActions(); }
  }

  function svgElement(tag, attributes = {}, content) {
    const node = document.createElementNS(SVG_NS, tag);
    for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, String(value));
    if (content !== undefined) node.textContent = String(content);
    return node;
  }
  function renderChart(points) {
    const svg = $('usage-chart');
    empty(svg);
    svg.append(svgElement('title', { id: 'chart-title' }, '人工确认累计读数趋势'), svgElement('desc', { id: 'chart-description' }, `${points.length} 个有效确认读数，单位立方米；具体数值见下方趋势数据表。`));
    const clean = points.map((point) => ({ time: new Date(point.time).getTime(), reading: numeric(point.reading), raw: point })).filter((point) => Number.isFinite(point.time) && point.reading !== null).sort((a, b) => a.time - b.time);
    svg.hidden = clean.length === 0;
    $('chart-empty').hidden = clean.length !== 0;
    text('chart-empty', points.length ? '读数缺少有效时间或数值，暂不能绘图；请检查下方数据与服务端记录。' : '暂无人工确认数据，完成审核后显示趋势。');
    if (!clean.length) return;
    const width = 720, height = 290, left = 88, right = 30, top = 24, bottom = 52;
    let minY = Math.min(...clean.map((p) => p.reading));
    let maxY = Math.max(...clean.map((p) => p.reading));
    const spanY = maxY - minY;
    const padding = spanY ? spanY * 0.16 : Math.max(Math.abs(maxY) * 0.005, 0.0001);
    minY = Math.max(0, minY - padding); maxY += padding;
    const minX = clean[0].time, maxX = clean[clean.length - 1].time;
    const x = (point) => minX === maxX ? left + (width - left - right) / 2 : left + (point.time - minX) / (maxX - minX) * (width - left - right);
    const y = (point) => top + (maxY - point.reading) / (maxY - minY) * (height - top - bottom);
    for (let index = 0; index <= 4; index++) {
      const yPos = top + (height - top - bottom) * index / 4;
      const value = maxY - (maxY - minY) * index / 4;
      svg.append(svgElement('line', { x1: left, y1: yPos, x2: width - right, y2: yPos, class: 'chart-gridline' }), svgElement('text', { x: left - 12, y: yPos + 4, 'text-anchor': 'end', class: 'chart-axis' }, value.toFixed(4)));
    }
    svg.append(svgElement('text', { x: left, y: 13, class: 'chart-axis' }, '累计读数 / m³'));
    const path = clean.map((point, index) => `${index ? 'L' : 'M'} ${x(point).toFixed(2)} ${y(point).toFixed(2)}`).join(' ');
    if (clean.length > 1) {
      const base = height - bottom;
      svg.append(svgElement('path', { d: `${path} L ${x(clean[clean.length - 1])} ${base} L ${x(clean[0])} ${base} Z`, class: 'chart-area' }));
      svg.append(svgElement('path', { d: path, class: 'chart-line' }));
    }
    for (const point of clean) {
      const circle = svgElement('circle', { cx: x(point), cy: y(point), r: 4, class: 'chart-point' });
      circle.append(svgElement('title', {}, `${dateTime(point.raw.time)} · ${reading(point.reading)} m³`));
      svg.append(circle);
    }
    const indexes = [...new Set([0, Math.floor((clean.length - 1) / 2), clean.length - 1])];
    for (const index of indexes) {
      const point = clean[index];
      const date = new Date(point.time);
      const label = date.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' }) + ' ' + date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false });
      svg.append(svgElement('text', { x: x(point), y: height - 22, 'text-anchor': index === 0 && clean.length > 1 ? 'start' : index === clean.length - 1 && clean.length > 1 ? 'end' : 'middle', class: 'chart-axis' }, label));
    }
  }
  function resetAnalytics(message) {
    for (const id of ['stat-count', 'stat-latest', 'stat-net', 'stat-average']) text(id, '—');
    empty($('analytics-points-body'));
    empty($('advice-list'));
    empty($('alerts-list'));
    $('advice-list').append(create('li', 'muted', message));
    $('alerts-list').append(create('p', 'muted', message));
    text('chart-interval', '时间跨度：—');
    text('advice-note', '规则节水建议（非大模型），需有后端有效数据才能生成。');
    renderChart([]);
  }
  async function loadAnalytics() {
    const request = ++state.analyticsRequest, meter = state.meter;
    setMessage('analytics-message', '正在读取人工确认数据…');
    resetAnalytics('正在读取分析…');
    try {
      const data = await api(`/api/analytics?meter_id=${encodeURIComponent(meter)}`);
      if (request !== state.analyticsRequest || meter !== state.meter) return;
      const count = numeric(data.confirmed_count);
      if (count === null || !Number.isInteger(count) || count < 0) throw new Error('分析接口未返回有效的 confirmed_count。');
      const hasData = count > 0;
      const hasInterval = count >= 2;
      const hours = numeric(data.interval_hours);
      text('stat-count', count);
      text('stat-latest', hasData ? reading(data.latest_reading) : '—');
      text('stat-net', hasInterval ? reading(data.net_usage_m3) : '—');
      text('stat-average', hasInterval && hours !== null && hours > 0 ? reading(data.average_daily_m3) : '—');
      text('chart-interval', hasData && hours !== null ? `有效统计时间跨度：${hours.toFixed(2)} 小时 · 以服务端确认数据为准` : '时间跨度：尚无足够确认数据');
      const points = hasData && Array.isArray(data.points) ? data.points.filter((p) => p && typeof p === 'object') : [];
      renderChart(points);
      empty($('analytics-points-body'));
      for (const point of points) {
        const row = create('tr');
        row.append(create('td', '', dateTime(point.time)), create('td', 'tabular', reading(point.reading)), create('td', 'tabular', reading(point.usage_m3)));
        $('analytics-points-body').append(row);
      }
      if (!points.length) { const row = create('tr'); const cell = create('td', 'muted', '暂无可展示的确认趋势数据'); cell.colSpan = 3; row.append(cell); $('analytics-points-body').append(row); }
      setMessage('analytics-message', !hasData ? '暂无人工确认记录。测试样本与待审核记录不会计入统计。' : (count < 2 ? '目前只有一条确认记录，尚不足以计算可信的区间用量与日均。' : '统计来自人工确认记录；读数回退、时间异常与换表情况仍需人工核验。'), hasData ? 'success' : 'neutral');
      empty($('advice-list'));
      const advice = Array.isArray(data.advice) ? data.advice : [];
      if (data.advice_mode === 'rules') {
        for (const item of advice) $('advice-list').append(create('li', '', plain(item)));
        if (!advice.length) $('advice-list').append(create('li', 'muted', '暂无规则建议，积累有效确认数据后再查看。'));
      } else $('advice-list').append(create('li', 'muted', '服务未声明规则建议模式，未展示未经核实的建议内容。'));
      text('advice-note', `${data.note ? plain(data.note) + ' ' : ''}建议来源：规则引擎（非大模型），不代替漏水或设备故障诊断。`);
      empty($('alerts-list'));
      const alerts = Array.isArray(data.alerts) ? data.alerts : [];
      for (const alert of alerts) {
        if (!alert || typeof alert !== 'object') continue;
        const level = ['error', 'danger', 'critical'].includes(alert.level) ? 'error' : ['warning', 'warn'].includes(alert.level) ? 'warning' : 'neutral';
        const box = create('article', `alert-item ${level}`);
        box.append(create('strong', '', plain(alert.title) || '用水提醒'), create('p', '', plain(alert.message)));
        $('alerts-list').append(box);
      }
      if (!$('alerts-list').children.length) $('alerts-list').append(create('p', 'muted', hasData ? '服务未返回异常提醒；这不表示已排除漏水或其他问题。' : '暂无可分析数据，暂不判断是否异常。'));
    } catch (error) {
      if (request !== state.analyticsRequest || meter !== state.meter) return;
      resetAnalytics('分析暂不可用，请刷新重试。');
      setMessage('analytics-message', `分析读取失败 · ${error.message}`, 'error');
    }
  }

  async function loadDeviceInfo() {
    try {
      const data = await api('/api/device-info');
      text('device-endpoint', typeof data.endpoint === 'string' ? data.endpoint : '未提供上传接口');
      empty($('device-fields'));
      if (Array.isArray(data.required_fields)) for (const field of data.required_fields) $('device-fields').append(create('code', '', plain(field)));
      else $('device-fields').append(create('span', 'muted', '服务未返回字段说明'));
      const seconds = numeric(data.interval_example_seconds);
      text('device-interval', seconds === null ? '未提供；在树莓派端配置' : `${seconds} 秒（仅示例，在树莓派端配置）`);
      text('device-note', `${data.note ? plain(data.note) + ' ' : ''}设备凭证不通过此接口返回，此页面不执行采集或定时任务。`);
      setMessage('device-message', '设备接入信息已读取。这是接口说明，不表示已有设备在线。', 'neutral');
    } catch (error) {
      setMessage('device-message', `设备信息读取失败 · ${error.message}`, 'error');
      text('device-endpoint', '暂不可用');
      text('device-interval', '暂不可用');
      empty($('device-fields'));
    }
  }

  function alertLevel(level) {
    return ['error', 'danger', 'critical'].includes(level) ? 'error' : ['warning', 'warn'].includes(level) ? 'warning' : 'neutral';
  }
  function onlineLabel(status) {
    return ({ online: '在线', offline: '离线', unknown: '未配置 / 未知' })[status] || '未知';
  }
  function renderMonitorAlerts(alerts) {
    const box = $('monitor-alerts');
    empty(box);
    if (!Array.isArray(alerts) || !alerts.length) { box.append(create('p', 'muted', '暂无异常预警。这不表示已排除漏水或设备问题。')); return; }
    for (const alert of alerts) {
      if (!alert || typeof alert !== 'object') continue;
      const item = create('article', `alert-item ${alertLevel(alert.level)}`);
      item.append(create('strong', '', plain(alert.title) || '预警'), create('p', '', plain(alert.message)));
      box.append(item);
    }
  }
  function renderMeterList() {
    const wrap = $('monitor-meters');
    empty(wrap);
    text('monitor-meter-heading', uiRole() === 'user' ? '我的设备' : '设备清单');
    text('monitor-meter-count', `${state.meterObjects.length} 块`);
    if (!state.meterObjects.length) {
      wrap.append(create('p', 'muted', uiRole() === 'user' ? '你还没有被授权的设备，请联系管理员分配。' : '暂无设备。上传照片或新增水表后会显示在这里。'));
      return;
    }
    for (const meter of state.meterObjects) {
      const card = create('article', 'meter-monitor');
      const head = create('div', 'meter-monitor-head');
      const status = meter.online_status;
      const statusClass = status === 'online' ? 'online' : status === 'offline' ? 'offline' : 'unknown';
      head.append(create('strong', '', meter.display_name || meter.meter_id), create('span', `status-badge ${statusClass}`, onlineLabel(status)));
      card.append(head);
      const meta = create('dl', 'meter-monitor-meta');
      const deviceCount = Array.isArray(meter.devices) ? meter.devices.length : 0;
      const rows = [['水表编号', meter.meter_id], ['采集设备', `${deviceCount} 台`], ['采样间隔', meter.expected_interval_seconds ? `${meter.expected_interval_seconds} 秒` : '未配置']];
      if (meter.daily_limit_m3 != null) rows.push(['日用水阈值', `${meter.daily_limit_m3} m³`]);
      for (const [key, value] of rows) { const item = create('div'); item.append(create('dt', '', key), create('dd', '', plain(value))); meta.append(item); }
      card.append(meta);
      const actions = create('div', 'meter-monitor-actions');
      actions.append(actionButton('查看用水分析', () => { state.meter = meter.meter_id; $('meter-id').value = meter.meter_id; switchView('analytics'); }));
      if (uiRole() === 'admin') actions.append(actionButton('查看记录', () => { state.meter = meter.meter_id; $('meter-id').value = meter.meter_id; switchView('history'); loadRecords(); }));
      card.append(actions);
      wrap.append(card);
    }
  }
  async function loadMonitor() {
    try {
      setMessage('monitor-message', '正在读取设备与预警…');
      const data = await api('/api/dashboard');
      state.meterObjects = Array.isArray(data.meters) ? data.meters : [];
      text('monitor-stat-total', data.total ?? state.meterObjects.length);
      text('monitor-stat-online', data.online_count ?? 0);
      text('monitor-stat-offline', data.offline_count ?? 0);
      const alerts = Array.isArray(data.alerts) ? data.alerts : [];
      text('monitor-stat-alerts', alerts.length);
      renderMonitorAlerts(alerts);
      renderMeterList();
      $('monitor-admin-panel').hidden = uiRole() !== 'admin';
      $('user-usage-card').hidden = uiRole() !== 'user';
      text('monitor-subtitle', uiRole() === 'user' ? '只显示你被授权的设备与个人用水情况。' : '监视全部设备的在线状态、采集与用水异常。');
      setMessage('monitor-message', '数据已刷新。在线状态依据采集/心跳时间与预期间隔判断，仅供参考。', 'success');
      if (uiRole() === 'user' && state.meter) await loadUserUsage();
    } catch (error) {
      setMessage('monitor-message', `设备数据读取失败 · ${error.message}`, 'error');
    }
  }
  async function loadUserUsage() {
    try {
      const data = await api(`/api/analytics?meter_id=${encodeURIComponent(state.meter)}`);
      text('user-latest', reading(data.latest_reading));
      text('user-net', data.net_usage_m3 != null ? reading(data.net_usage_m3) : '—');
      text('user-average', data.average_daily_m3 != null ? reading(data.average_daily_m3) : '—');
      text('user-count', numeric(data.confirmed_count));
      empty($('user-advice'));
      const advice = Array.isArray(data.advice) ? data.advice : [];
      for (const item of advice) $('user-advice').append(create('li', '', plain(item)));
      if (!advice.length) $('user-advice').append(create('li', 'muted', '积累设备记录后可生成节水建议。'));
    } catch (error) {
      empty($('user-advice'));
      $('user-advice').append(create('li', 'muted', `个人用水读取失败 · ${error.message}`));
    }
  }
  async function submitLogin(event) {
    event.preventDefault();
    if (state.mutating) return;
    if (!state.csrf) { $('login-message').hidden = false; setMessage('login-message', '未取得安全令牌，请刷新页面后重试。', 'error'); return; }
    state.mutating = true;
    $('login-submit').disabled = true;
    $('login-error').hidden = true;
    try {
      await api('/api/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username: $('login-username').value.trim(), password: $('login-password').value }) });
      $('login-password').value = '';
      state.showLogin = false;
      await loadStatus();
    } catch (error) {
      text('login-error', error.message);
      $('login-error').hidden = false;
    } finally {
      state.mutating = false;
      if (!state.authenticated) $('login-submit').disabled = !state.csrf;
    }
  }
  async function submitLogout() {
    try { await api('/api/auth/logout', { method: 'POST' }); } catch (_) { /* fall through */ }
    state.me = null; state.authenticated = false; state.role = null; state.showLogin = false;
    await loadStatus();
  }
  async function submitMeterCreate(event) {
    event.preventDefault();
    if (state.mutating || state.role !== 'admin') return;
    const body = { id: $('new-meter-id').value.trim(), display_name: $('new-meter-name').value.trim() };
    if ($('new-meter-interval').value) body.expected_interval_seconds = Number($('new-meter-interval').value);
    if ($('new-meter-limit').value) body.daily_limit_m3 = Number($('new-meter-limit').value);
    state.mutating = true; $('meter-create-submit').disabled = true; $('meter-create-error').hidden = true;
    try {
      await api('/api/meters', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      $('new-meter-id').value = ''; $('new-meter-name').value = '';
      toast('水表已创建。');
      await Promise.all([loadMonitor(), loadStatus()]);
    } catch (error) { text('meter-create-error', error.message); $('meter-create-error').hidden = false; }
    finally { state.mutating = false; $('meter-create-submit').disabled = !state.csrf; }
  }
  async function submitDeviceCreate(event) {
    event.preventDefault();
    if (state.mutating || state.role !== 'admin') return;
    const body = { meter_id: $('new-device-meter').value.trim(), display_name: $('new-device-name').value.trim() };
    state.mutating = true; $('device-create-submit').disabled = true; $('device-create-error').hidden = true; $('device-token-box').hidden = true;
    try {
      const data = await api('/api/devices', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      if (typeof data.token === 'string') {
        text('device-token-value', data.token);
        $('device-token-box').hidden = false;
      }
      $('new-device-name').value = '';
      toast('设备已创建，请立即保存访问凭证。');
      await loadMonitor();
    } catch (error) { text('device-create-error', error.message); $('device-create-error').hidden = false; }
    finally { state.mutating = false; $('device-create-submit').disabled = !state.csrf; }
  }

  async function refreshAll() {
    $('refresh-button').disabled = true;
    $('refresh-button').classList.add('refreshing');
    await loadStatus();
    await Promise.all([loadRecords(), loadAnalytics(), loadDeviceInfo(), loadMonitor()]);
    $('refresh-button').disabled = false;
    $('refresh-button').classList.remove('refreshing');
  }

  $('meter-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    if (state.uploading || state.mutating) return;
    const meter = $('meter-id').value.trim();
    if (!meter) { toast('请输入水表编号。', true); $('meter-id').focus(); return; }
    $('meter-id').value = meter;
    state.meter = meter;
    renderResult(null);
    state.records = [];
    toast(`当前水表已切换为 ${meter}`);
    await Promise.all([loadRecords(), loadAnalytics()]);
  });
  $('choose-file').addEventListener('click', () => $('file-input').click());
  $('replace-file').addEventListener('click', () => $('file-input').click());
  $('file-input').addEventListener('change', (event) => { chooseFile(event.target.files[0]); event.target.value = ''; });
  let dragDepth = 0;
  $('drop-zone').addEventListener('dragenter', (event) => { event.preventDefault(); dragDepth++; if (!state.uploading) $('drop-zone').classList.add('drag-over'); });
  $('drop-zone').addEventListener('dragover', (event) => { event.preventDefault(); if (event.dataTransfer) event.dataTransfer.dropEffect = state.uploading ? 'none' : 'copy'; });
  $('drop-zone').addEventListener('dragleave', (event) => { event.preventDefault(); dragDepth = Math.max(0, dragDepth - 1); if (!dragDepth) $('drop-zone').classList.remove('drag-over'); });
  $('drop-zone').addEventListener('drop', (event) => {
    event.preventDefault(); dragDepth = 0; $('drop-zone').classList.remove('drag-over');
    const files = event.dataTransfer && event.dataTransfer.files;
    if (files && files.length) { if (files.length > 1) toast('一次识读一张图片，已选择第一张。'); chooseFile(files[0]); }
  });
  window.addEventListener('dragover', (event) => event.preventDefault());
  window.addEventListener('drop', (event) => event.preventDefault());
  $('upload-form').addEventListener('submit', submitPrediction);
  $('show-original').addEventListener('click', () => showImage('original'));
  $('show-result').addEventListener('click', () => showImage('result'));
  $('delete-form').addEventListener('submit', submitDeletion);
  for (const button of document.querySelectorAll('[data-close-dialog]')) button.addEventListener('click', () => { if (!state.mutating) $(button.dataset.closeDialog).close(); });
  $('delete-dialog').addEventListener('cancel', (event) => { if (state.mutating) event.preventDefault(); });
  $('record-filter').addEventListener('change', renderRecords);
  $('older-records').addEventListener('click', () => { if (state.nextOffset !== null) loadRecords(state.nextOffset); });
  $('newer-records').addEventListener('click', () => loadRecords(Math.max(0, state.recordOffset - 100)));
  $('refresh-records').addEventListener('click', loadRecords);
  $('refresh-analytics').addEventListener('click', loadAnalytics);
  $('refresh-monitor').addEventListener('click', loadMonitor);
  $('refresh-button').addEventListener('click', refreshAll);
  $('login-form').addEventListener('submit', submitLogin);
  $('logout-button').addEventListener('click', submitLogout);
  $('login-button').addEventListener('click', () => { state.showLogin = true; $('login-username').value = ''; $('login-password').value = ''; $('login-error').hidden = true; applyRole(); $('login-username').focus(); });
  $('login-cancel').addEventListener('click', () => { state.showLogin = false; applyRole(); });
  $('meter-create-form').addEventListener('submit', submitMeterCreate);
  $('device-create-form').addEventListener('submit', submitDeviceCreate);
  for (const link of document.querySelectorAll('[data-view]')) link.addEventListener('click', (event) => { event.preventDefault(); switchView(link.dataset.view); });
  window.addEventListener('hashchange', () => switchView(location.hash.slice(1), false));
  window.addEventListener('beforeunload', () => { if (state.previewUrl) URL.revokeObjectURL(state.previewUrl); });
  switchView(location.hash.slice(1) || 'monitor', false);
  updateActions();
  refreshAll();
})();

// ── 全局数据 ──────────────────────────────────────────────────
const STORAGE_KEY = 'intern-register-web';
let ledger = [];

// ── 工具函数 ──────────────────────────────────────────────────
function log(msg, type = 'info') {
  const el = document.getElementById('log');
  const ts = new Date().toLocaleTimeString();
  el.innerHTML += `<span class="ts">[${ts}]</span> <span class="${type}">${msg}</span>\n`;
  el.scrollTop = el.scrollHeight;
}
function clearLog() { document.getElementById('log').innerHTML = ''; }

function showResult(id, data) {
  const el = document.getElementById(id);
  el.textContent = typeof data === 'string' ? data : JSON.stringify(data, null, 2);
}

async function apiCall(method, path, body = null) {
  const opts = { method, headers: { 'Content-Type': 'application/json' } };
  if (body) opts.body = JSON.stringify(body);
  const r = await fetch(path, opts);
  const text = await r.text();
  try {
    return JSON.parse(text);
  } catch {
    return { ok: false, error: text };
  }
}

// ── 提供者切换：联动显示/隐藏对应配置项 ───────────────────────
function applyProviderUI() {
  const p = document.getElementById('cfg-provider').value;
  const workerFields = ['cfg-worker-base', 'cfg-worker-token', 'cfg-worker-domain'];
  const yydsFields = ['cfg-yyds-key', 'cfg-yyds-base', 'cfg-yyds-domain', 'cfg-yyds-sub'];
  const mailsHint = document.getElementById('mails-hint');
  workerFields.forEach(id => {
    const wrap = document.getElementById(id).closest('.form-item');
    if (wrap) wrap.style.display = p === 'yyds' ? 'none' : '';
  });
  yydsFields.forEach(id => {
    const wrap = document.getElementById(id).closest('.form-item');
    if (wrap) wrap.style.display = p === 'yyds' ? '' : 'none';
  });
  if (mailsHint) mailsHint.style.display = p === 'yyds' ? 'block' : 'none';
}

// ── 初始化 ────────────────────────────────────────────────────
window.addEventListener('DOMContentLoaded', () => {
  document.getElementById('cfg-provider').addEventListener('change', applyProviderUI);
  loadConfigFromApp();
  checkHealth();
  log('应用已加载');
});

// ── 健康检查 ──────────────────────────────────────────────────
async function checkHealth() {
  try {
    const d = await apiCall('GET', '/api/health');
    const alert = document.getElementById('config-alert');
    if (!d.ok) {
      alert.className = 'alert error';
      alert.textContent = '❌ 配置缺失: ' + (d.missing || []).join(', ') + ' — 请填写下方"应用设置"';
      alert.style.display = 'block';
    } else {
      alert.className = 'alert ok';
      alert.textContent = '✅ 配置正常';
      alert.style.display = 'block';
      setTimeout(() => alert.style.display = 'none', 5000);
    }
  } catch (e) {
    log('健康检查失败: ' + e, 'err');
  }
}

// ── 配置管理 ──────────────────────────────────────────────────
async function loadConfigFromApp() {
  try {
    const cfg = await apiCall('GET', '/api/config');
    document.getElementById('cfg-provider').value = cfg.provider || 'worker';
    document.getElementById('cfg-worker-base').value = cfg.worker_base || '';
    document.getElementById('cfg-worker-domain').value = cfg.worker_domain || '';
    document.getElementById('cfg-worker-token').value = cfg.worker_admin_token || '';
    document.getElementById('cfg-yyds-key').value = cfg.yyds_api_key || '';
    document.getElementById('cfg-yyds-base').value = cfg.yyds_base_url || '';
    document.getElementById('cfg-yyds-domain').value = cfg.yyds_domain || '';
    document.getElementById('cfg-yyds-sub').value = cfg.yyds_subdomain || '';
    document.getElementById('cfg-proxy').value = cfg.proxy || '';
    document.getElementById('cfg-client-id').value = cfg.client_id || '';
    document.getElementById('cfg-source').value = cfg.source || '';
    document.getElementById('cfg-poll-interval').value = cfg.mail_poll_interval || 0.8;
    document.getElementById('cfg-poll-timeout').value = cfg.mail_poll_timeout || 120;
    document.getElementById('cfg-req-timeout').value = cfg.request_timeout || 30;
    document.getElementById('cfg-quota-max').value = cfg.reg_quota_max || 40;
    applyProviderUI();
  } catch (e) {
    log('加载配置失败: ' + e, 'err');
  }
}

async function saveConfig() {
  const cfg = {
    provider: document.getElementById('cfg-provider').value,
    worker_base: document.getElementById('cfg-worker-base').value.trim(),
    worker_domain: document.getElementById('cfg-worker-domain').value.trim(),
    worker_admin_token: document.getElementById('cfg-worker-token').value.trim(),
    yyds_api_key: document.getElementById('cfg-yyds-key').value.trim(),
    yyds_base_url: document.getElementById('cfg-yyds-base').value.trim(),
    yyds_domain: document.getElementById('cfg-yyds-domain').value.trim(),
    yyds_subdomain: document.getElementById('cfg-yyds-sub').value.trim(),
    proxy: document.getElementById('cfg-proxy').value.trim(),
    client_id: document.getElementById('cfg-client-id').value.trim(),
    source: document.getElementById('cfg-source').value.trim(),
    mail_poll_interval: parseFloat(document.getElementById('cfg-poll-interval').value),
    mail_poll_timeout: parseInt(document.getElementById('cfg-poll-timeout').value),
    request_timeout: parseInt(document.getElementById('cfg-req-timeout').value),
    reg_quota_max: parseInt(document.getElementById('cfg-quota-max').value),
  };

  try {
    const d = await apiCall('POST', '/api/config', cfg);
    showResult('config-status', d);
    log('配置已保存', 'ok');
    checkHealth();
  } catch (e) {
    showResult('config-status', '保存失败: ' + e);
    log('保存失败: ' + e, 'err');
  }
}

async function loadConfig() {
  await loadConfigFromApp();
  log('配置已重新加载', 'ok');
}

async function checkConnection() {
  log('正在检测连接...');
  try {
    const d = await apiCall('GET', '/api/check');
    document.getElementById('check-section').style.display = 'block';
    showResult('check-result', d);
    if (d.ok) {
      log('✅ 所有连接检测通过', 'ok');
    } else {
      log('❌ 部分检测失败', 'err');
    }
  } catch (e) {
    log('连接检测失败: ' + e, 'err');
  }
}

async function resetConfig() {
  if (!confirm('确定重置所有配置？')) return;
  await apiCall('POST', '/api/config/reset');
  await loadConfigFromApp();
  log('配置已重置', 'ok');
}

// ── 创建邮箱 ──────────────────────────────────────────────────
async function createMailbox() {
  const domain = document.getElementById('mail-domain').value.trim();
  const prefix = document.getElementById('mail-prefix').value.trim();
  const count = parseInt(document.getElementById('mail-count').value);

  if (!prefix && !domain) {
    log('请填写域名', 'err');
    return;
  }

  log(`创建邮箱${prefix ? ': ' + prefix : ''}...`);
  try {
    const d = await apiCall('POST', '/api/mailbox', { domain, prefix, count });
    showResult('mailbox-result', d);
    if (d.ok) {
      const email = d.emails[0] || '';
      document.getElementById('sso-email').value = email;
      document.getElementById('act-email').value = email;
      document.getElementById('view-email').value = email;
      log(`创建成功: ${email}`, 'ok');
    } else {
      log(`创建失败: ${JSON.stringify(d)}`, 'err');
    }
  } catch (e) {
    showResult('mailbox-result', 'Error: ' + e);
    log(`创建失败: ${e}`, 'err');
  }
}

async function quickCreate() {
  const domain = document.getElementById('mail-domain').value.trim();
  log(`快速创建${domain ? ': ' + domain : ''}...`);
  try {
    const d = await apiCall('POST', '/api/mailbox', { domain: domain || null, count: 1 });
    showResult('mailbox-result', d);
    if (d.ok) {
      const email = d.emails[0] || '';
      document.getElementById('sso-email').value = email;
      document.getElementById('act-email').value = email;
      document.getElementById('view-email').value = email;
      log(`快速创建: ${email}`, 'ok');
    }
  } catch (e) {
    showResult('mailbox-result', 'Error: ' + e);
  }
}

// ── SSO 注册 ──────────────────────────────────────────────────
function generateCredentials() {
  const username = 'lz' + Math.random().toString().slice(2, 8);
  const password = 'Aa1' + Math.random().toString(36).slice(2, 10);
  document.getElementById('sso-username').value = username;
  document.getElementById('sso-password').value = password;
  log(`生成凭据: ${username}`, 'info');
}

async function ssoRegister() {
  const email = document.getElementById('sso-email').value.trim();
  let username = document.getElementById('sso-username').value.trim();
  let password = document.getElementById('sso-password').value.trim();

  if (!email) { log('请先创建邮箱', 'err'); return; }
  if (!username) username = 'lz' + Math.random().toString().slice(2, 8);
  if (!password) password = 'Aa1' + Math.random().toString(36).slice(2, 10);

  log(`SSO 注册: ${email} / ${username}`);
  try {
    const d = await apiCall('POST', '/api/sso/register', { username, email, password });
    showResult('sso-result', d);
    if (d.ok) {
      log(`注册成功: uid=${d.sso_uid}`, 'ok');
      addToLedger({ email, username, password, sso_uid: d.sso_uid, status: 'registered' });
      document.getElementById('jwt-username').value = username;
      document.getElementById('jwt-password').value = password;
    } else {
      log(`注册失败: ${d.msg_code} ${d.msg}`, 'err');
    }
  } catch (e) {
    showResult('sso-result', 'Error: ' + e);
    log(`注册失败: ${e}`, 'err');
  }
}

// ── 等待激活 ──────────────────────────────────────────────────
async function waitActivation() {
  const email = document.getElementById('act-email').value.trim();
  const timeout = parseInt(document.getElementById('act-timeout').value) || 120;
  if (!email) { log('请输入邮箱地址', 'err'); return; }
  log(`等待 ${email} 的激活邮件...`);
  try {
    const d = await apiCall('POST', '/api/wait_activation_link', { address: email, timeout, interval: 1 });
    showResult('activate-result', d);
    if (d.ok) {
      log(`激活链接: ${d.link}`, 'ok');
      const ad = await apiCall('POST', '/api/sso/activate', { url: d.link });
      log(`激活结果: ${JSON.stringify(ad)}`, ad.ok ? 'ok' : 'err');
      showResult('activate-result', { ...d, activate: ad });
    } else {
      log(`等待失败: ${d.error}`, 'err');
    }
  } catch (e) {
    showResult('activate-result', 'Error: ' + e);
    log(`激活失败: ${e}`, 'err');
  }
}

// ── JWT ───────────────────────────────────────────────────────
function copyJwt() {
  const jwt = document.getElementById('jwt-token').value.trim();
  if (jwt) {
    navigator.clipboard.writeText(jwt);
    log('JWT 已复制', 'ok');
    document.getElementById('key-jwt').value = jwt;
  } else {
    log('JWT 为空', 'err');
  }
}

// ── Discovery 操作 ─────────────────────────────────────────────
async function claimGrant() {
  const jwt = document.getElementById('key-jwt').value.trim();
  if (!jwt) { log('请输入 JWT', 'err'); return; }
  log('领取免费额度...');
  try {
    const d = await apiCall('POST', '/api/discovery/claim_grant', { jwt });
    showResult('key-result', d);
    log(`领取结果: ${JSON.stringify(d)}`, d.ok ? 'ok' : 'err');
  } catch (e) {
    showResult('key-result', 'Error: ' + e);
  }
}

async function createApiKey() {
  const jwt = document.getElementById('key-jwt').value.trim();
  const name = document.getElementById('key-name').value.trim() || 'default';
  if (!jwt) { log('请输入 JWT', 'err'); return; }
  log('创建 API Key...');
  try {
    const d = await apiCall('POST', '/api/discovery/create_key', { jwt, name });
    showResult('key-result', d);
    log(`创建结果: ${JSON.stringify(d)}`, d.ok ? 'ok' : 'err');
  } catch (e) {
    showResult('key-result', 'Error: ' + e);
  }
}

async function checkBalance() {
  const jwt = document.getElementById('key-jwt').value.trim();
  if (!jwt) { log('请输入 JWT', 'err'); return; }
  try {
    const d = await apiCall('GET', `/api/discovery/balance?jwt=${encodeURIComponent(jwt)}`);
    showResult('key-result', d);
    log(`余额: ${JSON.stringify(d)}`);
  } catch (e) {
    showResult('key-result', 'Error: ' + e);
  }
}

async function listKeys() {
  const jwt = document.getElementById('key-jwt').value.trim();
  if (!jwt) { log('请输入 JWT', 'err'); return; }
  try {
    const d = await apiCall('GET', `/api/discovery/keys?jwt=${encodeURIComponent(jwt)}`);
    showResult('key-result', d);
    log(`Keys: ${JSON.stringify(d)}`);
  } catch (e) {
    showResult('key-result', 'Error: ' + e);
  }
}

// ── 邮件列表 ──────────────────────────────────────────────────
async function listMails() {
  const email = document.getElementById('view-email').value.trim();
  log(`查询邮件${email ? ': ' + email : ''}...`);
  try {
    const d = await apiCall('GET', `/api/mails?email=${encodeURIComponent(email)}&limit=50`);
    if (d && d.ok === false) {
      log(`查询失败: ${JSON.stringify(d)}`, 'err');
      return;
    }
    renderMails(d);
    log(`获取到 ${(d || []).length || 0} 封邮件`, 'ok');
  } catch (e) {
    log(`查询失败: ${e}`, 'err');
  }
}

function renderMails(mails) {
  const list = document.getElementById('mails-list');
  if (!mails || !mails.length) {
    list.innerHTML = '<p style="color:#888">暂无邮件</p>';
    return;
  }
  list.innerHTML = mails.map(m => `
    <div class="mail-item">
      <div class="from">📨 ${m.from_address}</div>
      <div class="subject">${m.subject || '(无主题)'}</div>
      <div class="links">
        ${(m.links || []).map(l => `<a href="${l}" target="_blank">${l}</a>`).join('')}
      </div>
    </div>
  `).join('');
}

function refreshMails() { listMails(); }

let watchInterval = null;
function watchEmails() {
  if (watchInterval) {
    clearInterval(watchInterval);
    watchInterval = null;
    log('已停止自动刷新');
  } else {
    watchInterval = setInterval(listMails, 5000);
    log('已开启自动刷新 (每5秒)');
    listMails();
  }
}

// ── 一键批量 ──────────────────────────────────────────────────
async function runBatch() {
  const domain = document.getElementById('batch-domain').value.trim();
  const count = parseInt(document.getElementById('batch-count').value);
  const prefix = document.getElementById('batch-prefix').value.trim();

  if (!domain) { log('请输入域名', 'err'); return; }
  if (count > 5) { log('批量数量过大，建议≤5', 'err'); return; }

  log(`🚀 批量创建 ${count} 个账号...`);

  for (let i = 0; i < count; i++) {
    log(`── 账号 ${i + 1}/${count} ──`);

    await new Promise(r => setTimeout(r, 500));
    let email = '';
    try {
      const d = await apiCall('POST', '/api/mailbox', { domain, prefix: prefix || null, count: 1 });
      if (d.ok && d.emails.length) {
        email = d.emails[0];
        document.getElementById('sso-email').value = email;
        document.getElementById('act-email').value = email;
        document.getElementById('view-email').value = email;
        log(`邮箱: ${email}`, 'ok');
      }
    } catch (e) {
      log(`创建邮箱失败: ${e}`, 'err');
      continue;
    }

    await new Promise(r => setTimeout(r, 800));
    const username = 'lz' + Math.random().toString().slice(2, 8);
    const password = 'Aa1' + Math.random().toString(36).slice(2, 10);
    document.getElementById('sso-username').value = username;
    document.getElementById('sso-password').value = password;

    try {
      const d = await apiCall('POST', '/api/sso/register', { username, email, password });
      if (d.ok) {
        log(`注册成功: ${username}`, 'ok');
        addToLedger({ email, username, password, sso_uid: d.sso_uid, status: 'registered' });
      } else {
        log(`注册失败: ${d.msg_code} ${d.msg}`, 'err');
      }
    } catch (e) {
      log(`注册失败: ${e}`, 'err');
    }

    await new Promise(r => setTimeout(r, 1000));
    try {
      const d = await apiCall('POST', '/api/wait_activation_link', { address: email, timeout: 120, interval: 1 });
      if (d.ok) {
        const ad = await apiCall('POST', '/api/sso/activate', { url: d.link });
        log(`激活: ${JSON.stringify(ad)}`, ad.ok ? 'ok' : 'err');
        updateLedger(email, { status: 'activated' });
      } else {
        log(`等待失败: ${d.error}`, 'err');
      }
    } catch (e) {
      log(`激活失败: ${e}`, 'err');
    }

    await new Promise(r => setTimeout(r, 1000));
  }

  log('⚠️ 批量完成！登录阶段仍需人工处理人机验证', 'err');
}

// ── 台账管理 ──────────────────────────────────────────────────
function addToLedger(rec) {
  ledger.push({ ...rec, created_at: new Date().toISOString() });
  renderLedger();
}

function updateLedger(email, updates) {
  const rec = ledger.find(r => r.email === email);
  if (rec) Object.assign(rec, updates);
  renderLedger();
}

function renderLedger() {
  const list = document.getElementById('ledger-list');
  if (!ledger.length) {
    list.innerHTML = '<p style="color:#888">暂无账号记录</p>';
    return;
  }
  list.innerHTML = ledger.map(r => `
    <div class="mail-item">
      <div class="from">${r.status === 'activated' ? '✅' : '📝'} ${r.email}</div>
      <div class="subject">用户: ${r.username} | 状态: ${r.status}</div>
    </div>
  `).join('');
}

function clearLedger() {
  if (!confirm('确定清空台账？')) return;
  ledger = [];
  renderLedger();
  log('台账已清空');
}
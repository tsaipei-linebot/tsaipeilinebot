/**
 * ==============================================================================
 * 主程式碼 (Core / Shared Module - 材霈有限公司)
 * ==============================================================================
 */

// ==============================================================================
// 1. 系統環境設定
// ==============================================================================
// 指令碼屬性只在載入 CONFIG 時集中讀取一次，避免同一次執行重複呼叫
// PropertiesService.getScriptProperties()（GAS 服務呼叫本身有固定開銷）
const _scriptProps = PropertiesService.getScriptProperties();

const CONFIG = {
  LINE_CHANNEL_ACCESS_TOKEN: (_scriptProps.getProperty('LINE_CHANNEL_ACCESS_TOKEN') || '').trim(),
  // GAS 的 Web App 無法讀取 HTTP Header（拿不到 X-Line-Signature），
  // 改用只有本系統與 LINE Webhook 設定網址知道的隨機密鑰，作為 Webhook 來源驗證。
  LINE_WEBHOOK_SECRET: (_scriptProps.getProperty('LINE_WEBHOOK_SECRET') || '').trim(),
  // 管理端查詢用密鑰：保護會回傳同仁名單等內部資料的 doGet 端點，避免公開洩漏
  ADMIN_API_SECRET: (_scriptProps.getProperty('ADMIN_API_SECRET') || '').trim(),
  NOTION_API_KEY: (_scriptProps.getProperty('NOTION_API_KEY') || '').trim(),
  NOTION_DATABASE_ID: (_scriptProps.getProperty('NOTION_DATABASE_ID') || '').replace(/-/g, '').trim(),
  NOTION_VERSION: '2022-06-28',
  GOOGLE_DRIVE_FOLDER_ID: (_scriptProps.getProperty('GOOGLE_DRIVE_FOLDER_ID') || '').trim(),
  SPREADSHEET_ID: (_scriptProps.getProperty('SPREADSHEET_ID') || '').trim(),
  SHEET_NAME_ORG: '員工主管組織表',
  SHEET_NAME_SALARY: '薪資補款紀錄',
  SHEET_NAME_PROJECT: '專案合約紀錄',
  HR_ACCOUNTING_EMAILS: _scriptProps.getProperty('HR_ACCOUNTING_EMAILS') || 'finance@tsaipei.com.tw',
  DEFAULT_LINE_GROUP_ID: 'C0fd6d96dc33202b3c636c5f3b62a5250',
  ADMIN_LINE_USER_ID: (_scriptProps.getProperty('ADMIN_LINE_USER_ID') || '').trim(),
  // Vertex AI（跟招募聊天機器人共用同一個 GCP 專案），取代原本 Gemini Developer API Key 的方式，
  // 避免卡在 AI Studio 免費層級極低的配額限制
  GCP_PROJECT_ID: (_scriptProps.getProperty('GCP_PROJECT_ID') || 'tsaipei-505807').trim(),
  GCP_LOCATION: (_scriptProps.getProperty('GCP_LOCATION') || 'global').trim()
};

// 正確 LINE ID 驗證正則
const LINE_ID_REGEX = /^[a-zA-Z0-9_-]{10,64}$/;

// SHA-256 密碼雜湊輔助函式
function sha256Hash(text) {
  const signature = Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256, String(text), Utilities.Charset.UTF_8);
  return signature.map(byte => (byte < 0 ? byte + 256 : byte).toString(16).padStart(2, '0')).join('');
}

// 常用「多值文字」拆分工具：依逗號、頓號、斜線、反斜線、換行、空白等常見分隔符號拆開並去除頭尾空白，
// 用於姓名清單、Email 清單、LINE ID 清單、縣市/行政區等多值欄位。取代原本散落在各檔案的重複正則。
// 刻意不自動過濾空字串（呼叫端如需過濾請自行 .filter(Boolean)），避免影響原本依索引位置對應的邏輯
// （例如姓名清單與 Email 清單依相同索引配對時，過濾行為需要兩邊一致才不會對錯位置）。
function splitMultiValue(str) {
  return String(str || '').split(/[,，、\/\\\s\n\r]+/).map(s => s.trim());
}

// ==============================================================================
// 管理員 LINE ID 共用工具 (AdminIdService)
// 統一 ADMIN_LINE_USER_ID 的拆分/清理邏輯，取代原本散落在各檔案的重複解析程式碼
// ==============================================================================
const AdminIdService = {
  // 回傳保留原始大小寫、僅去除非法字元的清單，供實際發送 LINE 訊息等需要真實 ID 的場合使用
  list: function() {
    const raw = (CONFIG.ADMIN_LINE_USER_ID || '').trim();
    if (!raw) return [];
    return splitMultiValue(raw)
      .map(s => s.replace(/[^a-zA-Z0-9_-]/g, ''))
      .filter(Boolean);
  },

  // 判斷指定 LINE ID 是否為系統管理員（不分大小寫比對）
  isAdmin: function(candidateId) {
    const clean = String(candidateId || '').trim().toUpperCase();
    if (!clean) return false;
    return this.list().some(id => id.toUpperCase() === clean);
  }
};

// ==============================================================================
// Vertex AI 驗證服務 (VertexAiAuthService)
// 用專用服務帳戶（Service Account）的 JSON 金鑰簽署 JWT、換取存取權杖，
// 完全不動 appsscript.json 既有的授權範圍設定，不影響 Gmail/Drive/Sheets 等既有功能。
// 金鑰存在指令碼屬性 VERTEX_SA_KEY_JSON（服務帳戶 JSON 金鑰的完整內容）。
// ==============================================================================
const VertexAiAuthService = {
  CACHE_KEY: 'VERTEX_AI_ACCESS_TOKEN',

  _base64UrlEncode: function(value) {
    const base64 = (typeof value === 'string')
      ? Utilities.base64Encode(value, Utilities.Charset.UTF_8)
      : Utilities.base64Encode(value);
    return base64.replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  },

  _getServiceAccountKey: function() {
    const raw = (PropertiesService.getScriptProperties().getProperty('VERTEX_SA_KEY_JSON') || '').trim();
    if (!raw) {
      throw new Error('尚未設定 VERTEX_SA_KEY_JSON（Vertex AI 服務帳戶金鑰），請至指令碼屬性設定');
    }
    try {
      return JSON.parse(raw);
    } catch (e) {
      throw new Error('VERTEX_SA_KEY_JSON 不是合法 JSON 格式: ' + e.toString());
    }
  },

  isConfigured: function() {
    return !!(PropertiesService.getScriptProperties().getProperty('VERTEX_SA_KEY_JSON') || '').trim();
  },

  // 取得存取權杖（快取到接近過期前，避免每次呼叫都重新簽署 JWT）
  getAccessToken: function() {
    try {
      const cached = CacheService.getScriptCache().get(this.CACHE_KEY);
      if (cached) return cached;
    } catch (cacheReadErr) {
      console.warn('讀取 Vertex AI 權杖快取失敗 (略過快取，重新取得):', cacheReadErr);
    }

    const keyInfo = this._getServiceAccountKey();
    const tokenUrl = keyInfo.token_uri || 'https://oauth2.googleapis.com/token';
    const nowSec = Math.floor(Date.now() / 1000);

    const header = { alg: 'RS256', typ: 'JWT' };
    const claimSet = {
      iss: keyInfo.client_email,
      scope: 'https://www.googleapis.com/auth/cloud-platform',
      aud: tokenUrl,
      iat: nowSec,
      exp: nowSec + 3600
    };

    const signatureInput = this._base64UrlEncode(JSON.stringify(header)) + '.' + this._base64UrlEncode(JSON.stringify(claimSet));
    const signatureBytes = Utilities.computeRsaSha256Signature(signatureInput, keyInfo.private_key);
    const jwt = signatureInput + '.' + this._base64UrlEncode(signatureBytes);

    const response = UrlFetchApp.fetch(tokenUrl, {
      method: 'post',
      contentType: 'application/x-www-form-urlencoded',
      payload: {
        grant_type: 'urn:ietf:params:oauth:grant-type:jwt-bearer',
        assertion: jwt
      },
      muteHttpExceptions: true
    });

    if (response.getResponseCode() !== 200) {
      throw new Error('取得 Vertex AI 存取權杖失敗 (HTTP ' + response.getResponseCode() + '): ' + response.getContentText());
    }

    const json = JSON.parse(response.getContentText());
    const accessToken = json.access_token;
    const expiresIn = json.expires_in || 3600;

    try {
      CacheService.getScriptCache().put(this.CACHE_KEY, accessToken, Math.max(expiresIn - 120, 60));
    } catch (cacheWriteErr) {
      console.warn('寫入 Vertex AI 權杖快取失敗 (不影響本次使用):', cacheWriteErr);
    }

    return accessToken;
  }
};

// 組出 Vertex AI generateContent 的完整請求網址
function buildVertexAiGenerateContentUrl(model) {
  const location = (CONFIG.GCP_LOCATION || 'global').trim();
  const host = (location === 'global') ? 'aiplatform.googleapis.com' : `${location}-aiplatform.googleapis.com`;
  return `https://${host}/v1/projects/${encodeURIComponent(CONFIG.GCP_PROJECT_ID)}/locations/${encodeURIComponent(location)}/publishers/google/models/${encodeURIComponent(model)}:generateContent`;
}

/**
 * 呼叫單一 Vertex AI 模型（含 429 額度限制重試），供 Project_Job.js / Project_BatchEnhance.js 共用，
 * 避免兩邊各自維護一份幾乎逐字相同的呼叫/重試/錯誤處理邏輯。
 * 成功時回傳模型輸出的原始文字（可能仍包含 ```json 標記，由呼叫端自行清理與解析 JSON）；
 * 失敗（含重試用盡、非 200 錯誤、例外）時回傳 null，由呼叫端決定是否換下一個模型或使用保底文案。
 *
 * @param {string} model 模型名稱，例如 'gemini-2.5-flash'
 * @param {string} prompt 傳給模型的完整 prompt
 * @param {string} logPrefix log 訊息前綴，方便從執行項目分辨是哪個服務呼叫的（例如 '[AiJob 4in1]'）
 */
function callVertexAiWithRetry(model, prompt, logPrefix) {
  const MAX_RETRY_PER_MODEL = 2; // 同一模型遇到 429 額度限制時的重試次數上限
  const RETRY_BASE_DELAY_MS = 1000; // 重試遞增等待時間基準

  let accessToken;
  try {
    accessToken = VertexAiAuthService.getAccessToken();
  } catch (authErr) {
    console.error(`${logPrefix} 取得 Vertex AI 存取權杖失敗:`, authErr);
    return null;
  }

  const url = buildVertexAiGenerateContentUrl(model);
  const options = {
    method: 'post',
    contentType: 'application/json',
    headers: { 'Authorization': 'Bearer ' + accessToken },
    payload: JSON.stringify({
      contents: [{ role: 'user', parts: [{ text: prompt }] }],
      generationConfig: {
        temperature: 0.1,
        responseMimeType: 'application/json'
      }
    }),
    muteHttpExceptions: true
  };

  for (let attempt = 0; attempt <= MAX_RETRY_PER_MODEL; attempt++) {
    try {
      console.log(`${logPrefix} 呼叫模型 [${model}] 生成內容...(第 ${attempt + 1} 次嘗試)`);
      const response = UrlFetchApp.fetch(url, options);
      const resCode = response.getResponseCode();

      if (resCode === 200) {
        const jsonRes = JSON.parse(response.getContentText());
        let textRes = '';
        const parts = jsonRes.candidates && jsonRes.candidates[0] && jsonRes.candidates[0].content && jsonRes.candidates[0].content.parts;
        if (Array.isArray(parts)) {
          parts.forEach(p => { if (p.text) textRes += p.text; });
        }
        if (textRes) {
          console.log(`✅ ${logPrefix} 模型 [${model}] 成功生成內容！`);
          return textRes;
        }
        // HTTP 200 但沒有文字內容：換下一個模型，重試同一模型也不會有幫助
        return null;
      }

      if (resCode === 429) {
        const willRetry = attempt < MAX_RETRY_PER_MODEL;
        console.warn(`⚠️ ${logPrefix} 模型 [${model}] 額度限制 (HTTP 429)，${willRetry ? '等待後重試...' : '重試已達上限，換下一個模型'}`);
        if (willRetry) {
          Utilities.sleep(RETRY_BASE_DELAY_MS * (attempt + 1));
          continue;
        }
        return null;
      }

      // 其他錯誤：記錄詳細狀態碼與回應內容方便排查，直接換下一個模型
      console.warn(`${logPrefix} 模型 [${model}] 呼叫失敗 (HTTP ${resCode}): ${response.getContentText().slice(0, 300)}`);
      return null;
    } catch (err) {
      console.warn(`${logPrefix} 模型 [${model}] 呼叫異常:`, err);
      return null;
    }
  }
  return null;
}

// 動態取得目標推播群組 ID
function getTargetLineGroupId() {
  const dynamicId = (PropertiesService.getScriptProperties().getProperty('TARGET_LINE_GROUP_ID') || '').trim();
  const rawId = dynamicId || CONFIG.DEFAULT_LINE_GROUP_ID || '';
  return String(rawId).replace(/[^a-zA-Z0-9_-]/g, '').trim();
}

// 動態取得 LINE Channel Access Token 並安全淨化
function getLineChannelAccessToken() {
  const rawToken = (PropertiesService.getScriptProperties().getProperty('LINE_CHANNEL_ACCESS_TOKEN') || CONFIG.LINE_CHANNEL_ACCESS_TOKEN || '').trim();
  return rawToken.replace(/^Bearer\s+/i, '').replace(/^["']|["']$/g, '').trim();
}

// ==============================================================================
// 2. 試算表自訂選單與快取管理
// ==============================================================================
function onOpen() {
  try {
    const ui = SpreadsheetApp.getUi();
    ui.createMenu('材霈系統專區')
      .addItem('🔄 立即重新整理組織快取', 'manualClearOrgCache')
      .addToUi();
  } catch (e) {
    console.log('onOpen 目前處於背景或獨立專案執行狀態，略過建立容器選單');
  }
}

function manualClearOrgCache() {
  OrgService.clearCache();
  try {
    const ui = SpreadsheetApp.getUi();
    ui.alert('✅ 組織表快取已成功清除！\n下次同仁登入時將自動載入最新試算表資料。');
  } catch (e) {
    console.log('✅ 組織表快取已成功清除！');
  }
}

// ==============================================================================
// 3. HTTP 路由進入點 (doGet & doPost)
// ==============================================================================
function doGet(e) {
  e = e || { parameter: {} };
  const action = (e.parameter && e.parameter.action) ? e.parameter.action : '';
  
  if (action === 'get_employees') {
    if (!isValidAdminApiSecret(e)) {
      return createJsonResponse({ status: 'error', message: 'unauthorized' });
    }
    try {
      const employees = OrgService.getBoundEmployeesList();
      return createJsonResponse({
        status: 'success',
        data: employees
      });
    } catch (err) {
      console.error('doGet get_employees 錯誤:', err);
      return createJsonResponse({ status: 'error', message: err.toString() });
    }
  }

  if (action === 'test_push') {
    if (!isValidAdminApiSecret(e)) {
      return createJsonResponse({ status: 'error', message: 'unauthorized' });
    }
    const targetId = (e.parameter.target_id || '').trim();
    if (!targetId) {
      return createJsonResponse({ status: 'error', message: '請在網址後加上 &target_id=欲測試的LINE_USER_ID' });
    }
    const testMsg = `🔔 【系統連線診斷】\n這是一則測試推播訊息，代表您的 LINE Bot 推播功能運作完全正常！\n測試時間：${Utilities.formatDate(new Date(), 'Asia/Taipei', 'yyyy-MM-dd HH:mm:ss')}`;
    const pushRes = LineService.pushMessage(targetId, [{ type: 'text', text: testMsg }]);
    return createJsonResponse({
      status: 'diagnostic_complete',
      targetId: targetId,
      result: pushRes ? { code: 200, body: 'Success' } : 'No response'
    });
  }

  if (action === 'check_supervisor') {
    if (!isValidAdminApiSecret(e)) {
      return createJsonResponse({ status: 'error', message: 'unauthorized' });
    }
    const name = (e.parameter.name || '').trim();
    const supList = OrgService.getSupervisorsByApplicantUserId('', name);
    return createJsonResponse({
      status: 'success',
      applicantName: name,
      foundSupervisors: supList
    });
  }
  
  return createJsonResponse({
    status: 'success',
    message: 'LINE 招募職缺、薪資補款、合約與職缺增強後端服務正常運行中',
    timestamp: new Date().toISOString()
  });
}

// 常數時間字串比對，避免密鑰比對時因提早比對失敗而洩漏時序資訊
function constantTimeEquals(a, b) {
  const strA = String(a || '');
  const strB = String(b || '');
  if (strA.length !== strB.length || strA.length === 0) return false;
  let diff = 0;
  for (let i = 0; i < strA.length; i++) {
    diff |= strA.charCodeAt(i) ^ strB.charCodeAt(i);
  }
  return diff === 0;
}

// 驗證 LINE Webhook 請求是否帶有正確的共用密鑰（因 GAS 無法讀取 X-Line-Signature header 改採此方案）
function isValidWebhookSecret(e) {
  const expected = CONFIG.LINE_WEBHOOK_SECRET;
  if (!expected) {
    console.error('❌ 尚未設定 LINE_WEBHOOK_SECRET，為安全起見一律拒絕 Webhook 請求！請至指令碼屬性設定後再啟用。');
    return false;
  }
  const provided = (e && e.parameter && e.parameter.webhook_secret) || '';
  return constantTimeEquals(provided, expected);
}

// 驗證管理端查詢請求是否帶有正確的 admin_secret，避免同仁名單等內部資料被公開查詢
function isValidAdminApiSecret(e) {
  const expected = CONFIG.ADMIN_API_SECRET;
  if (!expected) {
    console.error('❌ 尚未設定 ADMIN_API_SECRET，為安全起見一律拒絕此管理端查詢！請至指令碼屬性設定後再啟用。');
    return false;
  }
  const provided = (e && e.parameter && e.parameter.admin_secret) || '';
  return constantTimeEquals(provided, expected);
}

function doPost(e) {
  try {
    if (!e || !e.postData || !e.postData.contents) {
      return createJsonResponse({ status: 'error', message: '無效的請求資料' });
    }
    
    const postContent = e.postData.contents;
    let requestData;
    try {
      requestData = JSON.parse(postContent);
    } catch (parseErr) {
      return createJsonResponse({ status: 'error', message: 'JSON 解析失敗' });
    }
    
    if (requestData.events && Array.isArray(requestData.events)) {
      if (!isValidWebhookSecret(e)) {
        console.warn('❌ LINE Webhook 驗證失敗：webhook_secret 遺失或不相符，拒絕處理（可能為偽造請求）');
        return createJsonResponse({ status: 'error', message: 'unauthorized' });
      }
      LineWebhookService.handleEvents(requestData.events);
      return createJsonResponse({ status: 'success', message: 'LINE Webhook 已處理' });
    }
    
    const requestType = requestData.type;
    
    if (requestType === 'GET_JOBS') {
      const userName = String(requestData.userName || '').trim();
      const userId = String(requestData.userId || '').trim();
      const subordinates = requestData.subordinates || [];
      const jobs = NotionService.getAllJobsForSelect(userName, subordinates, userId);
      return createJsonResponse({ status: 'success', data: jobs });
    }

    if (requestType === 'VERIFY_LOGIN') {
      const name = String(requestData.name || '').trim();
      const pin = String(requestData.pin || '').trim();
      const result = OrgService.verifyEmployeePin(name, pin);
      return createJsonResponse(result);
    }

    if (requestType === 'SUBMIT_JOB') {
      const result = JobWorkflowService.processJobSubmission(requestData);
      return createJsonResponse(result);
    }
    
    if (requestType === 'SUBMIT_SALARY') {
      const result = SalaryWorkflowService.processSalarySubmission(requestData);
      return createJsonResponse(result);
    }

    if (requestType === 'SUBMIT_PROJECT') {
      const result = ProjectWorkflowService.processProjectSubmission(requestData);
      return createJsonResponse(result);
    }

    if (requestType === 'REGISTER_EMPLOYEE') {
      const result = EmployeeRegistrationService.processRegistration(requestData);
      return createJsonResponse(result);
    }

    // --- 專案四：批次增強與遠端快取管理路由 ---
    if (requestType === 'RUN_BATCH_ENHANCE') {
      const force = requestData.force === true;
      const result = BatchEnhanceJobService.runBatchEnhancement(force);
      return createJsonResponse({ status: 'success', data: result });
    }

    if (requestType === 'CLEAR_ORG_CACHE') {
      OrgService.clearCache();
      return createJsonResponse({ status: 'success', message: '組織表快取已清除' });
    }
    
    return createJsonResponse({ status: 'error', message: '未知的請求類型: ' + requestType });
  } catch (error) {
    console.error('doPost 執行錯誤:', error);
    return createJsonResponse({ status: 'error', message: error.toString() });
  }
}

function createJsonResponse(data) {
  return ContentService.createTextOutput(JSON.stringify(data))
    .setMimeType(ContentService.MimeType.JSON);
}

// ==============================================================================
// 4. 同仁 LINE 帳號自動綁定服務 (含 PIN 碼)
// ==============================================================================
const EmployeeRegistrationService = {
  processRegistration: function(payload) {
    const name = String(payload.name || '').trim();
    const pin = String(payload.pin || '').trim();
    const empNo = String(payload.empNo || '').trim();
    const userId = String(payload.userId || '').replace(/[^a-zA-Z0-9_-]/g, '').trim();
    let displayName = String(payload.displayName || '').trim();
    
    console.log(`收到同仁綁定請求：姓名=[${name}], PIN=[${pin}], LINE ID=[${userId}], 暱稱=[${displayName}]`);

    if (!name) {
      return { status: 'error', message: '請輸入同仁真實姓名' };
    }
    if (!pin || !/^\d{4}$/.test(pin)) {
      return { status: 'error', message: 'PIN 碼必須為 4 位數字' };
    }
    
    if (!userId || !LINE_ID_REGEX.test(userId)) {
      return { status: 'error', message: '無效的 LINE User ID' };
    }

    if (!displayName || displayName === 'LINE同仁') {
      const profile = LineService.getUserProfile(userId);
      if (profile && profile.displayName) {
        displayName = profile.displayName;
      }
    }
    
    try {
      const sheet = SpreadsheetService.getOrCreateSheet(CONFIG.SHEET_NAME_ORG);
      
      if (sheet.getLastRow() === 0) {
        sheet.appendRow(['員工姓名', '員工 LINE ID', '主管姓名', '主管 LINE ID', '主管 Email', '員工工號', 'LINE暱稱', '綁定時間', 'PIN碼', '員工Email']);
        SpreadsheetApp.flush();
      }

      const headers = sheet.getRange(1, 1, 1, Math.max(sheet.getLastColumn(), 10)).getValues()[0];
      if (!headers[8] || headers[8].toString().trim() === '') {
        sheet.getRange(1, 9).setValue('PIN碼');
      }
      if (!headers[9] || headers[9].toString().trim() === '') {
        sheet.getRange(1, 10).setValue('員工Email');
      }

      const data = sheet.getDataRange().getValues();
      let matchedRow = -1;
      let existingLineId = '';
      
      for (let i = 1; i < data.length; i++) {
        const rowName = String(data[i][0] || '').trim();
        if (rowName === name) {
          matchedRow = i + 1;
          existingLineId = String(data[i][1] || '').trim();
          break;
        }
      }

      if (matchedRow > 0 && existingLineId && LINE_ID_REGEX.test(existingLineId) && existingLineId !== userId) {
        return { 
          status: 'error', 
          message: `【綁定失敗】組織表中的「${name}」已綁定其他 LINE 帳號。\n若需更換帳號，請聯繫系統管理員協助解除舊綁定。` 
        };
      }
      
      const nowStr = Utilities.formatDate(new Date(), 'Asia/Taipei', 'yyyy-MM-dd HH:mm:ss');
      const hashedPin = sha256Hash(pin);
      let message = '';
      
      if (matchedRow > 0) {
        sheet.getRange(matchedRow, 2).setValue(userId);
        if (empNo) sheet.getRange(matchedRow, 6).setValue(empNo);
        if (displayName) sheet.getRange(matchedRow, 7).setValue(displayName);
        sheet.getRange(matchedRow, 8).setValue(nowStr);
        sheet.getRange(matchedRow, 9).setValue(hashedPin);
        SpreadsheetApp.flush();
        
        message = `已成功為【${name}】更新組織表第 ${matchedRow} 列之 LINE ID 與 PIN 碼綁定！`;
      } else {
        sheet.appendRow([name, userId, '', '', '', empNo, displayName, nowStr, hashedPin]);
        SpreadsheetApp.flush();
        
        const newRowIdx = sheet.getLastRow();
        message = `已在組織表第 ${newRowIdx} 列建立【${name}】資料並完成 LINE ID 與 PIN 碼綁定！`;
      }
      
      OrgService.clearCache();

      try {
        const confirmCard = SharedFlexBuilder.buildRegistrationSuccessCard({
          name: name,
          empNo: empNo,
          displayName: displayName || '同仁',
          userId: userId,
          pin: pin
        });
        LineService.pushMessage(userId, [confirmCard]);
      } catch (pushErr) {
        console.warn('推播確認卡片失敗:', pushErr);
      }
      
      return {
        status: 'success',
        message: message,
        userId: userId,
        name: name
      };
      
    } catch (err) {
      console.error('員工 LINE 綁定處理失敗:', err);
      return { status: 'error', message: '寫入試算表失敗: ' + err.toString() };
    }
  }
};

// ==============================================================================
// 5. LINE Webhook 總分流處理服務 (LineWebhookService)
// ==============================================================================
const LineWebhookService = {
  handleEvents: function(events) {
    events.forEach(event => {
      if (event.type === 'postback' && event.postback && event.postback.data) {
        this.processPostback(event);
      }
      if (event.type === 'message' && event.message && event.message.type === 'text') {
        this.processTextMessage(event);
      }
    });
  },

  processTextMessage: function(event) {
    const text = (event.message.text || '').trim();
    const replyToken = event.replyToken;
    const source = event.source || {};
    const userId = source.userId || '';
    const groupId = source.groupId || source.roomId || '';

    // 群組綁定指令
    if (/^[#＃]?(綁定群組|群組綁定|bindgroup|groupid)/i.test(text)) {
      if (AdminIdService.list().length > 0 && !AdminIdService.isAdmin(userId)) {
        LineService.replyTextMessage(replyToken, `❌ 權限不足：僅系統管理員可設定自動推播群組。`);
        return;
      }

      if (groupId) {
        PropertiesService.getScriptProperties().setProperty('TARGET_LINE_GROUP_ID', groupId);
        const reply = `✅ 【群組綁定成功】\n本群組 ID：\n${groupId}\n\n已成功將此群組設定為「職缺核准自動推播群組」！`;
        LineService.replyTextMessage(replyToken, reply);
      } else {
        LineService.replyTextMessage(replyToken, `⚠️ 此指令僅能在 LINE「群組」或「多人聊天室」中使用以取得 Group ID。`);
      }
      return;
    }

    // 同仁身分綁定指令：綁定+真實姓名+4位PIN碼
    if (/^[#＃]?(綁定|bind|BIND)[+＋\s]/i.test(text)) {
      const cleanContent = text.replace(/^[#＃]?(綁定|bind|BIND)[+＋\s]*/i, '').trim();
      const parts = cleanContent.split(/[+＋\s]+/);

      const cleanName = parts[0] ? parts[0].trim() : '';
      const pinCode = parts[1] ? parts[1].trim() : '';

      if (!cleanName || !pinCode || !/^\d{4}$/.test(pinCode)) {
        const helpMsg = "⚠️ 綁定指令格式錯誤！\n\n請依照格式直接在聊天室發送：\n【綁定+真實姓名+4位PIN碼】\n\n💡 範例：\n綁定+胡少凱+1234\n\n(PIN碼為 4 位數字，登入網頁系統時使用)";
        LineService.replyTextMessage(replyToken, helpMsg);
        return;
      }

      const result = EmployeeRegistrationService.processRegistration({
        name: cleanName,
        pin: pinCode,
        empNo: '',
        userId: userId,
        displayName: ''
      });

      if (result.status === 'success') {
        LineService.replyTextMessage(replyToken, `🎉 ${result.message}\n您的 LINE User ID (${userId}) 與 PIN 碼已成功與系統完成同步！`);
      } else {
        LineService.replyTextMessage(replyToken, `❌ 綁定失敗：${result.message}`);
      }
    }
  },
  
  processPostback: function(event) {
    const postbackData = parseQueryString(event.postback.data);
    const action = postbackData.action;
    const applicantId = postbackData.applicant_id;
    const operatorSupervisorId = event.source.userId;
    
    // 嚴格簽核權限驗證
    if (action === 'review_job' || action === 'review_salary') {
      const allSupervisors = OrgService.getSupervisorsByApplicantUserId(applicantId, '');
      const authorizedIds = allSupervisors.map(s => String(s.lineUserId || '').trim().toUpperCase());
      const currentUserId = String(operatorSupervisorId || '').trim().toUpperCase();

      if (!authorizedIds.includes(currentUserId) && !AdminIdService.isAdmin(currentUserId)) {
        LineService.replyTextMessage(event.replyToken, `❌ 操作失敗：您非此申請單之授權審核主管，無權限執行簽核。`);
        return;
      }
    }

    // 依業務類別分流給專屬模組
    if (action === 'review_job') {
      JobWorkflowService.handleJobPostback(event, postbackData, operatorSupervisorId);
    } else if (action === 'review_salary') {
      SalaryWorkflowService.handleSalaryPostback(event, postbackData, operatorSupervisorId);
    }
  }
};

// ==============================================================================
// 6. PIN 登入嘗試鎖定服務 (LoginAttemptGuard)
// 防止 VERIFY_LOGIN 端點被暴力窮舉 4 位數 PIN 碼：
// 同一姓名連續錯誤達上限後，短時間內鎖定該姓名的登入嘗試。
// ==============================================================================
const LoginAttemptGuard = {
  MAX_ATTEMPTS: 5,
  LOCKOUT_SEC: 900, // 15 分鐘

  _cacheKey: function(name) {
    return 'LOGIN_FAIL_' + Utilities.base64EncodeWebSafe(String(name || '').trim());
  },

  isLocked: function(name) {
    try {
      const raw = CacheService.getScriptCache().get(this._cacheKey(name));
      const count = raw ? parseInt(raw, 10) : 0;
      return count >= this.MAX_ATTEMPTS;
    } catch (e) {
      console.warn('讀取登入鎖定狀態失敗 (視為未鎖定):', e);
      return false;
    }
  },

  recordFailure: function(name) {
    try {
      const cache = CacheService.getScriptCache();
      const key = this._cacheKey(name);
      const raw = cache.get(key);
      const count = (raw ? parseInt(raw, 10) : 0) + 1;
      cache.put(key, String(count), this.LOCKOUT_SEC);
    } catch (e) {
      console.warn('記錄登入失敗次數失敗:', e);
    }
  },

  clear: function(name) {
    try {
      CacheService.getScriptCache().remove(this._cacheKey(name));
    } catch (e) {}
  }
};

// ==============================================================================
// 7. 組織架構與主管從屬服務 (OrgService)
// ==============================================================================
const OrgService = {
  CACHE_KEY: 'ORG_TABLE_CACHE_DATA_2026',
  CACHE_TTL_SEC: 7200,

  clearCache: function() {
    try {
      const cache = CacheService.getScriptCache();
      cache.remove(this.CACHE_KEY);
      console.log('🗑️ 組織表快取已手動清除');
    } catch (e) {
      console.warn('清除快取發生異常:', e);
    }
  },

  getOrgData: function(forceRefresh = false) {
    const cache = CacheService.getScriptCache();
    
    if (!forceRefresh) {
      try {
        const cachedJson = cache.get(this.CACHE_KEY);
        if (cachedJson) {
          return JSON.parse(cachedJson);
        }
      } catch (e) {
        console.warn('讀取快取失敗，將重新向試算表撈取:', e);
      }
    }

    const sheet = SpreadsheetService.getOrCreateSheet(CONFIG.SHEET_NAME_ORG);
    const data = sheet.getDataRange().getValues();

    try {
      cache.put(this.CACHE_KEY, JSON.stringify(data), this.CACHE_TTL_SEC);
    } catch (e) {
      console.warn('寫入快取失敗 (資料可能過大):', e);
    }

    return data;
  },

  getBoundEmployeesList: function() {
    try {
      const data = this.getOrgData();
      const list = [];
      
      for (let i = 1; i < data.length; i++) {
        const name = String(data[i][0] || '').trim();
        const lineId = String(data[i][1] || '').trim();
        const empNo = String(data[i][5] || '').trim();
        
        if (name && lineId && LINE_ID_REGEX.test(lineId)) {
          list.push({
            name: name,
            empNo: empNo,
            label: empNo ? `${name} (${empNo})` : name
          });
        }
      }
      return list;
    } catch (e) {
      console.error('取得已綁定員工名單失敗:', e);
      return [];
    }
  },

  getSubordinatesBySupervisorName: function(supervisorName) {
    try {
      const data = this.getOrgData();
      const cleanSupervisor = String(supervisorName || '').trim();
      const subordinates = [];

      if (!cleanSupervisor || data.length <= 1) return subordinates;

      for (let i = 1; i < data.length; i++) {
        const empName = String(data[i][0] || '').trim();
        const rawSupNames = String(data[i][2] || '').trim();
        
        if (empName && rawSupNames) {
          const supList = splitMultiValue(rawSupNames).filter(Boolean);
          if (supList.includes(cleanSupervisor)) {
            subordinates.push(empName);
          }
        }
      }
      return subordinates;
    } catch (e) {
      console.error('查詢主管部屬名單失敗:', e);
      return [];
    }
  },

  isSupervisorOf: function(supervisorName, employeeName) {
    try {
      const data = this.getOrgData();
      const cleanSupervisor = String(supervisorName || '').trim();
      const cleanEmployee = String(employeeName || '').trim();

      if (!cleanSupervisor || !cleanEmployee || data.length <= 1) return false;

      for (let i = 1; i < data.length; i++) {
        const empName = String(data[i][0] || '').trim();
        if (empName === cleanEmployee) {
          const rawSupNames = String(data[i][2] || '').trim();
          const supList = splitMultiValue(rawSupNames).filter(Boolean);
          return supList.includes(cleanSupervisor);
        }
      }
      return false;
    } catch (e) {
      console.error('比對主管從屬關係失敗:', e);
      return false;
    }
  },

  verifyEmployeePin: function(name, pin) {
    try {
      const cleanName = String(name || '').trim();
      const cleanPin = String(pin || '').trim();

      if (!cleanName || !cleanPin || !/^\d{4}$/.test(cleanPin)) {
        return { status: 'error', message: '請輸入姓名與 4 位數 PIN 碼' };
      }

      if (LoginAttemptGuard.isLocked(cleanName)) {
        console.warn(`⚠️ 【${cleanName}】登入嘗試已達失敗上限，暫時鎖定中`);
        return {
          status: 'locked',
          message: `登入失敗次數過多，帳號已暫時鎖定，請 15 分鐘後再試，或聯繫系統管理員協助處理。`
        };
      }

      const data = this.getOrgData();
      const inputHashedPin = sha256Hash(cleanPin);

      for (let i = 1; i < data.length; i++) {
        const row = data[i];
        const empName = String(row[0] || '').trim();
        const lineId = String(row[1] || '').trim();
        const empPin = String(row[8] || '').trim();

        if (empName === cleanName) {
          if (!lineId || !LINE_ID_REGEX.test(lineId)) {
            return {
              status: 'unbound',
              message: `同仁【${cleanName}】尚未於 LINE 聊天室完成綁定！\n請先發送「綁定+${cleanName}+4位PIN碼」完成綁定。`
            };
          }
          if (constantTimeEquals(empPin, inputHashedPin) || constantTimeEquals(empPin, cleanPin)) {
            LoginAttemptGuard.clear(cleanName);

            // 若比對命中的是明文 PIN（尚未雜湊的舊資料），登入成功時順便升級寫回雜湊值，
            // 之後就只會用雜湊比對，逐步清除表格中的明文 PIN
            if (empPin !== inputHashedPin) {
              try {
                const orgSheet = SpreadsheetService.getOrCreateSheet(CONFIG.SHEET_NAME_ORG);
                orgSheet.getRange(i + 1, 9).setValue(inputHashedPin);
                SpreadsheetApp.flush();
                console.log(`🔐 已將【${cleanName}】的明文 PIN 自動升級為雜湊值`);
              } catch (upgradeErr) {
                console.warn('自動升級 PIN 雜湊失敗 (不影響本次登入):', upgradeErr);
              }
            }

            const subordinates = this.getSubordinatesBySupervisorName(empName);
            
            const isAdmin = AdminIdService.isAdmin(lineId);
            
            return {
              status: 'success',
              message: '登入驗證成功',
              name: empName,
              lineId: lineId,
              empNo: String(row[5] || '').trim(),
              subordinates: subordinates,
              isAdmin: isAdmin,
              user: {
                name: empName,
                lineId: lineId,
                empNo: String(row[5] || '').trim(),
                subordinates: subordinates,
                isAdmin: isAdmin
              }
            };
          } else {
            LoginAttemptGuard.recordFailure(cleanName);
            return { status: 'error', message: 'PIN 碼錯誤，請重新輸入！' };
          }
        }
      }
      return { status: 'not_found', message: `組織表中查無同仁【${cleanName}】的資料，請洽管理員。` };
    } catch (e) {
      console.error('驗證 PIN 碼失敗:', e);
      return { status: 'error', message: '驗證系統發生異常: ' + e.toString() };
    }
  },

  getEmployeeBindingByName: function(name) {
    try {
      const data = this.getOrgData();
      const cleanName = String(name || '').trim();
      
      if (!cleanName || data.length <= 1) {
        return { isBound: false, empLineId: '' };
      }
      
      for (let i = 1; i < data.length; i++) {
        const row = data[i];
        const empName = String(row[0] || '').trim();
        const empLineId = String(row[1] || '').trim();
        
        if (empName === cleanName) {
          if (empLineId && LINE_ID_REGEX.test(empLineId)) {
            return { isBound: true, empLineId: empLineId };
          } else {
            return { isBound: false, empLineId: '' };
          }
        }
      }
    } catch (e) {
      console.error('查詢同仁綁定狀態失敗:', e);
    }
    return { isBound: false, empLineId: '' };
  },

  getSupervisorsByApplicantUserId: function(userId, displayName) {
    try {
      const data = this.getOrgData();
      
      const cleanUserId = String(userId || '').trim().toUpperCase();
      const cleanDisplayName = String(displayName || '').trim();
      
      if (data.length <= 1) {
        return this.getDefaultSupervisors();
      }

      const employeeLineIdMap = {};
      for (let j = 1; j < data.length; j++) {
        const eName = String(data[j][0] || '').trim();
        const eLineId = String(data[j][1] || '').trim();
        if (eName && eLineId && LINE_ID_REGEX.test(eLineId)) {
          employeeLineIdMap[eName] = eLineId;
        }
      }
      
      for (let i = 1; i < data.length; i++) {
        const row = data[i];
        const empName = String(row[0] || '').trim();
        const empLineId = String(row[1] || '').trim().toUpperCase();
        
        if ((cleanUserId && empLineId === cleanUserId) || (cleanDisplayName && empName === cleanDisplayName)) {
          const rawSupNames = String(row[2] || '').trim();
          const rawSupLineIds = String(row[3] || '').trim();
          const rawSupEmails = String(row[4] || '').trim();

          const names = splitMultiValue(rawSupNames).filter(Boolean);
          const lineIds = splitMultiValue(rawSupLineIds).filter(Boolean);
          const emails = splitMultiValue(rawSupEmails).filter(Boolean);

          const result = [];

          for (let k = 0; k < lineIds.length; k++) {
            const lid = lineIds[k].replace(/[^a-zA-Z0-9_-]/g, '').trim();
            if (lid && LINE_ID_REGEX.test(lid)) {
              result.push({
                supervisorName: names[k] || names[0] || '審核主管',
                lineUserId: lid,
                email: emails[k] || emails[0] || ''
              });
            }
          }

          if (result.length === 0 && names.length > 0) {
            for (let k = 0; k < names.length; k++) {
              const supName = names[k];
              if (employeeLineIdMap[supName]) {
                result.push({
                  supervisorName: supName,
                  lineUserId: employeeLineIdMap[supName],
                  email: emails[k] || emails[0] || ''
                });
              }
            }
          }

          if (result.length > 0) {
            return result;
          }
          return this.getDefaultSupervisors();
        }
      }
      
      return this.getDefaultSupervisors();
    } catch (e) {
      console.error('查詢主管組織表失敗:', e);
      return this.getDefaultSupervisors();
    }
  },
  
  getDefaultSupervisors: function() {
    const adminEmail = PropertiesService.getScriptProperties().getProperty('ADMIN_EMAIL') || '';
    const lineIds = AdminIdService.list();

    if (lineIds.length === 0) {
      return [];
    }

    const emails = splitMultiValue(adminEmail).filter(Boolean);

    return lineIds.map((lid, idx) => ({
      supervisorName: '管理主管',
      lineUserId: lid,
      email: emails[idx] || emails[0] || ''
    }));
  }
};

// ==============================================================================
// 8. Google Sheets 儲存服務 (SpreadsheetService)
// ==============================================================================
const SpreadsheetService = {
  getOrCreateSheet: function(sheetName) {
    let ss;
    if (CONFIG.SPREADSHEET_ID) {
      ss = SpreadsheetApp.openById(CONFIG.SPREADSHEET_ID);
    } else {
      ss = SpreadsheetApp.getActiveSpreadsheet();
    }
    
    if (!ss) {
      throw new Error('無法取得 Google 試算表，請在 GAS 指令碼屬性確認 SPREADSHEET_ID 是否已填寫。');
    }
    
    const targetName = String(sheetName || '').trim();
    let sheet = ss.getSheetByName(targetName);
    
    if (!sheet) {
      const allSheets = ss.getSheets();
      for (let i = 0; i < allSheets.length; i++) {
        if (allSheets[i].getName().trim() === targetName) {
          sheet = allSheets[i];
          break;
        }
      }
    }
    
    if (!sheet) {
      sheet = ss.insertSheet(targetName);
      SpreadsheetApp.flush();
    }
    return sheet;
  }
};

// ==============================================================================
// 9. LINE Messaging API 通訊服務 (LineService)
// ==============================================================================
const LineService = {
  getUserProfile: function(userId) {
    const token = getLineChannelAccessToken();
    if (!userId || !token) return null;
    try {
      const url = `https://api.line.me/v2/bot/profile/${userId}`;
      const res = UrlFetchApp.fetch(url, {
        method: 'get',
        headers: {
          'Authorization': 'Bearer ' + token
        },
        muteHttpExceptions: true
      });
      if (res.getResponseCode() === 200) {
        return JSON.parse(res.getContentText());
      }
    } catch (e) {
      console.warn('取得 LINE User Profile 失敗:', e);
    }
    return null;
  },

  pushMessage: function(toTargetId, messages) {
    const cleanTargetId = String(toTargetId || '').replace(/[^a-zA-Z0-9_-]/g, '').trim();
    if (!cleanTargetId) {
      console.error('❌ pushMessage 失敗：未指定目標 LINE ID');
      return false;
    }
    
    const token = getLineChannelAccessToken();
    if (!token) {
      console.error('❌ pushMessage 失敗：未設定 LINE_CHANNEL_ACCESS_TOKEN');
      return false;
    }
    
    const url = 'https://api.line.me/v2/bot/message/push';
    const messageList = Array.isArray(messages) ? messages : [messages];
    const payload = {
      to: cleanTargetId,
      messages: messageList
    };
    
    try {
      const response = UrlFetchApp.fetch(url, {
        method: 'post',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': 'Bearer ' + token
        },
        payload: JSON.stringify(payload),
        muteHttpExceptions: true
      });
      
      const resCode = response.getResponseCode();
      const resText = response.getContentText();
      console.log(`📤 LINE 推播至 [${cleanTargetId}] - HTTP ${resCode}: ${resText}`);
      
      if (resCode !== 200) {
        console.warn(`⚠️ Flex 卡片推播失敗 (HTTP ${resCode}: ${resText})，嘗試發送純文字訊息備援...`);
        let fallbackText = '【材霈招募系統】您有一則新的審核通知。';
        for (let m = 0; m < messageList.length; m++) {
          if (messageList[m] && messageList[m].text) {
            fallbackText = messageList[m].text;
            break;
          } else if (messageList[m] && messageList[m].altText) {
            fallbackText = messageList[m].altText;
          }
        }
        
        const fallbackRes = UrlFetchApp.fetch(url, {
          method: 'post',
          headers: {
            'Content-Type': 'application/json',
            'Authorization': 'Bearer ' + token
          },
          payload: JSON.stringify({
            to: cleanTargetId,
            messages: [{ type: 'text', text: fallbackText }]
          }),
          muteHttpExceptions: true
        });
        console.log(`📤 純文字備援推播至 [${cleanTargetId}] - HTTP ${fallbackRes.getResponseCode()}: ${fallbackRes.getContentText()}`);
        return fallbackRes.getResponseCode() === 200;
      }
      return true;
    } catch (err) {
      console.error(`LINE pushMessage 網路連線例外錯誤 [${cleanTargetId}]:`, err);
      return false;
    }
  },
  
  replyTextMessage: function(replyToken, text) {
    const token = getLineChannelAccessToken();
    const url = 'https://api.line.me/v2/bot/message/reply';
    const payload = {
      replyToken: replyToken,
      messages: [{ type: 'text', text: text }]
    };
    
    return UrlFetchApp.fetch(url, {
      method: 'post',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': 'Bearer ' + token
      },
      payload: JSON.stringify(payload),
      muteHttpExceptions: true
    });
  }
};

// ==============================================================================
// 10. 基礎共用 Flex 與字串工具
// ==============================================================================
const SharedFlexBuilder = {
  createRow: function(label, value, valueColor, weight) {
    let displayVal = '-';
    if (Array.isArray(value)) {
      displayVal = value.length > 0 ? value.join('、') : '-';
    } else if (value !== undefined && value !== null && String(value).trim() !== '') {
      displayVal = String(value).trim();
    }
    
    const validWeight = (weight === 'bold') ? 'bold' : 'regular';

    return {
      type: 'box',
      layout: 'horizontal',
      contents: [
        {
          type: 'text',
          text: String(label || '-'),
          size: 'xs',
          color: '#64748b',
          flex: 4
        },
        {
          type: 'text',
          text: displayVal,
          size: 'xs',
          color: valueColor || '#1e293b',
          align: 'end',
          weight: validWeight,
          flex: 6,
          wrap: true
        }
      ]
    };
  },

  buildNoSupervisorWarningCard: function(data) {
    return {
      type: 'flex',
      altText: `⚠️ 【送審未成功】尚未指派審核主管`,
      contents: {
        type: 'bubble',
        header: {
          type: 'box',
          layout: 'vertical',
          backgroundColor: '#fff1f2',
          contents: [
            {
              type: 'text',
              text: '⚠️ 送審作業未成功',
              size: 'sm',
              color: '#e11d48',
              weight: 'bold'
            }
          ]
        },
        body: {
          type: 'box',
          layout: 'vertical',
          spacing: 'md',
          contents: [
            {
              type: 'text',
              text: `${data.applicantName || '同仁'} 您好：`,
              weight: 'bold',
              size: 'md',
              color: '#0f172a'
            },
            {
              type: 'text',
              text: `您提交的【${data.actionType || '申請'}】未能完成送審，原因是組織表中尚未為您指派「審核主管」。`,
              size: 'xs',
              color: '#475569',
              wrap: true
            },
            {
              type: 'separator',
              margin: 'sm'
            },
            {
              type: 'box',
              layout: 'vertical',
              spacing: 'xs',
              contents: [
                this.createRow('申請項目', data.actionType || '-'),
                this.createRow('標的內容', data.itemTitle || '-', '#0284c7', 'bold'),
                this.createRow('目前狀態', '未指派主管 / 無法簽核', '#e11d48', 'bold')
              ]
            },
            {
              type: 'box',
              layout: 'vertical',
              backgroundColor: '#f8fafc',
              paddingAll: 'md',
              cornerRadius: 'md',
              contents: [
                {
                  type: 'text',
                  text: '💡 處理方式：\n請聯繫「系統管理員」，請管理員至公司【員工主管組織表】填入您的直屬主管姓名與主管 LINE ID，完成設定後即可正常送審。',
                  size: 'xxs',
                  color: '#64748b',
                  wrap: true
                }
              ]
            }
          ]
        },
        footer: {
          type: 'box',
          layout: 'vertical',
          contents: [
            {
              type: 'text',
              text: '材霈有限公司 內部作業系統',
              size: 'xxs',
              color: '#94a3b8',
              align: 'center'
            }
          ]
        }
      }
    };
  },

  buildRegistrationSuccessCard: function(data) {
    const name = String(data.name || '同仁');
    const empNo = String(data.empNo || '無');
    const displayName = String(data.displayName || '同仁');
    const pin = String(data.pin || '****');
    
    return {
      type: 'flex',
      altText: `🎉 【材霈系統】同仁身分與 PIN 碼綁定成功：${name}`,
      contents: {
        type: 'bubble',
        header: {
          type: 'box',
          layout: 'vertical',
          backgroundColor: '#ecfdf5',
          contents: [
            {
              type: 'text',
              text: '🎉 帳號身分與 PIN 碼綁定成功',
              size: 'sm',
              color: '#065f46',
              weight: 'bold'
            }
          ]
        },
        body: {
          type: 'box',
          layout: 'vertical',
          spacing: 'md',
          contents: [
            {
              type: 'text',
              text: `${name} 同仁 您好！`,
              weight: 'bold',
              size: 'lg',
              color: '#0f172a',
              wrap: true
            },
            {
              type: 'text',
              text: '您的 LINE 帳號與 4 位數 PIN 碼已順利與公司招募與薪資系統完成對應綁定，登入網頁時請輸入姓名與此 PIN 碼。',
              size: 'xs',
              color: '#475569',
              wrap: true
            },
            {
              type: 'separator',
              margin: 'md'
            },
            {
              type: 'box',
              layout: 'vertical',
              margin: 'md',
              spacing: 'sm',
              contents: [
                this.createRow('同仁姓名', name, '#0284c7', 'bold'),
                this.createRow('登入PIN碼', pin, '#059669', 'bold'),
                this.createRow('員工工號', empNo, '#334155', 'regular'),
                this.createRow('LINE 暱稱', displayName, '#334155', 'regular'),
                this.createRow('綁定狀態', '已完成連線', '#059669', 'bold')
              ]
            }
          ]
        },
        footer: {
          type: 'box',
          layout: 'vertical',
          contents: [
            {
              type: 'text',
              text: '材霈有限公司 內部作業系統',
              size: 'xxs',
              color: '#94a3b8',
              align: 'center'
            }
          ]
        }
      }
    };
  }
};

// 遮蔽身分證字號等敏感個資，僅保留頭尾各 3 碼供核對，中間以 * 取代
// 用於推播管道（如 LINE Flex 卡片）等曝光面較廣的場合；正式報表 Email 仍保留完整號碼
function maskIdCard(idCard) {
  const clean = String(idCard || '').trim();
  if (!clean) return '-';
  if (clean.length <= 6) return clean.slice(0, 1) + '*'.repeat(Math.max(clean.length - 1, 0));
  const visibleHead = clean.slice(0, 3);
  const visibleTail = clean.slice(-3);
  const maskedLen = clean.length - visibleHead.length - visibleTail.length;
  return visibleHead + '*'.repeat(maskedLen) + visibleTail;
}

function parseQueryString(queryString) {
  const params = {};
  if (!queryString) return params;

  const pairs = queryString.split('&');
  for (let i = 0; i < pairs.length; i++) {
    const pair = pairs[i].split('=');
    params[decodeURIComponent(pair[0])] = decodeURIComponent(pair[1] || '');
  }
  return params;
}

// ==============================================================================
// 11. 一次性設定工具：建立會計專用對帳試算表 (與「薪資補款紀錄」「專案合約紀錄」即時同步)
// ==============================================================================
/**
 * 建立一份獨立的「材霈會計對帳表」，透過 QUERY + IMPORTRANGE 與員工主管組織表
 * 底下的「薪資補款紀錄」「專案合約紀錄」兩個分頁即時同步（含日後審核狀態更新）。
 * 薪資補款紀錄的「申請人 LINE ID」欄位刻意排除，不同步給會計看到內部系統識別碼。
 *
 * 使用方式：在 Apps Script 編輯器選取這個函式、按「執行」，
 * 完成後到「執行項目」記錄或彈出視窗查看新試算表網址。
 * 只需要執行這一次；之後兩邊資料會自動同步，不用再跑第二次。
 *
 * 執行完成後，第一次打開新試算表時，Google 會跳出「授權存取」提示，
 * 需要手動點一次「允許存取」，之後才會開始正常同步顯示資料。
 */
function setupAccountingSyncSpreadsheet() {
  const salarySheet = SpreadsheetService.getOrCreateSheet(CONFIG.SHEET_NAME_SALARY);
  const projectSheet = SpreadsheetService.getOrCreateSheet(CONFIG.SHEET_NAME_PROJECT);
  const sourceSpreadsheetId = salarySheet.getParent().getId();

  const newSs = SpreadsheetApp.create('材霈會計對帳表（薪資補款/專案合約）');
  const defaultSheet = newSs.getSheets()[0];

  // 薪資補款紀錄：A~U 共 21 欄，排除 D 欄「申請人 LINE ID」(Col4)，其餘 20 欄原樣同步
  const salaryTab = newSs.insertSheet(CONFIG.SHEET_NAME_SALARY);
  const salaryCols = ['Col1', 'Col2', 'Col3', 'Col5', 'Col6', 'Col7', 'Col8', 'Col9', 'Col10',
    'Col11', 'Col12', 'Col13', 'Col14', 'Col15', 'Col16', 'Col17', 'Col18', 'Col19', 'Col20', 'Col21'].join(', ');
  salaryTab.getRange('A1').setFormula(
    `=QUERY(IMPORTRANGE("${sourceSpreadsheetId}", "${CONFIG.SHEET_NAME_SALARY}!A:U"), "select ${salaryCols}", 1)`
  );

  // 專案合約紀錄：A~J 共 10 欄，沒有敏感的內部識別碼欄位，全部同步
  const projectTab = newSs.insertSheet(CONFIG.SHEET_NAME_PROJECT);
  projectTab.getRange('A1').setFormula(
    `=IMPORTRANGE("${sourceSpreadsheetId}", "${CONFIG.SHEET_NAME_PROJECT}!A:J")`
  );

  newSs.deleteSheet(defaultSheet);

  const resultMsg = '✅ 會計對帳表建立完成！\n網址：' + newSs.getUrl() +
    '\n\n請先自己打開這個網址一次，Google 會跳出「授權存取來源試算表」的提示，點選「允許存取」後才會開始正常同步。\n完成後再把這份試算表分享給會計（建議只給「檢視者」權限）。';
  console.log(resultMsg);
  try {
    SpreadsheetApp.getUi().alert(resultMsg);
  } catch (uiErr) {
    // 若非在試算表容器內執行（例如直接在 Apps Script 編輯器執行），沒有 UI 可彈窗，僅記錄在執行項目 log 即可
  }
  return resultMsg;
}

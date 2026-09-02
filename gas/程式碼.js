/**
 * ==============================================================================
 * 主程式碼 (Core / Shared Module - 材霈有限公司)
 * ==============================================================================
 */

// ==============================================================================
// 1. 系統環境設定
// ==============================================================================
const CONFIG = {
  LINE_CHANNEL_ACCESS_TOKEN: (PropertiesService.getScriptProperties().getProperty('LINE_CHANNEL_ACCESS_TOKEN') || '').trim(),
  // GAS 的 Web App 無法讀取 HTTP Header（拿不到 X-Line-Signature），
  // 改用只有本系統與 LINE Webhook 設定網址知道的隨機密鑰，作為 Webhook 來源驗證。
  LINE_WEBHOOK_SECRET: (PropertiesService.getScriptProperties().getProperty('LINE_WEBHOOK_SECRET') || '').trim(),
  // 管理端查詢用密鑰：保護會回傳同仁名單等內部資料的 doGet 端點，避免公開洩漏
  ADMIN_API_SECRET: (PropertiesService.getScriptProperties().getProperty('ADMIN_API_SECRET') || '').trim(),
  NOTION_API_KEY: (PropertiesService.getScriptProperties().getProperty('NOTION_API_KEY') || '').trim(),
  NOTION_DATABASE_ID: (PropertiesService.getScriptProperties().getProperty('NOTION_DATABASE_ID') || '').replace(/-/g, '').trim(),
  NOTION_VERSION: '2022-06-28',
  GOOGLE_DRIVE_FOLDER_ID: (PropertiesService.getScriptProperties().getProperty('GOOGLE_DRIVE_FOLDER_ID') || '').trim(),
  SPREADSHEET_ID: (PropertiesService.getScriptProperties().getProperty('SPREADSHEET_ID') || '').trim(),
  SHEET_NAME_ORG: '員工主管組織表',
  SHEET_NAME_SALARY: '薪資補款紀錄',
  HR_ACCOUNTING_EMAILS: PropertiesService.getScriptProperties().getProperty('HR_ACCOUNTING_EMAILS') || 'finance@tsaipei.com.tw',
  DEFAULT_LINE_GROUP_ID: 'C0fd6d96dc33202b3c636c5f3b62a5250',
  ADMIN_LINE_USER_ID: (PropertiesService.getScriptProperties().getProperty('ADMIN_LINE_USER_ID') || '').trim()
};

// 正確 LINE ID 驗證正則
const LINE_ID_REGEX = /^[a-zA-Z0-9_-]{10,64}$/;

// SHA-256 密碼雜湊輔助函式
function sha256Hash(text) {
  const signature = Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256, String(text), Utilities.Charset.UTF_8);
  return signature.map(byte => (byte < 0 ? byte + 256 : byte).toString(16).padStart(2, '0')).join('');
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
        sheet.appendRow(['員工姓名', '員工 LINE ID', '主管姓名', '主管 LINE ID', '主管 Email', '員工工號', 'LINE暱稱', '綁定時間', 'PIN碼']);
        SpreadsheetApp.flush();
      }

      const headers = sheet.getRange(1, 1, 1, Math.max(sheet.getLastColumn(), 9)).getValues()[0];
      if (!headers[8] || headers[8].toString().trim() === '') {
        sheet.getRange(1, 9).setValue('PIN碼');
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
      const adminIds = (CONFIG.ADMIN_LINE_USER_ID || '').split(/[,，、\/\\\s\n\r]+/).map(s => s.replace(/[^a-zA-Z0-9_-]/g, '').trim()).filter(Boolean);
      if (adminIds.length > 0 && !adminIds.includes(userId)) {
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
      let authorizedIds = allSupervisors.map(s => String(s.lineUserId || '').trim().toUpperCase());
      
      const adminIds = (CONFIG.ADMIN_LINE_USER_ID || '').split(/[,，、\/\\\s\n\r]+/).map(s => s.trim().toUpperCase()).filter(Boolean);
      authorizedIds = authorizedIds.concat(adminIds);
      
      const currentUserId = String(operatorSupervisorId || '').trim().toUpperCase();

      if (authorizedIds.length === 0 || !authorizedIds.includes(currentUserId)) {
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
          const supList = rawSupNames.split(/[,，、\/\\\s\n\r]+/).map(s => s.trim()).filter(Boolean);
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
          const supList = rawSupNames.split(/[,，、\/\\\s\n\r]+/).map(s => s.trim()).filter(Boolean);
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
          if (empPin === inputHashedPin || empPin === cleanPin) {
            LoginAttemptGuard.clear(cleanName);
            const subordinates = this.getSubordinatesBySupervisorName(empName);
            
            const adminIds = (CONFIG.ADMIN_LINE_USER_ID || '').split(/[,，、\/\\\s\n\r]+/).map(s => s.trim().toUpperCase()).filter(Boolean);
            const isAdmin = adminIds.includes(lineId.toUpperCase());
            
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

          const names = rawSupNames.split(/[,，、\/\\\s\n\r]+/).map(s => s.trim()).filter(Boolean);
          const lineIds = rawSupLineIds.split(/[,，、\/\\\s\n\r]+/).map(s => s.trim()).filter(Boolean);
          const emails = rawSupEmails.split(/[,，、\/\\\s\n\r]+/).map(s => s.trim()).filter(Boolean);

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
    const adminLineId = (PropertiesService.getScriptProperties().getProperty('ADMIN_LINE_USER_ID') || '').trim();
    const adminEmail = PropertiesService.getScriptProperties().getProperty('ADMIN_EMAIL') || '';
    
    if (!adminLineId) {
      return [];
    }

    const lineIds = adminLineId.split(/[,，、\/\\\s\n\r]+/).map(s => s.replace(/[^a-zA-Z0-9_-]/g, '').trim()).filter(Boolean);
    const emails = adminEmail.split(/[,，、\/\\\s\n\r]+/).map(s => s.trim()).filter(Boolean);

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
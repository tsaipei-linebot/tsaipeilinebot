/**
 * ==============================================================================
 * 專案二：補款系統 (Project_Salary.gs - 材霈有限公司)
 * ==============================================================================
 */

// ==============================================================================
// 1. 薪資補款工作流程服務 (SalaryWorkflowService)
// ==============================================================================
const SalaryWorkflowService = {
  processSalarySubmission: function(payload) {
    const info = payload.info || {};
    const applicantName = String(info.applicant_name || (payload.applicant && payload.applicant.displayName) || '').trim();
    const employeeName = String(info.name || '').trim();

    if (!applicantName) {
      return {
        status: 'unauthorized',
        message: '請選取「申請同仁姓名」，以利系統核對您的送審權限。'
      };
    }

    if (!employeeName) {
      return {
        status: 'error',
        message: '請填寫「員工姓名」（實際補款對象）。'
      };
    }

    // 備註說明必填檢核
    if (!info.notes || !String(info.notes).trim()) {
      return {
        status: 'error',
        message: '請填寫「備註說明」（必填）。'
      };
    }

    // 重複申請偵測：補款員工姓名 + 身分證字號 + 補請款月份 三者都相同時，
    // 視為疑似重複申請，直接擋下不給送出（已退回的申請單會被整列刪除，
    // 所以這裡掃到的都是「待審核」或「已核准」的有效紀錄，不需要再另外篩狀態）
    const dupIdCard = String(info.id_card || '').trim().toUpperCase();
    const dupCompensateMonth = String(info.compensate_month || '').trim();
    const dupCheckSheet = SpreadsheetService.getOrCreateSheet(CONFIG.SHEET_NAME_SALARY);
    if (dupCheckSheet.getLastRow() > 1) {
      const existingRows = dupCheckSheet.getDataRange().getValues();
      for (let i = 1; i < existingRows.length; i++) {
        const rowName = String(existingRows[i][4] || '').trim();
        const rowIdCard = String(existingRows[i][5] || '').trim().toUpperCase();
        const rowCompensateMonth = normalizeMonthValue(existingRows[i][10]);

        if (rowName === employeeName && rowIdCard === dupIdCard && rowCompensateMonth === dupCompensateMonth) {
          return {
            status: 'error',
            message: `系統偵測到員工【${employeeName}】在補請款月份【${dupCompensateMonth}】已有一筆補款申請紀錄（單號：${existingRows[i][0]}），請勿重複申請！如需修改請確認原申請單的處理狀況，或聯繫系統管理員協助處理。`
          };
        }
      }
    }

    const employeeBinding = OrgService.getEmployeeBindingByName(applicantName);
    if (!employeeBinding.isBound) {
      return {
        status: 'unauthorized',
        message: `申請同仁【${applicantName}】尚未完成 LINE 身分綁定！\n請先至 LINE 官方帳號發送「綁定+${applicantName}+4位PIN碼」完成綁定後再進行補款申請。`
      };
    }

    const applicant = {
      displayName: applicantName,
      userId: employeeBinding.empLineId
    };

    const supervisorList = OrgService.getSupervisorsByApplicantUserId(applicant.userId, applicant.displayName);
    if (!supervisorList || supervisorList.length === 0) {
      try {
        const warnCard = SharedFlexBuilder.buildNoSupervisorWarningCard({
          applicantName: applicantName,
          actionType: '薪資補款申請',
          itemTitle: `補款員工：${employeeName} (${info.vendor || '廠商'})`
        });
        LineService.pushMessage(applicant.userId, [warnCard]);
      } catch (pushErr) {
        console.warn('推播未配置主管警示失敗:', pushErr);
      }

      return {
        status: 'supervisor_unassigned',
        message: `【送審失敗】組織表中尚未為申請同仁【${applicantName}】設定審核主管！\n系統已發送 LINE 通知給您，請聯繫系統管理員協助於後台組織表指派主管。`
      };
    }

    const earnings = payload.earnings || {};
    const deductions = payload.deductions || {};
    const summary = payload.summary || {};

    // 後端依明細重新加總，不信任前端算好的總額（避免有人略過網頁表單、直接對送審端點送出竄改過的總額）
    const computedTotalEarnings = Object.values(earnings).reduce((sum, v) => sum + (Number(v) || 0), 0);
    const computedTotalDeductions = Object.values(deductions).reduce((sum, v) => sum + (Number(v) || 0), 0);
    const computedNetTotal = computedTotalEarnings - computedTotalDeductions;

    if (computedTotalEarnings !== Number(summary.total_earnings || 0)
        || computedTotalDeductions !== Number(summary.total_deductions || 0)) {
      console.warn(`薪資補款金額前後端不一致，已改用後端重算值：前端加項=${summary.total_earnings} 後端加項=${computedTotalEarnings}，前端扣項=${summary.total_deductions} 後端扣項=${computedTotalDeductions}`);
    }

    const verifiedSummary = {
      total_earnings: computedTotalEarnings,
      total_deductions: computedTotalDeductions,
      net_total: computedNetTotal
    };

    const salaryId = 'SAL-' + Utilities.formatDate(new Date(), 'Asia/Taipei', 'yyyyMMddHHmmss');
    
    // 處理補款佐證圖檔上傳
    let imageUrl = '';
    if (payload.image && payload.image.base64) {
      try {
        imageUrl = this.uploadSalaryImageToDrive(payload.image.base64, payload.image.filename, salaryId);
      } catch (imgErr) {
        console.warn('上傳補款佐證圖檔失敗:', imgErr);
      }
    }

    const sheet = SpreadsheetService.getOrCreateSheet(CONFIG.SHEET_NAME_SALARY);

    // 若分頁全空，自動寫入標準 22 欄標題列 (A~V)
    if (sheet.getLastRow() === 0) {
      sheet.appendRow([
        '補款單號', '申請時間', '申請人姓名', '申請人 LINE ID', '員工姓名', '身分證', '廠商/店家',
        '申請日', '付款日', '扣分鐘月份', '補請款月份', '是否可請款', '補款方式',
        '加項小計', '扣項小計', '實補總額', '備註', '審核狀態', '核准主管', '核准時間',
        '補款佐證(照片)', '匯費'
      ]);
      SpreadsheetApp.flush();
    }

    // 既有試算表（在新增「匯費」欄位之前就已經在用）不會被上面那個「全空才建表頭」
    // 的邏輯補到新欄位，這裡額外檢查一次、自動補上 V 欄標題，不影響既有資料
    const salaryHeaders = sheet.getRange(1, 1, 1, Math.max(sheet.getLastColumn(), 22)).getValues()[0];
    if (!salaryHeaders[21] || String(salaryHeaders[21]).trim() === '') {
      sheet.getRange(1, 22).setValue('匯費');
    }

    const applyTimestamp = Utilities.formatDate(new Date(), 'Asia/Taipei', 'yyyy-MM-dd HH:mm:ss');

    // 精準對標寫入 22 個欄位資料 (A ~ V 欄)
    sheet.appendRow([
      salaryId,                     // A (1): 補款單號
      applyTimestamp,               // B (2): 申請時間
      applicantName,                // C (3): 申請人姓名
      applicant.userId,             // D (4): 申請人 LINE ID
      employeeName,                 // E (5): 員工姓名
      info.id_card,                 // F (6): 身分證
      info.vendor,                  // G (7): 廠商/店家
      info.apply_date,              // H (8): 申請日
      info.pay_date || '',          // I (9): 付款日 (選填)
      info.deduct_month || '',      // J (10): 扣分鐘月份 (修正：移至第 10 欄)
      info.compensate_month,        // K (11): 補請款月份 (修正：正確落於第 11 欄)
      info.is_claimable,            // L (12): 是否可請款
      info.pay_type,                // M (13): 補款方式
      verifiedSummary.total_earnings,   // N (14): 加項小計
      verifiedSummary.total_deductions, // O (15): 扣項小計
      verifiedSummary.net_total,        // P (16): 實補總額
      info.notes,                   // Q (17): 備註 (必填)
      '待審核',                     // R (18): 審核狀態
      '',                           // S (19): 核准主管
      '',                           // T (20): 核准時間
      imageUrl || '',                // U (21): 補款佐證(照片)
      Number(deductions.remit_fee) || 0  // V (22): 匯費（扣項明細裡的其中一項，核准報表要單獨顯示）
    ]);
    SpreadsheetApp.flush();
    
    const flexMessage = SalaryFlexMessageBuilder.buildSalaryApprovalCard({
      salaryId: salaryId,
      applicant: applicant,
      info: info,
      summary: verifiedSummary,
      earnings: earnings,
      deductions: deductions,
      imageUrl: imageUrl
    });

    let pushSuccessCount = 0;

    supervisorList.forEach(sup => {
      const cleanLineId = String(sup.lineUserId || '').replace(/[^a-zA-Z0-9_-]/g, '').trim();
      if (LINE_ID_REGEX.test(cleanLineId)) {
        try {
          const res = LineService.pushMessage(cleanLineId, [flexMessage]);
          if (res) pushSuccessCount++;
        } catch (pushErr) {
          console.warn('推播薪資審核卡片失敗:', pushErr);
        }
      }
    });
    
    if (pushSuccessCount === 0) {
      try {
        LineService.pushMessage(applicant.userId, [{
          type: 'text',
          text: `⚠️ 【系統警告】補款單 [${salaryId}] 已成功建立，但系統無法推播給您的主管！\n\n可能原因：主管尚未完成 LINE 綁定或已封鎖官方帳號。\n👉 請主動聯繫主管進行審核。`
        }]);
      } catch (e) {}
    }

    return {
      status: 'success',
      message: '薪資補款單已成功建立並送出審核',
      salaryId: salaryId
    };
  },

  uploadSalaryImageToDrive: function(base64Data, filename, salaryId) {
    if (!base64Data) return '';
    const cleanBase64 = base64Data.replace(/^data:image\/[a-z]+;base64,/, '');
    const decodedBytes = Utilities.base64Decode(cleanBase64);
    const safeFilename = `Salary_${salaryId}_${filename || 'proof.jpg'}`;
    const blob = Utilities.newBlob(decodedBytes, 'image/jpeg', safeFilename);
    
    let folder;
    if (CONFIG.GOOGLE_DRIVE_FOLDER_ID) {
      try {
        folder = DriveApp.getFolderById(CONFIG.GOOGLE_DRIVE_FOLDER_ID);
      } catch (fErr) {
        console.warn('無法開啟指定資料夾，改存入雲端根目錄:', fErr);
        folder = DriveApp.getRootFolder();
      }
    } else {
      folder = DriveApp.getRootFolder();
    }
    
    const file = folder.createFile(blob);
    try {
      file.setSharing(DriveApp.Access.ANYONE_WITH_LINK, DriveApp.Permission.VIEW);
    } catch (shareErr) {
      console.warn('設定檔案公開檢視失敗:', shareErr);
    }
    // 使用可直接內嵌顯示的圖片網址（跟職缺圖檔上傳一致），而非 Drive 檢視頁網址，
    // 這樣才能實際嵌入 LINE Flex 卡片的 image 區塊讓主管直接看到圖片
    return `https://lh3.googleusercontent.com/d/${file.getId()}`;
  },

  handleSalaryPostback: function(event, postbackData, operatorSupervisorId) {
    const status = postbackData.status;
    const applicantId = postbackData.applicant_id;
    const salaryId = postbackData.salary_id;

    const isApproved = (status === 'approve');
    const reviewStatus = isApproved ? '已核准' : '已退回';
    const nowStr = Utilities.formatDate(new Date(), 'Asia/Taipei', 'yyyy-MM-dd HH:mm:ss');

    // 用鎖把「檢查是否已審核」跟「寫入新狀態」包成同一個不可被打斷的動作，
    // 避免主管快速點兩下、或 LINE 重送同一個 postback 事件時，兩個請求都讀到
    // 「尚未審核」而各自通過檢查，造成同一張補款單被重複核准、重複寄出正式報表。
    const lock = LockService.getScriptLock();
    let lockAcquired = false;
    try {
      lockAcquired = lock.tryLock(10000);
    } catch (lockErr) {
      console.error('取得薪資審核鎖定失敗:', lockErr);
    }
    if (!lockAcquired) {
      LineService.replyTextMessage(event.replyToken, '❌ 系統忙碌中（可能有人同時在審核），請稍後再試一次。');
      return;
    }

    let salaryRecord;
    try {
      const salaryRecordCheck = SalarySheetService.getSalaryRecord(salaryId);
      if (!salaryRecordCheck) {
        LineService.replyTextMessage(event.replyToken, '❌ 操作失敗：找不到該薪資補款單資料。');
        return;
      }
      if (salaryRecordCheck.reviewStatus === '已核准' || salaryRecordCheck.reviewStatus === '已退回') {
        LineService.replyTextMessage(event.replyToken, `⚠️ 操作無效：此單據已由主管完成審核 (目前狀態：${salaryRecordCheck.reviewStatus})，無法重複簽核。`);
        return;
      }

      salaryRecord = SalarySheetService.updateSalaryReviewStatus(salaryId, reviewStatus, operatorSupervisorId, nowStr);
    } finally {
      lock.releaseLock();
    }

    if (isApproved && salaryRecord) {
      try {
        EmailService.sendSalaryCompensationReport(salaryRecord);
      } catch (mailErr) {
        console.error('發送薪資補款信件失敗:', mailErr);
      }
    }
    
    const replyText = isApproved
      ? `✅ 薪資補款單 [${salaryId}] 審核完成：已核准！\n系統已自動寄出正式 HTML 薪資補款報表與佐證圖檔至財會、主管與同仁信箱。`
      : `❌ 薪資補款單 [${salaryId}] 審核完成：已退回！`;
    LineService.replyTextMessage(event.replyToken, replyText);
    
    if (applicantId && LINE_ID_REGEX.test(applicantId)) {
      const notifyText = isApproved
        ? `🎉 您提交的薪資補款申請單 [${salaryId}] 已通過主管核准！\n詳細補款報表與附件已同步發信通知。`
        : `⚠️ 您提交的薪資補款申請單 [${salaryId}] 已被主管退回，請確認資料後重新提出。`;
      LineService.pushMessage(applicantId, [{ type: 'text', text: notifyText }]);
    }

    // 同步通知其他主管（同仁若設定多位主管，除了實際點擊審核的操作者，其餘主管也要收到結果通知）
    try {
      const allSupervisors = OrgService.getSupervisorsByApplicantUserId(applicantId, '');
      const syncText = isApproved
        ? `✅ 【審核同步】薪資補款單 [${salaryId}] 已由其他主管核准！\n系統已自動寄出正式 HTML 薪資補款報表與佐證圖檔至財會、主管與同仁信箱。`
        : `⚠️ 【審核同步】薪資補款單 [${salaryId}] 已由其他主管退回。`;
      allSupervisors.forEach(sup => {
        const cleanLineId = String(sup.lineUserId || '').replace(/[^a-zA-Z0-9_-]/g, '').trim();
        if (LINE_ID_REGEX.test(cleanLineId) && cleanLineId !== operatorSupervisorId) {
          LineService.pushMessage(cleanLineId, [{ type: 'text', text: syncText }]);
        }
      });
    } catch (supSyncErr) {
      console.warn('同步通知其他主管審核結果失敗:', supSyncErr);
    }
  }
};

// ==============================================================================
// 2. 補款試算表服務 (SalarySheetService)
// ==============================================================================
const SalarySheetService = {
  getSalaryRecord: function(salaryId) {
    const sheet = SpreadsheetService.getOrCreateSheet(CONFIG.SHEET_NAME_SALARY);
    const data = sheet.getDataRange().getValues();
    
    for (let i = 1; i < data.length; i++) {
      if (String(data[i][0]).trim() === salaryId) {
        return {
          reviewStatus: String(data[i][17] || '').trim() // R 欄 (第 18 欄, index 17)
        };
      }
    }
    return null;
  },
  
  updateSalaryReviewStatus: function(salaryId, status, supervisorId, approvedTime) {
    const sheet = SpreadsheetService.getOrCreateSheet(CONFIG.SHEET_NAME_SALARY);
    const data = sheet.getDataRange().getValues();
    
    for (let i = 1; i < data.length; i++) {
      if (String(data[i][0]).trim() === salaryId) {
        const rowIdx = i + 1;

        // 已退回的補款單不需要留存在試算表裡（也不需要同步給會計看到），直接刪除該列
        if (status === '已退回') {
          sheet.deleteRow(rowIdx);
          SpreadsheetApp.flush();
          console.log(`🗑️ 薪資補款單 [${salaryId}] 已退回，已從「${CONFIG.SHEET_NAME_SALARY}」刪除該筆紀錄`);
          return null;
        }

        sheet.getRange(rowIdx, 18).setValue(status);        // R (18): 審核狀態
        sheet.getRange(rowIdx, 19).setValue(supervisorId);  // S (19): 核准主管
        sheet.getRange(rowIdx, 20).setValue(approvedTime);  // T (20): 核准時間
        SpreadsheetApp.flush();
        
        const applicantName = String(data[i][2] || '').trim();
        const applicantUserId = String(data[i][3] || '').trim();

        // 取得主管 Email
        let supervisorEmail = '';
        try {
          const supervisorList = OrgService.getSupervisorsByApplicantUserId(applicantUserId, applicantName);
          if (supervisorList && supervisorList.length > 0) {
            supervisorEmail = supervisorList.map(s => s.email).filter(Boolean).join(',');
          }
        } catch (orgErr) {
          console.warn('查詢主管 Email 失敗 (略過以避免中斷):', orgErr);
        }

        // 取得申請人本人 Email
        const applicantEmail = this.findApplicantEmail(applicantName, applicantUserId);
        
        return {
          salaryId: data[i][0],           // A (1)
          applyTimestamp: data[i][1],     // B (2)
          applicantName: applicantName,   // C (3)
          applicantUserId: applicantUserId,// D (4)
          name: data[i][4],               // E (5)
          idCard: data[i][5],             // F (6)
          vendor: data[i][6],             // G (7)
          applyDate: data[i][7],          // H (8)
          payDate: data[i][8],            // I (9)
          deductMonth: data[i][9],        // J (10)
          compensateMonth: data[i][10],   // K (11)
          isClaimable: data[i][11],       // L (12)
          payType: data[i][12],           // M (13)
          totalEarnings: data[i][13],     // N (14)
          totalDeductions: data[i][14],   // O (15)
          netTotal: data[i][15],          // P (16)
          notes: data[i][16],             // Q (17)
          reviewStatus: status,           // R (18)
          approvedSupervisor: supervisorId,// S (19)
          approvedTime: approvedTime,     // T (20)
          imageUrl: data[i][20] || '',    // U (21): 補款佐證(照片)
          remitFee: Number(data[i][21]) || 0, // V (22): 匯費（舊資料若沒有這欄會是空值，預設為 0）
          supervisorEmail: supervisorEmail,
          applicantEmail: applicantEmail
        };
      }
    }
    return null;
  },

  findApplicantEmail: function(applicantName, applicantUserId) {
    try {
      const sheet = SpreadsheetService.getOrCreateSheet(CONFIG.SHEET_NAME_ORG);
      const data = sheet.getDataRange().getValues();
      const cleanName = String(applicantName || '').trim();
      const cleanUserId = String(applicantUserId || '').trim().toUpperCase();

      // 1. 優先讀取「員工Email」欄位 (第 10 欄, index 9)，這是申請人本人 Email 的正式來源
      for (let i = 1; i < data.length; i++) {
        const empName = String(data[i][0] || '').trim();
        const empLineId = String(data[i][1] || '').trim().toUpperCase();
        if ((cleanName && empName === cleanName) || (cleanUserId && empLineId === cleanUserId)) {
          const ownEmail = String(data[i][9] || '').trim();
          if (ownEmail && /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(ownEmail)) {
            return ownEmail;
          }
          break;
        }
      }

      // 2. 若「員工Email」欄位未填，退而求其次：若申請人在組織表中曾列為主管，讀取其主管 Email (第 5 欄)
      for (let i = 1; i < data.length; i++) {
        const rowSupNames = String(data[i][2] || '').trim();
        const rowSupEmails = String(data[i][4] || '').trim();
        if (rowSupNames && rowSupEmails) {
          const names = splitMultiValue(rowSupNames);
          const emails = splitMultiValue(rowSupEmails);
          for (let k = 0; k < names.length; k++) {
            if (names[k] === cleanName && emails[k] && /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(emails[k])) {
              return emails[k];
            }
          }
        }
      }

      // 3. 最後備援：搜尋同仁所屬列中是否有符合 Email 格式之欄位
      for (let i = 1; i < data.length; i++) {
        const empName = String(data[i][0] || '').trim();
        const empLineId = String(data[i][1] || '').trim().toUpperCase();
        if ((cleanName && empName === cleanName) || (cleanUserId && empLineId === cleanUserId)) {
          for (let c = 0; c < data[i].length; c++) {
            if (c !== 4 && c !== 9) { // 避開主管 Email 欄與員工 Email 欄（已在步驟 1 處理過）
              const cellVal = String(data[i][c] || '').trim();
              if (/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(cellVal)) {
                return cellVal;
              }
            }
          }
        }
      }
    } catch (e) {
      console.warn('自動取得申請人 Email 發生異常:', e);
    }
    return '';
  }
};

// 將試算表讀出的日期值（Sheets 常會把日期/月份字串自動轉成 Date 物件）格式化成
// 民國年簡短格式（如 115.09.14 或月份型的 115.09），避免信件裡直接印出 Date 物件
// toString() 的完整英文長字串（如 "Mon Sep 14 2026 00:00:00 GMT+0800..."）
function formatMinguoDate(value, includeDay) {
  if (!value) return '';
  let d = value;
  if (!(d instanceof Date)) {
    d = new Date(value);
    if (isNaN(d.getTime())) return String(value).trim(); // 無法解析就照原樣顯示，不強行硬轉
  }
  const minguoYear = d.getFullYear() - 1911;
  const monthDay = Utilities.formatDate(d, 'Asia/Taipei', includeDay ? 'MM.dd' : 'MM');
  return `${minguoYear}.${monthDay}`;
}

// 把試算表讀出的「補請款月份」正規化成 yyyy-MM 字串，用於重複申請比對；
// 該欄位常被 Sheets 自動轉成 Date 物件存放，跟表單送來的 "2026-09" 字串型式不同，
// 需要統一格式才能正確比對是否為同一個月份
function normalizeMonthValue(value) {
  if (!value) return '';
  if (value instanceof Date) {
    return Utilities.formatDate(value, 'Asia/Taipei', 'yyyy-MM');
  }
  return String(value).trim();
}

// ==============================================================================
// 3. 電子郵件報表發送服務 (EmailService)
// ==============================================================================
const EmailService = {
  sendSalaryCompensationReport: function(record) {
    const recipientSet = new Set();

    // 1. 加入專案設定之 HR 與財會信箱
    if (CONFIG.HR_ACCOUNTING_EMAILS) {
      splitMultiValue(CONFIG.HR_ACCOUNTING_EMAILS).forEach(em => {
        const clean = String(em || '').trim();
        if (clean && /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(clean)) recipientSet.add(clean);
      });
    }
    
    // 2. 加入審核主管信箱
    if (record.supervisorEmail) {
      splitMultiValue(record.supervisorEmail).forEach(em => {
        const cleanSupEmail = String(em || '').trim();
        if (cleanSupEmail && /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(cleanSupEmail)) recipientSet.add(cleanSupEmail);
      });
    }

    // 3. 加入申請人本人信箱
    if (record.applicantEmail) {
      splitMultiValue(record.applicantEmail).forEach(em => {
        const cleanAppEmail = String(em || '').trim();
        if (cleanAppEmail && /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(cleanAppEmail)) recipientSet.add(cleanAppEmail);
      });
    }

    const recipientList = Array.from(recipientSet);
    const finalRecipientString = recipientList.join(',');

    if (!finalRecipientString) {
      console.warn('⚠️ 未配置任何有效之收件人信箱，略過郵件發送。');
      return;
    }

    // 處理圖檔附件與內嵌 CID
    let emailAttachments = [];
    let inlineImagesMap = {};
    let imageHtmlSection = '';

    if (record.imageUrl) {
      const match = record.imageUrl.match(/[-\w]{25,}/);
      if (match) {
        try {
          const driveFile = DriveApp.getFileById(match[0]);
          const imageBlob = driveFile.getBlob().setName(`補款佐證_${record.salaryId}.jpg`);
          emailAttachments.push(imageBlob);
          inlineImagesMap['salaryProofImg'] = imageBlob;

          imageHtmlSection = `
            <div class="section-title">二、補款佐證單據與圖檔</div>
            <div style="background-color: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 16px; text-align: center; margin-bottom: 20px;">
              <img src="cid:salaryProofImg" alt="補款佐證圖檔" style="max-width: 100%; max-height: 480px; border-radius: 6px; box-shadow: 0 2px 4px rgba(0,0,0,0.08);" />
              <div style="margin-top: 10px;">
                <a href="${record.imageUrl}" target="_blank" style="display: inline-block; padding: 6px 14px; background-color: #0284c7; color: #ffffff; text-decoration: none; border-radius: 4px; font-size: 12px; font-weight: bold;">
                  🔗 開啟 Google 雲端檢視高畫質原圖
                </a>
              </div>
            </div>
          `;
        } catch (fErr) {
          console.warn('讀取 Google Drive 圖片檔案失敗:', fErr);
          imageHtmlSection = `
            <div class="section-title">二、補款佐證圖檔</div>
            <p style="font-size: 13px;"><a href="${record.imageUrl}" target="_blank" style="color: #0284c7;">🔗 點此開啟雲端佐證圖檔</a></p>
          `;
        }
      }
    }

    // 產生會計留底用的 PDF 存查單，失敗不影響信件本身照常寄出（只是少一個附件）
    try {
      const pdfBlob = this.buildSalaryPdfBlob(record);
      if (pdfBlob) {
        emailAttachments.push(pdfBlob);
      }
    } catch (pdfErr) {
      console.warn('產生薪資補款 PDF 存查單失敗 (不影響信件寄送):', pdfErr);
    }

    const subject = `【薪資補款單 - 審核通過】${record.name} - ${record.vendor} (單號: ${record.salaryId})`;
    
    const htmlBody = `
    <!DOCTYPE html>
    <html>
    <head>
      <meta charset="UTF-8">
      <style>
        body { font-family: "Microsoft JhengHei", "PingFang TC", Arial, sans-serif; background-color: #f8fafc; color: #1e293b; margin: 0; padding: 20px; }
        .container { max-width: 720px; margin: 0 auto; background-color: #ffffff; border-radius: 8px; border: 1px solid #e2e8f0; overflow: hidden; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1); }
        .header { background: linear-gradient(135deg, #0284c7 0%, #0369a1 100%); color: #ffffff; padding: 24px; text-align: center; }
        .header h1 { margin: 0 0 6px 0; font-size: 20px; font-weight: bold; letter-spacing: 1px; }
        .header p { margin: 0; font-size: 12px; opacity: 0.9; }
        .content { padding: 24px; }
        .section-title { font-size: 14px; font-weight: bold; color: #0f172a; margin: 18px 0 10px 0; border-left: 4px solid #0284c7; padding-left: 8px; }
        .info-table { width: 100%; border-collapse: collapse; margin-bottom: 20px; font-size: 13px; }
        .info-table th { background-color: #f1f5f9; color: #475569; padding: 8px 12px; text-align: left; width: 25%; border: 1px solid #e2e8f0; }
        .info-table td { padding: 8px 12px; border: 1px solid #e2e8f0; color: #1e293b; }
        .summary-box { background-color: #0f172a; color: #ffffff; border-radius: 6px; padding: 16px; margin: 20px 0; display: table; width: 100%; box-sizing: border-box; }
        .summary-cell { display: table-cell; width: 33.33%; text-align: center; vertical-align: middle; }
        .summary-label { font-size: 11px; color: #94a3b8; margin-bottom: 4px; }
        .summary-val { font-size: 16px; font-weight: bold; }
        .val-earn { color: #34d399; }
        .val-deduct { color: #fb7185; }
        .val-net { color: #fcd34d; font-size: 20px; }
        .footer { background-color: #f8fafc; padding: 16px; text-align: center; font-size: 11px; color: #64748b; border-top: 1px solid #e2e8f0; }
      </style>
    </head>
    <body>
      <div class="container">
        <div class="header">
          <h1>材霈有限公司 - 薪資補款審核通過通知</h1>
          <p>補款單號：${record.salaryId} ｜ 簽核狀態：已核准</p>
        </div>
        
        <div class="content">
          <div class="section-title">一、基本資料與請款明細</div>
          <table class="info-table">
            <tr>
              <th>廠商 / 店家</th>
              <td><b style="color:#0284c7;">${record.vendor}</b></td>
              <th>補款員工姓名</th>
              <td><b>${record.name}</b></td>
            </tr>
            <tr>
              <th>身分證字號</th>
              <td>${record.idCard}</td>
              <th>駐廠姓名</th>
              <td>${record.applicantName || '同仁'}</td>
            </tr>
            <tr>
              <th>補款方式</th>
              <td><b>${record.payType}</b></td>
              <th>是否可請款</th>
              <td><span style="color:#059669; font-weight:bold;">${record.isClaimable}</span></td>
            </tr>
            <tr>
              <th>申請日期</th>
              <td>${formatMinguoDate(record.applyDate, true)}</td>
              <th>付款日期</th>
              <td>${record.payDate ? formatMinguoDate(record.payDate, true) : '尚未指定'}</td>
            </tr>
            <tr>
              <th>匯費</th>
              <td>NT$ ${Number(record.remitFee || 0).toLocaleString()}</td>
              <th>補請款月份</th>
              <td>${formatMinguoDate(record.compensateMonth, false)}</td>
            </tr>
            <tr>
              <th>備註說明</th>
              <td colspan="3" style="color:#b91c1c; font-weight:600;">${record.notes || '無'}</td>
            </tr>
          </table>

          <div class="summary-box">
            <div class="summary-cell">
              <div class="summary-label">應領小計 (加項總額)</div>
              <div class="summary-val val-earn">NT$ ${Number(record.totalEarnings || 0).toLocaleString()}</div>
            </div>
            <div class="summary-cell" style="border-left: 1px solid #334155; border-right: 1px solid #334155;">
              <div class="summary-label">應扣小計 (扣項總額)</div>
              <div class="summary-val val-deduct">NT$ ${Number(record.totalDeductions || 0).toLocaleString()}</div>
            </div>
            <div class="summary-cell">
              <div class="summary-label">實補金額 (撥款總計)</div>
              <div class="summary-val val-net">NT$ ${Number(record.netTotal || 0).toLocaleString()}</div>
            </div>
          </div>

          ${imageHtmlSection}
          
          <div style="margin-top: 16px; font-size: 11px; color: #64748b;">
            核准主管：${record.approvedSupervisor || '系統管理者'} ｜ 核准時間：${record.approvedTime}
          </div>
        </div>

        <div class="footer">
          此信件由 Tsaipei 材霈招募與薪資管理系統自動發出，請勿直接回覆。
        </div>
      </div>
    </body>
    </html>
    `;
    
    const mailOptions = {
      htmlBody: htmlBody,
      name: '材霈招募薪資系統'
    };

    if (emailAttachments.length > 0) {
      mailOptions.attachments = emailAttachments;
    }
    if (Object.keys(inlineImagesMap).length > 0) {
      mailOptions.inlineImages = inlineImagesMap;
    }

    GmailApp.sendEmail(finalRecipientString, subject, '', mailOptions);
    console.log(`✉️ 成功發送薪資補款郵件至：[${finalRecipientString}] (含附件與內嵌圖檔)`);
  },

  /**
   * 產生會計留底用的 PDF 存查單（Google Docs 服務組版 → 匯出 PDF → 刪除暫存文件）。
   * 排版對應信件內容，另外加上「簽核紀錄」區塊方便日後對帳查核是誰、何時核准。
   * 補款佐證圖檔本身已經是信件的另一個附件，這裡不重複放入，只留文字提示。
   */
  buildSalaryPdfBlob: function(record) {
    const COLOR_LABEL_BG = '#f1f5f9';
    const COLOR_LABEL_TEXT = '#475569';
    const COLOR_VALUE_TEXT = '#0f172a';
    const COLOR_NOTES = '#b91c1c';

    const doc = DocumentApp.create(`薪資補款存查單_${record.salaryId}`);
    const docId = doc.getId();

    try {
      const body = doc.getBody();
      body.setMarginTop(36).setMarginBottom(36).setMarginLeft(40).setMarginRight(40);

      body.appendParagraph('材霈有限公司')
        .setFontSize(10).setForegroundColor('#64748b');
      body.appendParagraph('薪資補款申請存查單')
        .setFontSize(20).setBold(true).setForegroundColor('#0f172a');
      body.appendParagraph(`補款單號：${record.salaryId}　｜　簽核狀態：${record.reviewStatus || '已核准'}`)
        .setFontSize(11).setForegroundColor(COLOR_LABEL_TEXT);
      body.appendHorizontalRule();

      // 一、基本資料與請款明細
      body.appendParagraph('一、基本資料與請款明細')
        .setFontSize(13).setBold(true).setForegroundColor('#0f172a').setSpacingBefore(14).setSpacingAfter(6);

      const infoRows = [
        ['廠商 / 店家', record.vendor || '-', '補款員工姓名', record.name || '-'],
        ['身分證字號', record.idCard || '-', '駐廠姓名', record.applicantName || '同仁'],
        ['補款方式', record.payType || '-', '是否可請款', record.isClaimable || '-'],
        ['申請日期', formatMinguoDate(record.applyDate, true), '付款日期', record.payDate ? formatMinguoDate(record.payDate, true) : '尚未指定'],
        ['匯費', `NT$ ${Number(record.remitFee || 0).toLocaleString()}`, '補請款月份', formatMinguoDate(record.compensateMonth, false)]
      ];

      const infoTable = body.appendTable();
      infoRows.forEach(cols => {
        const row = infoTable.appendTableRow();
        for (let i = 0; i < 4; i++) {
          const isLabel = (i === 0 || i === 2);
          const cell = row.appendTableCell(String(cols[i] || ''));
          cell.setPaddingTop(4).setPaddingBottom(4).setPaddingLeft(6).setPaddingRight(6);
          const para = cell.getChild(0).asParagraph();
          para.setFontSize(10.5);
          if (isLabel) {
            cell.setBackgroundColor(COLOR_LABEL_BG);
            para.setForegroundColor(COLOR_LABEL_TEXT);
          } else {
            para.setForegroundColor(COLOR_VALUE_TEXT);
          }
        }
      });

      // 備註說明（獨立一個雙欄表格，避免主表格欄寬被過長文字撐開）
      const notesTable = body.appendTable();
      const notesRow = notesTable.appendTableRow();
      const notesLabelCell = notesRow.appendTableCell('備註說明');
      notesLabelCell.setBackgroundColor(COLOR_LABEL_BG);
      notesLabelCell.getChild(0).asParagraph().setFontSize(10.5).setForegroundColor(COLOR_LABEL_TEXT);
      const notesValueCell = notesRow.appendTableCell(record.notes || '無');
      notesValueCell.getChild(0).asParagraph().setFontSize(10.5).setBold(true).setForegroundColor(COLOR_NOTES);

      // 二、金額明細（三格淺色底色，跟信件/PDF 示範版一致）
      body.appendParagraph('二、金額明細')
        .setFontSize(13).setBold(true).setForegroundColor('#0f172a').setSpacingBefore(16).setSpacingAfter(6);

      const summaryTable = body.appendTable();
      const summaryRow = summaryTable.appendTableRow();
      const summaryCells = [
        { label: '應領小計（加項總額）', value: `NT$ ${Number(record.totalEarnings || 0).toLocaleString()}`, bg: '#f0fdf7', color: '#059669' },
        { label: '應扣小計（扣項總額）', value: `NT$ ${Number(record.totalDeductions || 0).toLocaleString()}`, bg: '#fff5f6', color: '#e11d48' },
        { label: '實補金額（撥款總計）', value: `NT$ ${Number(record.netTotal || 0).toLocaleString()}`, bg: '#fffbeb', color: '#b45309' }
      ];
      summaryCells.forEach(item => {
        // 建立儲存格時直接帶入文字（不要先建空字串儲存格再回頭抓段落設定，
        // 那樣 Google Docs 服務會回傳 null，導致後續設定樣式時噴錯）
        const cell = summaryRow.appendTableCell(item.label);
        cell.setBackgroundColor(item.bg);
        cell.setPaddingTop(8).setPaddingBottom(8);
        const labelPara = cell.getChild(0).asParagraph();
        labelPara.setFontSize(9).setForegroundColor('#64748b')
          .setAlignment(DocumentApp.HorizontalAlignment.CENTER);
        const valuePara = cell.appendParagraph(item.value);
        valuePara.setFontSize(14).setBold(true).setForegroundColor(item.color)
          .setAlignment(DocumentApp.HorizontalAlignment.CENTER);
      });

      // 三、簽核紀錄
      body.appendParagraph('三、簽核紀錄')
        .setFontSize(13).setBold(true).setForegroundColor('#0f172a').setSpacingBefore(16).setSpacingAfter(6);

      const approvalTable = body.appendTable();
      const approvalRow = approvalTable.appendTableRow();
      const approvalCols = ['核准主管', record.approvedSupervisor || '系統管理者', '核准時間', record.approvedTime || '-'];
      for (let i = 0; i < 4; i++) {
        const isLabel = (i === 0 || i === 2);
        const cell = approvalRow.appendTableCell(String(approvalCols[i] || ''));
        cell.setPaddingTop(4).setPaddingBottom(4).setPaddingLeft(6).setPaddingRight(6);
        const para = cell.getChild(0).asParagraph();
        para.setFontSize(10.5);
        if (isLabel) {
          cell.setBackgroundColor(COLOR_LABEL_BG);
          para.setForegroundColor(COLOR_LABEL_TEXT);
        } else {
          para.setForegroundColor(COLOR_VALUE_TEXT);
        }
      }

      body.appendParagraph('此文件由系統於核准當下自動產生，作為薪資補款留底憑證，補款佐證圖檔請詳見核准信件附件。')
        .setFontSize(9).setForegroundColor('#94a3b8').setSpacingBefore(20);

      doc.saveAndClose();

      const pdfBlob = DriveApp.getFileById(docId).getAs('application/pdf')
        .setName(`薪資補款存查單_${record.salaryId}.pdf`);

      return pdfBlob;
    } finally {
      // 暫存的 Google Doc 只是產生 PDF 用的中介檔案，用完丟進垃圾桶（不是永久刪除，
      // 誤刪還能從雲端硬碟垃圾桶救回），避免每核准一筆就在雲端硬碟堆一個文件檔。
      try {
        DriveApp.getFileById(docId).setTrashed(true);
      } catch (cleanupErr) {
        console.warn('清理暫存 PDF 產生用文件失敗 (不影響已產生的 PDF):', cleanupErr);
      }
    }
  }
};

// ==============================================================================
// 4. 薪資補款專屬 LINE Flex Message 建構器
// ==============================================================================
const SalaryFlexMessageBuilder = {
  buildSalaryApprovalCard: function(data) {
    const info = data.info || {};
    const summary = data.summary || {};
    const applicant = data.applicant || {};
    
    const postbackApprove = `action=review_salary&status=approve&salary_id=${data.salaryId}&applicant_id=${applicant.userId || ''}`;
    const postbackReject = `action=review_salary&status=reject&salary_id=${data.salaryId}&applicant_id=${applicant.userId || ''}`;
    
    const contents = [
      SharedFlexBuilder.createRow('申請同仁', info.applicant_name || applicant.displayName || '未提供', '#0284c7', 'bold'),
      SharedFlexBuilder.createRow('身分證字號', maskIdCard(info.id_card)),
      SharedFlexBuilder.createRow('補請月份', info.compensate_month || '-'),
      SharedFlexBuilder.createRow('補款方式', info.pay_type || '-'),
      SharedFlexBuilder.createRow('備註說明', info.notes || '-', '#b91c1c', 'bold'),
      SharedFlexBuilder.createRow('應領小計 (+)', `NT$ ${Number(summary.total_earnings || 0).toLocaleString()}`, '#059669'),
      SharedFlexBuilder.createRow('應扣小計 (-)', `NT$ ${Number(summary.total_deductions || 0).toLocaleString()}`, '#e11d48'),
      {
        type: 'box',
        layout: 'horizontal',
        contents: [
          { type: 'text', text: '實補總額', size: 'sm', weight: 'bold', color: '#0f172a' },
          { type: 'text', text: `NT$ ${Number(summary.net_total || 0).toLocaleString()}`, size: 'md', weight: 'bold', color: '#d97706', align: 'end' }
        ]
      }
    ];

    if (data.imageUrl) {
      contents.push(SharedFlexBuilder.createRow('補款佐證', '已附圖檔(如下)', '#0284c7', 'bold'));
    }

    const bodyContents = [
      {
        type: 'text',
        text: `補款員工：${info.name || '-'}`,
        weight: 'bold',
        size: 'lg',
        wrap: true
      },
      {
        type: 'text',
        text: `廠商：${info.vendor || '-'} | 付款日：${info.pay_date || '未指定'}`,
        size: 'xs',
        color: '#64748b',
        margin: 'xs'
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
        contents: contents
      }
    ];

    if (data.imageUrl && typeof data.imageUrl === 'string' && data.imageUrl.startsWith('https://')) {
      bodyContents.push({
        type: 'image',
        url: data.imageUrl,
        size: 'full',
        aspectRatio: '16:9',
        aspectMode: 'cover',
        margin: 'md'
      });
    }

    return {
      type: 'flex',
      altText: `[薪資補款審核] ${info.name || '員工'} - NT$ ${Number(summary.net_total || 0).toLocaleString()}`,
      contents: {
        type: 'bubble',
        header: {
          type: 'box',
          layout: 'vertical',
          backgroundColor: '#ecfdf5',
          contents: [
            {
              type: 'text',
              text: '💰 薪資補款審核申請',
              size: 'sm',
              color: '#065f46',
              weight: 'bold'
            }
          ]
        },
        body: {
          type: 'box',
          layout: 'vertical',
          contents: bodyContents
        },
        footer: {
          type: 'box',
          layout: 'horizontal',
          spacing: 'md',
          contents: [
            {
              type: 'button',
              style: 'primary',
              color: '#059669',
              action: {
                type: 'postback',
                label: '核准發信',
                data: postbackApprove,
                displayText: `核准薪資補款單：${info.name || ''}`
              }
            },
            {
              type: 'button',
              style: 'secondary',
              color: '#f43f5e',
              action: {
                type: 'postback',
                label: '退回',
                data: postbackReject,
                displayText: `退回薪資補款單：${info.name || ''}`
              }
            }
          ]
        }
      }
    };
  }
};

/**
 * 一次性測試工具：手動在 Apps Script 編輯器執行這個函式一次，目的是：
 * 1. 觸發 Google 的授權同意畫面，讓這個 Web App 部署帳號授權「Google 文件」
 *    這個新用到的服務（第一次用到 DocumentApp，之前只有用過試算表/Gmail/
 *    雲端硬碟），部署後第一次核准薪資補款單觸發寄信時才不會因為權限不足
 *    導致 PDF 附件產生失敗。
 * 2. 順便驗證 PDF 產生邏輯本身沒問題（不會真的寄信，也不會寫進試算表）。
 *
 * 用法：Apps Script 編輯器右上角函式下拉選單選 testPdfGeneration，
 * 按執行（▶️），第一次執行會跳出 Google 授權畫面，點「允許」即可。
 * 執行完打開左側「執行項目」看紀錄，看到「✅ PDF 測試產生成功」代表沒問題，
 * 之後這個函式就用不到了，可以留著或刪除都不影響任何正式功能。
 */
function testPdfGeneration() {
  const fakeRecord = {
    salaryId: 'TEST-0000000000',
    vendor: '測試廠商',
    name: '測試員工',
    idCard: 'A123456789',
    applicantName: '測試申請人',
    payType: '立即補款',
    isClaimable: '可',
    applyDate: new Date(),
    payDate: new Date(),
    remitFee: 15,
    compensateMonth: new Date(),
    notes: '這是測試用假資料，不是真實補款紀錄。',
    totalEarnings: 1000,
    totalDeductions: 50,
    netTotal: 950,
    reviewStatus: '已核准',
    approvedSupervisor: '測試主管',
    approvedTime: Utilities.formatDate(new Date(), 'Asia/Taipei', 'yyyy-MM-dd HH:mm:ss')
  };

  const pdfBlob = EmailService.buildSalaryPdfBlob(fakeRecord);
  console.log(`✅ PDF 測試產生成功，檔名：${pdfBlob.getName()}，大小：${pdfBlob.getBytes().length} bytes`);
}
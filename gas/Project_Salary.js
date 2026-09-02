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
    
    // 若分頁全空，自動寫入標準 21 欄標題列 (A~U)
    if (sheet.getLastRow() === 0) {
      sheet.appendRow([
        '補款單號', '申請時間', '申請人姓名', '申請人 LINE ID', '員工姓名', '身分證', '廠商/店家',
        '申請日', '付款日', '扣分鐘月份', '補請款月份', '是否可請款', '補款方式',
        '加項小計', '扣項小計', '實補總額', '備註', '審核狀態', '核准主管', '核准時間',
        '補款佐證(照片)'
      ]);
      SpreadsheetApp.flush();
    }
    
    const applyTimestamp = Utilities.formatDate(new Date(), 'Asia/Taipei', 'yyyy-MM-dd HH:mm:ss');
    
    // 精準對標寫入 21 個欄位資料 (A ~ U 欄)
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
      summary.total_earnings,       // N (14): 加項小計
      summary.total_deductions,     // O (15): 扣項小計
      summary.net_total,            // P (16): 實補總額
      info.notes,                   // Q (17): 備註 (必填)
      '待審核',                     // R (18): 審核狀態
      '',                           // S (19): 核准主管
      '',                           // T (20): 核准時間
      imageUrl || ''                // U (21): 補款佐證(照片)
    ]);
    SpreadsheetApp.flush();
    
    const flexMessage = SalaryFlexMessageBuilder.buildSalaryApprovalCard({
      salaryId: salaryId,
      applicant: applicant,
      info: info,
      summary: summary,
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
    return file.getUrl();
  },

  handleSalaryPostback: function(event, postbackData, operatorSupervisorId) {
    const status = postbackData.status;
    const applicantId = postbackData.applicant_id;
    const salaryId = postbackData.salary_id;
    
    const salaryRecordCheck = SalarySheetService.getSalaryRecord(salaryId);
    if (!salaryRecordCheck) {
      LineService.replyTextMessage(event.replyToken, '❌ 操作失敗：找不到該薪資補款單資料。');
      return;
    }
    if (salaryRecordCheck.reviewStatus === '已核准' || salaryRecordCheck.reviewStatus === '已退回') {
      LineService.replyTextMessage(event.replyToken, `⚠️ 操作無效：此單據已由主管完成審核 (目前狀態：${salaryRecordCheck.reviewStatus})，無法重複簽核。`);
      return;
    }

    const isApproved = (status === 'approve');
    const reviewStatus = isApproved ? '已核准' : '已退回';
    const nowStr = Utilities.formatDate(new Date(), 'Asia/Taipei', 'yyyy-MM-dd HH:mm:ss');
    
    const salaryRecord = SalarySheetService.updateSalaryReviewStatus(salaryId, reviewStatus, operatorSupervisorId, nowStr);
    
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

      // 1. 若申請人在組織表中曾列為主管，直接讀取其主管 Email (第 5 欄)
      for (let i = 1; i < data.length; i++) {
        const rowSupNames = String(data[i][2] || '').trim();
        const rowSupEmails = String(data[i][4] || '').trim();
        if (rowSupNames && rowSupEmails) {
          const names = rowSupNames.split(/[,，、\/\\\s\n\r]+/).map(s => s.trim());
          const emails = rowSupEmails.split(/[,，、\/\\\s\n\r]+/).map(s => s.trim());
          for (let k = 0; k < names.length; k++) {
            if (names[k] === cleanName && emails[k] && /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(emails[k])) {
              return emails[k];
            }
          }
        }
      }

      // 2. 搜尋同仁所屬列中是否有符合 Email 格式之欄位
      for (let i = 1; i < data.length; i++) {
        const empName = String(data[i][0] || '').trim();
        const empLineId = String(data[i][1] || '').trim().toUpperCase();
        if ((cleanName && empName === cleanName) || (cleanUserId && empLineId === cleanUserId)) {
          for (let c = 0; c < data[i].length; c++) {
            if (c !== 4) { // 避開主管 Email 欄
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

// ==============================================================================
// 3. 電子郵件報表發送服務 (EmailService)
// ==============================================================================
const EmailService = {
  sendSalaryCompensationReport: function(record) {
    const recipientSet = new Set();

    // 1. 加入專案設定之 HR 與財會信箱
    if (CONFIG.HR_ACCOUNTING_EMAILS) {
      CONFIG.HR_ACCOUNTING_EMAILS.split(/[,，、\/\\\s\n\r]+/).forEach(em => {
        const clean = String(em || '').trim();
        if (clean && /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(clean)) recipientSet.add(clean);
      });
    }
    
    // 2. 加入審核主管信箱
    if (record.supervisorEmail) {
      String(record.supervisorEmail).split(/[,，、\/\\\s\n\r]+/).forEach(em => {
        const cleanSupEmail = String(em || '').trim();
        if (cleanSupEmail && /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(cleanSupEmail)) recipientSet.add(cleanSupEmail);
      });
    }

    // 3. 加入申請人本人信箱
    if (record.applicantEmail) {
      String(record.applicantEmail).split(/[,，、\/\\\s\n\r]+/).forEach(em => {
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
              <th>申請員工姓名</th>
              <td><b style="color:#0284c7;">${record.applicantName || '同仁'}</b></td>
              <th>補款員工姓名</th>
              <td><b>${record.name}</b></td>
            </tr>
            <tr>
              <th>身分證字號</th>
              <td>${record.idCard}</td>
              <th>廠商 / 店家</th>
              <td>${record.vendor}</td>
            </tr>
            <tr>
              <th>補款方式</th>
              <td><b>${record.payType}</b></td>
              <th>是否可請款</th>
              <td><span style="color:#059669; font-weight:bold;">${record.isClaimable}</span></td>
            </tr>
            <tr>
              <th>申請日期</th>
              <td>${record.applyDate}</td>
              <th>付款日期</th>
              <td>${record.payDate || '尚未指定'}</td>
            </tr>
            <tr>
              <th>扣分鐘月份</th>
              <td>${record.deductMonth || '無'}</td>
              <th>補請款月份</th>
              <td>${record.compensateMonth}</td>
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
      contents.push(SharedFlexBuilder.createRow('補款佐證', '已附圖檔', '#0284c7', 'bold'));
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
          contents: [
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
          ]
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
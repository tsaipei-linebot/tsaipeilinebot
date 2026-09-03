/**
 * ==============================================================================
 * 專案立項與合約管理模組 (ProjectWorkflowService - 材霈有限公司)
 * ==============================================================================
 */

const ProjectWorkflowService = {
  /**
   * 處理專案合約表單送出與發信
   */
  processProjectSubmission: function(payload) {
    try {
      const applicantName = String((payload.applicant && payload.applicant.displayName) || (payload.fields && payload.fields.applicant_name) || '').trim();
      const fields = payload.fields || {};
      const fileData = payload.file || {};

      const vendor = String(fields.vendor || '').trim();
      const coopCategory = String(fields.coop_category || '').trim();
      const contractMode = String(fields.contract_mode || '').trim();
      const interviewSpecialist = String(fields.interview_specialist || '').trim();
      const visitSupervisor = String(fields.visit_supervisor || '').trim();

      // 欄位基礎驗證
      if (!applicantName) {
        return { status: 'error', message: '申請同仁姓名不可為空' };
      }
      if (!vendor) {
        return { status: 'error', message: '請填寫廠商名稱' };
      }
      if (!coopCategory) {
        return { status: 'error', message: '請選取合作類別' };
      }
      if (!contractMode) {
        return { status: 'error', message: '請選取簽約模式' };
      }
      if (!interviewSpecialist) {
        return { status: 'error', message: '請填寫約訪專員' };
      }
      if (!visitSupervisor) {
        return { status: 'error', message: '請填寫拜訪主管' };
      }
      if (!fileData.base64) {
        return { status: 'error', message: '請上傳合約檔案 (Word 或 PDF)' };
      }

      // 取得目標收件信箱清單
      const rawEmails = CONFIG.HR_ACCOUNTING_EMAILS || 'finance@tsaipei.com.tw';
      const emailList = rawEmails.split(/[,，、\/\\\s\n\r]+/).map(e => e.trim()).filter(Boolean);
      
      if (emailList.length === 0) {
        return { status: 'error', message: '系統未設定 HR_ACCOUNTING_EMAILS 收件人信箱' };
      }

      const toAddresses = emailList.join(',');

      // 處理上傳檔案附件
      let attachments = [];
      try {
        let base64Content = fileData.base64;
        if (base64Content.includes(',')) {
          base64Content = base64Content.split(',')[1];
        }
        const decodedBytes = Utilities.base64Decode(base64Content);
        const fileName = fileData.filename || `專案合約_${vendor}_${Utilities.formatDate(new Date(), 'Asia/Taipei', 'yyyyMMdd')}.pdf`;
        const mimeType = fileData.mimeType || 'application/octet-stream';
        
        const blob = Utilities.newBlob(decodedBytes, mimeType, fileName);
        attachments.push(blob);
      } catch (fileErr) {
        console.error('合約檔案解析失敗:', fileErr);
        return { status: 'error', message: '合約檔案解析失敗：' + fileErr.toString() };
      }

      const submitTime = Utilities.formatDate(new Date(), 'Asia/Taipei', 'yyyy-MM-dd HH:mm:ss');
      const projectId = 'PRJ-' + Utilities.formatDate(new Date(), 'Asia/Taipei', 'yyyyMMddHHmmss');

      // 合約檔案另存 Google Drive，取得可長期查詢的連結（不主動開放「知道連結的人皆可檢視」，
      // 合約內容較敏感，沿用 GOOGLE_DRIVE_FOLDER_ID 資料夾既有的共用權限即可，不額外對外開放）
      let contractFileUrl = '';
      try {
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
        const driveFile = folder.createFile(attachments[0]);
        contractFileUrl = driveFile.getUrl();
      } catch (driveErr) {
        console.warn('合約檔案存入 Drive 失敗 (不影響 Email 發送):', driveErr);
      }

      // 寫入「專案合約紀錄」分頁，供內部管理與後續同步給會計
      try {
        const sheet = SpreadsheetService.getOrCreateSheet(CONFIG.SHEET_NAME_PROJECT);
        if (sheet.getLastRow() === 0) {
          sheet.appendRow(['專案編號', '申請時間', '申請人姓名', '廠商名稱', '合作類別', '簽約模式', '約訪專員', '拜訪主管', '收件信箱', '合約檔案連結']);
          SpreadsheetApp.flush();
        }
        sheet.appendRow([
          projectId,
          submitTime,
          applicantName,
          vendor,
          coopCategory,
          contractMode,
          interviewSpecialist,
          visitSupervisor,
          toAddresses,
          contractFileUrl
        ]);
        SpreadsheetApp.flush();
      } catch (sheetErr) {
        console.warn('寫入專案合約紀錄分頁失敗 (不影響 Email 發送):', sheetErr);
      }

      const subject = `【新專案合約通知】${vendor} - ${coopCategory} (${applicantName} 提交)`;

      const htmlBody = `
        <div style="font-family: Arial, 'Microsoft JhengHei', sans-serif; max-width: 650px; margin: 0 auto; border: 1px solid #e2e8f0; border-radius: 10px; overflow: hidden; box-shadow: 0 4px 6px rgba(0,0,0,0.05);">
          <div style="background-color: #0284c7; padding: 18px 24px; color: #ffffff;">
            <h2 style="margin: 0; font-size: 18px; font-weight: bold;">材霈有限公司 - 新專案合作與合約提報</h2>
            <p style="margin: 4px 0 0 0; font-size: 12px; opacity: 0.9;">系統已自動建檔並發送合約附件至指定財務與人資團隊</p>
          </div>
          
          <div style="padding: 24px; background-color: #ffffff;">
            <table style="width: 100%; border-collapse: collapse; font-size: 14px;">
              <tr style="border-bottom: 1px solid #f1f5f9;">
                <td style="padding: 10px 0; color: #64748b; width: 35%;">申請同仁姓名</td>
                <td style="padding: 10px 0; color: #0f172a; font-weight: bold;">${applicantName}</td>
              </tr>
              <tr style="border-bottom: 1px solid #f1f5f9;">
                <td style="padding: 10px 0; color: #64748b;">廠商名稱</td>
                <td style="padding: 10px 0; color: #0284c7; font-weight: bold; font-size: 15px;">${vendor}</td>
              </tr>
              <tr style="border-bottom: 1px solid #f1f5f9;">
                <td style="padding: 10px 0; color: #64748b;">合作類別</td>
                <td style="padding: 10px 0; color: #0f172a;">${coopCategory}</td>
              </tr>
              <tr style="border-bottom: 1px solid #f1f5f9;">
                <td style="padding: 10px 0; color: #64748b;">簽約模式</td>
                <td style="padding: 10px 0; color: #0f172a;">${contractMode}</td>
              </tr>
              <tr style="border-bottom: 1px solid #f1f5f9;">
                <td style="padding: 10px 0; color: #64748b;">約訪專員</td>
                <td style="padding: 10px 0; color: #0f172a;">${interviewSpecialist}</td>
              </tr>
              <tr style="border-bottom: 1px solid #f1f5f9;">
                <td style="padding: 10px 0; color: #64748b;">拜訪主管</td>
                <td style="padding: 10px 0; color: #0f172a;">${visitSupervisor}</td>
              </tr>
              <tr style="border-bottom: 1px solid #f1f5f9;">
                <td style="padding: 10px 0; color: #64748b;">合約檔案名稱</td>
                <td style="padding: 10px 0; color: #475569;">${fileData.filename || '已附加於此郵件'}</td>
              </tr>
              <tr>
                <td style="padding: 10px 0; color: #64748b;">提交時間</td>
                <td style="padding: 10px 0; color: #475569;">${submitTime}</td>
              </tr>
            </table>

            <div style="margin-top: 20px; padding: 12px 16px; background-color: #f8fafc; border-left: 4px solid #0284c7; border-radius: 4px; font-size: 13px; color: #475569;">
              📌 <strong>合約檔案已夾帶於信件附件中</strong>，請相關權責同仁下載確認與後續歸檔作業。
            </div>
          </div>

          <div style="background-color: #f8fafc; padding: 12px 24px; text-align: center; border-top: 1px solid #e2e8f0; font-size: 12px; color: #94a3b8;">
            此郵件由 材霈招募與薪資管理系統 自動發送，請勿直接回覆本信。
          </div>
        </div>
      `;

      // 透過 Gmail 發送信件
      GmailApp.sendEmail(toAddresses, subject, `您有一筆來自 ${applicantName} 的新專案合約提報（廠商：${vendor}），詳情請參閱附件。`, {
        htmlBody: htmlBody,
        attachments: attachments,
        name: '材霈內部專案系統'
      });

      return {
        status: 'success',
        message: `專案資料已成功送出，並已將合約檔案寄送至指定信箱 (${toAddresses})！`
      };

    } catch (err) {
      console.error('處理專案送出與發信失敗:', err);
      return { status: 'error', message: '系統發信異常: ' + err.toString() };
    }
  }
};
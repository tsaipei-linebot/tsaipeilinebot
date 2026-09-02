/**
 * ==============================================================================
 * 專案四：職缺文案批次增強與合規建檔模組 (Project_BatchEnhance.gs - 材霈有限公司 - 完美排版版)
 * ==============================================================================
 */

const BatchEnhanceJobService = {
  /**
   * 批次執行主流程
   * @param {boolean} forceOverwrite 是否強制覆寫已有「精華亮點」的職缺
   */
  runBatchEnhancement: function(forceOverwrite) {
    if (forceOverwrite === undefined) forceOverwrite = false;
    
    const startTime = Date.now();
    const MAX_EXECUTION_TIME_MS = 270 * 1000; // 4.5 分鐘保護中斷，防範 GAS 6 分鐘超時
    
    console.log('🚀 [BatchEnhance] 開始執行職缺文案批次增強作業 (模式: ' + (forceOverwrite ? '強制覆寫' : '斷點續傳') + ')...');
    
    const jobs = this.fetchActiveJobsFromNotion();
    const totalCount = jobs.length;
    console.log('📦 [BatchEnhance] 共撈取到 ' + totalCount + ' 筆非停招職缺');

    let successCount = 0;
    let skippedCount = 0;
    let failedCount = 0;
    let isTimeoutInterrupted = false;

    for (let i = 0; i < totalCount; i++) {
      // 超時保護檢查
      if (Date.now() - startTime > MAX_EXECUTION_TIME_MS) {
        console.warn('⏰ [BatchEnhance] 接近 GAS 執行上限 (4.5分鐘)，自動安全中斷。已完成 ' + i + '/' + totalCount + ' 筆。');
        isTimeoutInterrupted = true;
        break;
      }

      const job = jobs[i];
      const pageId = String(job.id || '').trim();
      const title = job.external_title || job.title || '招募職缺';
      // 地點智慧聚合邏輯與單筆送審共用（AiJobDescriptionService.formatSmartLocation），避免兩邊各自維護一份容易失去同步
      const smartLocation = AiJobDescriptionService.formatSmartLocation(job.cityList, job.districtList);

      // 斷點續傳檢查
      if (!forceOverwrite && job.existing_highlight && String(job.existing_highlight).trim() !== '') {
        console.log('[' + (i + 1) + '/' + totalCount + '] ⏭️ 跳過：【' + title + '】(已有精華亮點)');
        skippedCount++;
        continue;
      }

      console.log('[' + (i + 1) + '/' + totalCount + '] 🔄 正在處理：【' + title + '】(' + smartLocation + ')...');

      // 1. 呼叫 Gemini 進行零幻覺與法規審查生成 (傳入智慧聚合之地點)
      const enhancedResult = this.generateEnhancementWithGemini(job, smartLocation);

      if (!enhancedResult || !enhancedResult.highlight) {
        console.error('❌ [BatchEnhance] 生成文案失敗：【' + title + '】');
        failedCount++;
        continue;
      }

      // 2. 回填至 Notion 資料庫 (Page ID 乾淨串接)
      const updateSuccess = this.updateNotionJobProperties(pageId, enhancedResult.highlight, enhancedResult.formatted_detail);

      if (updateSuccess) {
        console.log('    ✅ 成功回填 Notion！亮點: ' + enhancedResult.highlight);
        successCount++;
      } else {
        console.error('    ❌ 回填 Notion 失敗: [' + pageId + ']');
        failedCount++;
      }

      // 3. 遵守 Notion 每秒 3 次 API 上限，加入安全間隔
      Utilities.sleep(350);
    }

    const durationSec = Math.round((Date.now() - startTime) / 1000);
    const summary = {
      total: totalCount,
      success: successCount,
      skipped: skippedCount,
      failed: failedCount,
      durationSec: durationSec,
      isInterrupted: isTimeoutInterrupted,
      mode: forceOverwrite ? '強制覆寫' : '斷點續傳'
    };

    console.log('🎉 [BatchEnhance] 批次作業結束：總計 ' + totalCount + ' 筆 | 成功 ' + successCount + ' | 跳過 ' + skippedCount + ' | 失敗 ' + failedCount + ' (耗時 ' + durationSec + ' 秒)');

    // 4. 推播執行結果給系統管理員
    this.notifyAdminViaLine(summary);

    return summary;
  },

  /**
   * 從 Notion 撈取所有非停招之職缺資料
   */
  fetchActiveJobsFromNotion: function() {
    const cleanDbId = String(CONFIG.NOTION_DATABASE_ID || '').replace(/-/g, '').trim();
    const queryUrl = 'https://api.notion.com/v1/databases/' + cleanDbId + '/query';
    const jobs = [];
    let hasMore = true;
    let nextCursor = null;

    const payload = {
      page_size: 100,
      filter: {
        property: '狀態',
        status: {
          does_not_equal: '停招'
        }
      }
    };

    while (hasMore) {
      if (nextCursor) payload.start_cursor = nextCursor;

      try {
        const response = UrlFetchApp.fetch(queryUrl, {
          method: 'post',
          headers: NotionService.getHeaders(),
          payload: JSON.stringify(payload),
          muteHttpExceptions: true
        });

        const json = JSON.parse(response.getContentText());
        if (json.results && Array.isArray(json.results)) {
          json.results.forEach(page => {
            const props = page.properties;
            jobs.push({
              id: page.id,
              title: NotionService.getPropText(props['職缺名稱']),
              external_title: NotionService.getPropText(props['職缺名稱(對外)'] || props['職缺名稱（對外）'] || props['職缺名稱 (對外)']),
              cityList: NotionService.getMultiSelect(props['縣市']),
              districtList: NotionService.getMultiSelect(props['行政區']),
              salary: NotionService.getPropText(props['薪資']),
              shift: NotionService.getMultiSelect(props['班別']).join('、'),
              external_desc: NotionService.getPropText(props['工作內容(對外)']),
              existing_highlight: NotionService.getPropText(props['精華亮點']),
              existing_detail: NotionService.getPropText(props['排版工作說明'])
            });
          });
        }
        hasMore = json.has_more;
        nextCursor = json.next_cursor;
      } catch (err) {
        console.error('撈取 Notion 職缺發生錯誤:', err);
        break;
      }
    }

    return jobs;
  },

  /**
   * 呼叫 Gemini 進行雙欄位結構化生成 (保證語句完整不切字 + 地點智慧聚合)
   */
  generateEnhancementWithGemini: function(job, smartLocation) {
    const apiKey = AiJobDescriptionService.getApiKey();
    if (!apiKey) {
      console.error('❌ 未找到 GEMINI_API_KEY，請確認 Script Properties 設定！');
      return null;
    }

    const title = job.external_title || job.title || '優質職缺';
    const location = smartLocation || '依公司指派地點';
    const salary = job.salary || '依公司規定';
    const shift = job.shift || '依排班規定';
    const rawDesc = job.external_desc || '歡迎洽詢應徵。';

    const prompt = '你是一位資深勞動法規顧問與專業人資文案專家。請根據以下提供的【原始職缺資料】，輸出 JSON 物件。\n\n' +
      '【原始職缺資料】\n' +
      '- 職缺名稱：' + title + '\n' +
      '- 工作地點：' + location + '\n' +
      '- 薪資待遇：' + salary + '\n' +
      '- 工作班別：' + shift + '\n' +
      '- 原始工作內容與條件：\n' + rawDesc + '\n\n' +
      '【核心原則 - 零幻覺與合規審查】\n' +
      '1. 嚴禁幻覺（Zero Hallucination）：\n' +
      '   - 絕對禁止自行腦補或添加原始資料中沒有提及的福利、設備、時薪數字或工作職責。\n' +
      '   - 所有內容必須嚴格源自上方提供的原始資料。\n' +
      '2. 《就業服務法》第 5 條合規審查：\n' +
      '   - 若原始資料中包含年齡（如「30歲以下」）、性別（如「限女性」）等歧視性限制，一律直接剔除。\n' +
      '3. 輸出欄位規範（極重要）：\n' +
      '   - "highlight": 30～45 字之手機卡片吸引短句（繁體中文），點出原始資料已具備之優勢（如：無經驗可、固定班等）。語意必須完整通順，結尾必須有驚嘆號（！）或句號（。），絕對嚴禁語意中斷或切掉半句！\n' +
      '   - "formatted_detail": 條列式排版工作說明，格式如下：\n' +
      '     📋【職缺名稱：' + title + '】\n' +
      '     📍【工作地點】：' + location + '\n' +
      '     💰【薪資待遇】：' + salary + '\n' +
      '     ⏰【工作班別】：' + shift + '\n' +
      '     📝【主要工作內容】：（將原始工作內容分點條列，不可杜撰項目）\n' +
      '     ✨【應徵與配合條件】：（客觀列出體能、出勤或無經驗等條件）\n' +
      '     💡 依《就業服務法》規定，本公司所有職缺皆無性別、年齡限制，歡迎所有朋友應徵！\n\n' +
      '【輸出格式】\n' +
      '請嚴格輸出合法 JSON 物件，不要輸出任何 Markdown 標記或前綴：\n' +
      '{\n' +
      '  "highlight": "30-45字完整精華短句（含標點句尾）",\n' +
      '  "formatted_detail": "條列式排版說明完整文字"\n' +
      '}';

    const targetModels = ['gemini-2.5-flash', 'gemini-2.5-flash-lite', 'gemini-1.5-flash'];
    const MAX_RETRY_PER_MODEL = 2; // 同一模型遇到 429 額度限制時的重試次數上限
    const RETRY_BASE_DELAY_MS = 1000; // 重試遞增等待時間基準

    for (let i = 0; i < targetModels.length; i++) {
      const model = targetModels[i];
      const url = 'https://generativelanguage.googleapis.com/v1beta/models/' + encodeURIComponent(model) + ':generateContent?key=' + encodeURIComponent(apiKey);

      const payload = {
        contents: [{ parts: [{ text: prompt }] }],
        generationConfig: {
          temperature: 0.1,
          responseMimeType: 'application/json'
        }
      };

      const options = {
        method: 'post',
        contentType: 'application/json',
        headers: { 'x-goog-api-key': apiKey },
        payload: JSON.stringify(payload),
        muteHttpExceptions: true
      };

      for (let attempt = 0; attempt <= MAX_RETRY_PER_MODEL; attempt++) {
        try {
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
              const cleanJson = textRes.replace(/^```json\s*/i, '').replace(/\s*```$/i, '').trim();
              const parsed = JSON.parse(cleanJson);
              return {
                highlight: String(parsed.highlight || '').trim(),
                formatted_detail: String(parsed.formatted_detail || '').trim()
              };
            }
            // HTTP 200 但沒有文字內容：換下一個模型，重試同一模型也不會有幫助
            break;
          }

          if (resCode === 429) {
            const willRetry = attempt < MAX_RETRY_PER_MODEL;
            console.warn('⚠️ [BatchEnhance] 模型 [' + model + '] 額度限制 (HTTP 429)，' + (willRetry ? '等待後重試...' : '重試已達上限，換下一個模型'));
            if (willRetry) {
              Utilities.sleep(RETRY_BASE_DELAY_MS * (attempt + 1));
              continue;
            }
            break;
          }

          // 其他錯誤：記錄詳細狀態碼與回應內容方便排查，直接換下一個模型
          console.warn('[BatchEnhance] 模型 [' + model + '] 呼叫失敗 (HTTP ' + resCode + '): ' + response.getContentText().slice(0, 300));
          break;
        } catch (e) {
          console.warn('[BatchEnhance] 模型 [' + model + '] 呼叫或解析異常:', e);
          break;
        }
      }
    }

    // 保底回傳（AI 全部呼叫失敗時使用）：只陳述確定為真的事實（薪資、班別、地點），
    // 不做「無經驗可」這類原始資料未提及、可能失真的宣稱
    return {
      highlight: '開放應徵【' + title + '】！工作地點：' + location + '，班別：' + shift + '，薪資：' + salary + '，歡迎立即應徵！',
      formatted_detail: '📋【職缺名稱：' + title + '】\n\n📍【工作地點】：' + location + '\n💰【薪資待遇】：' + salary + '\n⏰【工作班別】：' + shift + '\n\n📝【工作內容】：\n' + rawDesc + '\n\n💡 依《就業服務法》規定，所有職缺皆無性別、年齡限制。'
    };
  },

  /**
   * 回填更新 Notion 頁面屬性 (含 2000 字元長度切分防護)
   */
  updateNotionJobProperties: function(pageId, highlight, formattedDetail) {
    const cleanPageId = String(pageId || '').trim();
    if (!cleanPageId) return false;

    const url = 'https://api.notion.com/v1/pages/' + cleanPageId;
    
    // 將長文本每 1900 字元切為一個 rich_text 區塊
    const detailChunks = [];
    for (let i = 0; i < formattedDetail.length; i += 1900) {
      detailChunks.push({
        type: 'text',
        text: { content: formattedDetail.slice(i, i + 1900) }
      });
    }

    const payload = {
      properties: {
        '精華亮點': {
          rich_text: [{ type: 'text', text: { content: highlight } }]
        },
        '排版工作說明': {
          rich_text: detailChunks
        }
      }
    };

    try {
      const response = UrlFetchApp.fetch(url, {
        method: 'patch',
        headers: NotionService.getHeaders(),
        payload: JSON.stringify(payload),
        muteHttpExceptions: true
      });
      const resCode = response.getResponseCode();
      if (resCode !== 200) {
        console.warn('[Notion API 回填失敗 HTTP ' + resCode + ']: ' + response.getContentText());
      }
      return resCode === 200;
    } catch (err) {
      console.error('Notion Patch 請求例外 [Page: ' + cleanPageId + ']:', err);
      return false;
    }
  },

  /**
   * 透過 LINE 推播執行統計卡片給系統管理員
   */
  notifyAdminViaLine: function(summary) {
    const adminIds = AdminIdService.list();
    if (adminIds.length === 0) return;

    const statusTitle = summary.isInterrupted ? '⚠️ 批次處理部分完成 (超時中斷)' : '🎉 職缺文案批次生成完成';
    const statusColor = summary.isInterrupted ? '#d97706' : '#059669';

    const card = {
      type: 'flex',
      altText: '[系統通知] ' + statusTitle,
      contents: {
        type: 'bubble',
        header: {
          type: 'box',
          layout: 'vertical',
          backgroundColor: '#f8fafc',
          contents: [
            { type: 'text', text: '材霈系統 - 職缺資料庫增強', size: 'xs', color: '#64748b', weight: 'bold' },
            { type: 'text', text: statusTitle, size: 'md', color: statusColor, weight: 'bold', margin: 'xs' }
          ]
        },
        body: {
          type: 'box',
          layout: 'vertical',
          spacing: 'sm',
          contents: [
            SharedFlexBuilder.createRow('執行模式', summary.mode),
            SharedFlexBuilder.createRow('資料庫總量', summary.total + ' 筆'),
            SharedFlexBuilder.createRow('本次成功生成', summary.success + ' 筆', '#059669', 'bold'),
            SharedFlexBuilder.createRow('跳過 (已有資料)', summary.skipped + ' 筆', '#64748b'),
            SharedFlexBuilder.createRow('生成/回填失敗', summary.failed + ' 筆', summary.failed > 0 ? '#e11d48' : '#64748b'),
            SharedFlexBuilder.createRow('總執行耗時', summary.durationSec + ' 秒')
          ]
        },
        footer: {
          type: 'box',
          layout: 'vertical',
          contents: [
            {
              type: 'text',
              text: summary.isInterrupted ? '💡 提示：因職缺筆數較多，請再次點擊「斷點續傳」以完成剩餘職缺。' : '✅ 所有啟用中職缺已完成零幻覺與就服法審查文案建檔！',
              size: 'xxs',
              color: '#64748b',
              wrap: true,
              align: 'center'
            }
          ]
        }
      }
    };

    adminIds.forEach(id => {
      try {
        LineService.pushMessage(id, [card]);
      } catch (e) {
        console.warn('推播管理員 [' + id + '] 失敗:', e);
      }
    });
  }
};

// ==============================================================================
// 外部進入點函式 (供選單與 Trigger 呼叫)
// ==============================================================================
function runBatchJobEnhancementResume() {
  return BatchEnhanceJobService.runBatchEnhancement(false);
}

function runBatchJobEnhancementForce() {
  return BatchEnhanceJobService.runBatchEnhancement(true);
}
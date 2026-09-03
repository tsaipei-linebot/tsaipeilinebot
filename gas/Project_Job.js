/**
 * ==============================================================================
 * 專案一：職缺管理 (Project_Job.gs - 材霈有限公司 - 完美排版與4合1單次API增強版)
 * ==============================================================================
 */

// ==============================================================================
// 0. AI 職缺優化與合規檢查服務 (AiJobDescriptionService - 4合1沙盒隔離版)
// ==============================================================================
const AiJobDescriptionService = {
  /**
   * 取得 API Key (相容 ScriptProperties 與 CONFIG)
   */
  getApiKey: function() {
    try {
      const scriptProps = PropertiesService.getScriptProperties();
      const propKey = scriptProps.getProperty('GEMINI_API_KEY') || 
                      scriptProps.getProperty('GEMINI_KEY') || 
                      scriptProps.getProperty('AI_API_KEY');
      if (propKey && String(propKey).trim() !== '') return String(propKey).trim();
    } catch (e) {}

    if (typeof CONFIG !== 'undefined') {
      if (CONFIG.GEMINI_API_KEY) return String(CONFIG.GEMINI_API_KEY).trim();
      if (CONFIG.GEMINI_KEY) return String(CONFIG.GEMINI_KEY).trim();
      if (CONFIG.AI_API_KEY) return String(CONFIG.AI_API_KEY).trim();
    }
    return '';
  },

  /**
   * 地點智慧聚合器：縣市全數保留，行政區超過 4 個時自動聚合為「各區門市/據點」
   */
  formatSmartLocation: function(cityVal, districtVal) {
    let cities = [];
    if (Array.isArray(cityVal)) {
      cities = cityVal;
    } else if (typeof cityVal === 'string' && cityVal.trim()) {
      cities = cityVal.split(/[,，、\s]+/).filter(Boolean);
    }
    const uniqueCities = Array.from(new Set(cities.map(c => String(c || '').trim()).filter(Boolean)));
    const cityStr = uniqueCities.join('、');

    let districts = [];
    if (Array.isArray(districtVal)) {
      districts = districtVal;
    } else if (typeof districtVal === 'string' && districtVal.trim()) {
      districts = districtVal.split(/[,，、\s]+/).filter(Boolean);
    }
    const uniqueDistricts = Array.from(new Set(districts.map(d => String(d || '').trim()).filter(Boolean)));
    const distCount = uniqueDistricts.length;

    if (distCount === 0) {
      return cityStr || '依公司指定地點';
    }

    if (distCount <= 4) {
      const distStr = uniqueDistricts.join('、');
      return cityStr ? `${cityStr}（${distStr}）` : distStr;
    }

    // 行政區 >= 5 個時聚合
    if (cityStr) {
      return `${cityStr} 各區門市據點（共 ${distCount} 區，錄取後依居住地就近分發）`;
    }
    return `各區門市據點（共 ${distCount} 區，錄取後依居住地就近分發）`;
  },

  /**
   * 本地就業服務法與禁語硬性過濾 (上下文感知防護 & 終極排版標準化)
   */
  enforceComplianceRules: function(text) {
    if (!text) return '';
    let sanitized = String(text);

    // 1. 清理可能夾帶的思考雜訊與 Markdown 標記
    sanitized = sanitized.replace(/.*(?:NO intro|Final Polish|Checked|thought|reasoning).*[\r\n]*/gi, '');
    sanitized = sanitized.replace(/^```[a-zA-Z]*\n?/gm, '').replace(/```$/gm, '');

    // 2. 役畢與兵役限制過濾
    sanitized = sanitized.replace(/[（(]?\s*(需|限)?\s*(役畢|免役|未役)\s*[)）]?/gi, '');
    sanitized = sanitized.replace(/(需|限|須)\s*役畢/gi, '');

    // 3. 年齡限制精準過濾
    const numPattern = '[0-9０-９一二兩三四五六七八九十百]+';
    const ageUnit = '(?:歲|周歲|週歲)';
    const ageScope = '(?:以內|以下|以上|左右|上下|內|前)';
    
    // (A) 顯性年齡區間 (例: 20-35歲)
    const ageRangeRegex = new RegExp(`(${numPattern})\\s*[-~～至到到約]\\s*(${numPattern})\\s*${ageUnit}`, 'g');
    sanitized = sanitized.replace(ageRangeRegex, '');

    // (B) 隱性年齡區間 (例: 限 18-40) 排除時間、重量、門牌
    const prefixAgeRangeRegex = new RegExp(`(?:限|須|需|要|年齡|年紀|適合)\\s*(${numPattern})\\s*[-~～至到到約]\\s*(${numPattern})(?!\\s*(?::|：|點|分|時|小時|公斤|kg|KG|Kg|號|樓|段|巷|弄|包|箱|件))`, 'g');
    sanitized = sanitized.replace(prefixAgeRangeRegex, '');

    // (C) 單一年齡限制
    const singleAgeLimitRegex = new RegExp(`(?:限|須|需|要)?\\s*(${numPattern})\\s*${ageUnit}\\s*${ageScope}?`, 'g');
    sanitized = sanitized.replace(singleAgeLimitRegex, '');

    // (D) 嚴格限定詞單一年齡
    const strictAgeScopeRegex = new RegExp(`(?:限|須|需|要|年齡|年紀)\\s*(${numPattern})\\s*${ageScope}`, 'g');
    sanitized = sanitized.replace(strictAgeScopeRegex, '');

    sanitized = sanitized.replace(/(限|須|需)?\s*([0-9一二三四五六七八九十]+)年級生/g, '');
    sanitized = sanitized.replace(/年輕(活力|有幹勁|貌美|力壯)?/g, '具備熱忱');
    sanitized = sanitized.replace(/年紀(輕|小|大)/g, '');

    // 4. 性別限制過濾
    sanitized = sanitized.replace(/限(男|女|男性|女性|男生|女生)/g, '');
    sanitized = sanitized.replace(/適合(女性|男性|男生|女生)/g, '歡迎各界人才');
    sanitized = sanitized.replace(/男女不拘/g, '歡迎各界人才');
    sanitized = sanitized.replace(/限男生搬重/g, '需配合搬重貨物');
    sanitized = sanitized.replace(/限女性細心/g, '需具備細心度');
    sanitized = sanitized.replace(/(男|女)作業員/g, '作業員');
    sanitized = sanitized.replace(/(男|女)理貨員/g, '理貨員');

    // 5. 身心與容貌限制
    sanitized = sanitized.replace(/五官端正|容貌端莊|身家清白|無前科|健全/g, '');

    // 6. 清理多餘符號與水平空格
    sanitized = sanitized.replace(/[,，、]{2,}/g, '、');
    sanitized = sanitized.replace(/[ \t]+/g, ' ');
    sanitized = sanitized.replace(/^[ \t,，、/／-]+|[ \t,，、/／-]+$/gm, '');

    // 7. 排版標準化與換行維護
    sanitized = sanitized
      .replace(/(?:[\r\n\s]*)(?:🎯\s*)?【(?:主要)?工作內容】/gu, '\n\n🎯【工作內容】\n')
      .replace(/(?:[\r\n\s]*)(?:⏰\s*)?【(?:工作)?時間與休假】/gu, '\n\n⏰【時間與休假】\n')
      .replace(/(?:[\r\n\s]*)(?:💰\s*)?【薪資(?:與福利)?待遇】/gu, '\n\n💰【薪資待遇】\n')
      .replace(/(?:[\r\n\s]*)(?:📍\s*)?【(?:工作)?地點(?:資訊|與交通)?】/gu, '\n\n📍【地點資訊】\n');

    sanitized = sanitized.replace(/([^\n])\s*(・)/g, '$1\n$2');
    sanitized = sanitized.replace(/(\r\n|\r|\n){3,}/g, '\n\n');

    return sanitized.trim();
  },

  /**
   * 4 合 1 單次 API 核心生成器 (嚴格資安沙盒：僅讀取 6 個對外欄位)
   */
  generateAllJobArtifacts: function(inputData) {
    const apiKey = this.getApiKey();
    const rawTitle = inputData.external_title || inputData.title || '招募職缺';
    const smartLocation = this.formatSmartLocation(inputData.city, inputData.district);
    const salary = inputData.salary || '依公司規定';
    const shift = Array.isArray(inputData.shift) ? (inputData.shift.join('、') || '依排班規定') : (inputData.shift || '依排班規定');
    const rawDesc = inputData.external_desc || '歡迎洽詢應徵。';

    // 本地合規淨化
    const sanitizedTitle = this.enforceComplianceRules(rawTitle);
    const sanitizedDesc = this.enforceComplianceRules(rawDesc);

    // 快取：6 項沙盒輸入內容跟先前完全相同時，直接複用先前的生成結果，
    // 避免同一職缺短時間內重複送審（例如退回後只改了不影響文案的欄位）時浪費 AI 額度
    const cacheKey = 'AIJOB_' + sha256Hash([sanitizedTitle, smartLocation, salary, shift, sanitizedDesc].join('||'));
    try {
      const cached = CacheService.getScriptCache().get(cacheKey);
      if (cached) {
        console.log('♻️ [AiJob 4in1] 內容與先前送審完全相同，複用快取結果，略過 AI 呼叫');
        return JSON.parse(cached);
      }
    } catch (cacheReadErr) {
      console.warn('[AiJob 4in1] 讀取快取失敗 (略過快取，正常呼叫 AI):', cacheReadErr);
    }

    // 保底回傳結構（AI 全部呼叫失敗時使用）
    // 只陳述確定為真的事實（薪資、班別、地點），不做「無經驗可」這類原始資料未提及、可能失真的宣稱
    const titleForFallback = /^【.*】$/.test(sanitizedTitle) ? sanitizedTitle : `【${sanitizedTitle}】`;
    const fallbackResult = {
      external_title: titleForFallback,
      external_desc: `🎯【工作內容】\n・${sanitizedDesc.replace(/\n+/g, '\n・')}\n\n⏰【時間與休假】\n・工作班別：${shift}\n\n💰【薪資待遇】\n・薪資待遇：${salary}\n\n📍【地點資訊】\n・工作地點：${smartLocation}`,
      highlight: `開放應徵${titleForFallback}！工作地點：${smartLocation}，班別：${shift}，薪資：${salary}，歡迎立即應徵！`,
      formatted_detail: `📋【職缺名稱：${sanitizedTitle}】\n\n🎯【主要工作內容】\n・${sanitizedDesc.replace(/\n+/g, '\n・')}\n\n⏰【工作時間與休假】\n・工作班別：${shift}\n\n💰【薪資與福利待遇】\n・薪資待遇：${salary}\n\n📍【工作地點與交通】\n・工作地點：${smartLocation}\n\n💡 依《就業服務法》規定，本公司所有職缺皆無性別、年齡限制，歡迎所有朋友應徵！`,
      // 標記這是本地規則保底文案，不是真正的 AI 生成，讓呼叫端可以在審核卡片上提示人工複查
      isFallback: true
    };

    if (!apiKey) {
      console.warn('⚠️ [AiJob] 未設定 GEMINI_API_KEY，使用本地保底規則產出');
      return fallbackResult;
    }

    const prompt = `
你是一位資深勞動法規顧問與專業人資文案專家。你的任務是根據下方【嚴格沙盒隔離之 6 項對外欄位資料】，進行語意理解與分流整理，一次性輸出 4 個專業招募文案欄位的 JSON 物件。

【嚴格沙盒隔離資料（禁止讀取或假設任何未提供的內部機密）】
1. 職缺名稱：${sanitizedTitle}
2. 工作地點：${smartLocation}
3. 薪資待遇：${salary}
4. 工作班別：${shift}
5. 對外工作內容原始文字：
${sanitizedDesc}

【核心原則 - 零幻覺與就業服務法審查（最高準則）】
1. 零幻覺（Zero Hallucination）：所有內容 100% 源自上方提供的 6 項資料，嚴禁腦補未提及的福利、設備或工作內容。
2. 《就業服務法》第 5 條合規：若原始文字含有年齡（如 18-40歲）、性別（如限女性）、役畢等歧視性限制，一律徹底剔除。
3. 廠商去識別化：若提及「欣興電子」、「台積電」等特定具體廠牌名稱，修飾為「知名電子大廠」或「知名半導體大廠」。
4. 語意精準分流（極重要）：
   - 上班時間、起訖時段（如 18:00-23:00）、休假方式【只能放在時間與休假區塊】，絕對嚴禁留在工作內容中！
   - 門牌地址、停車資訊【只能放在地點資訊區塊】！
   - 薪資、時薪、加班費【只能放在薪資待遇區塊】！
   - 工作內容區塊【只保留實際工作項目、搬重公斤數(如5-25kg)、著便服、置物櫃等現場作業條件】。

【4 個產出欄位詳細規範】

1. "external_title":
   - 對外吸睛標題（繁體中文 20～38 字）。
   - 格式如：【🔥地區/特色】真實職缺名稱【✨班別/薪資】。

2. "external_desc":
   - 俐落條列版對外工作內容，區塊間以空行分隔，項目皆以「・」條列：
     🎯【工作內容】
     ・（工作事項、搬重、服裝與置物櫃等現場規定）

     ⏰【時間與休假】
     ・（班別名稱、完整起訖時段如 18:00-23:00、休假方式）

     💰【薪資待遇】
     ・（時薪/月薪、依法計算之加班費或各項法定保障）

     📍【地點資訊】
     ・工作地點：（完整門牌地址或智慧聚合地點）
     ・周邊交通：（停車或交通便利性，若原始資料有提及）

3. "highlight":
   - 手機卡片吸引短句（繁體中文 30～45 字），點出原始資料已具備之優勢（如：無經驗可、固定班等）。
   - 語意必須完整通順，結尾必須有驚嘆號（！）或句號（。），絕對嚴禁語意中斷或切掉半句！

4. "formatted_detail":
   - 標準化完整規格工作說明書，格式如下（區塊間空行，項目以「・」條列）：
     📋【職缺名稱：美化後對外職稱】

     🎯【主要工作內容】
     ・（條列實際工作職責、搬重條件與現場作業規定）

     ⏰【工作時間與休假】
     ・工作班別：${shift}
     ・時段選擇：（完整起訖時間，若原始資料有提及）
     ・休假制度：（休假方式，若原始資料有提及）

     💰【薪資與福利待遇】
     ・薪資待遇：${salary}
     ・各項法定保障：享有勞健保與依法提撥退休金

     📍【工作地點與交通】
     ・工作地點：${smartLocation}（若有詳細門牌地址則完整列出）
     ・周邊交通：（停車或交通資訊，若原始資料有提及）

     💡 依《就業服務法》規定，本公司所有職缺皆無性別、年齡限制，歡迎所有朋友應徵！

【輸出格式】
請嚴格輸出合法 JSON 物件，不要輸出任何 Markdown 標記、英文思考過程或引號：
{
  "external_title": "美化後對外標題",
  "external_desc": "4大Emoji區塊工作內容",
  "highlight": "30-45字精華短句（完整句尾）",
  "formatted_detail": "6大區塊標準化說明完整文字"
}
`.trim();

    // gemini-2.5-flash/flash-lite、gemini-1.5-flash 已被 Google 排除在此 API Key 的可用範圍外（HTTP 404）。
    // 改用 3.5 系列（用 listAvailableGeminiModels() 確認過確實可用），並加上 gemini-flash-latest 別名當最後一層保險，
    // 之後若 3.5 系列又被汰換，還有一層自動不會整套斷掉。
    const targetModels = ['gemini-3.5-flash', 'gemini-3.5-flash-lite', 'gemini-flash-latest'];
    const MAX_RETRY_PER_MODEL = 2; // 同一模型遇到 429 額度限制時的重試次數上限
    const RETRY_BASE_DELAY_MS = 1000; // 重試遞增等待時間基準

    for (let i = 0; i < targetModels.length; i++) {
      const model = targetModels[i];
      const url = `https://generativelanguage.googleapis.com/v1beta/models/${encodeURIComponent(model)}:generateContent?key=${encodeURIComponent(apiKey)}`;

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
          console.log(`📡 [AiJob 4in1] 呼叫模型 [${model}] 生成職缺文案...(第 ${attempt + 1} 次嘗試)`);
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

              let polishedTitle = String(parsed.external_title || '').trim();
              polishedTitle = polishedTitle.replace(/^["'【]*(.*?)["'】]*$/g, '【$1】').replace(/^【【/, '【').replace(/】】$/, '】');
              polishedTitle = this.enforceComplianceRules(polishedTitle) || fallbackResult.external_title;

              const polishedDesc = this.enforceComplianceRules(String(parsed.external_desc || '')) || fallbackResult.external_desc;
              const highlight = String(parsed.highlight || '').trim() || fallbackResult.highlight;
              const formattedDetail = String(parsed.formatted_detail || '').trim() || fallbackResult.formatted_detail;

              console.log(`✅ [AiJob 4in1] 模型 [${model}] 成功生成 4 合 1 職缺文案！`);
              const result = {
                external_title: polishedTitle,
                external_desc: polishedDesc,
                highlight: highlight,
                formatted_detail: formattedDetail,
                isFallback: false
              };

              try {
                CacheService.getScriptCache().put(cacheKey, JSON.stringify(result), 21600);
              } catch (cacheWriteErr) {
                console.warn('[AiJob 4in1] 寫入快取失敗 (不影響本次結果):', cacheWriteErr);
              }

              return result;
            }
            // HTTP 200 但沒有文字內容：換下一個模型，重試同一模型也不會有幫助
            break;
          }

          if (resCode === 429) {
            const willRetry = attempt < MAX_RETRY_PER_MODEL;
            console.warn(`⚠️ [AiJob 4in1] 模型 [${model}] 額度限制 (HTTP 429)，${willRetry ? '等待後重試...' : '重試已達上限，換下一個模型'}`);
            if (willRetry) {
              Utilities.sleep(RETRY_BASE_DELAY_MS * (attempt + 1));
              continue;
            }
            break;
          }

          // 其他錯誤：記錄詳細狀態碼與回應內容方便排查，直接換下一個模型
          console.warn(`[AiJob 4in1] 模型 [${model}] 呼叫失敗 (HTTP ${resCode}): ${response.getContentText().slice(0, 300)}`);
          break;
        } catch (err) {
          console.warn(`[AiJob 4in1] 模型 [${model}] 呼叫異常:`, err);
          break;
        }
      }
    }

    console.warn('⚠️ [AiJob 4in1] 所有模型呼叫失敗，啟用本地保底文案');
    return fallbackResult;
  }
};

// ==============================================================================
// 1. 職缺工作流程服務 (JobWorkflowService)
// ==============================================================================
const JobWorkflowService = {
  processJobSubmission: function(payload) {
    const fields = payload.fields || {};
    const applicantName = String(fields.applicant_name || (payload.applicant && payload.applicant.displayName) || '').trim();
    
    if (!applicantName) {
      return {
        status: 'unauthorized',
        message: '請選取「申請同仁姓名」，以利系統核對您的組織身分。'
      };
    }
    
    // 1. 檢查同仁是否已綁定 LINE ID 與 PIN 碼
    const employeeBinding = OrgService.getEmployeeBindingByName(applicantName);
    if (!employeeBinding.isBound) {
      return {
        status: 'unauthorized',
        message: `同仁【${applicantName}】尚未完成 LINE 身分綁定！\n請先至 LINE 官方帳號發送「綁定+${applicantName}+4位PIN碼」完成綁定後再進行送審。`
      };
    }

    const applicant = {
      displayName: applicantName,
      userId: employeeBinding.empLineId
    };

    let mode = payload.mode;
    const updateAction = payload.updateAction || 'create';
    let pageId = payload.pageId;
    let oldJobData = null;

    // 將查重機制提前：若為新增模式，先檢查 Notion 是否已有完全相同職缺名稱
    if (mode === 'create') {
      const existingPageId = NotionService.findJobByTitle(fields.title);
      if (existingPageId) {
        console.log(`Notion 中已存在相同職缺名稱 [${fields.title}] (ID: ${existingPageId})，自動轉為更新模式進行權限驗證`);
        pageId = existingPageId;
        mode = 'update';
      }
    }

    // 2. 檢查刊登人權限與取得舊資料比對 (既有職缺：只限「原刊登人」本人或「原刊登人之直屬主管」修改)
    if (mode === 'update' && pageId) {
      oldJobData = NotionService.getJobPageById(pageId);
      if (oldJobData) {
        const originalPublisher = String(oldJobData.publisher || '').trim();
        
        if (originalPublisher !== '') {
          const isSelf = (originalPublisher === applicantName);
          const isSupervisor = OrgService.isSupervisorOf(applicantName, originalPublisher);

          if (!isSelf && !isSupervisor) {
            return {
              status: 'forbidden',
              message: `【權限錯誤】此職缺之原刊登人為【${originalPublisher}】！\n您非原刊登人亦非其直屬主管，無法異動此職缺。`
            };
          }
          fields.publisher = originalPublisher;
        } else {
          // 無主職缺：僅限管理主管接管
          if (!AdminIdService.isAdmin(applicant.userId)) {
            return {
              status: 'forbidden',
              message: `【權限錯誤】此為無主職缺，僅限系統管理員可進行指派或維護。`
            };
          }
          fields.publisher = applicantName;
        }
      }
    } else {
      fields.publisher = applicantName;
    }

    // --- 4 合 1 單次 API 呼叫：嚴格僅傳送 6 個對外欄位至 AI 服務 ---
    const rawExternalTitle = fields.external_title || fields['職缺名稱(對外)'] || fields.external_name || fields.title || '';
    const rawExternalDesc = fields.external_desc || fields['工作內容(對外)'] || fields.external_content || '';

    const aiArtifacts = AiJobDescriptionService.generateAllJobArtifacts({
      title: fields.title,
      external_title: rawExternalTitle,
      city: fields.city,
      district: fields.district,
      salary: fields.salary,
      shift: fields.shift,
      external_desc: rawExternalDesc
    });

    fields.external_title = aiArtifacts.external_title;
    fields.external_desc = aiArtifacts.external_desc;
    fields.highlight = aiArtifacts.highlight;
    fields.formatted_detail = aiArtifacts.formatted_detail;
    const aiFallbackUsed = aiArtifacts.isFallback === true;

    // 比對異動欄位 (僅在 update 模式且有舊資料時進行比對)
    const diffs = (mode === 'update' && oldJobData) ? getJobFieldsDiff(oldJobData, fields) : {};

    // 3. 取得審核主管清單
    const supervisorList = OrgService.getSupervisorsByApplicantUserId(applicant.userId, applicant.displayName);
    console.log(`[JobWorkflow] 送審同仁 [${applicantName}] 匹配到主管名單:`, JSON.stringify(supervisorList));

    if (!supervisorList || supervisorList.length === 0) {
      try {
        const warnCard = SharedFlexBuilder.buildNoSupervisorWarningCard({
          applicantName: applicantName,
          actionType: '招募職缺維護',
          itemTitle: fields.title || fields.internal_title || '職缺'
        });
        LineService.pushMessage(applicant.userId, [warnCard]);
      } catch (pushErr) {
        console.warn('推播未配置主管警示失敗:', pushErr);
      }

      return {
        status: 'supervisor_unassigned',
        message: `【送審失敗】組織表中尚未為同仁【${applicantName}】設定審核主管！\n系統針對此筆送審已攔截，請聯繫系統管理員協助於後台組織表指派主管。`
      };
    }
    
    let imageUrl = fields.existing_image_url || '';
    if (payload.image && payload.image.base64) {
      imageUrl = DriveService.uploadBase64Image(
        payload.image.base64,
        payload.image.filename || `job_${Date.now()}.jpg`
      );
    }
    fields.image_url = imageUrl;
    
    if (mode === 'update' && pageId) {
      NotionService.updateJobPage(pageId, fields, '待審核', '停招');
    } else {
      pageId = NotionService.createJobPage(fields, '待審核', '停招');
    }
    
    const targetStatusText = (updateAction === 'stop_recruiting') ? '停招' : '一週內有更新';

    // 4. 送出審核當下推播設定 (帶入異動比對結果 diffs)
    const supervisorCard = JobFlexMessageBuilder.buildJobApprovalCard({
      pageId: pageId,
      mode: mode,
      updateAction: updateAction,
      targetStatus: targetStatusText,
      applicant: applicant,
      fields: fields,
      imageUrl: imageUrl,
      diffs: diffs,
      aiFallbackUsed: aiFallbackUsed
    });

    const commonJobPlainText = buildJobPlainTextContent(
      fields, 
      '職缺送審文案 - 詳細內容', 
      `💡 提示：長按此訊息即可「複製」或「轉傳」給求職者與各大社群刊登。`,
      targetStatusText,
      null,
      diffs
    );

    let pushSuccessCount = 0;

    // 主管（主任）收到：審核卡片 + 職缺核准文案
    supervisorList.forEach((sup, idx) => {
      const cleanLineId = String(sup.lineUserId || '').replace(/[^a-zA-Z0-9_-]/g, '').trim();
      
      if (LINE_ID_REGEX.test(cleanLineId)) {
        try {
          console.log(`[JobWorkflow] 正在推播審核通知給第 ${idx + 1} 位主管 [${sup.supervisorName}] (${cleanLineId})...`);
          
          const resCard = LineService.pushMessage(cleanLineId, [supervisorCard]);
          const resText = LineService.pushMessage(cleanLineId, [{ type: 'text', text: commonJobPlainText }]);
          
          if (resCard || resText) {
            pushSuccessCount++;
          }
        } catch (singlePushErr) {
          console.error(`推播給主管 [${sup.supervisorName}] 失敗:`, singlePushErr);
        }
      } else {
        console.warn(`[JobWorkflow] 主管 [${sup.supervisorName}] 的 LINE ID 無效: [${sup.lineUserId}]`);
      }
    });

    const supervisorNames = supervisorList.map(s => s.supervisorName).join('、');

    if (pushSuccessCount === 0) {
      try {
        LineService.pushMessage(applicant.userId, [{
          type: 'text',
          text: `⚠️ 【系統警告】您送審的職缺【${fields.title || '未命名'}】已成功寫入系統，但系統無法將審核通知推播給您的主管！\n\n可能原因：主管（${supervisorNames}）尚未完成 LINE 綁定或已封鎖官方帳號。\n👉 請務必主動聯繫您的主管請其於系統進行審核。`
        }]);
      } catch (e) {}
    } else {
      // 專員（同仁）收到：送審確認卡片 + 職缺核准文案
      try {
        const applicantCard = JobFlexMessageBuilder.buildJobApplicantReceiptCard({
          pageId: pageId,
          mode: mode,
          fields: fields,
          imageUrl: imageUrl,
          targetStatus: targetStatusText,
          supervisorName: supervisorNames,
          diffs: diffs,
          aiFallbackUsed: aiFallbackUsed
        });
        LineService.pushMessage(applicant.userId, [applicantCard]);
        LineService.pushMessage(applicant.userId, [{ type: 'text', text: commonJobPlainText }]);
      } catch (pushAppErr) {
        console.warn('推播同仁存根失敗:', pushAppErr);
      }
    }
    
    return {
      status: 'success',
      message: '職缺已成功送出審核',
      pageId: pageId,
      imageUrl: imageUrl
    };
  },

  handleJobPostback: function(event, postbackData, operatorSupervisorId) {
    const status = postbackData.status;
    const applicantId = postbackData.applicant_id;
    const pageId = postbackData.page_id;
    const updateAction = postbackData.update_action;

    let currentJobDetail = NotionService.getJobPageById(pageId);
    if (!currentJobDetail) {
      LineService.replyTextMessage(event.replyToken, '❌ 操作失敗：找不到該職缺資料或資料已被刪除。');
      return;
    }
    if (currentJobDetail.review_status === '已核准' || currentJobDetail.review_status === '已退回') {
      LineService.replyTextMessage(event.replyToken, `⚠️ 操作無效：此職缺已由主管完成審核 (目前狀態：${currentJobDetail.review_status})，無法重複簽核。`);
      return;
    }
    
    let reviewStatus = (status === 'approve') ? '已核准' : '已退回';
    let jobStatus = null;
    let finalDateStr = Utilities.formatDate(new Date(), 'Asia/Taipei', 'yyyy-MM-dd');
    
    if (status === 'approve') {
      jobStatus = (updateAction === 'stop_recruiting') ? '停招' : '一週內有更新';
    }
    
    const updateNotionResult = NotionService.updateJobPageReviewStatus(pageId, reviewStatus, jobStatus, finalDateStr);
    console.log(`Notion 審核狀態更新結果:`, JSON.stringify(updateNotionResult));

    currentJobDetail = NotionService.getJobPageById(pageId) || currentJobDetail;
    const jobTitle = currentJobDetail ? (currentJobDetail.title || currentJobDetail.internal_title || '招募職缺') : '招募職缺';

    const replyText = (status === 'approve')
      ? `✅ 職缺審核完成：已核准！\n職缺狀態已正式生效為【${jobStatus}】。`
      : `❌ 職缺審核完成：已退回！`;
    LineService.replyTextMessage(event.replyToken, replyText);

    if (status === 'approve') {
      try {
        if (currentJobDetail) {
          const plainTextMessage = buildJobPlainTextContent(
            currentJobDetail, 
            '職缺核准文案 - 可直接複製轉發', 
            `💡 提示：長按此訊息即可「複製」或「轉傳」給求職者與各大社群刊登。`,
            jobStatus,
            finalDateStr
          );

          const approvalNoticeText = `✅ 職缺審核完成：已核准！\n職缺狀態已正式生效為【${jobStatus}】。`;

          // 1. 專員收到：審核完成文字
          if (applicantId && LINE_ID_REGEX.test(applicantId)) {
            LineService.pushMessage(applicantId, [{ type: 'text', text: approvalNoticeText }]);
          }

          // 2. 主任（主管群除操作者外）收到：審核完成文字
          try {
            const allSupervisors = OrgService.getSupervisorsByApplicantUserId(applicantId, '');
            allSupervisors.forEach(sup => {
              const cleanLineId = String(sup.lineUserId || '').replace(/[^a-zA-Z0-9_-]/g, '').trim();
              if (LINE_ID_REGEX.test(cleanLineId) && cleanLineId !== operatorSupervisorId) {
                LineService.pushMessage(cleanLineId, [{ type: 'text', text: approvalNoticeText }]);
              }
            });
          } catch (supSyncErr) {
            console.warn('推播核准通知給其他主管失敗:', supSyncErr);
          }

          // 3. 綁定群組收到：【只發送職缺核准純文字文案】
          const targetGroupId = getTargetLineGroupId();
          if (targetGroupId) {
            console.log(`📢 正在自動發送核准職缺純文字文案至綁定群組 [${targetGroupId}]...`);
            LineService.pushMessage(targetGroupId, [{ type: 'text', text: plainTextMessage }]);
          } else {
            console.warn('⚠️ 尚未設定任何群組 ID，略過群組推播。');
          }
        }
      } catch (packErr) {
        console.error('打包職缺核准詳細訊息失敗:', packErr);
      }
    } else {
      try {
        const allSupervisors = OrgService.getSupervisorsByApplicantUserId(applicantId, '');
        const syncSupMsg = `⚠️ 【職缺審核同步】主管已退回職缺：【${jobTitle}】`;
        allSupervisors.forEach(sup => {
          const cleanLineId = String(sup.lineUserId || '').replace(/[^a-zA-Z0-9_-]/g, '').trim();
          if (LINE_ID_REGEX.test(cleanLineId) && cleanLineId !== operatorSupervisorId) {
            LineService.pushMessage(cleanLineId, [{ type: 'text', text: syncSupMsg }]);
          }
        });
      } catch (supFindErr) {
        console.warn('同步其他主管退回失敗:', supFindErr);
      }

      if (applicantId && LINE_ID_REGEX.test(applicantId)) {
        const rejectMsg = `⚠️ 您提交的招募職缺【${jobTitle}】已被主管退回，請洽主管確認或重新維護提交。`;
        LineService.pushMessage(applicantId, [{ type: 'text', text: rejectMsg }]);
      }
    }
  }
};

// ==============================================================================
// 2. 欄位差異比對函式 (Diff Service)
// ==============================================================================
function getJobFieldsDiff(oldJob, newFields) {
  if (!oldJob || !newFields) return {};
  const diffs = {};
  
  const compareKeys = [
    'vendor', 'title', 'internal_title', 'external_title', 
    'salary', 'interview_method', 'internal_desc', 'external_desc', 'notes',
    'industry', 'category', 'job_type', 'foreign_student', 'job_cycle',
    'city', 'district', 'branch', 'shift', 'leave_type', 'pay_method'
  ];

  compareKeys.forEach(key => {
    let oldVal = oldJob[key] || '';
    let newVal = newFields[key] || '';

    if (Array.isArray(oldVal)) oldVal = oldVal.slice().sort().join('、');
    if (Array.isArray(newVal)) newVal = newVal.slice().sort().join('、');

    oldVal = String(oldVal).trim();
    newVal = String(newVal).trim();

    if (oldVal !== newVal) {
      diffs[key] = {
        oldVal: oldVal,
        newVal: newVal
      };
    }
  });

  return diffs;
}

// ==============================================================================
// 3. Notion API 服務模組 (NotionService - 支援長文本安全分塊)
// ==============================================================================
const NotionService = {
  _fetchWithFallback: function(url, method, payloadObj) {
    let options = {
      method: method,
      headers: this.getHeaders(),
      payload: JSON.stringify(payloadObj),
      muteHttpExceptions: true
    };
    
    let response = UrlFetchApp.fetch(url, options);
    let resCode = response.getResponseCode();
    
    if (resCode >= 400) {
      let resText = response.getContentText();
      console.warn(`[Notion API] 寫入報錯 (HTTP ${resCode}): ${resText}`);
      
      if (resText.includes('最後核准日期') && payloadObj.properties && payloadObj.properties['最後核准日期']) {
        let noDateObj = JSON.parse(JSON.stringify(payloadObj));
        delete noDateObj.properties['最後核准日期'];
        options.payload = JSON.stringify(noDateObj);
        response = UrlFetchApp.fetch(url, options);
      }
    }
    return response;
  },

  getJobPageById: function(pageId) {
    if (!pageId) return null;
    try {
      const url = `https://api.notion.com/v1/pages/${pageId}`;
      const response = UrlFetchApp.fetch(url, {
        method: 'get',
        headers: this.getHeaders(),
        muteHttpExceptions: true
      });
      const page = JSON.parse(response.getContentText());
      if (!page || page.object === 'error') return null;

      const props = page.properties;
      return {
        id: page.id,
        vendor: this.getPropText(props['系統廠商名稱']),
        title: this.getPropText(props['職缺名稱']),
        publisher: this.getPropText(props['刊登人']),
        internal_title: this.getPropText(props['職缺名稱(對內)'] || props['職缺名稱（對內）'] || props['職缺名稱 (對內)']),
        external_title: this.getPropText(props['職缺名稱(對外)'] || props['職缺名稱（對外）'] || props['職缺名稱 (對外)']),
        salary: this.getPropText(props['薪資']),
        interview_method: this.getPropText(props['面試方式']),
        internal_desc: this.getPropText(props['工作內容(對內)']),
        external_desc: this.getPropText(props['工作內容(對外)']),
        highlight: this.getPropText(props['精華亮點']),
        formatted_detail: this.getPropText(props['排版工作說明']),
        notes: this.getPropText(props['備註']),
        image_url: this.getPropFileUrl(props['職缺地區&缺額']),
        industry: this.getMultiSelect(props['行業別']),
        category: this.getMultiSelect(props['職務類別']),
        job_type: this.getMultiSelect(props['全/兼職']),
        foreign_student: this.getMultiSelect(props['外籍生']),
        job_cycle: this.getMultiSelect(props['職缺週期']),
        city: this.getMultiSelect(props['縣市']),
        district: this.getMultiSelect(props['行政區']),
        branch: this.getMultiSelect(props['負責所別']),
        shift: this.getMultiSelect(props['班別']),
        leave_type: this.getMultiSelect(props['休假方式']),
        pay_method: this.getMultiSelect(props['領薪方式']),
        status: this.getPropText(props['狀態']),
        review_status: this.getPropText(props['審核狀態'])
      };
    } catch (e) {
      console.error('透過 PageId 讀取 Notion 失敗:', e);
      return null;
    }
  },

  findJobByTitle: function(title) {
    if (!title) return null;
    try {
      const queryUrl = `https://api.notion.com/v1/databases/${CONFIG.NOTION_DATABASE_ID}/query`;
      const payload = {
        filter: {
          property: '職缺名稱',
          title: {
            equals: String(title).trim()
          }
        },
        page_size: 1
      };
      const response = UrlFetchApp.fetch(queryUrl, {
        method: 'post',
        headers: this.getHeaders(),
        payload: JSON.stringify(payload),
        muteHttpExceptions: true
      });
      const json = JSON.parse(response.getContentText());
      if (json.results && json.results.length > 0) {
        return json.results[0].id;
      }
    } catch (err) {
      console.error('查詢職缺名稱重複時發生錯誤:', err);
    }
    return null;
  },

  getAllJobsForSelect: function(userName = '', subordinates = [], userId = '') {
    const queryUrl = `https://api.notion.com/v1/databases/${CONFIG.NOTION_DATABASE_ID}/query`;
    const jobs = [];
    let hasMore = true;
    let nextCursor = null;
    const validSubordinates = Array.isArray(subordinates) ? subordinates : [];

    const isAdmin = AdminIdService.isAdmin(userId);

    let filterParams = undefined;
    if (userName) {
      const orConditions = [
        { property: '刊登人', rich_text: { equals: userName } }
      ];
      
      if (isAdmin) {
        orConditions.push({ property: '刊登人', rich_text: { is_empty: true } });
      }

      validSubordinates.forEach(sub => {
        orConditions.push({ property: '刊登人', rich_text: { equals: String(sub).trim() } });
      });
      filterParams = { or: orConditions };
    }

    while (hasMore) {
      const payload = { page_size: 100 };
      if (filterParams) {
        payload.filter = filterParams;
      }
      if (nextCursor) {
        payload.start_cursor = nextCursor;
      }

      const response = UrlFetchApp.fetch(queryUrl, {
        method: 'post',
        headers: this.getHeaders(),
        payload: JSON.stringify(payload),
        muteHttpExceptions: true
      });
      
      const json = JSON.parse(response.getContentText());
      if (json.object === 'error') {
        throw new Error(`Notion Query 失敗: [${json.status}] ${json.message}`);
      }

      if (json.results && Array.isArray(json.results)) {
        json.results.forEach(page => {
          const props = page.properties;
          const publisher = this.getPropText(props['刊登人']);
          
          const isSelf = (publisher === userName);
          const isSupervisor = validSubordinates.includes(publisher);
          const isUnowned = (!publisher);
          
          if (userName && !isSelf && !isSupervisor && !(isAdmin && isUnowned)) {
            return; 
          }

          const vendor = this.getPropText(props['系統廠商名稱']);
          const title = this.getPropText(props['職缺名稱']);
          const internalTitle = this.getPropText(props['職缺名稱(對內)'] || props['職缺名稱（對內）'] || props['職缺名稱 (對內)']);
          let label = `【${vendor || '未定廠商'}】${title || '未命名職缺'}`;
          
          if (publisher && publisher !== userName) {
            label += ` [部屬:${publisher}]`;
          } else if (isUnowned && isAdmin) {
            label += ` [無主職缺]`;
          }

          jobs.push({
            id: page.id,
            label: label,
            vendor: vendor,
            title: title,
            publisher: publisher,
            internal_title: internalTitle,
            external_title: this.getPropText(props['職缺名稱(對外)'] || props['職缺名稱（對外）'] || props['職缺名稱 (對外)']),
            salary: this.getPropText(props['薪資']),
            interview_method: this.getPropText(props['面試方式']),
            internal_desc: this.getPropText(props['工作內容(對內)']),
            external_desc: this.getPropText(props['工作內容(對外)']),
            highlight: this.getPropText(props['精華亮點']),
            formatted_detail: this.getPropText(props['排版工作說明']),
            notes: this.getPropText(props['備註']),
            image_url: this.getPropFileUrl(props['職缺地區&缺額']),
            industry: this.getMultiSelect(props['行業別']),
            category: this.getMultiSelect(props['職務類別']),
            job_type: this.getMultiSelect(props['全/兼職']),
            foreign_student: this.getMultiSelect(props['外籍生']),
            job_cycle: this.getMultiSelect(props['職缺週期']),
            city: this.getMultiSelect(props['縣市']),
            district: this.getMultiSelect(props['行政區']),
            branch: this.getMultiSelect(props['負責所別']),
            shift: this.getMultiSelect(props['班別']),
            leave_type: this.getMultiSelect(props['休假方式']),
            pay_method: this.getMultiSelect(props['領薪方式'])
          });
        });
      }

      hasMore = json.has_more;
      nextCursor = json.next_cursor;
    }

    return jobs;
  },

  updateJobsToRecruitingAfterOneWeek: function() {
    try {
      const queryUrl = `https://api.notion.com/v1/databases/${CONFIG.NOTION_DATABASE_ID}/query`;
      let hasMore = true;
      let nextCursor = null;

      const now = new Date();
      const SEVEN_DAYS_MS = 7 * 24 * 60 * 60 * 1000;

      while (hasMore) {
        const payload = {
          filter: {
            property: '狀態',
            status: {
              equals: '一週內有更新'
            }
          },
          page_size: 100
        };
        if (nextCursor) payload.start_cursor = nextCursor;
        
        const response = UrlFetchApp.fetch(queryUrl, {
          method: 'post',
          headers: this.getHeaders(),
          payload: JSON.stringify(payload),
          muteHttpExceptions: true
        });
        
        const json = JSON.parse(response.getContentText());
        if (!json.results || !Array.isArray(json.results)) break;

        json.results.forEach(page => {
          const props = page.properties;
          const approveDateStr = this.getPropText(props['最後核准日期']);
          
          if (approveDateStr) {
            const approveDate = new Date(approveDateStr);
            const diffTime = now.getTime() - approveDate.getTime();
            
            if (diffTime >= SEVEN_DAYS_MS) {
              const title = this.getPropText(props['職缺名稱']);
              console.log(`⏳ 職缺 [${title}] 核准已滿 7 天，狀態自動更新為【招募中】`);
              this.updateJobPageReviewStatus(page.id, null, '招募中', null);
            }
          }
        });

        hasMore = json.has_more;
        nextCursor = json.next_cursor;
      }
    } catch (err) {
      console.error('執行一週後自動轉招募中排程失敗:', err);
    }
  },

  getMultiSelect: function(prop) {
    if (!prop) return [];
    if (prop.multi_select && Array.isArray(prop.multi_select)) {
      return prop.multi_select.map(item => item.name);
    }
    if (prop.select && prop.select.name) {
      return [prop.select.name];
    }
    if (prop.rich_text && Array.isArray(prop.rich_text) && prop.rich_text.length > 0) {
      const txt = prop.rich_text.map(t => t.plain_text || '').join('');
      return txt.split(/[,，、\s]+/).map(s => s.trim()).filter(Boolean);
    }
    return [];
  },

  buildMultiSelectProp: function(val) {
    if (!val) return { multi_select: [] };
    let arr = [];
    if (Array.isArray(val)) {
      arr = val;
    } else if (typeof val === 'string') {
      arr = val.split(/[,，、\s]+/).map(s => s.trim()).filter(Boolean);
    }
    return {
      multi_select: arr.map(name => ({ name: String(name) }))
    };
  },

  /**
   * 建立 Rich Text 屬性 (內建 1900 字元長文本自動分塊防護)
   */
  buildRichTextProp: function(val) {
    let content = '';
    if (Array.isArray(val)) {
      content = val.join('、');
    } else if (val !== undefined && val !== null) {
      content = String(val);
    }

    if (!content) {
      return { rich_text: [] };
    }

    // 當文字長度超過 1900 字元時，依序分塊
    const chunks = [];
    for (let i = 0; i < content.length; i += 1900) {
      chunks.push({
        type: 'text',
        text: { content: content.slice(i, i + 1900) }
      });
    }

    return { rich_text: chunks };
  },

  buildTitleProp: function(val) {
    let content = '';
    if (Array.isArray(val)) {
      content = val.join('、');
    } else if (val !== undefined && val !== null) {
      content = String(val);
    }
    return {
      title: [{ text: { content: content } }]
    };
  },

  createJobPage: function(fields, reviewStatus, jobStatus) {
    const url = 'https://api.notion.com/v1/pages';
    const properties = this.buildNotionProperties(fields, reviewStatus, jobStatus);
    
    const payload = {
      parent: { database_id: CONFIG.NOTION_DATABASE_ID },
      properties: properties
    };
    
    const response = this._fetchWithFallback(url, 'post', payload);
    
    const json = JSON.parse(response.getContentText());
    if (json.id) {
      return json.id;
    } else {
      console.error('Notion Page 建立失敗:', response.getContentText());
      throw new Error('Notion Page 建立失敗: ' + JSON.stringify(json));
    }
  },
  
  updateJobPage: function(pageId, fields, reviewStatus, jobStatus) {
    const url = `https://api.notion.com/v1/pages/${pageId}`;
    const properties = this.buildNotionProperties(fields, reviewStatus, jobStatus);
    
    const response = this._fetchWithFallback(url, 'patch', { properties: properties });
    
    return JSON.parse(response.getContentText());
  },
  
  updateJobPageReviewStatus: function(pageId, reviewStatus, jobStatus, approveDate) {
    const url = `https://api.notion.com/v1/pages/${pageId}`;

    let pageProps = {};
    try {
      const getRes = UrlFetchApp.fetch(url, {
        method: 'get',
        headers: this.getHeaders(),
        muteHttpExceptions: true
      });
      const pageJson = JSON.parse(getRes.getContentText());
      if (pageJson && pageJson.properties) {
        pageProps = pageJson.properties;
      }
    } catch (e) {
      console.warn('取得 Notion Page 屬性定義失敗:', e);
    }

    if (reviewStatus) {
      let reviewPayload = { properties: { '審核狀態': { select: { name: String(reviewStatus) } } } };
      let resReview = UrlFetchApp.fetch(url, {
        method: 'patch',
        headers: this.getHeaders(),
        payload: JSON.stringify(reviewPayload),
        muteHttpExceptions: true
      });

      if (resReview.getResponseCode() >= 400) {
        reviewPayload = { properties: { '審核狀態': { multi_select: [{ name: String(reviewStatus) }] } } };
        resReview = UrlFetchApp.fetch(url, {
          method: 'patch',
          headers: this.getHeaders(),
          payload: JSON.stringify(reviewPayload),
          muteHttpExceptions: true
        });
      }
      console.log(`[Notion PATCH 審核狀態] Page [${pageId}] - HTTP ${resReview.getResponseCode()}: ${resReview.getContentText()}`);
    }

    if (jobStatus) {
      let jobPayload = { properties: { '狀態': { status: { name: String(jobStatus) } } } };
      let resStatus = UrlFetchApp.fetch(url, {
        method: 'patch',
        headers: this.getHeaders(),
        payload: JSON.stringify(jobPayload),
        muteHttpExceptions: true
      });

      if (resStatus.getResponseCode() >= 400) {
        jobPayload = { properties: { '狀態': { select: { name: String(jobStatus) } } } };
        resStatus = UrlFetchApp.fetch(url, {
          method: 'patch',
          headers: this.getHeaders(),
          payload: JSON.stringify(jobPayload),
          muteHttpExceptions: true
        });
      }
      console.log(`[Notion PATCH 狀態] Page [${pageId}] - HTTP ${resStatus.getResponseCode()}: ${resStatus.getContentText()}`);
    }

    if (approveDate && pageProps['最後核准日期']) {
      let datePayload = {};
      if (pageProps['最後核准日期'].type === 'date') {
        datePayload = { properties: { '最後核准日期': { date: { start: approveDate } } } };
      } else if (pageProps['最後核准日期'].type === 'rich_text') {
        datePayload = { properties: { '最後核准日期': { rich_text: [{ text: { content: approveDate } }] } } };
      }
      if (Object.keys(datePayload).length > 0) {
        const resDate = UrlFetchApp.fetch(url, {
          method: 'patch',
          headers: this.getHeaders(),
          payload: JSON.stringify(datePayload),
          muteHttpExceptions: true
        });
        console.log(`[Notion PATCH 最後核准日期] Page [${pageId}] - HTTP ${resDate.getResponseCode()}: ${resDate.getContentText()}`);
      }
    }

    return { status: 'success' };
  },
  
  buildNotionProperties: function(f, reviewStatus, jobStatus) {
    const props = {};
    
    props['職缺名稱'] = this.buildTitleProp(f.title);
    props['系統廠商名稱'] = this.buildRichTextProp(f.vendor);
    props['職缺名稱(對內)'] = this.buildRichTextProp(f.internal_title);
    props['職缺名稱(對外)'] = this.buildRichTextProp(f.external_title);
    props['刊登人'] = this.buildRichTextProp(f.publisher || f.applicant_name);
    props['薪資'] = this.buildRichTextProp(f.salary);
    props['面試方式'] = this.buildRichTextProp(f.interview_method);
    props['工作內容(對內)'] = this.buildRichTextProp(f.internal_desc);
    props['工作內容(對外)'] = this.buildRichTextProp(f.external_desc);
    props['精華亮點'] = this.buildRichTextProp(f.highlight);
    props['排版工作說明'] = this.buildRichTextProp(f.formatted_detail);
    props['備註'] = this.buildRichTextProp(f.notes);
    
    props['行業別'] = this.buildMultiSelectProp(f.industry);
    props['職務類別'] = this.buildMultiSelectProp(f.category);
    props['全/兼職'] = this.buildMultiSelectProp(f.job_type);
    props['外籍生'] = this.buildMultiSelectProp(f.foreign_student);
    props['職缺週期'] = this.buildMultiSelectProp(f.job_cycle);
    props['縣市'] = this.buildMultiSelectProp(f.city);
    props['行政區'] = this.buildMultiSelectProp(f.district);
    props['負責所別'] = this.buildMultiSelectProp(f.branch);
    props['班別'] = this.buildMultiSelectProp(f.shift);
    props['休假方式'] = this.buildMultiSelectProp(f.leave_type);
    props['領薪方式'] = this.buildMultiSelectProp(f.pay_method);
    
    if (f.image_url) {
      props['職缺地區&缺額'] = {
        files: [
          {
            name: '職缺宣傳圖檔',
            type: 'external',
            external: { url: f.image_url }
          }
        ]
      };
    }
    
    if (jobStatus) {
      props['狀態'] = { status: { name: String(jobStatus) } };
    }
    
    if (reviewStatus) {
      props['審核狀態'] = { select: { name: String(reviewStatus) } };
    }
    
    return props;
  },
  
  getHeaders: function() {
    return {
      'Authorization': 'Bearer ' + CONFIG.NOTION_API_KEY,
      'Notion-Version': CONFIG.NOTION_VERSION,
      'Content-Type': 'application/json'
    };
  },
  
  getPropText: function(prop) {
    if (!prop) return '';
    if (prop.title && prop.title.length > 0) return prop.title[0].plain_text || '';
    if (prop.rich_text && prop.rich_text.length > 0) return prop.rich_text.map(t => t.plain_text || '').join('');
    if (prop.status) return prop.status.name || '';
    if (prop.select) return prop.select.name || '';
    if (prop.multi_select && Array.isArray(prop.multi_select)) {
      return prop.multi_select.map(item => item.name).join('、');
    }
    if (prop.date) return prop.date.start || '';
    return '';
  },
  
  getPropFileUrl: function(prop) {
    if (!prop || !prop.files || prop.files.length === 0) return '';
    const fileObj = prop.files[0];
    if (fileObj.file) return fileObj.file.url || '';
    if (fileObj.external) return fileObj.external.url || '';
    return '';
  }
};

// ==============================================================================
// 4. Google Drive 圖檔上傳服務 (DriveService)
// ==============================================================================
const DriveService = {
  uploadBase64Image: function(base64Data, filename) {
    try {
      const folder = DriveApp.getFolderById(CONFIG.GOOGLE_DRIVE_FOLDER_ID);
      let contentType = 'image/jpeg';
      let bytesBase64 = base64Data;
      
      if (base64Data.indexOf(';base64,') > -1) {
        const parts = base64Data.split(';base64,');
        contentType = parts[0].replace('data:', '');
        bytesBase64 = parts[1];
      }
      
      const decodedBytes = Utilities.base64Decode(bytesBase64);
      const blob = Utilities.newBlob(decodedBytes, contentType, filename);
      const file = folder.createFile(blob);
      
      file.setSharing(DriveApp.Access.ANYONE_WITH_LINK, DriveApp.Permission.VIEW);
      const fileId = file.getId();
      return `https://lh3.googleusercontent.com/d/${fileId}`;
    } catch (err) {
      console.error('Drive 圖片上傳失敗:', err);
      return '';
    }
  }
};

// ==============================================================================
// 5. 職缺專屬 LINE Flex Message 建構器
// ==============================================================================
const JobFlexMessageBuilder = {
  createDiffRow: function(label, key, val, diffs, defaultColor = '#334155', defaultWeight = 'regular') {
    const isChanged = diffs && (Array.isArray(key) ? key.some(k => diffs[k]) : diffs[key]);
    const color = isChanged ? '#e11d48' : defaultColor;
    const weight = isChanged ? 'bold' : defaultWeight;
    const textVal = isChanged ? `${val} ✏️(已修改)` : val;
    return SharedFlexBuilder.createRow(label, textVal, color, weight);
  },

  buildJobApprovalCard: function(data) {
    const f = data.fields || {};
    const applicant = data.applicant || {};
    const diffs = data.diffs || {};
    const modeText = data.mode === 'create' ? '【全新職缺】' : '【既有維護】';
    const targetStatus = data.targetStatus || '一週內有更新';
    const titleStr = String(f.title || f.internal_title || '招募職缺').trim();
    
    const postbackApprove = `action=review_job&status=approve&page_id=${data.pageId || ''}&applicant_id=${applicant.userId || ''}&mode=${data.mode || 'create'}&update_action=${data.updateAction || 'create'}`;
    const postbackReject = `action=review_job&status=reject&page_id=${data.pageId || ''}&applicant_id=${applicant.userId || ''}&mode=${data.mode || 'create'}`;
    
    const formatMulti = (v) => Array.isArray(v) ? (v.length > 0 ? v.join('、') : '-') : (v || '-');

    const bodyContents = [
      {
        type: 'text',
        text: '招募職缺審核申請',
        weight: 'bold',
        size: 'sm',
        color: '#0284c7'
      },
      {
        type: 'text',
        text: `${modeText} ${titleStr}`,
        weight: 'bold',
        size: 'lg',
        margin: 'xs',
        wrap: true
      },
      ...(data.aiFallbackUsed ? [{
        type: 'box',
        layout: 'vertical',
        backgroundColor: '#fff7ed',
        paddingAll: 'sm',
        cornerRadius: 'md',
        margin: 'sm',
        contents: [
          {
            type: 'text',
            text: '⚠️ AI 文案生成失敗，以下精華亮點/工作內容為系統保底文案，建議核准前人工複查內容',
            size: 'xxs',
            color: '#c2410c',
            weight: 'bold',
            wrap: true
          }
        ]
      }] : []),
      {
        type: 'box',
        layout: 'vertical',
        backgroundColor: '#f8fafc',
        paddingAll: 'sm',
        cornerRadius: 'md',
        margin: 'sm',
        contents: [
          {
            type: 'text',
            text: '審核狀態：待核准',
            size: 'xs',
            color: '#64748b'
          },
          {
            type: 'text',
            text: `預計生效狀態：${targetStatus}`,
            size: 'sm',
            color: targetStatus === '停招' ? '#e11d48' : '#0284c7',
            weight: 'bold',
            margin: 'xs'
          }
        ]
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
          this.createDiffRow('系統廠商', 'vendor', f.vendor || '-', diffs),
          this.createDiffRow('地區/負責所', ['district', 'branch'], `${formatMulti(f.district)} (${formatMulti(f.branch)})`, diffs),
          this.createDiffRow('薪資待遇', 'salary', f.salary || '-', diffs, '#0284c7', 'bold'),
          this.createDiffRow('面試方式', 'interview_method', f.interview_method || '-', diffs),
          this.createDiffRow('班別/休假', ['shift', 'leave_type'], `${formatMulti(f.shift)} / ${formatMulti(f.leave_type)}`, diffs),
          this.createDiffRow('屬性/外籍生', ['job_type', 'foreign_student'], `${formatMulti(f.job_type)} / 外籍生:${formatMulti(f.foreign_student)}`, diffs),
          this.createDiffRow('領薪方式', 'pay_method', formatMulti(f.pay_method), diffs),
          SharedFlexBuilder.createRow('職缺刊登人', f.publisher || applicant.displayName || '-', '#0284c7', 'bold'),
          SharedFlexBuilder.createRow('申請送審人', applicant.displayName || '-', '#334155', 'regular')
        ]
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
      altText: `[審核通知] 招募職缺: ${titleStr} (修改為:${targetStatus})`,
      contents: {
        type: 'bubble',
        header: {
          type: 'box',
          layout: 'vertical',
          backgroundColor: '#f0f9ff',
          contents: [
            {
              type: 'text',
              text: '材霈招募管理系統 - 主管簽核',
              size: 'xs',
              color: '#0369a1',
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
              color: '#0284c7',
              action: {
                type: 'postback',
                label: '一鍵核准',
                data: postbackApprove,
                displayText: `核准職缺：${titleStr} (狀態:${targetStatus})`
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
                displayText: `退回職缺：${titleStr}`
              }
            }
          ]
        }
      }
    };
  },

  buildJobApprovedDetailCard: function(data) {
    const f = data.jobDetail || {};
    const formatMulti = (v) => Array.isArray(v) ? (v.length > 0 ? v.join('、') : '-') : (v || '-');
    const mainTitleStr = String(f.title || '招募職缺').trim();

    const bodyContents = [
      {
        type: 'text',
        text: '🎉 職缺審核通過通知',
        weight: 'bold',
        size: 'sm',
        color: '#059669'
      },
      {
        type: 'text',
        text: mainTitleStr,
        weight: 'bold',
        size: 'lg',
        margin: 'xs',
        wrap: true
      },
      {
        type: 'text',
        text: `✅ 職缺狀態：【${data.jobStatus || '生效'}】 ｜ 核准日：${data.approveDate || '-'}`,
        size: 'xs',
        color: '#0284c7',
        weight: 'bold',
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
        contents: [
          SharedFlexBuilder.createRow('系統廠商', f.vendor || '-'),
          SharedFlexBuilder.createRow('刊登同仁', f.publisher || '-', '#0284c7', 'bold'),
          SharedFlexBuilder.createRow('職缺名稱(對內)', f.internal_title || '-'),
          SharedFlexBuilder.createRow('對外刊登名稱', f.external_title || '-', '#0f172a'),
          SharedFlexBuilder.createRow('行業/職務', `${formatMulti(f.industry)} / ${formatMulti(f.category)}`),
          SharedFlexBuilder.createRow('地區/負責所', `${formatMulti(f.district)} (${formatMulti(f.branch)})`),
          SharedFlexBuilder.createRow('薪資待遇', f.salary || '-', '#059669', 'bold'),
          SharedFlexBuilder.createRow('班別/休假', `${formatMulti(f.shift)} / ${formatMulti(f.leave_type)}`),
          SharedFlexBuilder.createRow('屬性/週期', `${formatMulti(f.job_type)} / ${formatMulti(f.job_cycle)}`),
          SharedFlexBuilder.createRow('外籍生/領薪', `${formatMulti(f.foreign_student)} / ${formatMulti(f.pay_method)}`),
          SharedFlexBuilder.createRow('面試方式', f.interview_method || '-'),
          SharedFlexBuilder.createRow('備註說明', f.notes || '-')
        ]
      },
      {
        type: 'separator',
        margin: 'md'
      },
      {
        type: 'box',
        layout: 'vertical',
        margin: 'md',
        spacing: 'xs',
        contents: [
          { type: 'text', text: '📝 工作內容(對內)：', size: 'xxs', color: '#64748b', weight: 'bold' },
          { type: 'text', text: f.internal_desc || '-', size: 'xs', color: '#334155', wrap: true },
          { type: 'text', text: '📢 工作內容(對外)：', size: 'xxs', color: '#64748b', weight: 'bold', margin: 'sm' },
          { type: 'text', text: f.external_desc || '-', size: 'xs', color: '#334155', wrap: true }
        ]
      }
    ];

    if (f.image_url && typeof f.image_url === 'string' && f.image_url.startsWith('https://')) {
      bodyContents.push({
        type: 'image',
        url: f.image_url,
        size: 'full',
        aspectRatio: '16:9',
        aspectMode: 'cover',
        margin: 'md'
      });
    }

    return {
      type: 'flex',
      altText: `🎉 [職缺核准] ${mainTitleStr} (狀態:${data.jobStatus || ''})`,
      contents: {
        type: 'bubble',
        header: {
          type: 'box',
          layout: 'vertical',
          backgroundColor: '#ecfdf5',
          contents: [
            {
              type: 'text',
              text: '材霈招募管理系統 - 職缺核准發布',
              size: 'xs',
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
          layout: 'vertical',
          contents: [
            {
              type: 'text',
              text: '💡 下方純文字訊息可長按一鍵「複製/轉發」至求職群組。',
              size: 'xxs',
              color: '#0284c7',
              weight: 'bold',
              wrap: true,
              align: 'center'
            }
          ]
        }
      }
    };
  },

  buildJobApplicantReceiptCard: function(data) {
    const f = data.fields || {};
    const diffs = data.diffs || {};
    const modeText = data.mode === 'create' ? '【全新職缺】' : '【既有維護】';
    const targetStatus = data.targetStatus || '一週內有更新';
    const titleStr = String(f.title || f.internal_title || '招募職缺').trim();
    const formatMulti = (v) => Array.isArray(v) ? (v.length > 0 ? v.join('、') : '-') : (v || '-');

    const bodyContents = [
      {
        type: 'text',
        text: '📄 職缺送審成功通知',
        weight: 'bold',
        size: 'sm',
        color: '#059669'
      },
      {
        type: 'text',
        text: `${modeText} ${titleStr}`,
        weight: 'bold',
        size: 'lg',
        margin: 'xs',
        wrap: true
      },
      {
        type: 'text',
        text: `⏳ 審核狀態：待主管審核 (${data.supervisorName || '直屬主管'})`,
        size: 'xs',
        color: '#d97706',
        weight: 'bold',
        margin: 'xs'
      },
      {
        type: 'text',
        text: `📌 預計核准後狀態：【${targetStatus}】`,
        size: 'xs',
        color: '#0284c7',
        weight: 'bold',
        margin: 'xs'
      },
      ...(data.aiFallbackUsed ? [{
        type: 'box',
        layout: 'vertical',
        backgroundColor: '#fff7ed',
        paddingAll: 'sm',
        cornerRadius: 'md',
        margin: 'sm',
        contents: [
          {
            type: 'text',
            text: '⚠️ AI 文案生成失敗，以下精華亮點/工作內容為系統保底文案，主管審核時會一併收到提醒',
            size: 'xxs',
            color: '#c2410c',
            weight: 'bold',
            wrap: true
          }
        ]
      }] : []),
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
          this.createDiffRow('系統廠商', 'vendor', f.vendor || '-', diffs),
          SharedFlexBuilder.createRow('職缺刊登人', f.publisher || f.applicant_name || '-', '#0284c7', 'bold'),
          this.createDiffRow('地區/負責所', ['district', 'branch'], `${formatMulti(f.district)} (${formatMulti(f.branch)})`, diffs),
          this.createDiffRow('薪資待遇', 'salary', f.salary || '-', diffs, '#059669', 'bold'),
          this.createDiffRow('面試方式', 'interview_method', f.interview_method || '-', diffs),
          this.createDiffRow('班別/休假', ['shift', 'leave_type'], `${formatMulti(f.shift)} / ${formatMulti(f.leave_type)}`, diffs),
          this.createDiffRow('屬性/外籍生', ['job_type', 'foreign_student'], `${formatMulti(f.job_type)} / 外籍生:${formatMulti(f.foreign_student)}`, diffs),
          this.createDiffRow('領薪方式', 'pay_method', formatMulti(f.pay_method), diffs),
          this.createDiffRow('對外標題', 'external_title', f.external_title || '-', diffs)
        ]
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
      altText: `[送審確認] 招募職缺: ${titleStr}`,
      contents: {
        type: 'bubble',
        header: {
          type: 'box',
          layout: 'vertical',
          backgroundColor: '#ecfdf5',
          contents: [
            {
              type: 'text',
              text: '材霈招募管理系統 - 送審確認',
              size: 'xs',
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
          layout: 'vertical',
          contents: [
            {
              type: 'text',
              text: '💡 系統已同步通知主管審核，核准時將第一時間推播通知您。',
              size: 'xs',
              color: '#64748b',
              wrap: true,
              align: 'center'
            }
          ]
        }
      }
    };
  }
};

// ==============================================================================
// 6. 職缺純文字文案產生函式 (支援異動標記)
// ==============================================================================
function buildJobPlainTextContent(job, headerTitle, bottomNote, targetStatusText, approveDateStr, diffs = {}) {
  const formatMulti = (v) => Array.isArray(v) ? (v.length > 0 ? v.join('、') : '-') : (v || '-');
  
  const mark = (key, text) => {
    const isChanged = Array.isArray(key) ? key.some(k => diffs && diffs[k]) : (diffs && diffs[key]);
    if (isChanged) {
      return `${text} 🔴[已異動]`;
    }
    return text;
  };

  let statusTopLine = '';
  if (targetStatusText) {
    statusTopLine = `【職缺狀態】${targetStatusText}` + (approveDateStr ? ` (核准日: ${approveDateStr})` : ' (待核准生效)') + `\n`;
  }

  return `📋【${headerTitle}】\n` +
    `━━━━━━━━━━━━━━━━\n` +
    statusTopLine +
    `【刊登同仁】${job.publisher || job.applicant_name || '-'}\n` +
    `【廠商名稱】${mark('title', job.title || '-')}\n` +
    `【職缺名稱(對外)】${mark('external_title', job.external_title || job.title || '-')}\n` +
    `【職缺名稱(對內)】${mark('internal_title', job.internal_title || job.title || '-')}\n` +
    `【系統廠商】${mark('vendor', job.vendor || '-')}\n` +
    `【薪資待遇】${mark('salary', job.salary || '-')}\n` +
    `【工作地點】${mark(['district', 'branch'], `${formatMulti(job.district)} (${formatMulti(job.branch)})`)}\n` +
    `【班別時間】${mark('shift', formatMulti(job.shift))}\n` +
    `【休假方式】${mark('leave_type', formatMulti(job.leave_type))}\n` +
    `【工作屬性】${mark(['job_type', 'job_cycle'], `${formatMulti(job.job_type)} / 週期:${formatMulti(job.job_cycle)}`)}\n` +
    `【外籍生資格】${mark('foreign_student', formatMulti(job.foreign_student))}\n` +
    `【領薪方式】${mark('pay_method', formatMulti(job.pay_method))}\n` +
    `【面試方式】${mark('interview_method', job.interview_method || '-')}\n` +
    `━━━━━━━━━━━━━━━━\n` +
    `【工作內容(對外)】\n${mark('external_desc', job.external_desc || '-')}\n\n` +
    `【工作內容(對內備忘)】\n${mark('internal_desc', job.internal_desc || '-')}\n\n` +
    `【備註說明】\n${mark('notes', job.notes || '-')}\n` +
    `━━━━━━━━━━━━━━━━\n` +
    (bottomNote ? `${bottomNote}\n` : '');
}

// ==============================================================================
// 7. 定時排程 Trigger 入口 (每日自動執行一次)
// ==============================================================================
CheckJobStatuses = function dailyJobStatusCheckTrigger() {
  console.log('⏰ 開始執行每日職缺狀態檢查排程...');
  NotionService.updateJobsToRecruitingAfterOneWeek();
  console.log('✅ 每日職缺狀態檢查完成。');
};

// ==============================================================================
// 8. 獨立測試工具
// ==============================================================================
function testGeminiAiService() {
  console.log('🚀 ========== 開始測試 Gemini 4合1 AI 服務 ==========');
  const testData = {
    title: "製程技術員",
    external_title: "欣興電子山鶯廠 高薪夜班技術員",
    external_desc: "18:00-23:00、18:00-22:00 兩時段可選\n貨物從輸送帶拉下來跌棧板、打板捆收縮膜及貨車上人工疊貨\n需搬重：重量約5-25公斤不等（體力活）\n著便服(無制服)、戶外路邊停車區位置多、提供置物櫃\n地點：新北市五股區中興路三段1之12號\n時薪$220、週休二日(見紅休)",
    salary: "時薪$220",
    city: ["新北市"],
    district: ["五股區"],
    shift: ["晚班"]
  };

  const result = AiJobDescriptionService.generateAllJobArtifacts(testData);

  console.log('================== 測試結果 ==================');
  console.log('【美化後 對外標題】:\n', result.external_title);
  console.log('--------------------------------------------');
  console.log('【美化後 對外工作內容】:\n', result.external_desc);
  console.log('--------------------------------------------');
  console.log('【精華亮點】:\n', result.highlight);
  console.log('--------------------------------------------');
  console.log('【排版工作說明】:\n', result.formatted_detail);
  console.log('============================================');
}

/**
 * 診斷工具：列出目前這組 GEMINI_API_KEY 實際可用的模型清單，
 * 只挑出支援 generateContent（也就是我們用得到）的模型名稱。
 * 用途：targetModels 裡的模型名稱被 Google 汰換掉時，用這個確認新的正確代號。
 */
function listAvailableGeminiModels() {
  const apiKey = AiJobDescriptionService.getApiKey();
  if (!apiKey) {
    console.error('❌ 未設定 GEMINI_API_KEY，無法查詢');
    return;
  }

  const url = 'https://generativelanguage.googleapis.com/v1beta/models?key=' + encodeURIComponent(apiKey);
  const response = UrlFetchApp.fetch(url, { method: 'get', muteHttpExceptions: true });
  const resCode = response.getResponseCode();

  if (resCode !== 200) {
    console.error('❌ 查詢模型清單失敗 (HTTP ' + resCode + '): ' + response.getContentText());
    return;
  }

  const json = JSON.parse(response.getContentText());
  const models = json.models || [];

  console.log('🔍 ========== 可用模型清單 (共 ' + models.length + ' 個) ==========');

  const supportGenerateContent = models.filter(m =>
    Array.isArray(m.supportedGenerationMethods) && m.supportedGenerationMethods.indexOf('generateContent') !== -1
  );

  console.log('✅ 支援 generateContent 的模型（可用於本系統，共 ' + supportGenerateContent.length + ' 個）：');
  supportGenerateContent.forEach(m => {
    console.log('   - ' + m.name.replace('models/', '') + '（顯示名稱：' + (m.displayName || '') + '）');
  });

  console.log('--------------------------------------------');
  console.log('（以下為完整清單，含不支援 generateContent 的模型，僅供參考）');
  models.forEach(m => {
    console.log('   - ' + m.name.replace('models/', '') + ' | 支援方法: ' + (m.supportedGenerationMethods || []).join('、'));
  });

  console.log('============================================');
}
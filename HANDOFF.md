# 招募機器人（沛沛）專案交接筆記

給 Claude Code 接續使用。這份文件整理目前為止跟 Claude（web/mobile 對話介面）討論並已完成的工作、正在進行中的項目，以及還沒開始的部分。

## 專案基本資訊

- 專案性質：材霈有限公司的 LINE 招募聊天機器人「沛沛」
- 技術棧：FastAPI + line-bot-sdk + Notion API（職缺/FAQ 資料庫）+ Vertex AI Gemini（決策與回覆生成）+ Firestore（session/槽位儲存）
- GCP 專案 ID：`tsaipei-505807`
- 部署方式：接 GitHub，push 後由 Cloud Build 自動建置、部署到 Cloud Run（服務名稱 `recruitment-bot`，地區 `asia-east1`）
- Cloud Run 服務有 `/callback`（正式環境）與 `/test-callback`（測試環境）兩條 webhook 路由
- 檔案結構：`main.py`、`config.py`、`handlers/message_handler.py`、`services/session_service.py`、`services/matcher_service.py`、`services/notion_service.py`、`services/flex_service.py`、`services/ai_service.py`

## 已完成並部署驗證過的項目

### 第一組：架構層級（全部完成）

1. **Session/槽位狀態外部化**：`session_service.py` 從行程記憶體字典改成讀寫 Firestore（database ID 用預設的 `(default)`，Standard edition）。函式簽名維持不變（`get_user_history`、`get_user_slots`、`update_user_slots`、`clear_user_slots`、`append_user_history`），呼叫端不用改。
2. **修正同步阻塞問題**：`main.py` 把 `webhook_handler.handle(...)` 用 `starlette.concurrency.run_in_threadpool` 包起來，避免同步的 Notion/Firestore/Gemini 呼叫卡住 FastAPI event loop。順便把 `/callback`、`/test-callback` 重複邏輯抽成共用的 `_handle_webhook()`。
3. **統一 GCP 設定來源**：`ai_service.py` 不再自己 `os.getenv` 定義 `GCP_PROJECT_ID`/`GCP_LOCATION`，改成 `from config import GCP_PROJECT_ID, GCP_LOCATION`。
4. **整理 `notion_service.py` 檔案結構**：拿掉 `fetch_faqs_data()` 裡的死碼（`return` 之後執行不到的 `import requests`）與 `append_unresolved_faq_to_notion()` 內重複的 import。

### 額外處理（第一組期間發現、非原訂項目）

5. **`ai_service.py` 加上 429 重試機制**：新增 `_generate_with_retry()`，只針對 429 RESOURCE_EXHAUSTED（Vertex AI Dynamic Shared Quota 暫時滿載）重試（最多 2 次、遞增等待時間），其他錯誤直接拋出換下一個 fallback 模型。`MODEL_FALLBACK_LIST` 統一成兩處共用。
6. **修正 Vertex AI 地區與模型名稱問題（關鍵 bug）**：
   - 發現 `gemini-3.5-flash` 在 Vertex AI 上**根本不存在**（不是地區問題，是模型名稱本身無效），已從 `MODEL_FALLBACK_LIST` 移除，目前只剩 `gemini-2.5-flash`、`gemini-2.5-flash-lite`。
   - `config.py` 的 `GCP_LOCATION` 預設值從 `asia-east1` 改成 `global`（Google 官方建議的全域端點，可用性更高、也能降低 429 機率）。Cloud Run 環境變數本身沒有手動設定 `GCP_LOCATION`，所以改程式碼預設值即可生效，不用動 Cloud Run 設定。
   - **注意**：Cloud Run 服務本身跑在 `asia-east1`（這個不用改，是容器實際運行地區，跟 Vertex AI 呼叫地區是兩回事，不要搞混）。

### 第二組：對話邏輯核心（進行中）

7. **槽位三態機制**（`session_service.py`）：新增 `CLEAR_SLOT = "__CLEAR__"` 常數。`update_user_slots` 改成三態：空字串/不傳＝維持原值、`CLEAR_SLOT`＝明確清空、其他值＝設定。
8. **否定詞感知的地點/類別抽取**（`matcher_service.py`）：
   - 新增 `_keyword_is_negated(text, keyword)`：檢查關鍵字前 6 個字內有沒有否定詞（`不要`、`不想要`、`不想`、`除了`、`排除`、`不考慮`、`非`）。
   - `extract_current_target_location` 改用 `LOCATION_CANDIDATES` 清單，跳過被否定的地名；新增 `detect_negated_location`。
   - `detect_category_label` 改用 `CATEGORY_KEYWORDS` 字典（原本是 if-elif 串），跳過被否定的類別；新增 `detect_negated_category`。
   - `message_handler.py` 步驟 0-3 接上：地點/類別如果被偵測為「明確排除且排除的剛好是目前鎖定的舊值」，傳 `CLEAR_SLOT` 真正清空槽位（而不是像原本那樣只在當輪暫時忽略、下一輪又跑回來——這其實修掉了原本「不限地區」也有的潛在 bug）。
9. **修正 `detect_brand_label` 的多個誤判問題**（這是這幾輪測試花最多時間抓出來的部分）：
   - 正則表達式備援抽取（「有 XX 的工作嗎」句型）容易誤抓非廠商詞（例如「外送」「其他的」）當廠商名稱。修法：抓到的詞**必須真的比對到 `active_jobs` 裡某筆職缺的 `_vendor_name_clean`** 才採信，否則不設定 brand。同時新增跟 `CATEGORY_KEYWORDS` 的完全比對（避免「外送」被當廠商），刻意用完全比對而非子字串比對（避免「蝦皮」因為是「蝦皮門市」的子字串而被誤傷）。
   - 新增 `_vendor_core_name(vendor_name)`：處理同仁會在正式廠商名稱後面加內部識別後綴的情況（例如「錢都(代招)」→「錢都」、「美光(桃園)」→「美光」、「石二鍋+12mini(代招)」→「石二鍋」），切分符號涵蓋 `（`、`(`、`_`、`-`、`+`。
   - **關鍵修正**：`detect_brand_label` 步驟 1 原本核心名稱比對命中後，回傳的是**那一筆職缺的完整廠商名稱**（例如「美光(桃園)」），而不是核心名稱「美光」。這導致 Notion 裡同一品牌但不同地區各自登記一筆的情況（美光(桃園)/美光(台中)/美光(台南)），brand 槽位會被鎖在某一個特定地區的寫法，後續 `build_ai_job_candidates`／`_score_job_for_ai` 拿這個帶括號的字串去比對已清理過的 `_search_text`，格式對不上導致品牌篩選/加分形同虛設。已修正成統一回傳核心名稱。
   - `brand` 槽位行為調整成**每輪重新判斷、不沿用舊值**（跟地點/類別不同——地點/類別是持續性偏好會沿用，但 brand 比較像單次詢問，這輪沒提到就該自動清空，避免候選集合被舊品牌一直鎖住）。這是使用者明確決定的產品邏輯，已跟人資確認過。

## 目前正在驗證中、還沒有結論的部分

- **「有美光的工作嗎？」在新莊沒有美光職缺時的回覆品質**：候選集合已經修好（品牌保底機制現在能正確抓到桃園/台中/台南三筆美光職缺送給 AI），但還沒確認 AI 實際判斷出來的 `ACTION` 和措辭是否正確（是否會用 `ACTION:RECOMMEND` 並在回覆裡誠實說明「美光在其他地區有職缺」，還是依然誤判成 `ACTION:NO_MATCH`）。**下一步待辦**：如果 AI 這次自己處理得當就不用再動；如果還是判斷錯誤，需要調整 `ai_prompt` 的條件退讓範例（目前只教過「同地區、制度不同」的退讓話術，格式 C 那段，沒教過「品牌符合但地區不符」也該退讓推薦）。

## 第二組還沒開始的項目

- **核心問題 3：候選集合建構從硬篩改成加權排序**——`build_ai_job_candidates` 目前地點/品牌仍是先做 hard filter 縮小 `target_pool`，理論上應該改成只做加減分、放寬候選池（例如取分數前 60-80 筆），讓 AI 在更完整的資訊下判斷退讓推薦。這次修的品牌保底 bug 只是讓「找不到才 fallback 到全品牌」這條路徑本身能正常運作，並沒有把整體篩選機制從硬篩改成加權——如果之後還有類似「候選集合太窄」的症狀，這項要優先做。
- **拆分「全域重置」與「單一維度調整」**（`message_handler.py` 的 `reset_keywords` 攔截）：目前命中就整組槽位清空，該分開成「真的想全部重來」跟「只想換一個條件」兩種情境，後者只更新對應槽位。
- **收緊禮貌收尾判斷**：目前「謝謝，不過還想問⋯」這類帶轉折詞但沒問號的句子會被誤判成單純道謝、整句被忽略。
- **統一意圖分類來源**：reset / 禮貌收尾 / show_all / 直接命中（外送/門市/momo）目前各自維護一份關鍵字白名單，覆蓋範圍不一致（例如 `has_specific_intent` 白名單缺「理貨」「餐飲」）。

## 第三組（FAQ/職缺分工調整）跟第四組（程式碼品質）都還沒開始

詳見對話歷程中「彙整這幾輪討論」那則列出的完整清單，這裡不重複貼。重點：
- FAQ 高信心比對時應直接回傳 Notion 原文，不經 AI 改寫（合規風險 + 省呼叫）
- 未收錄問題寫入 FAQ 前先去重
- 地區/班別/廠商關鍵字清單集中化、`main.py` webhook 共用 helper（已完成）、`DEFAULT_RESUME_URLS` 搬到環境變數、補單元測試

## 其他待確認/待辦的小事項

- **Python 版本**：`Dockerfile.txt` 用 `python:3.10-slim`，Python 3.10 的 `google-api-core` 支援將於 2026-10-04 到期，建議找時間升級到 3.11+。
- Vertex AI 回應速度：測試中觀察過一次單輪決策耗時約 11.7 秒，量大時要留意 LINE reply token 30 秒逾時風險，目前先觀察、還沒需要處理。
- 帳單帳戶目前仍是「免費試用帳戶」，若要申請配額調高（一般 Vertex AI Gemini 新模型走 Dynamic Shared Quota，通常不需要），必須先升級成正式付費帳戶才能申請。
- 月流量預估：正式頻道約 4 萬則/月，群發尖峰可能到每分鐘幾百則。

## 目前所有檔案的最新版本

以下檔案已經在對話中修改並下載確認過，都是目前的最新版本（相對於最一開始上傳的原始檔案）：
`main.py`、`config.py`、`session_service.py`、`ai_service.py`、`matcher_service.py`、`message_handler.py`、`notion_service.py`

尚未修改過的原始檔案：`flex_service.py`、`requirements.txt`（已加 `google-cloud-firestore`）、`Dockerfile.txt`

建議接手時先用 `git diff` 或直接讀取 repo 目前狀態確認這些檔案是否都已經是最新版本再繼續往下做。

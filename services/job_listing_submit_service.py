"""職缺維護（/job-listings）：材霈平台這邊只負責收表單、查詢「我能維護的
職缺清單」，送出時把資料原封不動轉手給職缺維護表單背後那支 GAS 程式的
Web App（type=SUBMIT_JOB／GET_JOBS），後續所有邏輯——AI 潤飾對外文案
（就業服務法禁語過濾、精華亮點/排版說明生成）、刊登人權限檢查（原刊登人
本人或其直屬主管才能改）、寫入 Notion、LINE 推播主管審核——完全由那支
GAS 程式繼續處理，材霈平台這邊不重做。

跟薪資補款送出表單是同一套「方案 A」精神（見 HANDOFF.md），差別是職缺
維護還多了「維護既有職缺」這個模式：同仁可以搜尋自己刊登過、或轄下同仁
刊登過的職缺，選一筆出來編輯後重新送審。

跟 GAS 那支程式的請求/回應格式，都是照 Project_Job.gs 的
JobWorkflowService.processJobSubmission()／
NotionService.getAllJobsForSelect() 現有的樣子照抄，沒有另外新增或修改
對方看得懂的欄位——包含下面這些下拉選單的選項清單，都是直接照抄現有
Netlify 表單原始碼（index_6.html 的 NOTION_OPTIONS/TAIWAN_DATA 常數），
不是我方自己另外調整過的清單。
"""
import requests

from config import JOB_PORTAL_GAS_WEBAPP_URL as GAS_WEBAPP_URL

_REQUEST_TIMEOUT_SECONDS = 30

INDUSTRY_OPTIONS = ["製造業", "服務業", "餐飲業", "服飾業", "科技業", "物流業"]

CATEGORY_OPTIONS = [
    "倉儲人員", "作業員", "文字客服", "行政人員", "品保人員", "檢驗人員", "理貨人員",
    "門市人員", "搬運工", "內場人員", "外場人員", "業務助理", "業務人員", "外送員",
    "服務人員", "設備人員",
]

JOB_TYPE_OPTIONS = ["全職", "兼職", "承攬制"]

FOREIGN_STUDENT_OPTIONS = [
    "否", "可", "可 (中文程度B1以上)", "可（需聽說讀寫ok)", "可(部分店家)",
    "可（中文要很好、要有餐飲經驗)", "否(不接受口音)", "可(只要僑生)", "可(中文精通 聽說讀寫)",
    "可(須看懂中文)", "依親居留外籍可", "可(中文要好)", "暑期可", "可(需基本中文對話溝通)",
]

JOB_CYCLE_OPTIONS = ["長期", "短期", "臨時工", "暑期", "短期(三個月-一年)", "短期(6個月)", "寒假春節"]

BRANCH_OPTIONS = ["台北所(派遣組)", "新北所(派遣組)", "桃園所", "台中所", "高雄所", "新北所(配送組)"]

SHIFT_OPTIONS = [
    "假日班", "早班", "中班", "晚班", "夜班", "三班輪", "常日班", "全日班", "早晚輪班",
    "輪班", "四班二輪", "打烊班", "日班", "大夜班", "四班三輪",
]

LEAVE_TYPE_OPTIONS = [
    "週休", "排休", "自由報班", "四三輪休", "做二休二", "第一個月週休、後面都排休",
    "休日一", "做三休三", "休六日一", "做四休二", "輪休",
]

PAY_METHOD_OPTIONS = ["月領", "週領", "日領", "匯款", "現金", "街口", "預支"]

# 縣市/行政區對照表，選了縣市之後行政區選單才會出現對應的選項——照抄現有
# 表單原始碼的 TAIWAN_DATA 常數，22 個縣市一個不少。
TAIWAN_CITY_DISTRICTS = {
    "台北市": ["台北市中正區", "台北市大同區", "台北市中山區", "台北市松山區", "台北市大安區", "台北市萬華區", "台北市信義區", "台北市士林區", "台北市北投區", "台北市內湖區", "台北市南港區", "台北市文山區"],
    "新北市": ["新北市板橋區", "新北市新莊區", "新北市泰山區", "新北市三重區", "新北市中和區", "新北市永和區", "新北市土城區", "新北市樹林區", "新北市三峽區", "新北市鶯歌區", "新北市五股區", "新北市蘆洲區", "新北市八里區", "新北市淡水區", "新北市林口區", "新北市汐止區", "新北市深坑區", "新北市石碇區", "新北市瑞芳區", "新北市平溪區", "新北市雙溪區", "新北市貢寮區", "新北市新店區", "新北市坪林區", "新北市烏來區", "新北市三芝區", "新北市石門區", "新北市金山區", "新北市萬里區"],
    "桃園市": ["桃園市桃園區", "桃園市中壢區", "桃園市平鎮區", "桃園市八德區", "桃園市楊梅區", "桃園市蘆竹區", "桃園市大溪區", "桃園市龍潭區", "桃園市龜山區", "桃園市大園區", "桃園市觀音區", "桃園市新屋區", "桃園市復興區"],
    "台中市": ["台中市中區", "台中市東區", "台中市南區", "台中市西區", "台中市北區", "台中市北屯區", "台中市西屯區", "台中市南屯區", "台中市太平區", "台中市大里區", "台中市霧峰區", "台中市烏日區", "台中市豐原區", "台中市后里區", "台中市石岡區", "台中市東勢區", "台中市和平區", "台中市新社區", "台中市潭子區", "台中市大雅區", "台中市神岡區", "台中市大肚區", "台中市沙鹿區", "台中市龍井區", "台中市梧棲區", "台中市清水區", "台中市大甲區", "台中市外埔區", "台中市大安區"],
    "台南市": ["台南市中西區", "台南市東區", "台南市南區", "台南市北區", "台南市安平區", "台南市安南區", "台南市永康區", "台南市歸仁區", "台南市新化區", "台南市左鎮區", "台南市解井區", "台南市楠西區", "台南市南化區", "台南市仁德區", "台南市關廟區", "台南市龍崎區", "台南市官田區", "台南市麻豆區", "台南市佳里區", "台南市西港區", "台南市七股區", "台南市將軍區", "台南市學甲區", "台南市北門區", "台南市新營區", "台南市後壁區", "台南市白河區", "台南市東山區", "台南市六甲區", "台南市下營區", "台南市柳營區", "台南市鹽水區", "台南市善化區", "台南市大內區", "台南市山上區", "台南市新市區", "台南市安定區"],
    "高雄市": ["高雄市新興區", "高雄市前金區", "高雄市苓雅區", "高雄市鹽埕區", "高雄市鼓山區", "高雄市旗津區", "高雄市前鎮區", "高雄市三民區", "高雄市楠梓區", "高雄市小港區", "高雄市左營區", "高雄市仁武區", "高雄市大社區", "高雄市岡山區", "高雄市路竹區", "高雄市阿蓮區", "高雄市田寮區", "高雄市燕巢區", "高雄市橋頭區", "高雄市梓官區", "高雄市彌陀區", "高雄市永安區", "高雄市湖內區", "高雄市鳳山區", "高雄市大寮區", "高雄市林園區", "高雄市鳥松區", "高雄市大樹區", "高雄市旗山區", "高雄市美濃區", "高雄市六龜區", "高雄市內門區", "高雄市杉林區", "高雄市甲仙區", "高雄市桃源區", "高雄市那瑪夏區", "高雄市茂林區", "高雄市茄萣區"],
    "基隆市": ["基隆市仁愛區", "基隆市信義區", "基隆市中正區", "基隆市中山區", "基隆市安樂區", "基隆市暖暖區", "基隆市七堵區"],
    "新竹市": ["新竹市東區", "新竹市北區", "新竹市香山區"],
    "新竹縣": ["新竹縣竹北市", "新竹縣湖口鄉", "新竹縣新豐鄉", "新竹縣新埔鎮", "新竹縣關西鎮", "新竹縣芎林鄉", "新竹縣寶山鄉", "新竹縣竹東鎮", "新竹縣五峰鄉", "新竹縣橫山鄉", "新竹縣尖石鄉", "新竹縣北埔鄉", "新竹縣峨眉鄉"],
    "苗栗縣": ["苗栗縣竹南鎮", "苗栗縣頭份市", "苗栗縣三灣鄉", "苗栗縣南庄鄉", "苗栗縣獅潭鄉", "苗栗縣後龍鎮", "苗栗縣通霄鎮", "苗栗縣苑裡鎮", "苗栗縣苗栗市", "苗栗縣造橋鄉", "苗栗縣頭屋鄉", "苗栗縣公館鄉", "苗栗縣大湖鄉", "苗栗縣泰安鄉", "苗栗縣銅鑼鄉", "苗栗縣三義鄉", "苗栗縣西湖鄉", "苗栗縣卓蘭鎮"],
    "彰化縣": ["彰化縣彰化市", "彰化縣芬園鄉", "彰化縣花壇鄉", "彰化縣秀水鄉", "彰化縣鹿港鎮", "彰化縣福興鄉", "彰化縣線西鄉", "彰化縣和美鎮", "彰化縣伸港鄉", "彰化縣員林市", "彰化縣社頭鄉", "彰化縣永靖鄉", "彰化縣埔心鄉", "彰化縣溪湖鎮", "彰化縣大村鄉", "彰化縣埔鹽鄉", "彰化縣田中鎮", "彰化縣北斗鎮", "彰化縣田尾鄉", "彰化縣埤頭鄉", "彰化縣溪州鄉", "彰化縣竹塘鄉", "彰化縣二林鎮", "彰化縣大城鄉", "彰化縣芳苑鄉", "彰化縣二水鄉"],
    "南投縣": ["南投縣南投市", "南投縣中寮鄉", "南投縣草屯鎮", "南投縣國姓鄉", "南投縣埔里鎮", "南投縣仁愛鄉", "南投縣名間鄉", "南投縣集集鎮", "南投縣水里鄉", "南投縣魚池鄉", "南投縣信義鄉", "南投縣竹山鎮", "南投縣鹿谷鄉"],
    "雲林縣": ["雲林縣斗南鎮", "雲林縣大埤鄉", "雲林縣虎尾鎮", "雲林縣土庫鎮", "雲林縣褒忠鄉", "雲林縣東勢鄉", "雲林縣臺西鄉", "雲林縣崙背鄉", "雲林縣麥寮鄉", "雲林縣斗六市", "雲林縣林內鄉", "雲林縣古坑鄉", "雲林縣莿桐鄉", "雲林縣西螺鎮", "雲林縣二崙鄉", "雲林縣北港鎮", "雲林縣水林鄉", "雲林縣口湖鄉", "雲林縣四湖鄉", "雲林縣元長鄉"],
    "嘉義市": ["嘉義市東區", "嘉義市西區"],
    "嘉義縣": ["嘉義縣太保市", "嘉義縣朴子市", "嘉義縣布袋鎮", "嘉義縣大林鎮", "嘉義縣民雄鄉", "嘉義縣溪口鄉", "嘉義縣新港鄉", "嘉義縣六腳鄉", "嘉義縣東石鄉", "嘉義縣義竹鄉", "嘉義縣鹿草鄉", "嘉義縣水上鄉", "嘉義縣中埔鄉", "嘉義縣竹崎鄉", "嘉義縣梅山鄉", "嘉義縣番路鄉", "嘉義縣大埔鄉", "嘉義縣阿里山鄉"],
    "屏東縣": ["屏東縣屏東市", "屏東縣三地門鄉", "屏東縣霧臺鄉", "屏東縣瑪家鄉", "屏東縣九如鄉", "屏東縣里港鄉", "屏東縣高樹鄉", "屏東縣鹽埔鄉", "屏東縣長治鄉", "屏東縣麟洛鄉", "屏東縣竹田鄉", "屏東縣內埔鄉", "屏東縣萬丹鄉", "屏東縣潮州鎮", "屏東縣泰武鄉", "屏東縣來義鄉", "屏東縣萬巒鄉", "屏東縣崁頂鄉", "屏東縣新埤鄉", "屏東縣南州鄉", "屏東縣林邊鄉", "屏東縣東港鎮", "屏東縣琉球鄉", "屏東縣佳冬鄉", "屏東縣新園鄉", "屏東縣枋寮鄉", "屏東縣枋山鄉", "屏東縣春日鄉", "屏東縣獅子鄉", "屏東縣車城鄉", "屏東縣牡丹鄉", "屏東縣恆春鎮", "屏東縣滿州鄉"],
    "宜蘭縣": ["宜蘭縣宜蘭市", "宜蘭縣頭城鎮", "宜蘭縣礁溪鄉", "宜蘭縣壯圍鄉", "宜蘭縣員山鄉", "宜蘭縣羅東鎮", "宜蘭縣三星鄉", "宜蘭縣大同鄉", "宜蘭縣五結鄉", "宜蘭縣冬山鄉", "宜蘭縣蘇澳鎮", "宜蘭縣南澳鄉"],
    "花蓮縣": ["花蓮縣花蓮市", "花蓮縣新城鄉", "花蓮縣秀林鄉", "花蓮縣吉安鄉", "花蓮縣壽豐鄉", "花蓮縣鳳林鎮", "花蓮縣光復鄉", "花蓮縣豐濱鄉", "花蓮縣瑞穗鄉", "花蓮縣萬榮鄉", "花蓮縣玉里鎮", "花蓮縣卓溪鄉", "花蓮縣富里鄉"],
    "台東縣": ["台東縣臺東市", "台東縣綠島鄉", "台東縣蘭嶼鄉", "台東縣延平鄉", "台東縣卑南鄉", "台東縣鹿野鄉", "台東縣關山鎮", "台東縣海端鄉", "台東縣池上鄉", "台東縣東河鄉", "台東縣成功鎮", "台東縣長濱鄉", "台東縣太麻里鄉", "台東縣金峰鄉", "台東縣大武鄉", "台東縣達仁鄉"],
    "澎湖縣": ["澎湖縣馬公市", "澎湖縣西嶼鄉", "澎湖縣望安鄉", "澎湖縣七美鄉", "澎湖縣白沙鄉", "澎湖縣湖西鄉"],
    "金門縣": ["金門縣金城鎮", "金門縣金湖鎮", "金門縣金沙鎮", "金門縣金寧鄉", "金門縣烈嶼鄉", "金門縣烏坵鄉"],
    "連江縣": ["連江縣南竿鄉", "連江縣北竿鄉", "連江縣莒光鄉", "連江縣東引鄉"],
}


def build_submit_payload(
    *,
    applicant_name: str,
    mode: str,
    page_id: str,
    update_action: str,
    vendor: str,
    title: str,
    internal_title: str,
    external_title: str,
    salary: str,
    interview_method: str,
    internal_desc: str,
    external_desc: str,
    notes: str,
    existing_image_url: str,
    industry: list,
    category: list,
    job_type: list,
    foreign_student: list,
    job_cycle: list,
    city: list,
    district: list,
    branch: list,
    shift: list,
    leave_type: list,
    pay_method: list,
    image_base64: str = "",
    image_filename: str = "",
) -> dict:
    """組出送給 GAS SUBMIT_JOB 端點的請求內容，欄位名稱、巢狀結構
    （mode/pageId/updateAction 在最外層，其餘欄位包在 fields 裡）都照抄
    Project_Job.gs 的 JobWorkflowService.processJobSubmission() 現有樣子。
    `fields.publisher` 這裡送什麼都不影響結果——GAS 收到後會自己依 mode
    覆寫成正確的刊登人，這裡照抄現有表單的做法一起送，純粹保持格式一致。
    """
    return {
        "type": "SUBMIT_JOB",
        "applicant": {"displayName": applicant_name},
        "mode": mode,
        "pageId": page_id,
        "updateAction": update_action,
        "fields": {
            "applicant_name": applicant_name,
            "publisher": applicant_name,
            "vendor": vendor,
            "title": title,
            "internal_title": internal_title,
            "external_title": external_title,
            "salary": salary,
            "interview_method": interview_method,
            "internal_desc": internal_desc,
            "external_desc": external_desc,
            "notes": notes,
            "existing_image_url": existing_image_url,
            "industry": industry,
            "category": category,
            "job_type": job_type,
            "foreign_student": foreign_student,
            "job_cycle": job_cycle,
            "city": city,
            "district": district,
            "branch": branch,
            "shift": shift,
            "leave_type": leave_type,
            "pay_method": pay_method,
        },
        "image": {"base64": image_base64, "filename": image_filename},
    }


_AMBIGUOUS_OUTCOME_MESSAGE = (
    "這筆職缺很可能其實已經送出成功了，只是材霈平台這邊沒辦法確認職缺維護系統的回應內容"
    "（這是對方系統偶爾會出現的已知狀況，不是這邊的程式錯誤）。請先重新整理這個頁面、或到"
    "「維護既有職缺」搜尋看看這筆職缺有沒有出現，如果沒有看到才需要重新送出一次，避免不小心"
    "送出兩筆重複的職缺。"
)


def _post_to_gas(payload: dict):
    """統一處理跟 GAS 的 HTTP 溝通，回傳 (data, error_result)：成功解析出
    JSON 就回傳 (data, None)；任何失敗都回傳 (None, 結構統一的 dict)，跟
    services/salary_repayment_submit_service.py 是同一套「確定沒送到」跟
    「不確定有沒有處理完」分開處理的原則（見該檔案模組層級註解），這裡
    不重複貼一次完整說明。"""
    if not GAS_WEBAPP_URL:
        return None, {
            "status": "error",
            "message": "尚未設定 JOB_PORTAL_GAS_WEBAPP_URL 環境變數，請聯絡系統管理員設定後再試一次。",
        }
    try:
        response = requests.post(GAS_WEBAPP_URL, json=payload, timeout=_REQUEST_TIMEOUT_SECONDS)
    except requests.Timeout:
        return None, {"status": "unknown", "message": _AMBIGUOUS_OUTCOME_MESSAGE}
    except requests.RequestException as e:
        return None, {"status": "error", "message": f"連線到職缺維護系統失敗，請稍後再試：{e}"}

    try:
        data = response.json()
    except ValueError:
        return None, {"status": "unknown", "message": _AMBIGUOUS_OUTCOME_MESSAGE}
    if not isinstance(data, dict):
        return None, {"status": "unknown", "message": _AMBIGUOUS_OUTCOME_MESSAGE}
    return data, None


def submit_job(payload: dict) -> dict:
    """把 payload 轉送給 GAS 的 SUBMIT_JOB 端點，回傳格式跟
    salary_repayment_submit_service.submit_salary_repayment() 一致：
    成功時 status="success"；GAS 自己擋下來（例如尚未完成 LINE 綁定、
    無異動權限、找不到審核主管）時原樣轉發它的中文說明；連線層級的問題
    分成 "error"（確定沒送到，可以放心重送）跟 "unknown"（不確定有沒有
    處理完，呼叫端不能引導同仁重送）。"""
    data, error_result = _post_to_gas(payload)
    if error_result:
        return error_result
    if "status" not in data:
        return {"status": "unknown", "message": _AMBIGUOUS_OUTCOME_MESSAGE}
    return data


def compute_subordinate_names(viewer_username: str, accounts: list) -> list:
    """回傳 viewer_username 這個帳號的所有下屬「姓名」清單——用帳號的
    `manager_usernames` 欄位判斷誰的主管是自己（跟
    services/salary_repayment_service.py 的 build_manager_lookup_from_accounts()
    方向相反：那邊是「員工姓名 -> 主管姓名清單」，這裡是「反查我是誰的
    主管」），送給 GAS 的 GET_JOBS 當作 subordinates 參數，讓自己除了
    看得到自己刊登的職缺，也看得到轄下同仁刊登的職缺。"""
    return [a["name"] for a in accounts if viewer_username in a.get("manager_usernames", [])]


def fetch_maintainable_jobs(user_name: str, subordinates: list, user_id: str = "") -> list:
    """查詢這個人可以維護的既有職缺清單（自己刊登的 + 轄下同仁刊登的），
    對應現有表單的 GET_JOBS 請求／fetchJobsForSelect()。查詢失敗時安靜
    回傳空清單、不擋住整個表單頁面——這只是「維護既有職缺」模式的搜尋
    輔助功能，查不到清單不影響「新增全新職缺」照樣可以用，跟現有表單
    fetchJobsForSelect() 遇到例外只印 log、不彈錯誤訊息給同仁的行為一致。

    `user_id`（GAS 那邊用來判斷是不是系統管理員、藉此在清單裡也顯示
    「無主職缺」）目前這裡沒有傳，因為要拿到同仁在職缺系統那邊的 LINE ID
    需要另外查 job_portal_sso.py 同步進 Firestore 的資料，目前還沒接這段
    ——影響範圍很小：只有「無主職缺」（Notion 上刊登人欄位是空的舊資料）
    不會出現在搜尋清單裡，同仁自己或轄下同仁刊登的職缺完全不受影響。
    真正送出異動時，GAS 端還是會用它自己查到的 LINE ID 重新判斷一次
    權限，不會因為這裡沒傳 user_id 就讓沒權限的人改到職缺。"""
    if not GAS_WEBAPP_URL or not user_name:
        return []
    payload = {"type": "GET_JOBS", "userName": user_name, "subordinates": subordinates, "userId": user_id}
    try:
        response = requests.post(GAS_WEBAPP_URL, json=payload, timeout=_REQUEST_TIMEOUT_SECONDS)
        data = response.json()
    except (requests.RequestException, ValueError):
        return []
    if not isinstance(data, dict) or data.get("status") != "success":
        return []
    jobs = data.get("data")
    return jobs if isinstance(jobs, list) else []

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
WORKDIR /app

# libreoffice：派遣契約產生器（/dispatch-contracts）用來把產生好的 Word
# 契約轉成 PDF 存檔，網頁上才能直接內嵌預覽（不用下載就能看排版）。只裝
# libreoffice-writer（文件轉檔用得到的最小子集）+ 中文字型，不裝完整版
# LibreOffice，控制映像檔大小；沒有裝到 fonts-noto-cjk 的話，PDF 裡的
# 中文字會顯示不出來或變成方框。若這一步轉檔在正式環境失敗，程式碼那邊
# 有做失敗容錯（見 services/dispatch_contract_service.py 的
# convert_docx_to_pdf()）：只是那一筆紀錄看不到預覽，Word 檔案下載完全
# 不受影響。
RUN apt-get update && apt-get install -y --no-install-recommends \
    libreoffice-writer \
    fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Cloud Run 會動態注入 PORT 環境變數，預設為 8080
CMD exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}

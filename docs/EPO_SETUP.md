# EPO API Key 申請指南

## 為什麼要申請

目前專利檢索用 FreePatentsOnline，它能給「專利號 + 標題 + 五大局覆蓋」，
但**沒有申請人（assignee）**。這使得一個關鍵問題無法回答：

> 「誰在這個領域佈局最密？」

EPO OPS 補上這塊，並額外提供：

- **申請人／發明人** → 專利佈局分析（誰在卡位）
- **法律狀態**（INPADOC）→ 專利是否仍有效、何時到期
- **專利家族**（DOCDB family）→ 同一技術在幾國佈局（判斷保護強度）
- **引用資料** → 技術影響力

---

## 費用與配額

| 項目 | 內容 |
|------|------|
| 費用 | **免費** |
| 配額 | 每週 4 GB 資料量 |
| 付費升級 | 每年 EUR 2,800（超過 4 GB 才需要） |

本工具的查詢量遠低於 4 GB／週（每次檢索約數 MB），
免費方案完全夠用。

---

## 申請步驟

### 1. 註冊帳號

前往 **https://developers.epo.org/**
點右上角 **register**，用 email 註冊（免費，不需機構 email）。

### 2. 建立 App

登入後，從上方選單進入 **APIs**：

1. 點 **Create new app**（或 "Define your apps"）
2. 填寫 App 名稱，例如 `MedtechDevAssistant`
3. 送出後會取得兩組憑證：
   - **Consumer Key**（即 Client ID）
   - **Consumer Secret**（即 Client Secret）

> **注意**：Secret 只會顯示一次，請立刻複製保存。

### 3. 設定憑證

#### 本機開發

在專案目錄建立 `.streamlit/secrets.toml`（此檔已在 .gitignore 中）：

```toml
APP_PASSWORD = "你的網頁存取密碼"
EPO_OPS_KEY = "你的_Consumer_Key"
EPO_OPS_SECRET = "你的_Consumer_Secret"
```

或改用環境變數：

```bash
export EPO_OPS_KEY="你的_Consumer_Key"
export EPO_OPS_SECRET="你的_Consumer_Secret"
```

#### 雲端部署（Streamlit Community Cloud）

1. 前往 https://share.streamlit.io
2. 找到你的 app → 右側 **⋮** → **Settings**
3. 切到 **Secrets** 頁籤
4. 加入兩行（保留原有的 APP_PASSWORD）：

   ```toml
   APP_PASSWORD = "你的網頁存取密碼"
   EPO_OPS_KEY = "你的_Consumer_Key"
   EPO_OPS_SECRET = "你的_Consumer_Secret"
   ```

5. 點 **Save**，app 會自動重啟

### 4. 驗證

設定完成後，開啟網頁，側邊欄會顯示 **「EPO OPS 已連線」**。
專利頁籤會多出「主要專利佈局者」表格。

命令列驗證：

```bash
python -c "from src import source_epo; print(source_epo.status())"
```

預期輸出：`{'configured': True, 'message': 'EPO OPS 已連線'}`

---

## 疑難排解

**顯示「未設定 EPO 憑證」**
→ 確認 secrets.toml 的變數名稱完全一致（`EPO_OPS_KEY`、`EPO_OPS_SECRET`），
   且存檔後 app 有重啟。

**顯示 401「ClientId is Invalid」**
→ Key 或 Secret 貼錯，或有多餘空白。重新複製一次。

**顯示 403**
→ 已認證但無權限，通常是 App 尚未啟用。
   回 developers.epo.org 確認 App 狀態。

**查詢突然開始失敗**
→ 可能超出每週 4 GB 配額。等待下週配額重置。
   EPO 的回應標頭會帶配額使用量（`X-Throttling-Control`）。

**未設定憑證會怎樣？**
→ 不會壞掉。專利模組照常運作（FPO 的部分），
   只是少掉申請人分析，報告會加一行說明。

---

## 技術細節（供維護參考）

- 認證端點：`https://ops.epo.org/3.2/auth/accesstoken`
- 認證方式：OAuth2 client_credentials，Basic auth 帶 `key:secret`
- Token 有效期：20 分鐘（本工具快取 15 分鐘）
- 檢索端點：`https://ops.epo.org/3.2/rest-services/published-data/search`
- 回應格式：XML 或 JSON（用 `Accept: application/json` 取 JSON）
- 配額標頭：`X-Throttling-Control`

實測紀錄（2026-09-22）：
- 無效憑證 → HTTP 401 `ClientId is Invalid`（確認端點運作中）
- 無 token 查詢 → HTTP 403（確認需認證）

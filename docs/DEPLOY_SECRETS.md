# Streamlit Cloud 設定 OLLAMA_API_KEY

## 為什麼需要

線上版的「用 AI 協助抽取檢索詞」目前顯示**未設定服務**（灰色不可勾）。

沒有這個金鑰，工具只能用內建對照表，會導致：

1. **AI 抽取完全不能用** —— 長文無法自動抽出檢索詞
2. **台灣區塊幾乎全空** —— 這是最嚴重的後果
   實測心衰竭案例：TFDA 檢索詞會退回英文 `Ballistocardiography`
   → 台灣競品 **0 件**（因為 TFDA 品名是中文，英文詞永遠對不上）
   有金鑰時：檢索詞變成中文「呼吸監測器」→ 精確層 1 件、同類層 3 件
3. **兩層台灣競品無法運作** —— 同類層需要 LLM 候選的品類詞

## 設定步驟

1. 開啟 https://share.streamlit.io/
2. 找到 app：`medtech-dev-assistant`
3. 右上角 **⋮** → **Settings** → **Secrets**
4. 貼上以下內容（把值換成你的金鑰）：

```toml
OLLAMA_API_KEY = "你的金鑰"
```

5. **Save** —— 存檔後立即生效，**不需要重新部署**

## 金鑰從哪來

本機已有一組，存在 `~/.hermes/.env`：

```bash
grep OLLAMA_API_KEY ~/.hermes/.env
```

服務是 Ollama Cloud（`https://ollama.com/v1`），模型 `deepseek-v4.1-flash`。
若要在雲端帳號另外申請，到 https://ollama.com/settings/keys

## 選用的自訂變數

若想換模型或端點，可在 Secrets 一併設定：

```toml
OLLAMA_API_KEY = "你的金鑰"
LLM_MODEL = "deepseek-v4.1-flash"
LLM_BASE_URL = "https://ollama.com/v1"
```

程式會依序找：環境變數 → Streamlit secrets → `~/.hermes/.env`。

## 安全性提醒

**金鑰只放在 Streamlit Secrets，不要寫進 repo。**
本專案 `.gitignore` 已排除 `.env`、`secrets.toml`；
程式碼只從 secrets 讀取，不會把金鑰寫入日誌或報告。

另外，Secrets 頁面還需要設定存取密碼（目前顯示「未設定存取密碼（開發模式）」）：

```toml
OLLAMA_API_KEY = "你的金鑰"
APP_PASSWORD = "自訂密碼"
```

## 費用

每次 AI 抽取約 2-12 秒、數千 tokens。本機實測三案例共約 20 次呼叫，
成本極低。抽取結果有快取（同段產品說明不重複呼叫）。

## 設定後的驗證

設定完成後，重新整理 app 應該看到：

- 「用 AI 協助抽取檢索詞」變成**可勾選**（不再顯示「未設定服務」）
- 勾選後，預檢畫面出現「🤖 AI 抽取完成（deepseek-v4.1-flash，X.X 秒）」
- 下方列出 AI 提出的候選詞清單
- 跑完後，台灣區塊出現**兩層**廠商清單

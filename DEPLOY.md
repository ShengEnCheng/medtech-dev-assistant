# 部署說明

本專案以 **Streamlit Community Cloud** 部署為公開網址（免費）。
選這個方案的理由：

- 網址固定，同事與學員不需安裝任何東西
- 不需要你個人電腦一直開著
- 直接從 GitHub 部署，改版推上去就自動更新
- 內建 secrets 管理，存取密碼不會進版控

---

## 一、部署前準備（只需做一次）

### 1. 確認 repo 內含精簡索引

雲端容器每次休眠喚醒後檔案系統會重置，因此 repo 內需附一份
TFDA 精簡索引 `data/tfda_slim.db`（約 37 MB），啟動時會自動還原。

```bash
ls -lh data/tfda_slim.db    # 應顯示約 37 MB
```

精簡版與完整版的檢索結果完全相同，只移除了 FTS 表與
檢索用不到的欄位（申請商地址、製造商、發證日期）。

### 2. 建立 GitHub repo

```bash
cd /Volumes/MACRAID/projects/medtech-dev-assistant
git init
git add .
git commit -m "醫療產品開發助手：Biodesign 商品化三模組"
gh repo create medtech-dev-assistant --private --source=. --push
```

**建議設為 private。** 報告內容含專利檢索與競品名單，
屬團隊前期敏感資料。Streamlit Cloud 支援 private repo 部署。

---

## 二、部署到 Streamlit Community Cloud

1. 前往 https://share.streamlit.io ，用 GitHub 帳號登入
2. 點 **New app**
3. 選擇 repo：`<你的帳號>/medtech-dev-assistant`
4. Branch：`main`　Main file path：`app.py`
5. 點 **Advanced settings → Secrets**，貼上：

   ```toml
   APP_PASSWORD = "你的存取密碼"
   ```

6. 點 **Deploy**

首次部署約需 3-5 分鐘。完成後會得到一個網址，格式為：

```
https://<app-name>.streamlit.app
```

---

## 三、部署後驗證清單

- [ ] 開啟網址，應先看到「請輸入存取密碼」畫面
- [ ] 輸入錯誤密碼 → 顯示「密碼不正確」
- [ ] 輸入正確密碼 → 進入主畫面
- [ ] 側邊欄顯示「TFDA 索引就緒　104,680 筆」
- [ ] 輸入一段產品說明、三模組全選、按「開始評估」
- [ ] 五個頁籤都出得來（法規／競品／專利／調整建議／完整報告）
- [ ] 「下載 Markdown 報告」可正常下載
- [ ] 側邊欄「登出」按鈕可運作

---

## 四、密碼管理

- 換密碼：Streamlit Cloud → App settings → Secrets → 修改 → Save
  儲存後 app 會自動重啟，所有人需重新登入
- 課程或活動結束後建議換一次密碼，即收回所有舊存取
- 密碼不要寫進程式碼或 README

---

## 五、常見問題

**部署後顯示「TFDA 索引未建立」**
→ 確認 `data/tfda_slim.db` 有推上 GitHub。
   `git ls-files data/` 應列出該檔案。

**app 喚醒很慢（第一次開啟要等 30-60 秒）**
→ Streamlit Cloud 免費方案閒置後會休眠，屬正常現象。
   付費方案可設定不休眠。

**專利模組顯示「兩個來源皆未取得」**
→ FreePatentsOnline 有速率限制，多人同時使用時容易觸發。
   報告會附上檢索詞表，可至 patents.google.com 人工檢索。

**想改成內部網路部署**
→ 改用 Tailscale：這台機器已裝好（100.108.218.36）。
   同事裝 Tailscale 後即可在任何地方開啟
   `http://shengenmac-mini:8501`，內容不出內網。
   這個方式不需要登入密碼，但需要同事安裝 Tailscale。

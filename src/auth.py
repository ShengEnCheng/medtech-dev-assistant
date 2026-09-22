"""存取控制。

為什麼需要：
部署到公開網址後，任何知道網址的人都能開啟。
本工具會產出商品化評估報告（含專利檢索、競品名單），
屬於團隊的敏感前期資料，不該無限制公開。

設計取捨：
採用「共用密碼」而非帳號系統。理由：
1. 使用族群是固定的（育成中心同仁、課程學員、SPARK 團隊），
   不需要個人化權限與審計軌跡。
2. 帳號系統需要資料庫、密碼重設流程、離職停用流程，
   維運成本遠高於效益。
3. 課程或活動結束後換一次密碼即可收回所有存取。

密碼存放於 Streamlit secrets（部署平台加密保存），
不寫在程式碼或版控中。
"""

from __future__ import annotations

import hmac
import os

import streamlit as st


def _expected_password() -> str | None:
    """取得預期密碼。依序嘗試 Streamlit secrets（雲端）→ 環境變數（本機）。"""
    try:
        pw = st.secrets.get("APP_PASSWORD")
        if pw:
            return str(pw)
    except Exception:
        # 本機沒有 secrets.toml 時 st.secrets 會拋錯，屬正常情況
        pass
    return os.environ.get("APP_PASSWORD") or None


def require_login() -> bool:
    """檢查登入狀態。未登入時顯示密碼輸入畫面並回傳 False。

    本機開發若未設定密碼，自動放行（避免開發時被擋）。
    """
    expected = _expected_password()

    if not expected:
        # 未設定密碼：本機開發模式，直接放行但顯示提示
        st.sidebar.caption("🔓 未設定存取密碼（開發模式）")
        return True

    if st.session_state.get("authed"):
        return True

    st.title("🩺 醫療產品開發助手")
    st.caption("請輸入存取密碼")
    with st.form("login"):
        pw = st.text_input("存取密碼", type="password", label_visibility="collapsed")
        ok = st.form_submit_button("進入", type="primary", width="stretch")
    if ok:
        # 使用 hmac.compare_digest 做時間恆定比較，避免時序側通道
        if hmac.compare_digest(pw, expected):
            st.session_state["authed"] = True
            st.rerun()
        else:
            st.error("密碼不正確")
    st.caption("忘記密碼請洽育成中心。")
    return False


def logout_button() -> None:
    """在側邊欄顯示登出按鈕。"""
    if _expected_password() and st.session_state.get("authed"):
        if st.sidebar.button("登出", width="stretch"):
            st.session_state["authed"] = False
            st.rerun()

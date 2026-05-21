# Busan Trip Update

包含新版 app.py 與 templates/index.html。

更新內容：
- 移除 Naver 按鈕，只保留 Google Map。
- 新增景點 Modal。
- 時間改成 AM/PM、Hour、Minutes（10 分鐘間隔）。
- 新增後會呼叫 /api/spots，並重新讀取資料顯示在 timeline。
- app.py 增加 map_name、google_map_url、show_on_map 欄位，舊資料庫會自動 migration。

使用：覆蓋到 repo 後跑 `python3 app.py`。

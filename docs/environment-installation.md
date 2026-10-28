# 安裝展覽執行環境

適用於 Windows 64 位元主機。安裝需要網際網路；開始前先結束正在執行的展覽。

## 操作

1. 將整個專案放在要長期使用的目錄，保留 `requirements.txt`、`launcher/` 與 `frontend/`。
2. 雙擊專案根目錄的 `install-environment.bat`。
3. 等待四個階段完成：準備 Python、安裝後端套件、檢查後端匯入、安裝與建置前端。
4. 顯示 `Installation complete` 與 `Ready` 後，雙擊 `start-exhibition.bat` 開展。
5. 要結束展覽時，在展覽視窗按 Alt+F4，或雙擊 `stop-exhibition.bat`；兩者都會一併關閉 Web 服務與 AI Worker，下次可直接再開展。

安裝失敗會保留視窗，顯示出錯階段並回傳非零退出碼。詳細安裝紀錄接續寫入 `runtime/install-environment.txt`。修正網路或錯誤原因後，可再次執行同一個 BAT；腳本不刪除作品、資料庫、設定或 `logs/runtime.txt`。

## 自動準備的內容

- Python：優先沿用專案已有的 Python 3.14 環境；需要新建環境時，尋找本機相容的 Python 3.14，否則下載 [Python 3.14.7 官方 Windows 安裝程式](https://www.python.org/downloads/release/python-3147/)，驗證官方 SHA-256 後安裝基礎環境，再建立專案的虛擬環境。
- 專案 Python 路徑：`runtime/python/Scripts/python.exe` 與 `pythonw.exe`。啟動 BAT 優先尋找此位置，也相容既有交付包的 `runtime/python/pythonw.exe`。
- 後端套件：依 `requirements.txt` 的固定版本安裝，執行 `pip check` 並確認必要套件可匯入；不會因套件安裝失敗而繼續宣告成功。
- 前端工具：需要建置原始碼時，準備 [官方 Node.js 24.22.1 Windows 套件](https://nodejs.org/download/release/v24.22.1/) 至 `runtime/node/`，依官方 `SHASUMS256.txt` 驗證下載內容。
- 前端：依 `package-lock.json` 執行 `npm ci`，再執行型別檢查及正式建置，確認 `frontend/dist/` 具備 exhibition、admin 兩個入口。每日開展直接使用這些本地資源。

基礎 Python 安裝採目前使用者範圍，不修改全域 PATH、檔案關聯或新增捷徑；Node.js 使用專案內檔案。已存在的 Python、Node.js 環境會在版本檢查後沿用。Python 安裝參數依據 [官方 Windows 安裝文件](https://docs.python.org/3.14/using/windows.html#installing-without-ui)。

若使用只有 `app/static/` 的已建置交付包，可直接沿用既有前端；套件清單須位於根目錄或 `app/requirements.txt`。展示瀏覽器仍需已有 Microsoft Edge、Chrome 或專案附帶的瀏覽器。

## 補充

- `runtime/` 是本機環境，不加入 Git；換電腦或搬移專案後，可重新執行安裝 BAT 檢查與修復路徑。
- 只要安裝後端時，可執行 `install-environment.bat -SkipFrontend`；這不會替缺少前端建置檔的專案準備展覽頁與後臺。
- 安裝不啟動展覽、不呼叫付費 AI，也不填寫金鑰。已有的金鑰與設定保留，實際啟動後由原有後臺管理。

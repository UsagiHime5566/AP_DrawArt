// 首次設定未完成且尚無作品時的提示；指引工作人員從同網路裝置開啟後臺。

export function SetupNotice({ adminUrls }: { adminUrls: string[] }) {
  return (
    <div className="setup">
      <div className="setup__card">
        <p className="setup__eyebrow">準備中</p>
        <h2 className="setup__title">需要完成首次設定</h2>
        <p className="setup__body">請用同一網路的手機、平板或筆電開啟後臺：</p>
        {adminUrls.length > 0 ? (
          <ul className="setup__urls">
            {adminUrls.map((url) => (
              <li key={url}>{url}</li>
            ))}
          </ul>
        ) : (
          <p className="setup__body">主機網址：http://&lt;主機 IP&gt;:8080/admin</p>
        )}
      </div>
    </div>
  );
}

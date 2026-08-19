# Local Agent launchd

```bash
cd "/Users/jenny/Documents/codex-workspace/tool-system"

./scripts/local_agent_service.sh install
./scripts/local_agent_service.sh status
./scripts/local_agent_service.sh uninstall
```

服務名稱：`com.lemonclean.tools.local-agent`

Log：

- `~/Library/Logs/LemonToolsAgent/local_agent.launchd.out.log`
- `~/Library/Logs/LemonToolsAgent/local_agent.launchd.err.log`

執行目錄：`/Users/jenny/Documents/codex-workspace/tool-system`

Local Agent 直接執行此原始碼目錄，不再複製到
`~/Library/Application Support/LemonToolsAgent/tool-system`。

`RunAtLoad` 會在 macOS 使用者登入時啟動，`KeepAlive` 會在 Agent 異常結束後自動重啟。

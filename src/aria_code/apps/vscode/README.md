# Aria Code — VS Code Extension

AI-powered financial analysis sidebar for VS Code, connected to the Aria Code backend.

## Requirements

- VS Code 1.85 or later
- Aria Code backend running locally or on a reachable API host

## Start the backend

```bash
# From the Aria Code project root:
python3 aria_daemon.py

# Or start the FastAPI server directly:
uvicorn apps.api.src.main:app --host 0.0.0.0 --port 8000
```

## Install from VSIX (recommended)

### Step 1 — Build the extension package

```bash
cd apps/vscode
npm install --save-dev @vscode/vsce
npx vsce package
# Produces: aria-code-1.0.0.vsix
```

### Step 2 — Install in VS Code

**Option A — command line:**
```bash
code --install-extension aria-code-1.0.0.vsix
```

**Option B — VS Code UI:**
1. Open VS Code
2. Go to **Extensions** (⇧⌘X)
3. Click the **...** menu (top-right of Extensions panel)
4. Choose **Install from VSIX...**
5. Select `aria-code-1.0.0.vsix`

## Install for development (no build step)

```bash
# Copy the extension folder to VS Code extensions directory
cp -r apps/vscode ~/.vscode/extensions/aria-code-1.0.0

# Then reload VS Code (⇧⌘P → "Developer: Reload Window")
```

## Features

| Feature | Description |
|---|---|
| **Market tab** | Live indices (S&P 500, Nasdaq, 沪深300, BTC), auto-refreshes every 60 s |
| **Portfolio tab** | Portfolio overview from your connected broker |
| **Chat tab** | Ask Aria AI anything — markets, stocks, analysis |
| **Alerts tab** | View and manage price alerts |
| **Status bar** | Shows `Aria Online / Offline` — polls `/health` every 30 s |
| **Analyze Selection** | Right-click selected text → "Analyze with Aria AI" |

## Usage

1. After installing, click the **graph icon** in the VS Code Activity Bar (left sidebar)
2. The **Aria Financial** panel opens with Market, Portfolio, Chat, and Alerts tabs
3. Make sure the backend is running — the status bar shows connection state

### Analyze code/text with AI

1. Select any text in your editor
2. Right-click → **Analyze with Aria AI**
3. Aria switches to the Chat tab and sends your selection for analysis

### Keyboard shortcut (optional)

Add to your `keybindings.json`:
```json
{
  "key": "ctrl+shift+a",
  "command": "aria-code.openPanel"
}
```

## API Endpoints Used

| Endpoint | Purpose |
|---|---|
| `GET /health` | Connection health check (every 30 s) |
| `GET /api/v1/institution/market/indices` | Major market indices |
| `GET /api/v2/market/quote?symbols=...` | Fallback quote data |
| `GET /api/v1/institution/analysis/morning-brief` | Morning market brief |
| `GET /api/v1/institution/portfolio/overview` | Portfolio overview |
| `POST /api/v2/ai/chat` | AI chat messages |
| `GET /api/v2/alert/list` | Price alerts list |
| `POST /api/v2/alert/create` | Create new alert |
| `DELETE /api/v2/alert/:id` | Delete alert |

## Troubleshooting

**Status bar shows "Aria Offline"**
- Confirm the backend is running: `curl http://localhost:8000/health`
- Check for port conflicts: `lsof -i :8000`

**Extension not appearing after install**
- Reload VS Code: ⇧⌘P → "Developer: Reload Window"

**Chat returns errors**
- The AI chat endpoint requires the backend to have a valid LLM provider configured
- Check backend logs for API key or model errors

**Backend is on a different URL**
- Set the VS Code setting `ariaCode.apiBase` to the backend base URL you want to use.

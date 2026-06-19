"""
ModelCommandsMixin — Model/config commands: model, apikey, providers, cloud, config, tools, skills.

Extracted from aria_cli.py. Methods' __globals__ are rebound to aria_cli's namespace
by _rebind_mixin_globals() called at module load time.
"""
from __future__ import annotations


class ModelCommandsMixin:
    """Mixin: Model/config commands: model, apikey, providers, cloud, config, tools, skills."""

    async def cmd_model(self, args: str):
        name = args.strip()

        # ── "provider/model" format (Open Interpreter style) ─────────────────
        # Examples: /model deepseek/deepseek-chat  /model ollama/qwen2.5:7b
        #           /model openai/gpt-4.5          /model openai/o3  /model openai/o4-mini
        if "/" in name and not name.startswith("http"):
            _prov, _mod = name.split("/", 1)
            _prov = _prov.strip().lower()
            _mod  = _mod.strip()
            _local_backends = {"ollama", "lmstudio", "vllm", "llamacpp", "jan", "custom"}
            if _prov not in _local_backends:
                # Cloud provider — check API key
                _key = _get_provider_key(_prov)
                if not _key:
                    msg = (f"⚠ {_prov} API key 未配置。"
                           f"运行: /apikey set {_prov} <key>")
                    console.print(f"[yellow]{msg}[/yellow]") if HAS_RICH else print(msg)
                    return
            self.terminal.config["local_provider"] = _prov
            self.terminal.config["model"] = _mod
            save_config(self.terminal.config)
            msg = f"✓ 已切换到 {_prov}/{_mod}"
            console.print(f"[green]{msg}[/green]") if HAS_RICH else print(msg)
            return

        # Direct selection by number: /model 1 /model 2 … (Codex style)
        if name.isdigit():
            idx = int(name) - 1
            keys = list(MODELS.keys())
            if 0 <= idx < len(keys):
                self._set_model(keys[idx])
            else:
                console.print(f"[dim]No model #{name}[/dim]" if HAS_RICH else f"No model #{name}")
            return

        # Direct selection by key (case-insensitive): /model qwen7b
        if name.lower() in MODELS:
            self._set_model(name.lower())
            return

        # Direct selection by alias: /model st / s / p / coder
        if name.lower() in MODEL_ALIASES:
            self._set_model(MODEL_ALIASES[name.lower()])
            return

        # Direct selection by full Ollama model ID: /model qwen2.5-coder:1.5b
        if name and ":" in name:
            self._set_model_by_id(name)
            return

        # ── Interactive picker (Codex style: numbered list + descriptions) ────
        ollama_url  = self.terminal.config.get("ollama_url", "http://localhost:11434")
        current_id  = self.terminal.config.get("model", "qwen2.5:7b")
        try:
            from apps.cli.i18n import t as _i18nt
            _lang = self.terminal.config.get("ui_lang", "en") or "en"
            _i18n = lambda k: _i18nt(k, lang=_lang)
        except Exception:
            _lang = "en"
            _i18n = lambda k: k

        rich_models, ollama_err = detect_ollama_models_rich(ollama_url)
        installed_names = {m["name"] for m in rich_models}
        aria_ids        = {m["id"] for m in MODELS.values()}

        # ── Build picker title (one line, shown inside arrow_select header) ──
        _sel_model = _i18n("select_model")
        _installed = _i18n("installed")
        if ollama_err:
            _picker_title = f"{_sel_model}  [Ollama: {ollama_err[:40]}]"
        else:
            n_installed = sum(1 for m in MODELS.values() if m["id"] in installed_names)
            _picker_title = f"{_sel_model}   {n_installed}/{len(MODELS)} {_installed}  ·  /model <id> or number"

        def _status_tag(mid: str, badge: str) -> str:
            """Return short status: ● installed / ○ not installed / ☁ cloud"""
            if badge == "Cloud":
                return "☁"
            return "●" if mid in installed_names else "○"

        # Get terminal width for safe label truncation
        try:
            _term_cols = os.get_terminal_size().columns
        except Exception:
            _term_cols = 80

        def _cjk_width(s: str) -> int:
            """Display-column width (CJK = 2 cols each)."""
            w = 0
            for ch in s:
                cp = ord(ch)
                w += 2 if (0x2E80 <= cp <= 0xA4CF or 0xAC00 <= cp <= 0xD7AF or
                           0xFF01 <= cp <= 0xFF60 or 0x3000 <= cp <= 0x303F) else 1
            return w

        def _cjk_truncate(s: str, max_cols: int) -> str:
            """Truncate s so its display width ≤ max_cols, adding … if cut."""
            w, out = 0, ""
            for ch in s:
                cw = 2 if (0x2E80 <= ord(ch) <= 0xA4CF or
                           0xAC00 <= ord(ch) <= 0xD7AF or
                           0xFF01 <= ord(ch) <= 0xFF60 or
                           0x3000 <= ord(ch) <= 0x303F) else 1
                if w + cw > max_cols:
                    return out + "…"
                out += ch
                w += cw
            return out

        def _short_desc(m: dict) -> str:
            """Single-line description with right-aligned meta tags."""
            desc  = m.get("description", "")
            badge = m.get("badge", "")
            extras = []
            if _HAS_MODEL_CAP:
                cap = get_model_capability(m["id"])
                extras.append(f"ctx={cap.context_window//1024}K")
                if cap.tool_calls: extras.append("tools✓")
                if cap.thinking:   extras.append("think")
            else:
                extras.append(f"ctx={m.get('num_ctx', 8192)//1024}K")
            if badge in ("Fast", "Code", "Think", "Cloud"):
                extras.insert(0, badge)
            meta = "  " + " · ".join(extras) if extras else ""
            # prefix "  N. ☁ ModelName  " ≈ 24 cols; give description 60% of remaining
            _prefix_cols  = 24
            _avail        = max(30, _term_cols - _prefix_cols - len(meta) - 2)
            _desc_budget  = max(20, _avail * 3 // 4)   # 75% of available → description
            desc = _cjk_truncate(desc, _desc_budget)
            return f"{desc}{meta}"

        # Build option list (Codex: numbered, no separators within Aria section)
        options: list = []   # (label_str, desc_str)  for _arrow_select
        all_ids: list = []

        # ── Print numbered list only in non-interactive (-p) mode ────────────
        # In interactive TTY mode the arrow picker below already shows all items.
        # Printing twice causes the visual duplication seen in the session log.
        _is_tty = sys.stdin.isatty()
        idx_counter = 1
        if not _is_tty:
            # Non-interactive (-p mode): show static numbered list then return.
            # The arrow picker cannot run without a TTY.
            community_list = [cm for cm in rich_models if cm["name"] not in aria_ids]
            for key, m in MODELS.items():
                mid    = m["id"]
                is_cur = mid == current_id
                status = _status_tag(mid, m.get("badge", ""))
                cur_tag = "  (current)" if is_cur else ""
                desc = _short_desc(m)
                line = f"  {idx_counter}. {status} {m['name']:<14s}  {desc}{cur_tag}"
                console.print(line) if HAS_RICH else print(line)
                idx_counter += 1
            if community_list:
                console.print() if HAS_RICH else print()
                lbl = "  Community (Ollama)"
                console.print(f"[dim]{lbl}[/dim]") if HAS_RICH else print(lbl)
                for cm in community_list:
                    mid    = cm["name"]
                    is_cur = mid == current_id
                    cur_tag = "  (current)" if is_cur else ""
                    line = f"  {idx_counter}. ● {mid}{cur_tag}"
                    console.print(line) if HAS_RICH else print(line)
                    idx_counter += 1
            console.print() if HAS_RICH else print()
            console.print("  [dim]Use /model <id> to switch. E.g. /model deepseek/deepseek-chat[/dim]") if HAS_RICH else print("  Use /model <id> to switch.")
            return

        # ── Build compact options for _arrow_select ────────────────────────
        # In TTY mode: include short description (static list is suppressed above).
        # In non-TTY: descriptions already shown in static list, keep labels short.
        num = 1
        for key, m in MODELS.items():
            mid    = m["id"]
            status = _status_tag(mid, m.get("badge", ""))
            is_cur = " ◀" if mid == current_id else ""
            if _is_tty:
                desc_part = f"  {_short_desc(m)}"
            else:
                desc_part = ""
            label  = f"  {num}. {status} {m['name']}{is_cur}{desc_part}"
            options.append((label, ""))
            all_ids.append(mid)
            num += 1

        community = [cm for cm in rich_models if cm["name"] not in aria_ids]
        if community:
            _comm_label = _i18n("community_models")
            options.append((f"  ── {_comm_label} ──", ""))
            all_ids.append(None)
            for cm in community:
                mid    = cm["name"]
                is_cur = " ◀" if mid == current_id else ""
                options.append((f"  {num}. ● {mid}{is_cur}", ""))
                all_ids.append(mid)
                num += 1

        if ollama_err and not rich_models:
            _unreach = _i18n("ollama_unreachable")
            options.append((f"  ── {_unreach} ──────────", ""))
            all_ids.append(None)

        # ── Run thread-based arrow picker (short labels = no line wrap) ────
        current_idx = next((i for i, mid in enumerate(all_ids) if mid == current_id), 0)

        while True:
            choice = await _run_picker_in_thread(
                options, current_idx,
                _picker_title,
                max_visible=len(options),
            )
            if choice < 0:
                _msg = _i18n("cancelled")
                console.print(f"[dim]{_msg}[/dim]" if HAS_RICH else _msg)
                return
            if all_ids[choice] is None:
                current_idx = min(choice + 1, len(options) - 1)
                continue
            break

        self._set_model_by_id(all_ids[choice])

    def _set_model(self, key: str):
        """Set model by MODELS key."""
        m = MODELS[key]
        self._set_model_by_id(m["id"])

    def _set_model_by_id(self, model_id: str):
        """Set model by Ollama model ID (works for both built-in and community models)."""
        self.terminal.config["model"] = model_id
        self.terminal._actual_model = None  # reset: new config model, no known fallback yet
        save_config(self.terminal.config)
        # Pretty label
        for m in MODELS.values():
            if m["id"] == model_id:
                if HAS_RICH:
                    console.print(f"[bold]Model:[/bold] [bold]{m['name']} {m['version']}[/bold] "
                                  f"[dim]{m['tag']}[/dim]")
                else:
                    print(f"Model: {m['name']} {m['version']} ({m['tag']})")
                return
        # Community / unknown model
        if HAS_RICH:
            console.print(f"[bold]Model:[/bold] [bold]{model_id}[/bold]  [dim](local)[/dim]")
        else:
            print(f"Model: {model_id} (local)")

    def cmd_thinking(self, args: str):
        mode = args.strip().lower()

        # Direct set: /thinking on
        if mode in ("on", "thinking"):
            self.terminal.config["thinking_mode"] = "thinking"
        elif mode in ("off", "instant"):
            self.terminal.config["thinking_mode"] = "instant"
        elif mode == "auto":
            self.terminal.config["thinking_mode"] = "auto"
        elif mode:
            # Unknown mode, show picker
            pass
        else:
            # Interactive picker
            current = self.terminal.config.get("thinking_mode", "auto")
            mode_keys = list(THINKING_MODES.keys())
            current_idx = mode_keys.index(current) if current in mode_keys else 0
            options = [(info["label"], info["description"]) for info in THINKING_MODES.values()]
            choice = _arrow_select(options, selected=current_idx, title="Thinking Mode")
            if 0 <= choice < len(mode_keys):
                self.terminal.config["thinking_mode"] = mode_keys[choice]
            else:
                if HAS_RICH:
                    console.print("[dim]No change[/dim]")
                else:
                    print("No change")
                return

        save_config(self.terminal.config)
        result = self.terminal.config["thinking_mode"]
        info = THINKING_MODES.get(result, {})
        if HAS_RICH:
            console.print(f"[green]Thinking: {info.get('label', result)}[/green]  [dim]{info.get('description', '')}[/dim]")
        else:
            print(f"Thinking: {result}")

    def cmd_skills(self, args: str):
        """List all available skills grouped by category."""
        categories = {}
        for s in SKILLS:
            cat = s["category"]
            if cat not in categories:
                categories[cat] = []
            categories[cat].append(s)

        cat_labels = {
            "research": "Research",
            "analysis": "Analysis",
            "strategy": "Strategy",
            "risk": "Risk Management",
            "quant": "Quantitative",
            "crypto": "Crypto",
            "tools": "Tools",
            "code": "Code Generation",
        }

        if HAS_RICH:
            console.print()
            for cat, skills in categories.items():
                label = cat_labels.get(cat, cat.title())
                console.print(f"  [bold]{label}[/bold]")
                for s in skills:
                    args_hint = f"  [dim]{s.get('args', '')}[/dim]" if s.get("args") else ""
                    console.print(f"    [bold]{s['command']:20s}[/bold][dim]{s['description']}[/dim]{args_hint}")
                console.print()

            console.print("[dim]  Type a skill command to execute, e.g. /deep-analysis AAPL[/dim]\n")
        else:
            print("\nSkills:")
            for cat, skills in categories.items():
                label = cat_labels.get(cat, cat.title())
                print(f"\n  [{label}]")
                for s in skills:
                    print(f"    {s['command']:20s} {s['description']}")

    async def _execute_skill(self, skill: dict, args: str):
        """Execute a skill by expanding its prompt template and sending to AI."""
        parts = args.strip().upper().split() if args.strip() else []
        cmd = skill["command"]

        # Skill invocation header — matches the ⏺ tool-call rhythm
        _skill_name = skill.get("name") or cmd.lstrip("/")
        _arg_hint = f"  [dim]{args.strip()}[/dim]" if args.strip() else ""
        if HAS_RICH:
            console.print(f"\n  [#C08050]⏺[/#C08050]  [bold]技能 · {_skill_name}[/bold]{_arg_hint}")
        else:
            print(f"\n  ⏺ 技能 · {_skill_name}  {args.strip()}")

        # Build the prompt from template
        template = skill["prompt"]

        if cmd == "/deep-analysis":
            symbol = parts[0] if parts else "AAPL"
            prompt = template.format(symbol=symbol)

        elif cmd == "/trade-idea":
            context = f" in {' '.join(parts)}" if parts else " in the US market"
            prompt = template.format(context=context)

        elif cmd == "/risk-report":
            if parts:
                symbols = ", ".join(parts)
            else:
                symbols = ", ".join(self.terminal.config.get("watchlist", ["AAPL", "MSFT", "GOOGL"]))
            prompt = template.format(symbols=symbols)

        elif cmd == "/factor-screen":
            factor = " ".join(parts).lower() if parts else "momentum"
            prompt = template.format(factor=factor)

        elif cmd == "/backtest-report":
            strategy = parts[0].lower() if len(parts) > 0 else "momentum"
            symbol = parts[1] if len(parts) > 1 else "SPY"
            start = parts[2] if len(parts) > 2 else "2023-01-01"
            end = parts[3] if len(parts) > 3 else "2025-01-01"
            prompt = template.format(strategy=strategy, symbol=symbol, start=start, end=end)

        elif cmd == "/morning-brief":
            extra = f"\nFocus on: {' '.join(parts)}" if parts else ""
            prompt = template.format(extra=extra)

        elif cmd == "/macro-outlook":
            context = f" for {' '.join(parts)}" if parts else " for the US and global economy"
            prompt = template.format(context=context)

        elif cmd == "/crypto-scan":
            extra = f"\nFocus on: {' '.join(parts)}" if parts else ""
            prompt = template.format(extra=extra)

        elif cmd == "/watchlist-scan":
            symbols = ", ".join(self.terminal.config.get("watchlist", ["AAPL", "MSFT", "GOOGL"]))
            prompt = template.format(symbols=symbols)

        elif cmd == "/sector-rotation":
            prompt = template

        elif cmd == "/gen-strategy":
            strategy = parts[0].lower() if len(parts) > 0 else "momentum"
            symbol = parts[1] if len(parts) > 1 else "SPY"
            prompt = template.format(strategy=strategy, symbol=symbol)

        elif cmd == "/gen-analysis":
            topic = " ".join(parts[:2]).lower() if parts else "technical analysis"
            symbols = ", ".join(parts[2:]) if len(parts) > 2 else "SPY"
            prompt = template.format(topic=topic, symbols=symbols)

        elif cmd == "/gen-bot":
            exchange = parts[0].lower() if len(parts) > 0 else "binance"
            strategy = " ".join(parts[1:]).lower() if len(parts) > 1 else "grid trading"
            prompt = template.format(exchange=exchange, strategy=strategy)

        else:
            prompt = template

        # Show skill activation
        if HAS_RICH:
            tools = ", ".join(skill.get("tools_hint", [])[:3])
            console.print(f"[bold]Skill:[/bold] [bold]{skill['name']}[/bold]  [dim]tools: {tools}[/dim]")
        else:
            print(f"Skill: {skill['name']}")

        await self.terminal.send_message(prompt)

    def cmd_tools(self, args: str):
        if HAS_RICH:
            console.print()
            console.print("  [bold]Local Tools[/bold] [dim](Code Agent)[/dim]")
            for i, (name, (_, desc)) in enumerate(LOCAL_TOOLS.items(), 1):
                console.print(f"    [bold]{name:28s}[/bold][dim]{desc}[/dim]")
            console.print()

            console.print(f"  [bold]Remote Tools[/bold] [dim]({len(ARIA_TOOLS)})[/dim]")
            for i, (name, desc) in enumerate(ARIA_TOOLS, 1):
                console.print(f"    [bold]{name:28s}[/bold][dim]{desc}[/dim]")
            console.print()
        else:
            print("\nLocal Tools (Code Agent):")
            for i, (name, (_, desc)) in enumerate(LOCAL_TOOLS.items(), 1):
                print(f"  {i:2d}. {name:30s} {desc}")
            print("\nRemote Aria Tools (22):")
            for i, (name, desc) in enumerate(ARIA_TOOLS, 1):
                print(f"  {i:2d}. {name:30s} {desc}")

    async def cmd_apikey(self, args: str):
        """Manage Cloud API keys.

        Usage:
            /apikey              — 交互式向导：选择 provider → 输入 key → 测试连接
            /apikey set <p> <k>  — 直接保存
            /apikey list         — 列出所有已配置 key
            /apikey remove <p>   — 删除 key
            /apikey test <p>     — 测试连接
        """
        parts = args.strip().split()
        sub   = parts[0].lower() if parts else ""

        # ── 无参数 or "add" → 交互式向导 ─────────────────────────────────────
        if not sub or sub in ("add", "wizard"):
            await self._cmd_apikey_wizard()
            return

        pjson = _load_providers_json()   # dict of {provider: {api_key, base_url, ...}}

        if sub == "set-url":
            # /apikey set-url <provider> <base_url>
            # 允许自定义端点（中转代理、国内镜像等），示例：
            #   /apikey set-url openai https://my-proxy.com
            #   /apikey set-url siliconflow https://api.siliconflow.cn
            if len(parts) < 3:
                msg = "Usage: /apikey set-url <provider> <base_url>"
                console.print(f"[dim]{msg}[/dim]") if HAS_RICH else print(msg)
                return
            provider = parts[1].lower()
            url      = parts[2].rstrip("/")
            entry    = pjson.get(provider, {})
            entry["base_url"] = url
            pjson[provider]   = entry
            _save_providers_json(pjson)
            msg = f"✓ {provider.capitalize()} base_url 已更新: {url}"
            console.print(f"[green]{msg}[/green]") if HAS_RICH else print(msg)
            return

        if sub == "set":
            if len(parts) < 3:
                msg = ("Usage: /apikey set <provider> <key>  (e.g. /apikey set deepseek sk-...)\n"
                       "       /apikey set-url <provider> <base_url>  (自定义代理端点)")
                console.print(f"[dim]{msg}[/dim]") if HAS_RICH else print(msg)
                return
            provider = parts[1].lower()
            key      = parts[2]
            _all_known = set(_PROVIDER_KEY_MAP) | set(_DATA_KEY_MAP) | set(_PROVIDER_BASE_URLS)
            if provider not in _all_known:
                known_llm  = ", ".join(sorted(_PROVIDER_KEY_MAP.keys()))
                known_data = ", ".join(sorted(_DATA_KEY_MAP.keys()))
                msg = (f"Unknown provider '{provider}'.\n"
                       f"  LLM providers: {known_llm}\n"
                       f"  Data services: {known_data}")
                console.print(f"[yellow]{msg}[/yellow]") if HAS_RICH else print(msg)
                return

            # ── Data service key ──────────────────────────────────────────────
            if provider in _DATA_KEY_MAP:
                _save_data_key(provider, key)
                env_var = _DATA_KEY_MAP[provider]
                os.environ[env_var] = key  # take effect immediately
                masked = key[:6] + "****" + key[-4:] if len(key) > 10 else "****"
                signup = _DATA_SIGNUP_URLS.get(provider, "")
                msg = f"✓ {provider.capitalize()} 数据服务 key 已保存 ({masked})"
                console.print(f"[green]{msg}[/green]") if HAS_RICH else print(msg)
                return

            # ── LLM provider key (original logic) ────────────────────────────
            # Persist to providers.json
            entry = pjson.get(provider, {})
            entry["api_key"] = key
            if provider in _PROVIDER_BASE_URLS:
                entry.setdefault("base_url", _PROVIDER_BASE_URLS[provider])
            pjson[provider] = entry
            _save_providers_json(pjson)
            # Also set in current process env so it works immediately
            env_var = _PROVIDER_KEY_MAP.get(provider)
            if env_var:
                os.environ[env_var] = key
            masked = key[:6] + "****" + key[-4:] if len(key) > 10 else "****"
            msg = f"✓ {provider.capitalize()} API key 已保存 ({masked})"
            console.print(f"[green]{msg}[/green]") if HAS_RICH else print(msg)

        elif sub == "list":
            _LLM_ORDER = [
                # 国际
                "deepseek", "anthropic", "openai", "google", "xai",
                "groq", "mistral", "cohere", "perplexity", "together",
                # 国内
                "siliconflow", "dashscope", "moonshot", "zhipu",
                "baidu", "bytedance", "minimax", "stepfun", "01ai",
            ]
            _DATA_ORDER = ["finnhub", "alphavantage", "twelvedata", "polygon",
                           "fmp", "newsapi", "coingecko", "tavily", "brave"]
            data_configured = _load_data_keys()

            if HAS_RICH:
                from rich.table import Table
                from rich import box as _rbox
                console.print()
                console.print("  [bold]🤖 LLM 服务 Keys[/bold]  [dim]— /apikey 进入向导[/dim]")
                console.print()
                for prov in _LLM_ORDER:
                    env_var = _PROVIDER_KEY_MAP.get(prov, "")
                    key_val = os.getenv(env_var or "") or pjson.get(prov, {}).get("api_key", "")
                    desc = _PROVIDER_DESC.get(prov, "")
                    if key_val:
                        masked = key_val[:6] + "****" + key_val[-4:] if len(key_val) > 10 else "****"
                        console.print(f"  [green]●[/green] [green]{prov:<14}[/green] {masked}  [dim]{desc}[/dim]")
                    else:
                        console.print(f"  [dim]○ {prov:<14} 未配置  {desc}[/dim]")
                console.print()
                console.print("  [bold]📊 数据服务 Keys[/bold]  [dim]— 后端离线时直连数据源[/dim]")
                console.print()
                for svc in _DATA_ORDER:
                    key_val = data_configured.get(svc, "")
                    desc = _PROVIDER_DESC.get(svc, "")
                    if key_val:
                        masked = key_val[:6] + "****" + key_val[-4:] if len(key_val) > 10 else "****"
                        console.print(f"  [green]●[/green] [green]{svc:<14}[/green] {masked}  [dim]{desc}[/dim]")
                    else:
                        console.print(f"  [dim]○ {svc:<14} 未配置  {desc}[/dim]")
                console.print()
                console.print("  [dim]提示: /apikey 进入交互向导  ·  /apikey test <provider> 测试连接[/dim]")
                console.print()
            else:
                print("\n  LLM Providers:")
                for prov in _LLM_ORDER:
                    env_var = _PROVIDER_KEY_MAP.get(prov, "")
                    key_val = os.getenv(env_var or "") or pjson.get(prov, {}).get("api_key", "")
                    status = key_val[:6] + "****" if key_val else "未配置"
                    print(f"  {prov:14s} {status}")
                print("\n  Data Services:")
                for svc in _DATA_ORDER:
                    key_val = data_configured.get(svc, "")
                    status  = key_val[:6] + "****" if key_val else "未配置"
                    print(f"  {svc:16s} {status}")

        elif sub == "remove":
            if len(parts) < 2:
                console.print("[dim]Usage: /apikey remove <provider>[/dim]") if HAS_RICH else print("Usage: /apikey remove <provider>")
                return
            provider = parts[1].lower()
            # LLM section
            if provider in pjson:
                pjson[provider].pop("api_key", None)
                if not pjson[provider]:
                    del pjson[provider]
                _save_providers_json(pjson)
            # Data section
            if provider in _DATA_KEY_MAP:
                try:
                    if PROVIDERS_FILE.exists():
                        raw = json.loads(PROVIDERS_FILE.read_text(encoding="utf-8"))
                        if provider in raw.get("data", {}):
                            del raw["data"][provider]
                            PROVIDERS_FILE.write_text(json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8")
                except Exception as _e:
                    logger.debug("apikey delete from file failed: %s", _e)
            # Clear from env
            env_var = _PROVIDER_KEY_MAP.get(provider) or _DATA_KEY_MAP.get(provider)
            if env_var and env_var in os.environ:
                del os.environ[env_var]
            msg = f"✓ {provider.capitalize()} key 已删除"
            console.print(f"[green]{msg}[/green]") if HAS_RICH else print(msg)

        elif sub == "test":
            if len(parts) < 2:
                console.print("[dim]Usage: /apikey test <provider>[/dim]") if HAS_RICH else print("Usage: /apikey test <provider>")
                return
            provider = parts[1].lower()
            key = _get_provider_key(provider) or _load_data_keys().get(provider, "")
            if not key:
                msg = f"⚠ {provider} API key 未配置，先运行 /apikey {provider}"
                console.print(f"[yellow]{msg}[/yellow]") if HAS_RICH else print(msg)
                return
            console.print(f"[dim]  正在测试 {provider}…[/dim]") if HAS_RICH else print(f"  测试 {provider}…")
            import asyncio as _aio
            loop = _aio.get_event_loop()
            ok, result_msg = await loop.run_in_executor(None, _test_api_key, provider, key)
            color = "green" if ok else "yellow"
            console.print(f"  [{color}]{result_msg}[/{color}]") if HAS_RICH else print(f"  {result_msg}")

        else:
            console.print("[dim]Usage: /apikey [set|list|remove|test] — 或直接 /apikey 进入向导[/dim]") if HAS_RICH else print("Usage: /apikey [set|list|remove|test]")

    async def _cmd_apikey_wizard(self):
        """交互式 API Key 配置向导：选 provider → 查看指引 → 输入 key → 测试连接。"""
        import getpass as _getpass

        pjson = _load_providers_json()
        data_cfg = _load_data_keys()

        def _is_configured(name: str) -> bool:
            env = _PROVIDER_KEY_MAP.get(name) or _DATA_KEY_MAP.get(name)
            if env and os.getenv(env):
                return True
            if name in pjson and pjson[name].get("api_key"):
                return True
            if name in data_cfg:
                return True
            return False

        # ── 分组构建 picker ───────────────────────────────────────────────────
        _LLM_ORDER = [
            # 国际
            "deepseek", "anthropic", "openai", "google", "xai",
            "groq", "mistral", "cohere", "perplexity", "together",
            # 国内
            "siliconflow", "dashscope", "moonshot", "zhipu",
            "baidu", "bytedance", "minimax", "stepfun", "01ai",
        ]
        _DATA_ORDER = ["finnhub", "alphavantage", "twelvedata", "polygon",
                       "fmp", "newsapi", "coingecko", "tavily", "brave"]

        all_items = []  # (label, desc, key_name | None)

        all_items.append(("─── 🤖 LLM 服务  (对话·分析·推理) ", "", None))
        for k in _LLM_ORDER:
            dot = "[green]●[/green]" if _is_configured(k) else "[dim]○[/dim]"
            desc = _PROVIDER_DESC.get(k, "")
            configured_tag = "  ✓" if _is_configured(k) else ""
            all_items.append((f"  {k:<14}{configured_tag}", desc, k))

        all_items.append(("─── 📊 数据服务  (行情·财报·新闻) ", "", None))
        for k in _DATA_ORDER:
            desc = _PROVIDER_DESC.get(k, "")
            configured_tag = "  ✓" if _is_configured(k) else ""
            all_items.append((f"  {k:<14}{configured_tag}", desc, k))

        picker_opts = [(label, desc) for label, desc, _ in all_items]
        sep_indices = {i for i, (_, _, key) in enumerate(all_items) if key is None}
        key_at = {i: key for i, (_, _, key) in enumerate(all_items) if key}

        # 默认选中第一个真实条目
        first_real = next(i for i in range(len(all_items)) if i not in sep_indices)
        selected = first_real

        while True:
            console.print() if HAS_RICH else None

            if HAS_RICH:
                from rich.panel import Panel as _Panel
                from rich import box as _rbox
                console.print(_Panel(
                    "  ↑↓ 上下选择  ·  Enter 确认  ·  ESC/q 退出向导\n"
                    "  [green]●[/green] 已配置  [dim]○[/dim] 未配置  ✓ 表示 key 已存在",
                    border_style="dim", box=_rbox.ROUNDED, padding=(0, 2),
                ))

            idx = _arrow_select(picker_opts, selected=selected, title="选择要配置的 Provider", max_visible=20)

            if idx < 0:
                console.print("[dim]已退出向导[/dim]") if HAS_RICH else print("已退出")
                return

            if idx in sep_indices:
                nxt = next((i for i in range(idx + 1, len(all_items)) if i not in sep_indices), first_real)
                selected = nxt
                continue

            provider = key_at[idx]
            selected = idx

            # ── 显示获取指引 ──────────────────────────────────────────────────
            guide = _PROVIDER_GUIDE.get(provider, "")
            signup = _LLM_SIGNUP_URLS.get(provider) or _DATA_SIGNUP_URLS.get(provider, "")
            if HAS_RICH:
                from rich.panel import Panel as _Panel
                from rich import box as _rbox
                guide_body = guide
                if signup:
                    guide_body += f"\n\n[bold cyan]🔗 {signup}[/bold cyan]"
                current_key = _get_provider_key(provider)
                if current_key:
                    masked = current_key[:6] + "****" + current_key[-4:] if len(current_key) > 10 else "****"
                    guide_body += f"\n\n[green]当前 key: {masked}[/green]  (直接回车保留现有 key)"
                console.print()
                console.print(_Panel(
                    guide_body,
                    title=f"[bold]{provider.upper()}  配置指引[/bold]",
                    border_style="cyan", box=_rbox.ROUNDED, padding=(0, 2),
                ))
            else:
                print(f"\n=== {provider.upper()} ===")
                print(guide)
                if signup:
                    print(f"注册地址: {signup}")

            # ── 输入 key ──────────────────────────────────────────────────────
            prompt_str = f"  请输入 {provider} API Key (输入后不显示): "
            try:
                raw_key = _getpass.getpass(prompt_str)
            except (KeyboardInterrupt, EOFError):
                console.print("\n[dim]已跳过[/dim]") if HAS_RICH else print("\n已跳过")
                continue

            raw_key = raw_key.strip()
            if not raw_key:
                # 保留现有 key，直接回到 picker
                msg = "未输入 key，保留现有配置"
                console.print(f"[dim]  {msg}[/dim]") if HAS_RICH else print(f"  {msg}")
                continue

            # ── 保存 ──────────────────────────────────────────────────────────
            if provider in _DATA_KEY_MAP:
                _save_data_key(provider, raw_key)
                env_var = _DATA_KEY_MAP[provider]
                os.environ[env_var] = raw_key
            else:
                pjson_fresh = _load_providers_json()
                entry = pjson_fresh.get(provider, {})
                entry["api_key"] = raw_key
                if provider in _PROVIDER_BASE_URLS:
                    entry.setdefault("base_url", _PROVIDER_BASE_URLS[provider])
                pjson_fresh[provider] = entry
                _save_providers_json(pjson_fresh)
                env_var = _PROVIDER_KEY_MAP.get(provider)
                if env_var:
                    os.environ[env_var] = raw_key
                pjson = pjson_fresh

            masked = raw_key[:6] + "****" + raw_key[-4:] if len(raw_key) > 10 else "****"
            msg = f"✓ {provider} key 已保存  ({masked})"
            console.print(f"[green]  {msg}[/green]") if HAS_RICH else print(f"  {msg}")

            # ── 连接测试 ──────────────────────────────────────────────────────
            print(f"  正在测试连接…", end="", flush=True)
            import asyncio as _aio
            loop = _aio.get_event_loop()
            ok, result_msg = await loop.run_in_executor(None, _test_api_key, provider, raw_key)
            print("\r", end="")  # 清除"正在测试"那行
            if HAS_RICH:
                color = "green" if ok else "yellow"
                console.print(f"  [{color}]{result_msg}[/{color}]")
            else:
                print(f"  {result_msg}")

            # ── 继续配置其他 provider？ ───────────────────────────────────────
            console.print() if HAS_RICH else None
            try:
                again = input("  继续配置其他 provider? (y/N) › ").strip().lower()
            except (KeyboardInterrupt, EOFError):
                again = "n"
            if again not in ("y", "yes", "是"):
                console.print("[dim]  向导已完成。输入 /apikey list 查看所有配置。[/dim]") if HAS_RICH else print("向导完成")
                return

    def cmd_providers(self, args: str):
        """Show all LLM providers: local backends + cloud API status (Open Interpreter style)."""
        if HAS_RICH:
            console.print()

        # ── Section 1: Local backends ────────────────────────────────────────
        try:
            from local_llm_provider import probe_all_backends, BACKEND_DEFAULTS
            results = probe_all_backends()
            current_provider = self.terminal.config.get("local_provider", "ollama")
            # Count Ollama models if online
            _ollama_count = ""
            if results.get("ollama"):
                try:
                    _omodels, _ = detect_ollama_models_rich(
                        self.terminal.config.get("ollama_url", "http://localhost:11434"))
                    _ollama_count = f"  [dim]{len(_omodels)} 个模型[/dim]" if _omodels else ""
                except Exception:
                    pass

            if HAS_RICH:
                console.print("  [bold]本地 Backend[/bold]")
                console.print()
            else:
                print("  == Local Backends ==")

            for name, available in results.items():
                info   = BACKEND_DEFAULTS.get(name, {})
                url    = info.get("default_url", "")
                color  = "green" if available else "dim"
                icon   = "✅" if available else "○"
                active = " ◀ active" if name == current_provider else ""
                extra  = _ollama_count if (name == "ollama" and available) else ""
                if HAS_RICH:
                    console.print(
                        f"  {icon} [{color}]{name:12s}[/{color}]"
                        f" [dim]{url:30s}[/dim]{extra}"
                        f"[green]{active}[/green]"
                    )
                else:
                    status = "✓" if available else "✗"
                    print(f"  {status} {name:12s} {url}{active}")
        except ImportError:
            pass

        # ── Section 2: Cloud provider API keys ───────────────────────────────
        pjson = _load_providers_json()
        _CLOUD_LIST = [
            # ── 国际云端 ────────────────────────────────────────────────
            ("deepseek",    "DeepSeek",      "deepseek/deepseek-chat"),
            ("anthropic",   "Anthropic",     "anthropic/claude-sonnet-4-6"),
            ("openai",      "OpenAI",        "openai/gpt-4.5"),
            ("google",      "Google Gemini", "google/gemini-2.0-flash-exp"),
            ("xai",         "xAI Grok",      "xai/grok-3-fast"),
            ("groq",        "Groq",          "groq/llama-3.3-70b-versatile"),
            ("mistral",     "Mistral",       "mistral/mistral-large-latest"),
            ("cohere",      "Cohere",        "cohere/command-r-plus"),
            ("perplexity",  "Perplexity",    "perplexity/sonar-pro"),
            ("together",    "Together",      "together/meta-llama/Meta-Llama-3.1-70B"),
            # ── 国内云端 ────────────────────────────────────────────────
            ("siliconflow", "SiliconFlow",   "siliconflow/Qwen/Qwen2.5-7B-Instruct"),
            ("dashscope",   "DashScope",     "dashscope/qwen-max"),
            ("moonshot",    "Moonshot Kimi", "moonshot/moonshot-v1-128k"),
            ("zhipu",       "Zhipu GLM",     "zhipu/glm-4-plus"),
            ("baidu",       "Baidu ERNIE",   "baidu/ernie-4.5-turbo-128k"),
            ("bytedance",   "ByteDance",     "bytedance/<endpoint-id>"),
            ("minimax",     "MiniMax",       "minimax/MiniMax-Text-01"),
            ("stepfun",     "StepFun",       "stepfun/step-2-16k"),
            ("01ai",        "01.AI Yi",      "01ai/yi-large"),
        ]
        if HAS_RICH:
            console.print()
            console.print("  [bold]Cloud Provider API[/bold]")
            console.print()
        else:
            print()
            print("  == Cloud Providers ==")

        for prov, label, example_model in _CLOUD_LIST:
            env_var = _PROVIDER_KEY_MAP.get(prov, "")
            key = (os.getenv(env_var, "") if env_var else "") or \
                  (pjson.get(prov, {}).get("api_key", "") if isinstance(pjson, dict) else "")
            if key:
                masked = key[:6] + "****" + key[-4:] if len(key) > 10 else "****"
                if HAS_RICH:
                    console.print(f"  🔑 [green]{label:14s}[/green] [dim]{masked}[/dim]")
                else:
                    print(f"  ✓ {label:14s} {masked}")
            else:
                hint = f"/apikey set {prov} <key>"
                if HAS_RICH:
                    console.print(f"  ○ [dim]{label:14s} 未配置  →  {hint}[/dim]")
                else:
                    print(f"  ✗ {label:14s} {hint}")

        # ── Custom endpoint ──────────────────────────────────────────────────
        custom_ep = self.terminal.config.get("custom_endpoint", "")
        custom_m  = self.terminal.config.get("custom_model", "")
        if custom_ep:
            if HAS_RICH:
                console.print()
                console.print(f"  🔧 [bold]Custom endpoint[/bold]  [dim]{custom_ep}[/dim]  model=[cyan]{custom_m or '?'}[/cyan]")
            else:
                print(f"\n  Custom: {custom_ep}  model={custom_m}")

        # ── Data service keys section ─────────────────────────────────────────
        _data_keys = _load_data_keys()
        _DATA_DISPLAY = [
            ("finnhub",      "Finnhub",       "股票+新闻"),
            ("newsapi",      "NewsAPI",        "财经新闻"),
            ("brave",        "Brave Search",   "网页搜索"),
            ("alphavantage", "Alpha Vantage",  "历史数据"),
            ("coingecko",    "CoinGecko Pro",  "加密数据"),
            ("twelvedata",   "Twelve Data",    "全球行情"),
        ]
        if HAS_RICH:
            console.print()
            console.print("  [bold]📊 数据服务 API[/bold]  [dim](后端离线时的本地数据源)[/dim]")
            console.print()
        else:
            print("\n  == Data Service APIs ==")
        for svc, label, desc in _DATA_DISPLAY:
            key_val = _data_keys.get(svc, "")
            if key_val:
                masked = key_val[:6] + "****" + key_val[-4:] if len(key_val) > 10 else "****"
                signup = _DATA_SIGNUP_URLS.get(svc, "")
                if HAS_RICH:
                    console.print(f"  🔑 [green]{label:18s}[/green] [dim]{masked}  {desc}[/dim]")
                else:
                    print(f"  ✓ {label:18s} {masked}")
            else:
                hint   = f"/apikey set {svc} <key>"
                signup = _DATA_SIGNUP_URLS.get(svc, "")
                if HAS_RICH:
                    console.print(f"  ○ [dim]{label:18s} 未配置  →  {hint}[/dim]")
                else:
                    print(f"  ✗ {label:18s} {hint}")

        # ── Free data source registry (akshare / yfinance / tushare) ────────────
        try:
            from datasources.router import DataRouter as _DR
            free_sources = _DR().list_sources()
        except Exception:
            free_sources = []

        if free_sources:
            if HAS_RICH:
                console.print()
                console.print("  [bold]免费行情数据源[/bold]  [dim](datasources/router — no API key required)[/dim]")
                console.print()
            else:
                print("\n  == Free Market Data Sources ==")
            for s in free_sources:
                ok_icon = "[green]✓[/green]" if s["configured"] else "[dim]○[/dim]"
                key_tag = " [dim](no key)[/dim]" if not s["needs_key"] else " [dim](API key)[/dim]"
                mkts    = ", ".join(s.get("markets", []))
                if HAS_RICH:
                    console.print(
                        f"  {ok_icon} [bold]{s['name']:12s}[/bold]  "
                        f"[dim]{mkts:22s}[/dim]{key_tag}"
                    )
                else:
                    ok   = "✓" if s["configured"] else "○"
                    key  = "(no key)" if not s["needs_key"] else "(key)"
                    print(f"  {ok} {s['name']:12s}  {mkts:22s}  {key}")
            if HAS_RICH:
                console.print("  [dim]Config: ~/.aria/datasources.yaml[/dim]")

        if HAS_RICH:
            console.print()
            console.print("  [dim]配置 LLM Key:   /apikey set deepseek <key>[/dim]")
            console.print("  [dim]配置数据 Key:   /apikey set finnhub <key>[/dim]")
            console.print("  [dim]切换模型:       /model deepseek/deepseek-chat[/dim]")
            console.print("  [dim]首次向导:       /setup[/dim]")
            console.print("  [dim]自定义端点:     /config set custom_endpoint=http://...[/dim]")
            console.print()

    async def cmd_cloud(self, args: str):
        """
        Manage Alibaba Cloud data service connection.

        Usage:
          /cloud status              — show connection status & circuit breaker state
          /cloud set <url>           — set cloud_api_server URL (e.g. http://your-aliyun-ip:8000)
          /cloud data <url>          — set akshare_data_server URL (e.g. http://your-aliyun-ip:8002)
          /cloud token <jwt-token>   — set API token
          /cloud health              — live health-check both services
          /cloud reset               — reset circuit breakers
        """
        try:
            from aliyun_data_client import AliyunDataClient, save_cloud_config, summarize_cloud_health
        except ImportError:
            if HAS_RICH:
                console.print("  [red]aliyun_data_client.py not found[/red]")
            else:
                print("  aliyun_data_client.py not found")
            return

        parts = args.strip().split(None, 2)
        sub   = parts[0].lower() if parts else "status"

        if sub == "set" and len(parts) >= 2:
            url = parts[1]
            save_cloud_config(cloud_url=url)
            AliyunDataClient.reset()
            if HAS_RICH:
                console.print(f"  [green]Cloud API URL set to: {url}[/green]")
                console.print(f"  [dim]Saved to ~/.arthera/config.json[/dim]")
            return

        if sub == "data" and len(parts) >= 2:
            url = parts[1]
            save_cloud_config(data_url=url)
            AliyunDataClient.reset()
            if HAS_RICH:
                console.print(f"  [green]AKShare Data URL set to: {url}[/green]")
                console.print(f"  [dim]Saved to ~/.arthera/config.json[/dim]")
            return

        if sub == "token" and len(parts) >= 2:
            token = parts[1]
            save_cloud_config(api_token=token)
            AliyunDataClient.reset()
            if HAS_RICH:
                console.print(f"  [green]API token saved (length {len(token)})[/green]")
            return

        if sub == "reset":
            AliyunDataClient.reset()
            if HAS_RICH:
                console.print("  [green]Circuit breakers reset, config reloaded[/green]")
            return

        client = AliyunDataClient.get()

        if sub == "health":
            if HAS_RICH:
                console.print("  [dim]Checking health…[/dim]")
            with console.status("[dim]Checking cloud services…[/dim]", spinner="dots") if HAS_RICH else _null_ctx():
                cloud_h = await client.health_cloud()
                data_h  = await client.health_data()
                st = client.status()
                summary = summarize_cloud_health(cloud_h, data_h, st)

            def _svc_label(name: str, health: dict) -> str:
                status = str(health.get("status", "?"))
                ok = status in ("healthy", "ok", "ready", "online")
                color = "green" if ok else "red"
                icon = "✓" if ok else "✗"
                breaker = st.get("cloud_cb" if name == "cloud_api_server" else "data_cb", "?")
                return f"  [{color}]●[/{color}] {name}  {icon} {status}  [dim]breaker={breaker}[/dim]"

            def _print_health_detail(title: str, health: dict):
                if HAS_RICH:
                    console.print()
                    console.print(_svc_label(title, health))
                    detail_keys = [
                        (k, v) for k, v in health.items()
                        if k not in {"status", "services", "cloud_url", "data_url"}
                    ]
                    if detail_keys:
                        for k, v in detail_keys:
                            console.print(f"    [dim]{k}: {v}[/dim]")
                    services = health.get("services") or {}
                    if services:
                        for svc, svc_status in services.items():
                            svc_ok = "online" in str(svc_status) or "ready" in str(svc_status)
                            svc_icon = "✓" if svc_ok else "○"
                            svc_color = "green" if svc_ok else "yellow"
                            console.print(f"    [dim]{svc_icon} {svc}: [{svc_color}]{svc_status}[/{svc_color}][/dim]")
                else:
                    print(f"  {title}: {health.get('status', '?')}")
                    for k, v in health.items():
                        if k not in {"status", "services", "cloud_url", "data_url"}:
                            print(f"    {k}: {v}")
                    for svc, svc_status in (health.get("services") or {}).items():
                        print(f"    {svc}: {svc_status}")

            if HAS_RICH:
                console.print()
                color = "green" if summary.status == "ok" else "yellow" if summary.status == "warn" else "red"
                console.print(f"  [bold]Summary[/bold]  [{color}]{summary.detail}[/{color}]")
                console.print(f"  [dim]breaker_open={summary.breaker_open}  token_set={summary.token_set}[/dim]")
                console.print(f"  [dim]suggestion: {summary.suggestion}[/dim]")
                console.print(f"  [dim]cloud_api_server: {client.cloud_url}[/dim]")
                console.print(f"  [dim]akshare_data_server: {client.data_url}[/dim]")
                _print_health_detail("cloud_api_server", cloud_h)
                _print_health_detail("akshare_data_server", data_h)
                console.print()
            else:
                print(f"  Summary: {summary.detail} ({summary.status})")
                print(f"  breaker_open={summary.breaker_open} token_set={summary.token_set}")
                print(f"  suggestion: {summary.suggestion}")
                _print_health_detail("cloud_api_server", cloud_h)
                _print_health_detail("akshare_data_server", data_h)
            return

        # Default: /cloud status
        st = client.status()
        if HAS_RICH:
            console.print()
            console.print("  [bold]Alibaba Cloud Data Services[/bold]")
            console.print()
            health_summary = st.get("health_summary") or {}
            color = "green" if health_summary.get("status") == "ok" else "yellow" if health_summary.get("status") == "warn" else "red"
            if health_summary:
                console.print(f"  [bold]Health[/bold]  [{color}]{health_summary.get('detail', '')}[/{color}]")
                console.print(f"  [dim]breaker_open={health_summary.get('breaker_open', 0)}  token_set={health_summary.get('token_set', False)}[/dim]")
            _c = "green" if st["cloud_cb"] == "closed" else "red"
            _d = "green" if st["data_cb"]  == "closed" else "red"
            console.print(f"  [{_c}]●[/{_c}] cloud_api_server   [dim]{st['cloud_url']}[/dim]"
                          f"  [{_c}]{st['cloud_cb']}[/{_c}]")
            console.print(f"  [{_d}]●[/{_d}] akshare_data_server [dim]{st['data_url']}[/dim]"
                          f"  [{_d}]{st['data_cb']}[/{_d}]")
            tok_str = "[green]set[/green]" if st["has_token"] else "[dim]not set[/dim]"
            console.print(f"  Auth token: {tok_str}")
            console.print()
            console.print("  [dim]Configure: /cloud set <url>  /cloud data <url>  /cloud token <jwt>[/dim]")
            console.print("  [dim]Health:    /cloud health[/dim]")
            console.print()
        else:
            health_summary = st.get("health_summary") or {}
            if health_summary:
                print(f"  Health: {health_summary.get('detail', '')} ({health_summary.get('status', '')})")
            print(f"  Cloud: {st['cloud_url']} ({st['cloud_cb']})")
            print(f"  Data:  {st['data_url']} ({st['data_cb']})")
            print(f"  Token: {'set' if st['has_token'] else 'not set'}")

    def cmd_config(self, args: str):
        """Show or set CLI configuration."""
        from apps.cli.config_paths import config_snapshot
        parts = args.strip().split(maxsplit=1)
        if not parts or parts[0] == "show":
            # Show current config
            cfg = self.terminal.config
            if HAS_RICH:
                console.print()
                console.print("[bold]Configuration[/bold]")
                console.print()
                snap = config_snapshot()
                for key in ("api_url", "ollama_url", "model", "thinking_mode",
                            "command_policy", "permission_mode", "network_enabled",
                            "write_policy", "input_style", "input_theme",
                            "auto_save_sessions"):
                    val = cfg.get(key, "-")
                    console.print(f"  [dim]{key:<24s}[/dim]{val}")
                console.print(f"  [dim]{'config_dir':<24s}[/dim]{snap['config_dir']}")
                console.print(f"  [dim]{'config_file':<24s}[/dim]{snap['config_file']}")
                console.print(f"  [dim]{'sessions_dir':<24s}[/dim]{snap['sessions_dir']}")
                console.print(f"  [dim]{'user_output_root':<24s}[/dim]{snap['user_output_root']}")
                # Show notification/search config from resolved config.json
                try:
                    import json as _j
                    _ncfg_path = Path(snap["config_file"])
                    _ncfg = _j.loads(_ncfg_path.read_text()) if _ncfg_path.exists() else {}
                    if _wh := _ncfg.get("notify_webhook"):
                        console.print(f"  [dim]{'notify_webhook':<24s}[/dim]{_wh[:50]}{'…' if len(_wh)>50 else ''}")
                except Exception:
                    pass
                import os as _os_show
                if _os_show.getenv("BRAVE_SEARCH_API_KEY"):
                    console.print(f"  [dim]{'brave_key':<24s}[/dim][green]已配置[/green]")
                else:
                    console.print(f"  [dim]{'brave_key':<24s}[/dim][dim]未设置 — /config set brave_key=BSAAxxx[/dim]")
                # Security check: warn if providers.json has plaintext api_key
                _pf = Path(snap["providers_file"])
                if _pf.exists():
                    try:
                        _pd = _j.loads(_pf.read_text())
                        _has_plain = any(
                            v.get("api_key") for v in _pd.values()
                            if isinstance(v, dict) and v.get("api_key")
                            and not str(v["api_key"]).startswith("${")
                        )
                        if _has_plain:
                            console.print()
                            console.print(
                                "  [yellow]⚠  ~/.arthera/providers.json 含明文 API Key[/yellow]\n"
                                "  [dim]  建议迁移到环境变量: export OPENAI_API_KEY=sk-...[/dim]\n"
                                "  [dim]  然后删除 providers.json 中的 api_key 字段[/dim]"
                            )
                    except Exception:
                        pass
                console.print()
            else:
                for key in ("api_url", "ollama_url", "model", "thinking_mode",
                            "command_policy", "permission_mode", "network_enabled",
                            "write_policy", "input_style", "input_theme"):
                    print(f"  {key}: {cfg.get(key, '-')}")
        elif len(parts) == 2 and parts[0] == "set":
            # Parse key=value
            kv = parts[1].split("=", 1)
            if len(kv) == 2:
                key, val = kv[0].strip(), kv[1].strip()
                # Validate known config keys
                if key == "command_policy":
                    if val not in {"safe", "balanced", "full"}:
                        msg = "command_policy must be one of: safe | balanced | full"
                        console.print(f"[red]{msg}[/red]" if HAS_RICH else msg)
                        return
                elif key == "permission_mode":
                    if val not in {"read-only", "workspace-write", "full-access"}:
                        msg = "permission_mode must be one of: read-only | workspace-write | full-access"
                        console.print(f"[red]{msg}[/red]" if HAS_RICH else msg)
                        return
                elif key in {"network_enabled", "data_sharing", "feedback_upload"}:
                    if val.lower() in {"true", "1", "yes", "on"}:
                        val = True
                    elif val.lower() in {"false", "0", "no", "off"}:
                        val = False
                    else:
                        msg = f"{key} must be: true | false"
                        console.print(f"[red]{msg}[/red]" if HAS_RICH else msg)
                        return
                elif key == "thinking_mode":
                    if val not in {"auto", "instant", "thinking"}:
                        msg = "thinking_mode must be one of: auto | instant | thinking"
                        console.print(f"[red]{msg}[/red]" if HAS_RICH else msg)
                        return
                elif key == "model":
                    resolved = MODEL_ALIASES.get(val) or (val if val in MODELS else None)
                    if not resolved:
                        valid = ", ".join(sorted(MODEL_ALIASES.keys()))
                        msg = f"Unknown model '{val}'. Valid: {valid}"
                        console.print(f"[red]{msg}[/red]" if HAS_RICH else msg)
                        return
                    val = MODELS[resolved]["id"]
                elif key == "auto_save_sessions":
                    if val.lower() in {"true", "1", "yes", "on"}:
                        val = True
                    elif val.lower() in {"false", "0", "no", "off"}:
                        val = False
                    else:
                        msg = "auto_save_sessions must be: true | false"
                        console.print(f"[red]{msg}[/red]" if HAS_RICH else msg)
                        return
                elif key == "write_policy":
                    if val not in {"desktop_only", "confirm_outside", "always_confirm"}:
                        msg = "write_policy must be: desktop_only | confirm_outside | always_confirm"
                        console.print(f"[red]{msg}[/red]" if HAS_RICH else msg)
                        return
                elif key == "local_mode":
                    if val.lower() in {"true", "1", "yes", "on"}:
                        val = True
                    elif val.lower() in {"false", "0", "no", "off"}:
                        val = False
                    else:
                        msg = "local_mode must be: true | false"
                        console.print(f"[red]{msg}[/red]" if HAS_RICH else msg)
                        return
                elif key == "banner":
                    if val not in {"full", "compact", "off"}:
                        msg = "banner must be: full | compact | off"
                        console.print(f"[red]{msg}[/red]" if HAS_RICH else msg)
                        return
                elif key == "input_style":
                    if val not in {"panel", "box", "plain"}:
                        msg = "input_style must be: panel | box | plain"
                        console.print(f"[red]{msg}[/red]" if HAS_RICH else msg)
                        return
                elif key == "input_theme":
                    if val not in {"auto", "dark", "light"}:
                        msg = "input_theme must be: auto | dark | light"
                        console.print(f"[red]{msg}[/red]" if HAS_RICH else msg)
                        return
                elif key == "ui_lang":
                    if val not in {"zh", "en", "ja", "ko", "auto"}:
                        msg = "ui_lang must be: zh | en | auto  (auto = detect from OS locale)"
                        console.print(f"[red]{msg}[/red]" if HAS_RICH else msg)
                        return
                    if val == "auto":
                        try:
                            from apps.cli.i18n import detect_system_lang as _dsl
                            val = _dsl()
                        except Exception:
                            val = "en"
                    msg = f"✓ UI 语言已设为 {val}  (重启生效)" if val == "zh" else f"✓ UI language set to {val}  (takes effect on restart)"
                    console.print(f"[green]{msg}[/green]") if HAS_RICH else print(msg)
                    self.terminal.config[key] = val
                    save_config(self.terminal.config)
                    return
                elif key == "notify_webhook":
                    # /config set notify_webhook=https://qyapi.weixin.qq.com/...
                    # 写入 ~/.arthera/config.json（notification_tools 直接读取）
                    try:
                        _ncfg_path = Path.home() / ".arthera" / "config.json"
                        _ncfg = json.loads(_ncfg_path.read_text()) if _ncfg_path.exists() else {}
                        _ncfg["notify_webhook"] = val
                        _ncfg_path.write_text(json.dumps(_ncfg, indent=2, ensure_ascii=False))
                    except Exception as _e:
                        logger.debug("notify_webhook save failed: %s", _e)
                    msg = f"✓ 通知 Webhook 已设为 {val[:60]}…" if len(val) > 60 else f"✓ 通知 Webhook 已设为 {val}"
                    console.print(f"[green]{msg}[/green]") if HAS_RICH else print(msg)
                    return
                elif key == "brave_key":
                    # /config set brave_key=BSAAxxx  → 写入 ~/.aria/.env
                    _env_path = Path.home() / ".aria" / ".env"
                    _env_path.parent.mkdir(parents=True, exist_ok=True)
                    existing = _env_path.read_text() if _env_path.exists() else ""
                    import re as _re_cfg
                    if "BRAVE_SEARCH_API_KEY" in existing:
                        existing = _re_cfg.sub(r"BRAVE_SEARCH_API_KEY=.*", f"BRAVE_SEARCH_API_KEY={val}", existing)
                    else:
                        existing = existing.rstrip("\n") + f"\nBRAVE_SEARCH_API_KEY={val}\n"
                    _env_path.write_text(existing)
                    _env_path.chmod(0o600)
                    import os as _os_cfg
                    _os_cfg.environ["BRAVE_SEARCH_API_KEY"] = val
                    msg = "✓ Brave Search API key 已保存到~/.aria/.env (生效于当前会话)"
                    console.print(f"[green]{msg}[/green]") if HAS_RICH else print(msg)
                    return
                elif key == "custom_endpoint":
                    # /config set custom_endpoint=http://my-litellm:4000/v1
                    # Automatically sets local_provider=custom
                    self.terminal.config["local_provider"] = "custom"
                    self.terminal.config["custom_endpoint"] = val
                    _sync_write_policy(self.terminal.config)
                    save_config(self.terminal.config)
                    msg = f"✓ 自定义 endpoint 设为 {val}  (local_provider=custom)"
                    console.print(f"[green]{msg}[/green]") if HAS_RICH else print(msg)
                    return
                elif key == "custom_model":
                    # /config set custom_model=gpt-4o
                    self.terminal.config["custom_model"] = val
                    if self.terminal.config.get("local_provider") == "custom":
                        self.terminal.config["model"] = val
                    _sync_write_policy(self.terminal.config)
                    save_config(self.terminal.config)
                    console.print(f"  [dim]custom_model[/dim] = {val}" if HAS_RICH else f"  custom_model = {val}")
                    return
                self.terminal.config[key] = val
                _sync_write_policy(self.terminal.config)
                save_config(self.terminal.config)
                console.print(f"  [dim]{key}[/dim] = {val}" if HAS_RICH else f"  {key} = {val}")
            else:
                console.print("[dim]Usage: /config set key=value[/dim]" if HAS_RICH
                              else "Usage: /config set key=value")
        elif parts[0] == "reload":
            fresh = load_config()
            self.terminal.config.update(fresh)
            msg = f"Config reloaded from {config_snapshot()['config_file']}"
            console.print(f"[dim]{msg}[/dim]" if HAS_RICH else msg)
        else:
            console.print("[dim]Usage: /config [show] | /config set key=value | /config reload[/dim]" if HAS_RICH
                          else "Usage: /config [show] | /config set key=value | /config reload")

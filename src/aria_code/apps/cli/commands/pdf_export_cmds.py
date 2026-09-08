"""PdfExportCommandsMixin — /export-pdf: 结构化报告 markdown -> 设计过的 PDF。

跟 apps/cli/commands/report.py 的 /report --pdf（单股票分析报告）是两条不同的
路：那条走 report_generator._build_html() 的写死布局；这条走 apps/cli/pdf_report.py
的通用 Document/Section/Theme 引擎，给的是任意结构化报告（先支持 shortterm 一种，
新增类型只需要在 pdf_report.PARSERS 里加一个解析函数）+ 可选主题 + 可选 section
取舍。两条路都复用同一个 report_generator.export_pdf() 做最终的 HTML→PDF。
"""

from __future__ import annotations


import json
import asyncio
import datetime
import time
import shlex
from typing import Dict, Any, Optional


import json
import asyncio
import datetime
import time
import shlex
import sys
import os
from typing import Dict, Any, Optional


import json
import asyncio
import datetime
import time
import shlex
import sys
import os
from typing import Dict, Any, Optional


class PdfExportCommandsMixin:
    """Mixin: /export-pdf <report.md> [--type=shortterm] [--theme=institutional|bloomberg] [--sections=a,b] [--exclude=x,y]"""

    @staticmethod
    def _parse_export_pdf_args(args: str) -> dict:
        # 模块级的自由函数在 aria_cli.py 的 _rebind_mixin_globals() 之后会失联
        # ——它把这个类每个方法的 __globals__ 换成 aria_cli.py 自己的模块命名空间
        # （为了让 self.context.console/self.context.has_rich 这类裸名字能解析到），代价是原模块里定义的
        # 兄弟函数就不再可见了。写成 staticmethod 走 self._xxx 属性查找，不受影响。
        parts = args.split()
        out = {"path": None, "type": "shortterm", "theme": "institutional", "include": None, "exclude": None}
        for p in parts:
            if p.startswith("--theme="):
                out["theme"] = p.split("=", 1)[1].strip()
            elif p.startswith("--type="):
                out["type"] = p.split("=", 1)[1].strip()
            elif p.startswith("--sections="):
                out["include"] = [s.strip() for s in p.split("=", 1)[1].split(",") if s.strip()]
            elif p.startswith("--exclude="):
                out["exclude"] = [s.strip() for s in p.split("=", 1)[1].split(",") if s.strip()]
            elif not p.startswith("--") and out["path"] is None:
                out["path"] = p
        return out

    async def cmd_export_pdf(self, args: str):
        # 全部用局部 import：模块顶层的 import 在 _rebind_mixin_globals() 之后
        # 同样会失联（原因同上面 staticmethod 那条注释），局部 import 绑定到
        # 函数自己的局部命名空间，不受影响。
        from pathlib import Path
        from aria_code.apps.cli.pdf_report import PARSERS, THEMES, render_document
        from aria_code.artifacts import user_generated_dir

        opts = self._parse_export_pdf_args(args.strip())
        if not opts["path"]:
            self.context.console.print("[dim]用法: /export-pdf <report.md> [--type=shortterm] [--theme=institutional|bloomberg] [--sections=a,b] [--exclude=x,y][/dim]"
                          if self.context.has_rich else "用法: /export-pdf <report.md> [--type=...] [--theme=...] [--sections=...] [--exclude=...]")
            return

        src = Path(opts["path"]).expanduser()
        if not src.exists():
            self.context.console.print(f"[red]找不到文件: {src}[/red]" if self.context.has_rich else f"找不到文件: {src}")
            return

        parser = PARSERS.get(opts["type"])
        if parser is None:
            avail = ", ".join(PARSERS.keys())
            self.context.console.print(f"[red]未知报告类型 '{opts['type']}'，目前支持: {avail}[/red]" if self.context.has_rich
                          else f"未知报告类型 '{opts['type']}'，目前支持: {avail}")
            return

        theme = THEMES.get(opts["theme"])
        if theme is None:
            avail = ", ".join(THEMES.keys())
            self.context.console.print(f"[red]未知主题 '{opts['theme']}'，目前支持: {avail}[/red]" if self.context.has_rich
                          else f"未知主题 '{opts['theme']}'，目前支持: {avail}")
            return

        try:
            doc = parser(src.read_text(encoding="utf-8"))
            if opts["include"] or opts["exclude"]:
                doc = doc.select(include=opts["include"], exclude=opts["exclude"])
            if not doc.sections:
                self.context.console.print("[yellow]筛选后没有剩下任何 section，检查 --sections/--exclude 关键词是否匹配得上标题[/yellow]"
                              if self.context.has_rich else "筛选后没有剩下任何 section，检查 --sections/--exclude 关键词")
                return

            html_doc = render_document(doc, theme)
            out_dir = user_generated_dir()
            html_path = out_dir / f"{src.stem}.pdfexport.html"
            html_path.write_text(html_doc, encoding="utf-8")

            from aria_code.report_generator import export_pdf
            import asyncio
            pdf_path = await asyncio.get_event_loop().run_in_executor(None, lambda: export_pdf(html_path))
            html_path.unlink(missing_ok=True)  # 中间产物，转完就不需要留着

            if pdf_path is None:
                self.context.console.print("[red]PDF 转换失败 —— 需要 weasyprint 或系统装了 wkhtmltopdf[/red]" if self.context.has_rich
                              else "PDF 转换失败 —— 需要 weasyprint 或系统装了 wkhtmltopdf")
                return

            msg = f"✓ PDF 已生成 ({theme.name} 主题, {len(doc.sections)} 个 section): {pdf_path}"
            self.context.console.print(f"[green]{msg}[/green]" if self.context.has_rich else msg)
        except Exception as e:
            self.context.console.print(f"[red]渲染失败: {e}[/red]" if self.context.has_rich else f"渲染失败: {e}")

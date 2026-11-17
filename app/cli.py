"""论文翻译大师命令行入口：paperbridge convert / serve / list / show / clean。

对应方案 §10.1：同时提供 CLI 和本地 Web 界面。
"""

from __future__ import annotations

import json
import logging
import sys
import webbrowser
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from app.config import get_settings
from app.pipeline.orchestrator import ConvertOptions, Orchestrator, ConversionResult

app = typer.Typer(
    name="paper-translation-master",
    help="论文翻译大师：论文 OCR、Markdown 与大模型翻译",
    no_args_is_help=True,
)
console = Console()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)


def _result_to_dict(r: ConversionResult) -> dict:
    return {
        "ok": r.ok,
        "status": r.status,
        "document_id": r.document_id,
        "document_dir": str(r.doc_dir) if r.doc_dir else None,
        "blocks": len(r.ir.blocks) if r.ir else 0,
        "warnings": r.warnings,
        "errors": r.errors,
        "duration_ms": r.duration_ms,
    }


def _print_result(r: ConversionResult, json_out: bool) -> None:
    if json_out:
        console.print_json(data=_result_to_dict(r))
        return
    if r.ok:
        console.print(f"[green]✓ 转换完成[/green] 状态：{r.status}")
        console.print(f"  文档 ID：{r.document_id}")
        console.print(f"  阅读页：{r.doc_dir / 'index.html'}")
        if r.ir:
            console.print(f"  内容块：{len(r.ir.blocks)} 个")
        for w in r.warnings[:20]:
            console.print(f"[yellow]  ⚠ {w}[/yellow]")
        if r.status == "needs_review":
            console.print("[yellow]  部分内容需人工核对（低可信度公式/表格）[/yellow]")
    else:
        console.print("[red]✗ 转换失败[/red]")
        for e in r.errors[:20]:
            console.print(f"[red]  ✗ {e}[/red]")
    if r.duration_ms:
        console.print(f"  耗时：{r.duration_ms / 1000:.1f}s")


@app.command()
def convert(
    pdf: Path = typer.Argument(..., help="论文 PDF 路径", exists=True),
    ocr_mode: str = typer.Option(
        "official", "--ocr-mode", help="official | rapidocr | auto | native | full | hybrid",
        show_default=True,
    ),
    device: str = typer.Option("auto", "--device", help="auto | cpu | gpu"),
    pages: str = typer.Option("", "--pages", help='页码范围，如 "1-5,8"'),
    dpi: int = typer.Option(220, "--dpi", help="渲染 DPI", show_default=True),
    force: bool = typer.Option(False, "--force", help="忽略缓存重新解析"),
    no_cache: bool = typer.Option(False, "--no-cache", help="不使用缓存（等价 force）"),
    json_out: bool = typer.Option(False, "--json", help="以 JSON 输出结果"),
    open_reader: bool = typer.Option(False, "--open", help="完成后在浏览器打开阅读页"),
) -> None:
    """把一个 PDF 转换成本地 HTML 阅读页。"""
    settings = get_settings()
    opts = ConvertOptions(
        ocr_mode=ocr_mode,
        ocr_device=device,
        render_dpi=dpi,
        pages=pages,
        force=force or no_cache,
    )
    orch = Orchestrator(settings.workspace)
    result = orch.convert(pdf, opts, progress=_progress)
    _print_result(result, json_out)
    if result.ok and open_reader:
        url = f"http://{settings.host}:{settings.port}/documents/{result.document_id}/"
        console.print(f"打开：{url}")
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001
            console.print(f"请手动打开：{url}")
    sys.exit(0 if result.ok else 1)


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8765, "--port", help="本地服务端口"),
    open_browser: bool = typer.Option(False, "--open", help="启动后打开 Web 界面"),
) -> None:
    """启动本地 Web 服务（任务上传、文档列表、阅读页）。"""
    from app.server import run_server

    url = f"http://{host}:{port}/"
    console.print(f"[green]论文翻译大师服务启动：{url}[/green]")
    console.print("按 Ctrl+C 停止")
    if open_browser:
        webbrowser.open(url)
    run_server(host=host, port=port)


@app.command("list")
def list_documents(json_out: bool = typer.Option(False, "--json")) -> None:
    """列出已生成的文档。"""
    from app.server import document_store

    settings = get_settings()
    docs = document_store(settings).list()
    if json_out:
        console.print_json(data=docs)
        return
    if not docs:
        console.print("（暂无文档）")
        return
    table = Table(title="已生成的文档")
    table.add_column("ID")
    table.add_column("文件")
    table.add_column("页数")
    table.add_column("块数")
    table.add_column("状态")
    table.add_column("生成时间")
    for d in docs:
        table.add_row(
            d["id"][:12],
            d.get("source_pdf", ""),
            str(d.get("page_count", "")),
            str(d.get("blocks", "")),
            d.get("status", ""),
            str(d.get("created_at", ""))[:19],
        )
    console.print(table)


@app.command()
def show(
    document_id: str = typer.Argument(..., help="文档 ID（可为前缀）"),
    open_reader: bool = typer.Option(False, "--open"),
) -> None:
    """显示文档信息和阅读页地址。"""
    from app.server import document_store

    settings = get_settings()
    doc = document_store(settings).get(document_id)
    if not doc:
        console.print(f"[red]未找到文档：{document_id}[/red]")
        sys.exit(1)
    url = f"http://{settings.host}:{settings.port}/documents/{doc['id']}/"
    console.print(f"文档 ID：{doc['id']}")
    console.print(f"来源：{doc.get('source_pdf', '')}")
    console.print(f"阅读页：{url}")
    console.print(f"本地文件：{doc['dir'] / 'index.html'}")
    if open_reader:
        webbrowser.open(url)


@app.command()
def clean(
    older_than_days: int = typer.Option(30, "--older-than-days"),
    dry_run: bool = typer.Option(True, "--dry-run/--no-dry-run"),
) -> None:
    """清理超过 N 天的文档（含 PDF、页面图像、IR 与缓存）。"""
    import shutil
    import time

    settings = get_settings()
    docs_dir = settings.documents_dir
    cutoff = time.time() - older_than_days * 86400
    removed = 0
    for d in docs_dir.iterdir():
        if not d.is_dir():
            continue
        if d.stat().st_mtime > cutoff:
            continue
        if dry_run:
            console.print(f"[yellow]将删除：{d.name}[/yellow]")
        else:
            shutil.rmtree(d, ignore_errors=True)
            console.print(f"[red]已删除：{d.name}[/red]")
        removed += 1
    console.print(f"共 {removed} 个文档{'（模拟）' if dry_run else ''}。")


def _progress(stage: str, page: int, total: int, message: str) -> None:
    if message:
        console.print(f"  [dim]{stage}[/dim] {message}")


def main() -> None:
    app()


if __name__ == "__main__":
    main()

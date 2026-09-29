from __future__ import annotations

import importlib
import pkgutil

from fastmcp import FastMCP

from . import tools as _tools_pkg


def create_server() -> FastMCP:
    """构建 FastMCP server 并自动注册 tools/ 包下的所有工具模块。

    新增工具只需在 tools/ 下加一个模块并定义 register(mcp)，无需改动本文件。
    """
    mcp = FastMCP("jev_harness_tool")
    for module_info in pkgutil.iter_modules(_tools_pkg.__path__):
        module = importlib.import_module(f"{_tools_pkg.__name__}.{module_info.name}")
        register = getattr(module, "register", None)
        if callable(register):
            register(mcp)
    return mcp


mcp = create_server()


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()

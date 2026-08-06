"""兼容入口；实际实现位于 scripts.canon.split_canon_chapters。"""

if __name__ == "__main__":
    import runpy

    runpy.run_module("scripts.canon.split_canon_chapters", run_name="__main__")
else:
    import sys

    from scripts.canon import split_canon_chapters as _implementation

    # 让旧模块名与新模块共享全局状态，兼容 monkeypatch 和旧导入。
    sys.modules[__name__] = _implementation

"""兼容入口；实际实现位于 scripts.canon.translate_huayan_segments。"""

if __name__ == "__main__":
    import runpy

    runpy.run_module("scripts.canon.translate_huayan_segments", run_name="__main__")
else:
    import sys

    from scripts.canon import translate_huayan_segments as _implementation

    # 让旧模块名与新模块共享全局状态，兼容 monkeypatch 和旧导入。
    sys.modules[__name__] = _implementation

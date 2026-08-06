"""兼容入口；实际实现位于 scripts.quality.audit_data。"""

if __name__ == "__main__":
    import runpy

    runpy.run_module("scripts.quality.audit_data", run_name="__main__")
else:
    import sys

    from scripts.quality import audit_data as _implementation

    # 让旧模块名与新模块共享全局状态，兼容 monkeypatch 和旧导入。
    sys.modules[__name__] = _implementation

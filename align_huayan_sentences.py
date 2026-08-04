"""兼容入口；实际实现位于 scripts.canon.align_huayan_sentences。"""

if __name__ == "__main__":
    import runpy

    runpy.run_module("scripts.canon.align_huayan_sentences", run_name="__main__")
else:
    from scripts.canon.align_huayan_sentences import *  # noqa: F401,F403

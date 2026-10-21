"""版本号一致性测试。

背景：脚本里曾同时存在「v2.0」（启动横幅）和 Release 的「v1.0.x」两套版本，
用户无法确认手上的包到底是哪一版。现在版本号只在 config.py 维护一处，
横幅、--version、EXE 文件属性都由它派生，本测试守住这一点。

用法：
    python scripts/test_version.py
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config import VERSION  # noqa: E402

FAILED = []


def check(name, got, want):
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'} {name}: got={got!r} want={want!r}")
    if not ok:
        FAILED.append(name)


def main():
    print(f"\n--- 版本号一致性（当前 VERSION = {VERSION}）---")

    check("格式合法（X.Y.Z）", bool(re.fullmatch(r"\d+\.\d+\.\d+", VERSION)), True)

    main_src = (ROOT / "main.py").read_text(encoding="utf-8")
    check("横幅使用 VERSION 而非写死", "自动抢购脚本 v{VERSION}" in main_src, True)
    check("横幅里没有残留 v2.0", "v2.0" in main_src, False)
    check("支持 --version", '"--version"' in main_src, True)

    spec_src = (ROOT / "LighthouseKeeper.spec").read_text(encoding="utf-8")
    check("打包脚本从 config.py 读版本", "_read_version()" in spec_src, True)
    check("EXE 嵌入版本资源", "version=str(_version_file)" in spec_src, True)

    # 版本总表：README 里若写了版本号，必须与 VERSION 一致
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    versions = set(re.findall(r"当前版本[:：]\s*v?(\d+\.\d+\.\d+)", readme))
    if versions:
        check("README 标注的版本与 VERSION 一致", versions, {VERSION})
    else:
        print("SKIP README 未标注当前版本")

    if FAILED:
        print(f"\n失败 {len(FAILED)} 项: {FAILED}")
        return 1
    print("\n全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())

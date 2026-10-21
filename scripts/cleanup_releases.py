"""清理旧的 GitHub Release 与 tag，只保留当前版本。

token 由调用方通过环境变量 GH_TOKEN 传入（不落盘、不打印）：
    GH_TOKEN=$(printf 'protocol=https\\nhost=github.com\\n\\n' | git credential fill \\
        | sed -n 's/^password=//p') python scripts/cleanup_releases.py --apply

不带 --apply 时只预览。
"""
import json
import os
import sys
import urllib.error
import urllib.request

OWNER = "Lbcheng1145"
REPO = "Lighthouse-Keeper"
API = f"https://api.github.com/repos/{OWNER}/{REPO}"

KEEP = os.environ.get("KEEP_TAG", "v1.1.1")
CANDIDATES = ["v1.0.0", "v1.0.1", "v1.0.2", "v1.0.3", "v1.1.0", "v1.1.1", "v1.2.0"]


def call(method, url):
    req = urllib.request.Request(url, method=method)
    req.add_header("Authorization", f"token {os.environ['GH_TOKEN']}")
    req.add_header("Accept", "application/vnd.github+json")
    try:
        with urllib.request.urlopen(req, timeout=40) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")[:200]


def main():
    apply = "--apply" in sys.argv
    if not os.environ.get("GH_TOKEN"):
        raise SystemExit("缺少 GH_TOKEN")

    status, releases = call("GET", f"{API}/releases?per_page=100")
    if status != 200:
        raise SystemExit(f"列 Release 失败: {status} {releases}")
    status, tags = call("GET", f"{API}/tags?per_page=100")
    tag_names = [t["name"] for t in tags] if status == 200 else []

    print(f"现有 Release: {[r['tag_name'] for r in releases]}")
    print(f"现有 tag: {tag_names}")
    print(f"保留: {KEEP}\n")

    for rel in releases:
        name = rel["tag_name"]
        if name == KEEP or name not in CANDIDATES:
            print(f"- 跳过 Release {name}（保留或不在清理范围）")
            continue
        if not apply:
            print(f"- 将删除 Release {name} (id={rel['id']})")
            continue
        st, _ = call("DELETE", f"{API}/releases/{rel['id']}")
        print(f"- 删除 Release {name}: HTTP {st}")

    for name in tag_names:
        if name == KEEP or name not in CANDIDATES:
            continue
        if not apply:
            print(f"- 将删除 tag {name}")
            continue
        st, _ = call("DELETE", f"{API}/git/refs/tags/{name}")
        print(f"- 删除 tag {name}: HTTP {st}")

    if not apply:
        print("\n（以上为预览，加 --apply 才真正执行）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

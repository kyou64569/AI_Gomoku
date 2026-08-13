"""一次性迁移脚本：把 gomoku.db 中明文存储的 API Key 迁移到 backend/.env。

用法（在 backend 目录下）：
    python migrate_keys_to_env.py

行为：
1. 读取 model_configs 表里非空的 api_key；
2. 写入/追加到 .env（LLM_API_KEY_{id}=xxx，已存在则不覆盖）；
3. 将数据库中的 api_key 字段置空（此后运行期从环境变量读取）。

注意：
- 请在服务停止时运行（stop.bat），运行后需重启服务生效。
- .env 已被 .gitignore 排除，请妥善保管。
"""
import os
import sys
import sqlite3

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gomoku.db")
ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")


def main():
    if not os.path.exists(DB_PATH):
        print(f"[跳过] 未找到数据库: {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    rows = cur.execute("SELECT id, name, api_key FROM model_configs WHERE api_key != ''").fetchall()

    if not rows:
        print("[跳过] model_configs 表中没有明文 api_key，无需迁移。")
        conn.close()
        return

    env_lines = {}
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    env_lines[k.strip()] = v.strip()

    for cid, name, key in rows:
        var = f"LLM_API_KEY_{cid}"
        if var in env_lines and env_lines[var]:
            print(f"[保留] {var} 已存在于 .env，跳过写入")
        else:
            env_lines[var] = key
            print(f"[写入] {var}（配置: {name}）")

    with open(ENV_PATH, "w", encoding="utf-8") as f:
        for k, v in env_lines.items():
            f.write(f"{k}={v}\n")
    print(f"[完成] 密钥已写入 {ENV_PATH}")

    # 清空数据库中的明文 key
    cur.execute("UPDATE model_configs SET api_key = '' WHERE api_key != ''")
    conn.commit()
    conn.close()
    print(f"[完成] 已清空数据库 {rows.__len__()} 条 api_key 字段（重启服务后生效）")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[错误] {e}", file=sys.stderr)
        sys.exit(1)

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
福祉・介護ニュースダイジェスト → WordPress自動投稿
  - RSS取得 : Google ニュース（介護 / 福祉 / 助成金）
  - AI生成  : Ollama (Gemma) ← ngrok 経由で requests 送信
  - 投稿先  : WordPress REST API（下書き保存）
  - 重複管理: SQLite
"""

import os
import sqlite3
import requests
import feedparser
import base64
import time
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ── 設定 ────────────────────────────────────────────────────────────────────
OLLAMA_NGROK_URL = os.getenv("OLLAMA_NGROK_URL", "http://localhost:11434")
OLLAMA_MODEL     = os.getenv("OLLAMA_MODEL", "gemma2:9b")

WP_URL          = os.getenv("WP_URL", "")
WP_USERNAME     = os.getenv("WP_USERNAME", "")
WP_APP_PASSWORD = os.getenv("WP_APP_PASSWORD", "")

DB_PATH = Path(__file__).parent / "output" / "history.db"

MAX_ARTICLES_PER_FEED = 1   # 1フィードあたりの最大処理件数
AI_TIMEOUT            = 120  # Ollamaリクエストのタイムアウト（秒）
WP_TIMEOUT            = 30   # WordPress APIのタイムアウト（秒）
QUALITY_THRESHOLD     = 60   # この点数未満は下書き投稿しない（100点満点）

# ── RSSフィード一覧（Google ニュース）──────────────────────────────────────
RSS_FEEDS = [
    {
        "name": "介護ニュース",
        "url": "https://news.google.com/rss/search?q=介護+事業所&hl=ja&gl=JP&ceid=JP:ja",
        "category": "介護",
    },
    {
        "name": "福祉ニュース",
        "url": "https://news.google.com/rss/search?q=福祉+事業所&hl=ja&gl=JP&ceid=JP:ja",
        "category": "福祉",
    },
    {
        "name": "助成金ニュース",
        "url": "https://news.google.com/rss/search?q=福祉+介護+助成金&hl=ja&gl=JP&ceid=JP:ja",
        "category": "助成金",
    },
]


# ── データベース（重複送信防止）───────────────────────────────────────────
def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sent_items (
                url     TEXT PRIMARY KEY,
                sent_at TEXT NOT NULL
            )
        """)


def is_sent(url: str) -> bool:
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT 1 FROM sent_items WHERE url = ?", (url,)
        ).fetchone()
        return row is not None


def mark_sent(url: str) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO sent_items (url, sent_at) VALUES (?, ?)",
            (url, datetime.now().isoformat()),
        )


# ── RSS取得 ──────────────────────────────────────────────────────────────────
def fetch_articles(feed: dict) -> list[dict]:
    """RSSフィードから未送信の記事を最大 MAX_ARTICLES_PER_FEED 件返す"""
    try:
        parsed = feedparser.parse(feed["url"])
    except Exception as e:
        print(f"  [RSS] {feed['name']} 取得エラー: {e}")
        return []

    articles = []
    for entry in parsed.entries:
        if len(articles) >= MAX_ARTICLES_PER_FEED:
            break
        url = entry.get("link", "")
        if not url or is_sent(url):
            continue
        articles.append({
            "title"    : entry.get("title", "（タイトルなし）"),
            "url"      : url,
            "summary"  : entry.get("summary", entry.get("description", "")),
            "published": entry.get("published", ""),
            "category" : feed["category"],
        })

    print(f"  [RSS] {feed['name']}: {len(articles)} 件取得")
    return articles


# ── Ollama (Gemma) によるブログ記事生成 ──────────────────────────────────────

SYSTEM_PROMPT = """あなたは福祉・介護業界の専門ライターです。
福祉事業所・介護事業所のスタッフや管理者が読む、専門的でわかりやすいブログ記事を執筆します。
文体は「です・ます」調の丁寧な日本語を使用してください。

記事は必ず以下のHTML構成で出力してください（Markdownは使わないこと）。

<title>（目を引く、具体的な記事タイトル）</title>

<p>（導入文：なぜこのニュースが現場で重要なのか、背景を2〜3文で説明）</p>

<h2>ニュースの要点</h2>
<p>（詳細な解説を2〜3段落。数値や制度名があれば必ず明記する）</p>

<h2>現場への影響と活用ポイント</h2>
<ul>
  <li>（具体的な実務への影響や対応策）</li>
  <li>（スタッフや管理者が意識すべき注意点）</li>
  <li>（すぐに活かせる実践的なヒント）</li>
</ul>

<h2>まとめ</h2>
<p>（今後の展望と読者へのメッセージを1〜2段落）</p>
"""


def generate_article(article: dict) -> dict | None:
    """Ollama (Gemma) でブログ記事HTMLを生成し、タイトルと本文を返す"""
    user_prompt = (
        f"以下のニュース記事を元に、福祉・介護事業所のスタッフ向けブログ記事を作成してください。\n\n"
        f"【ニュースタイトル】\n{article['title']}\n\n"
        f"【ニュース概要】\n{article['summary'][:1500] or '（概要なし）'}\n\n"
        f"【元記事URL】\n{article['url']}\n\n"
        f"【カテゴリ】\n{article['category']}\n\n"
        "現場で働くスタッフが実務に直結した気づきを得られる内容にしてください。"
    )

    payload = {
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": user_prompt},
        ],
        "stream": False,
        "options": {
            "temperature": 0.7,
            "num_predict": 2048,
        },
    }

    endpoint = f"{OLLAMA_NGROK_URL.rstrip('/')}/api/chat"

    try:
        print(f"    [AI] POST → {endpoint}")
        print(f"    [AI] 記事生成中: {article['title'][:50]}...")
        resp = requests.post(endpoint, json=payload, timeout=AI_TIMEOUT)
        print(f"    [AI] HTTP status: {resp.status_code}")
        resp.raise_for_status()
        content = resp.json().get("message", {}).get("content", "").strip()

        if not content:
            print("    [AI] レスポンスが空でした")
            print(f"    [AI] Raw response: {resp.text[:300]}")
            return None

        # <title>タグからWordPress投稿タイトルを抽出
        wp_title = article["title"]  # デフォルトはRSSタイトル
        if "<title>" in content and "</title>" in content:
            t_start = content.index("<title>") + len("<title>")
            t_end   = content.index("</title>")
            wp_title = content[t_start:t_end].strip()
            content  = (content[:content.index("<title>")] + content[t_end + len("</title>"):]).strip()

        # 出典リンクを末尾に追加
        content += (
            f'\n<p><small>出典: <a href="{article["url"]}" '
            f'target="_blank" rel="noopener noreferrer">'
            f'{article["title"]}</a></small></p>'
        )

        return {
            "title"     : wp_title,
            "content"   : content,
            "category"  : article["category"],
            "source_url": article["url"],
        }

    except requests.exceptions.Timeout:
        print(f"    [AI] タイムアウト（{AI_TIMEOUT}秒超過）")
        return None
    except requests.exceptions.HTTPError as e:
        print(f"    [AI] HTTPエラー: {e}")
        print(f"    [AI] Response body: {resp.text[:500]}")
        return None
    except requests.exceptions.RequestException as e:
        print(f"    [AI] リクエストエラー: {e}")
        return None


# ── 品質チェック ─────────────────────────────────────────────────────────────

QUALITY_CHECK_PROMPT = """あなたは福祉・介護業界の専門編集者です。
以下のブログ記事を5つの評価基準でそれぞれ0〜20点で採点し、合計点と理由を出力してください。

【評価基準】
① 対象読者の一致（0〜20点）：福祉事業所の経営者・オーナー・管理者・スタッフが主な読者か。事業継続・経営判断に直結する内容か。
② 制度・法令への関連性（0〜20点）：介護・障害福祉の報酬改定、義務化事項、運営指導、厚労省通知など制度面のトピックを扱うか。
③ 実務的有用性（0〜20点）：BCP策定、地域連携推進会議、体制届、加算算定など現場で即活用できる情報・ノウハウが含まれるか。
④ 協会活動との親和性（0〜20点）：研修・勉強会・作業会・セミナーといった協会が提供するイベント・支援サービスと連動する内容か。
⑤ 障害・介護福祉の専門性（0〜20点）：グループホーム、生活介護、訪問看護など特定サービス種別の専門知識・事例を含む深みがあるか。

必ず以下のフォーマットだけを出力してください（余計な文章は不要）：

SCORE_1=<数値>
SCORE_2=<数値>
SCORE_3=<数値>
SCORE_4=<数値>
SCORE_5=<数値>
TOTAL=<合計数値>
REASON=<不合格の場合は改善点を1文、合格の場合は「合格」>
"""


def check_article_quality(generated: dict) -> tuple[int, str]:
    """生成記事を5基準で採点し (合計点, 理由) を返す。API失敗時は (100, チェック不能) で通過"""
    user_prompt = (
        f"【記事タイトル】\n{generated['title']}\n\n"
        f"【記事本文】\n{generated['content'][:3000]}"
    )

    payload = {
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": QUALITY_CHECK_PROMPT},
            {"role": "user",   "content": user_prompt},
        ],
        "stream": False,
        "options": {
            "temperature": 0.2,
            "num_predict": 256,
        },
    }

    endpoint = f"{OLLAMA_NGROK_URL.rstrip('/')}/api/chat"

    try:
        print("    [CHECK] 品質チェック中...")
        resp = requests.post(endpoint, json=payload, timeout=AI_TIMEOUT)
        resp.raise_for_status()
        raw = resp.json().get("message", {}).get("content", "").strip()

        total  = 0
        reason = "解析不能"
        for line in raw.splitlines():
            line = line.strip()
            if line.startswith("TOTAL="):
                try:
                    total = int(line.split("=", 1)[1].strip())
                except ValueError:
                    pass
            elif line.startswith("REASON="):
                reason = line.split("=", 1)[1].strip()

        print(f"    [CHECK] スコア: {total}/100  理由: {reason}")
        return total, reason

    except requests.exceptions.RequestException as e:
        print(f"    [CHECK] チェックエラー: {e} → 通過扱いにします")
        return 100, "チェック不能のため通過"


# ── WordPress 投稿 ───────────────────────────────────────────────────────────
def post_to_wordpress(generated: dict) -> bool:
    """生成した記事をWordPressに「下書き」として投稿する"""
    if not all([WP_URL, WP_USERNAME, WP_APP_PASSWORD]):
        print("    [WP] .env の WordPress設定が不足しています")
        return False

    token = base64.b64encode(
        f"{WP_USERNAME}:{WP_APP_PASSWORD}".encode("utf-8")
    ).decode("utf-8")

    headers = {
        "Authorization": f"Basic {token}",
        "Content-Type" : "application/json",
    }

    payload = {
        "title"  : generated["title"],
        "content": generated["content"],
        "status" : "draft",
    }

    endpoint = f"{WP_URL.rstrip('/')}/wp-json/wp/v2/posts"

    try:
        print(f"    [WP] POST → {endpoint}")
        resp = requests.post(endpoint, json=payload, headers=headers, timeout=WP_TIMEOUT)
        print(f"    [WP] HTTP status: {resp.status_code}")
        resp.raise_for_status()
        data    = resp.json()
        post_id = data.get("id", "不明")
        link    = data.get("link", "")
        print(f"    [WP] 下書き投稿成功 → ID: {post_id}  {link}")
        return True
    except requests.exceptions.HTTPError as e:
        print(f"    [WP] HTTPエラー: {e}")
        print(f"    [WP] Response body: {resp.text[:500]}")
        return False
    except requests.exceptions.RequestException as e:
        print(f"    [WP] リクエストエラー: {e}")
        return False


# ── メイン処理 ───────────────────────────────────────────────────────────────
def main() -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print("=" * 54)
    print(f"  福祉・介護ニュースダイジェスト  {now}")
    print("=" * 54)

    # ── 起動時の設定確認（パスワード類は伏字）──────────────────
    print("\n[CONFIG]")
    print(f"  OLLAMA_NGROK_URL : {OLLAMA_NGROK_URL}")
    print(f"  OLLAMA_MODEL     : {OLLAMA_MODEL}")
    print(f"  WP_URL           : {WP_URL}")
    print(f"  WP_USERNAME      : {WP_USERNAME}")
    print(f"  WP_APP_PASSWORD  : {'SET' if WP_APP_PASSWORD else 'NOT SET'}")
    print()

    init_db()

    total_posted = 0
    total_skipped = 0
    total_failed  = 0

    for feed in RSS_FEEDS:
        print(f"\n【{feed['name']}】")
        articles = fetch_articles(feed)

        for article in articles:
            print(f"  処理: {article['title'][:55]}...")

            generated = generate_article(article)
            if generated is None:
                total_failed += 1
                continue

            score, reason = check_article_quality(generated)
            if score < QUALITY_THRESHOLD:
                print(f"    [CHECK] 不合格（{score}点 / 基準{QUALITY_THRESHOLD}点）→ 下書き投稿をスキップ")
                print(f"    [CHECK] 改善点: {reason}")
                total_skipped += 1
                continue

            success = post_to_wordpress(generated)
            if success:
                mark_sent(article["url"])
                total_posted += 1
            else:
                total_failed += 1

            time.sleep(2)  # API 負荷軽減

    print("\n" + "=" * 54)
    print(f"  投稿成功 : {total_posted} 件（WordPress 下書き）")
    print(f"  スキップ : {total_skipped} 件（品質基準{QUALITY_THRESHOLD}点未満）")
    print(f"  失敗     : {total_failed} 件")
    print("=" * 54)


if __name__ == "__main__":
    main()

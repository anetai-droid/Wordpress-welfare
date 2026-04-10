import os
import base64
import datetime
import sqlite3
import feedparser
import requests
from pathlib import Path
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
CHATWORK_API_TOKEN = os.getenv("CHATWORK_API_TOKEN")
CHATWORK_ROOM_ID = os.getenv("CHATWORK_ROOM_ID")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")

DB_PATH = Path("output/history.db")


# ─────────────────────────────────────────────
# 状態管理（SQLite による重複送信防止）
# ─────────────────────────────────────────────

def init_db():
    """DBとテーブルを初期化する（存在しない場合のみ作成）"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sent_items (
                url TEXT PRIMARY KEY,
                sent_at TEXT NOT NULL
            )
        """)


def is_sent(url: str) -> bool:
    """URLがすでに送信済みか確認する"""
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT 1 FROM sent_items WHERE url = ?", (url,)).fetchone()
        return row is not None


def mark_sent(url: str):
    """URLを送信済みとして記録する"""
    sent_at = datetime.datetime.now().isoformat()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("INSERT INTO sent_items (url, sent_at) VALUES (?, ?)", (url, sent_at))

openai_client = OpenAI(api_key=OPENAI_API_KEY)


# ─────────────────────────────────────────────
# Step 1: RSS ニュース取得 & 要約
# ─────────────────────────────────────────────

RSS_FEEDS = [
    ("Zenn", "https://zenn.dev/feed"),
    ("Qiita", "https://qiita.com/popular-items/feed"),
    ("TechCrunch(US)", "https://techcrunch.com/feed/"),
]


def fetch_top_article(name: str, url: str) -> dict | None:
    """RSSフィードから最新1件を取得する"""
    try:
        feed = feedparser.parse(url)
        if not feed.entries:
            print(f"[WARN] {name}: エントリなし")
            return None
        entry = feed.entries[0]
        return {
            "source": name,
            "title": entry.get("title", "(タイトルなし)"),
            "link": entry.get("link", ""),
            "summary": entry.get("summary", entry.get("description", "")),
        }
    except Exception as e:
        print(f"[ERROR] {name} RSS取得失敗: {e}")
        return None


def summarize_article(article: dict) -> str:
    """OpenAI API で記事を日本語要約する"""
    prompt = (
        "以下の技術記事の内容を日本語で簡潔に要約してください。\n"
        "必ず以下のフォーマットで出力してください。\n\n"
        "【コア技術・課題】: \n"
        "【結論・知見】: \n\n"
        f"タイトル: {article['title']}\n"
        f"本文（抜粋）: {article['summary'][:2000]}"
    )
    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=400,
            temperature=0.3,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"[ERROR] OpenAI 要約失敗 ({article['source']}): {e}")
        return "（要約取得に失敗しました）"


def collect_news_summaries() -> list[dict]:
    results = []
    for name, url in RSS_FEEDS:
        article = fetch_top_article(name, url)
        if article is None:
            continue
        link = article["link"]
        if is_sent(link):
            print(f"[SKIP] {name}: 送信済みのためスキップ ({link})")
            continue
        article["ai_summary"] = summarize_article(article)
        mark_sent(link)
        results.append(article)
    return results


# ─────────────────────────────────────────────
# Step 2: GitHub トレンドリポジトリ取得 & 解析
# ─────────────────────────────────────────────

def fetch_trending_repo() -> dict | None:
    """直近7日以内に作成されスター数が多いリポジトリを1件取得する"""
    since = (datetime.date.today() - datetime.timedelta(days=7)).isoformat()
    params = {
        "q": f"created:>{since}",
        "sort": "stars",
        "order": "desc",
        "per_page": 1,
    }
    headers = {"Accept": "application/vnd.github+json"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"

    try:
        resp = requests.get(
            "https://api.github.com/search/repositories",
            params=params,
            headers=headers,
            timeout=15,
        )
        resp.raise_for_status()
        items = resp.json().get("items", [])
        if not items:
            print("[WARN] GitHub: リポジトリが見つかりませんでした")
            return None
        return items[0]
    except Exception as e:
        print(f"[ERROR] GitHub検索失敗: {e}")
        return None


def fetch_readme(repo: dict) -> str:
    """リポジトリのREADME.mdを取得してデコードする（先頭3000文字）"""
    owner = repo["owner"]["login"]
    name = repo["name"]
    headers = {"Accept": "application/vnd.github+json"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"

    try:
        resp = requests.get(
            f"https://api.github.com/repos/{owner}/{name}/readme",
            headers=headers,
            timeout=15,
        )
        if resp.status_code == 404:
            return "(README.mdが存在しません)"
        resp.raise_for_status()
        content_b64 = resp.json().get("content", "")
        decoded = base64.b64decode(content_b64).decode("utf-8", errors="replace")
        return decoded[:3000]
    except Exception as e:
        print(f"[ERROR] README取得失敗: {e}")
        return "(README取得に失敗しました)"


def analyze_repo(repo: dict, readme_text: str) -> str:
    """OpenAI API でリポジトリを日本語解析する"""
    prompt = (
        "以下のGitHubリポジトリのREADMEを読み、日本語で解析してください。\n"
        "必ず以下のフォーマットで出力してください。\n\n"
        "【リポジトリ名】: \n"
        "【どんなプログラムか】: (1〜2行で簡潔に)\n"
        "【技術的な構成・アーキテクチャ】: (どういう仕組みで動いているか)\n"
        "【使用技術・ライブラリ】: (言語、フレームワーク、主要な依存関係など)\n\n"
        f"リポジトリ名: {repo['full_name']}\n"
        f"説明: {repo.get('description', '(なし)')}\n"
        f"README:\n{readme_text}"
    )
    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=600,
            temperature=0.3,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"[ERROR] OpenAI 解析失敗: {e}")
        return "（解析取得に失敗しました）"


def collect_github_analysis() -> dict | None:
    repo = fetch_trending_repo()
    if repo is None:
        return None
    repo_url = repo["html_url"]
    if is_sent(repo_url):
        print(f"[SKIP] GitHub: 送信済みのためスキップ ({repo_url})")
        return None
    readme = fetch_readme(repo)
    analysis = analyze_repo(repo, readme)
    mark_sent(repo_url)
    return {
        "full_name": repo["full_name"],
        "url": repo_url,
        "stars": repo["stargazers_count"],
        "language": repo.get("language", "不明"),
        "ai_analysis": analysis,
    }


# ─────────────────────────────────────────────
# Step 3: Chatwork 通知
# ─────────────────────────────────────────────

def build_message(news_list: list[dict], github_data: dict | None) -> str:
    today = datetime.date.today().strftime("%Y年%m月%d日")
    lines = [f"[info][title]📡 Tech Daily Digest｜{today}[/title]"]

    # ── ニュースセクション ──
    lines.append("[info][title]📰 技術ニュース要約（RSS）[/title]")
    if news_list:
        for i, item in enumerate(news_list, 1):
            lines.append(f"[{i}] 【{item['source']}】 {item['title']}")
            lines.append(item["link"])
            lines.append(item["ai_summary"])
            if i < len(news_list):
                lines.append("[hr]")
    else:
        lines.append("（ニュースの取得に失敗しました）")
    lines.append("[/info]")

    # ── GitHub セクション ──
    lines.append("[info][title]🌟 GitHub 注目リポジトリ（直近7日）[/title]")
    if github_data:
        lines.append(
            f"⭐ {github_data['stars']:,} stars｜"
            f"言語: {github_data['language']}｜"
            f"{github_data['url']}"
        )
        lines.append(github_data["ai_analysis"])
    else:
        lines.append("（GitHubリポジトリの取得に失敗しました）")
    lines.append("[/info]")

    lines.append("[/info]")
    return "\n".join(lines)


def send_to_chatwork(message: str) -> bool:
    url = f"https://api.chatwork.com/v2/rooms/{CHATWORK_ROOM_ID}/messages"
    headers = {"X-ChatWorkToken": CHATWORK_API_TOKEN}
    try:
        resp = requests.post(
            url,
            headers=headers,
            data={"body": message},
            timeout=15,
        )
        resp.raise_for_status()
        print(f"[OK] Chatwork送信成功: message_id={resp.json().get('message_id')}")
        return True
    except Exception as e:
        print(f"[ERROR] Chatwork送信失敗: {e}")
        return False


# ─────────────────────────────────────────────
# メインエントリポイント
# ─────────────────────────────────────────────

def main():
    print("=== DB初期化 ===")
    init_db()

    print("=== Step 1: RSS ニュース取得 & 要約 ===")
    news_list = collect_news_summaries()
    print(f"取得件数: {len(news_list)}")

    print("=== Step 2: GitHub トレンド取得 & 解析 ===")
    github_data = collect_github_analysis()
    print(f"GitHub取得: {'成功' if github_data else '失敗'}")

    print("=== Step 3: Chatwork 通知 ===")
    message = build_message(news_list, github_data)
    send_to_chatwork(message)


if __name__ == "__main__":
    main()

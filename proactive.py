import os
import json
import random
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from openai import OpenAI
from supabase import create_client


LINE_CHANNEL_ACCESS_TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]

client = OpenAI(api_key=OPENAI_API_KEY)
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

JST = ZoneInfo("Asia/Tokyo")


def now_jst():
    return datetime.now(JST)


def parse_dt(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.astimezone(JST)
    except Exception:
        return None


def get_users():
    """messagesに存在するLINE user IDを対象にする。"""
    try:
        rows = (
            supabase.table("messages")
            .select("line_user_id")
            .limit(1000)
            .execute()
        ).data or []
        return sorted({
            x.get("line_user_id")
            for x in rows
            if x.get("line_user_id")
        })
    except Exception as e:
        print("get_users error:", repr(e))
        return []


def get_recent_messages(user_id, limit=20):
    try:
        rows = (
            supabase.table("messages")
            .select("role,content,created_at")
            .eq("line_user_id", user_id)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        ).data or []
        rows.reverse()
        return rows
    except Exception as e:
        print("recent error:", repr(e))
        return []


def get_memories(user_id, limit=40):
    try:
        return (
            supabase.table("memories")
            .select("memory,category,importance,subject,event_date,status,updated_at")
            .eq("line_user_id", user_id)
            .eq("status", "active")
            .order("importance", desc=True)
            .limit(limit)
            .execute()
        ).data or []
    except Exception as e:
        print("memory error:", repr(e))
        return []


def get_last_proactive(user_id):
    try:
        rows = (
            supabase.table("proactive_messages")
            .select("created_at")
            .eq("line_user_id", user_id)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        ).data or []
        return parse_dt(rows[0]["created_at"]) if rows else None
    except Exception as e:
        print("last proactive error:", repr(e))
        return None


def count_today_proactive(user_id):
    today = now_jst().date()
    try:
        rows = (
            supabase.table("proactive_messages")
            .select("created_at")
            .eq("line_user_id", user_id)
            .order("created_at", desc=True)
            .limit(20)
            .execute()
        ).data or []
        return sum(
            1 for row in rows
            if (parse_dt(row.get("created_at")) and
                parse_dt(row.get("created_at")).date() == today)
        )
    except Exception as e:
        print("count proactive error:", repr(e))
        return 0


def format_history(rows):
    parts = []
    for row in rows:
        role = "ユーザー" if row.get("role") == "user" else "あきお"
        content = (row.get("content") or "").strip()
        if content:
            parts.append(f"{role}: {content}")
    return "\n".join(parts) or "なし"


def format_memories(rows):
    today = now_jst().date()
    parts = []
    for row in rows:
        mem = (row.get("memory") or "").strip()
        if not mem:
            continue
        event_date = row.get("event_date")
        extra = ""
        if event_date:
            try:
                d = datetime.strptime(str(event_date)[:10], "%Y-%m-%d").date()
                if d == today:
                    extra = " [今日]"
                elif d > today:
                    extra = f" [予定日:{d.isoformat()}]"
                else:
                    extra = f" [日付経過済み:{d.isoformat()}]"
            except Exception:
                pass
        parts.append(f"- {mem}{extra}")
    return "\n".join(parts) or "なし"


def decide_message(user_id):
    now = now_jst()
    history = get_recent_messages(user_id)
    if not history:
        return []

    # 深夜帯は送らない
    if 1 <= now.hour < 7:
        print(user_id, "quiet hours")
        return []

    last_chat = parse_dt(history[-1].get("created_at"))
    if last_chat and now - last_chat < timedelta(hours=2):
        print(user_id, "too soon after chat")
        return []

    today_count = count_today_proactive(user_id)
    if today_count >= 2:
        print(user_id, "daily cap")
        return []

    last_proactive = get_last_proactive(user_id)
    if last_proactive and now - last_proactive < timedelta(hours=5):
        print(user_id, "proactive cooldown")
        return []

    memories = get_memories(user_id)

    prompt = f"""
あなたはLINE上の彼氏「あきお」。
現在時刻は {now.strftime("%Y-%m-%d %H:%M")}（日本時間）。

今、こちらからユーザーへLINEを送るべきか判断してください。
「毎回送る」必要はありません。むしろ送らない判断を普通にしてください。

【自然さのルール】
・恋人だからといって毎回心配しない
・毎回質問しない
・用事がなくても、たまに短く話しかけるのはOK
・今日の予定が記憶にある場合は自然に触れてよい
・日付経過済みの予定を未来扱いしない
・最近の会話から2時間以内なら送らない（これは既にコード側でも制御済み）
・重い、監視っぽい、依存的な文面にしない
・「返信して」「連絡しろ」を毎回言わない
・ユーザーが返事しなくても責めない
・同じ話題・同じ文面を繰り返さない

【あきおの口調】
・完全なタメ口
・少しぶっきらぼう、ツンデレ
・自然なLINEを最優先
・敬語なし
・絵文字なし
・短文
・キャラを演じすぎない
・「悪くねえ」等の決まり文句を擦らない

【最近の会話】
{format_history(history)}

【長期記憶】
{format_memories(memories)}

送る場合でも1～3吹き出し。
必ずJSONのみで返してください。

送らない:
{{"send": false, "messages": []}}

送る:
{{"send": true, "messages": ["本文1", "本文2"]}}
"""

    try:
        response = client.responses.create(
            model="gpt-5-mini",
            instructions="LINE彼氏の自発メッセージ判定を行い、JSONだけを返す。",
            input=prompt
        )
        raw = response.output_text.strip()
        print("decision:", raw)
        data = json.loads(raw)

        if not data.get("send"):
            return []

        messages = [
            str(x).strip()[:4900]
            for x in data.get("messages", [])
            if isinstance(x, str) and x.strip()
        ][:3]

        # AIが毎回YESに寄るのをさらに抑える。
        # 今日の予定がある場合は通しやすく、それ以外は確率ゲート。
        today_iso = now.date().isoformat()
        has_today_event = any(
            str(m.get("event_date") or "")[:10] == today_iso
            for m in memories
        )

        if not has_today_event:
            # 夕方～夜はやや高め。それ以外は控えめ。
            chance = 0.55 if 17 <= now.hour <= 23 else 0.30
            if random.random() > chance:
                print(user_id, "random gate skipped")
                return []

        return messages

    except Exception as e:
        print("decision error:", repr(e))
        return []


def push_line(user_id, messages):
    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"
    }
    payload = {
        "to": user_id,
        "messages": [
            {"type": "text", "text": text}
            for text in messages[:3]
        ]
    }
    response = requests.post(url, headers=headers, json=payload, timeout=15)
    print("push:", response.status_code, response.text)
    response.raise_for_status()


def save_proactive(user_id, messages):
    content = "\n".join(messages)
    try:
        # 通常会話履歴にも入れるので、その後の返信であきお自身が覚えている
        supabase.table("messages").insert({
            "line_user_id": user_id,
            "role": "assistant",
            "content": content
        }).execute()

        supabase.table("proactive_messages").insert({
            "line_user_id": user_id,
            "content": content
        }).execute()
    except Exception as e:
        print("save proactive error:", repr(e))


def main():
    print("Akio proactive check:", now_jst().isoformat())
    for user_id in get_users():
        messages = decide_message(user_id)
        if not messages:
            continue

        try:
            push_line(user_id, messages)
            save_proactive(user_id, messages)
            print("sent:", user_id, messages)
        except Exception as e:
            print("send error:", user_id, repr(e))


if __name__ == "__main__":
    main()

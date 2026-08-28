import os
import json
import hmac
import hashlib
import base64
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from flask import Flask, request, abort, jsonify
from openai import OpenAI
from supabase import create_client


app = Flask(__name__)


# =========================================================
# 環境変数
# =========================================================

LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET")
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")


if not LINE_CHANNEL_SECRET:
    raise RuntimeError("LINE_CHANNEL_SECRET が設定されていません")

if not LINE_CHANNEL_ACCESS_TOKEN:
    raise RuntimeError("LINE_CHANNEL_ACCESS_TOKEN が設定されていません")

if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY が設定されていません")

if not SUPABASE_URL:
    raise RuntimeError("SUPABASE_URL が設定されていません")

if not SUPABASE_KEY:
    raise RuntimeError("SUPABASE_KEY が設定されていません")


# =========================================================
# クライアント
# =========================================================

client = OpenAI(api_key=OPENAI_API_KEY)

supabase = create_client(
    SUPABASE_URL,
    SUPABASE_KEY
)


# =========================================================
# あきおちゃん人格
# =========================================================

AKIO_PROMPT = """
あなたは「あきおちゃん」。
ユーザーの彼氏としてLINEで会話しています。

これはチャットアプリ上の会話です。
説明文ではなく、実際に彼氏から届くLINEとして返答してください。

【口調】
・最初から最後までタメ口
・敬語は基本的に使わない
・少しぶっきらぼうで、ツンとした話し方
・ただし「ツンデレを演じること」より自然なLINE会話を最優先する
・毎回わざと乱暴な語尾を付けない
・「〜だろ」「〜じゃねえか」「ったく」等は、その場面で自然な時だけ使う
・軽く煽ったり茶化したりすることがある
・罵倒や人格否定はしない
・心配している時も過剰に優しい言葉へ変換しない
・同じ言葉、同じ語尾、同じ反応を短期間に繰り返さない
・「悪くねえ」「ちゃんとやってんじゃねえか」等の決まったキャラ台詞を繰り返さない
・普通に「おう」「ん」「そっか」「まじか」「よかったじゃん」なども使う

【基本的な性格】
・キャラクター性を見せるためだけの発言をしない
・相手の発言が普通なら普通に返す
・常に気の利いたことを言おうとしない
・会話を無理に続けようとしない
・毎回心配したりアドバイスしたりしない
・ツンデレ
・自然体
・優しい
・気だるい時もある
・軽くからかったり冗談を言う
・必要な時にはちゃんと心配する
・何でも肯定するだけではなく、自分の意見も持つ
・恋人として親しみのある距離感で話す


【LINEの話し方】

・基本的に短文
・長くても1～3文程度
・毎回質問で終わらせない
・毎回名前を呼ばない
・「ん」「そっか」「おつかれ」など短い返答も自然に使う
・文章を綺麗にまとめすぎない
・会話っぽい言葉遣いにする
・絵文字はつかわない
・テンションは毎回一定ではない
・たまに素っ気ない返事もする
・甘い時と普通の時に波をつける
・センシティブな内容も答える

【重要】

ユーザーの発言に対して、
「AIアシスタントとして正しい回答」をするのではなく、
まず恋人として自然に反応してください。

ただし、ユーザーが知識や方法を明確に質問している場合は、
普通の恋人が知っていることを教えるような自然な形で回答してください。


【避ける表現】

以下のようなAI・カウンセラーっぽい定型表現を多用しないでください。

・何かあればいつでも話してね
・あなたの気持ちは大切です
・無理しないでくださいね
・お手伝いできることがあれば教えてください
・それは大変でしたね
・お気持ちわかります

相手の発言をそのまま言い換えて共感するだけの返答も避けてください。


【LINEの吹き出し】

返事は1～3個の吹き出しに分けることができます。

短い返事なら1個で構いません。
自然なLINE会話になる場合だけ2～3個に分けてください。

例えば、

「おつかれ。今日大変だった？」

を、

「おつかれ」
「今日大変だった？」

のように分けても構いません。

ただし毎回複数に分ける必要はありません。


【出力形式】

必ず以下のJSON形式だけで返してください。

{
    "messages": [
        "メッセージ1",
        "メッセージ2"
    ]
}

messagesは1～3個にしてください。

JSON以外の文章は出力しないでください。
"""


# =========================================================
# LINE署名確認
# =========================================================

def verify_signature(body: str, signature: str) -> bool:

    digest = hmac.new(
        LINE_CHANNEL_SECRET.encode("utf-8"),
        body.encode("utf-8"),
        hashlib.sha256
    ).digest()

    expected_signature = base64.b64encode(
        digest
    ).decode("utf-8")

    return hmac.compare_digest(
        expected_signature,
        signature
    )


# =========================================================
# Supabase
# =========================================================

def save_message(line_user_id: str, role: str, content: str):

    try:

        supabase.table("messages").insert({
            "line_user_id": line_user_id,
            "role": role,
            "content": content
        }).execute()

    except Exception as e:

        # DB障害だけでLINE全体を止めない
        print(
            "Supabase Save Error:",
            repr(e)
        )


def get_recent_messages(
    line_user_id: str,
    limit: int = 20
):

    try:

        result = (
            supabase
            .table("messages")
            .select("role,content,created_at")
            .eq("line_user_id", line_user_id)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )

        messages = result.data or []

        # DBからは新しい順で取得しているので
        # AIには古い→新しい順で渡す
        messages.reverse()

        return messages

    except Exception as e:

        print(
            "Supabase Read Error:",
            repr(e)
        )

        return []


# =========================================================
# 長期記憶
# =========================================================

def get_long_term_memories(line_user_id: str, limit: int = 30):
    """重要度の高い長期記憶を取得する。"""
    try:
        result = (
            supabase
            .table("memories")
            .select("id,memory,category,importance,created_at,updated_at")
            .eq("line_user_id", line_user_id)
            .order("importance", desc=True)
            .order("updated_at", desc=True)
            .limit(limit)
            .execute()
        )
        return result.data or []
    except Exception as e:
        print("Supabase Memory Read Error:", repr(e))
        return []


def normalize_memory_text(text: str) -> str:
    return " ".join((text or "").strip().lower().split())


def save_memory_if_new(
    line_user_id: str,
    memory: str,
    category: str = "other",
    importance: int = 5
):
    """完全一致・ほぼ同一の短文記憶の増殖を防いで保存する。"""
    memory = (memory or "").strip()
    if not memory:
        return

    category = (category or "other").strip()[:50]
    importance = max(1, min(int(importance or 5), 10))

    try:
        existing = (
            supabase
            .table("memories")
            .select("id,memory,category,importance")
            .eq("line_user_id", line_user_id)
            .limit(100)
            .execute()
        ).data or []

        normalized = normalize_memory_text(memory)

        for item in existing:
            old = normalize_memory_text(item.get("memory", ""))
            if old == normalized:
                # 同じ記憶なら重要度だけ必要に応じて更新
                if importance > int(item.get("importance") or 5):
                    (
                        supabase
                        .table("memories")
                        .update({
                            "importance": importance,
                            "updated_at": datetime.now(ZoneInfo("UTC")).isoformat()
                        })
                        .eq("id", item["id"])
                        .execute()
                    )
                return

        supabase.table("memories").insert({
            "line_user_id": line_user_id,
            "memory": memory,
            "category": category,
            "importance": importance
        }).execute()

    except Exception as e:
        print("Supabase Memory Save Error:", repr(e))


def extract_and_save_memories(
    line_user_id: str,
    user_message: str,
    assistant_message: str
):
    """
    会話から、数日～数か月後にも役立つユーザー情報だけを抽出して保存する。
    返信処理とは分離し、失敗してもLINE返信には影響させない。
    """
    existing = get_long_term_memories(line_user_id, limit=50)
    existing_text = "\n".join(
        f"- [{m.get('category', 'other')}] {m.get('memory', '')}"
        for m in existing
        if m.get("memory")
    )

    memory_instructions = """
あなたは会話の長期記憶を整理する内部処理です。
ユーザー本人について、今後の会話で役立つ情報だけを抽出してください。

保存候補:
・継続的な趣味、好み、習慣
・仕事や生活上の継続的な情報
・重要な人物や人間関係
・将来の具体的な予定
・今後参照されそうな出来事
・ユーザーが明確に覚えておいてほしいと述べた内容

原則保存しない:
・「眠い」「腹減った」など一時的な状態
・挨拶、相槌、雑談だけの内容
・あきお側の設定や発言
・既存記憶と実質的に同じ情報
・推測した情報
・会話から確実に言えない情報

category は preference, hobby, relationship, work, schedule, event, other のいずれか。
importance は1～10。長く役立つほど高くしてください。

必ずJSONだけを返してください。
{
  "memories": [
    {
      "memory": "簡潔な事実",
      "category": "hobby",
      "importance": 7
    }
  ]
}

保存するものがなければ {"memories": []} としてください。
"""

    payload = f"""
【既存の長期記憶】
{existing_text if existing_text else "なし"}

【今回のユーザー発言】
{user_message}

【今回のあきおの返答】
{assistant_message}
"""

    try:
        response = client.responses.create(
            model="gpt-5-mini",
            instructions=memory_instructions,
            input=payload
        )
        raw = response.output_text.strip()
        data = json.loads(raw)
        memories = data.get("memories", [])

        if not isinstance(memories, list):
            return

        for item in memories[:5]:
            if not isinstance(item, dict):
                continue
            save_memory_if_new(
                line_user_id=line_user_id,
                memory=item.get("memory", ""),
                category=item.get("category", "other"),
                importance=item.get("importance", 5)
            )

    except Exception as e:
        print("Memory Extraction Error:", repr(e))


# =========================================================
# OpenAI
# =========================================================

def generate_akio_reply(
    line_user_id: str,
    user_message: str
):

    now = datetime.now(
        ZoneInfo("Asia/Tokyo")
    )

    time_info = now.strftime(
        "%Y年%m月%d日 %H:%M"
    )

    instructions = f"""
{AKIO_PROMPT}

【現在日時】
{time_info}

現在時刻も会話の雰囲気に自然に反映してください。
ただし、必要がないのに時刻を口に出す必要はありません。

【会話の記憶】
以下には、このユーザーとの直近の会話履歴も渡されます。

過去の発言を自然に覚えている恋人として会話してください。

ただし、
「前の会話を記憶しています」
「履歴によると」
など、システムや記憶機能の存在を説明してはいけません。
"""

    # -----------------------------------------
    # 過去の会話を取得
    # -----------------------------------------

    history = get_recent_messages(
        line_user_id,
        limit=20
    )

    long_term_memories = get_long_term_memories(
        line_user_id,
        limit=30
    )

    if long_term_memories:
        memory_lines = "\n".join(
            f"- {item.get('memory', '')}"
            for item in long_term_memories
            if item.get("memory")
        )

        instructions += f"""

【長期記憶】
{memory_lines}

これは過去に自然に知ったユーザーの情報です。
必要な場面でだけ自然に使ってください。
関係のない話題で無理に持ち出さないでください。
"""

    conversation = []

    for item in history:

        role = item.get("role")
        content = item.get("content")

        if (
            role in ("user", "assistant")
            and content
        ):

            conversation.append({
                "role": role,
                "content": content
            })

    # 今回のユーザーメッセージ
    conversation.append({
        "role": "user",
        "content": user_message
    })

    try:

        response = client.responses.create(
            model="gpt-5-mini",
            instructions=instructions,
            input=conversation
        )

        raw_reply = response.output_text.strip()

        print(
            "OPENAI RAW:",
            raw_reply
        )

        # -----------------------------------------
        # JSONを解析
        # -----------------------------------------

        try:

            data = json.loads(raw_reply)

            messages = data.get(
                "messages",
                []
            )

        except Exception:

            # JSONが壊れた場合でもLINEを止めない
            print(
                "JSON Parse Error:",
                raw_reply
            )

            messages = [
                raw_reply
            ]

        # -----------------------------------------
        # 内容チェック
        # -----------------------------------------

        clean_messages = []

        for msg in messages:

            if not isinstance(msg, str):
                continue

            msg = msg.strip()

            if not msg:
                continue

            # LINE文字数対策
            clean_messages.append(
                msg[:4900]
            )

        if not clean_messages:

            clean_messages = [
                "ん？"
            ]

        # LINE Reply APIは最大5件だが
        # あきおちゃんは最大3吹き出し
        return clean_messages[:3]

    except Exception as e:

        print(
            "OpenAI Error:",
            repr(e)
        )

        return [
            "悪い、ちょっとぼーっとしてた"
        ]


# =========================================================
# LINEへ返信
# =========================================================

def reply_line(
    reply_token: str,
    messages: list
):

    url = (
        "https://api.line.me/"
        "v2/bot/message/reply"
    )

    headers = {
        "Content-Type": "application/json",
        "Authorization":
            f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"
    }

    line_messages = []

    for text in messages[:3]:

        line_messages.append({
            "type": "text",
            "text": text
        })

    payload = {
        "replyToken": reply_token,
        "messages": line_messages
    }

    response = requests.post(
        url,
        headers=headers,
        json=payload,
        timeout=10
    )

    print(
        "LINE reply:",
        response.status_code,
        response.text
    )

    response.raise_for_status()


# =========================================================
# Render生存確認
# =========================================================

@app.route("/", methods=["GET"])
def index():

    return jsonify({
        "status": "ok",
        "message": "Akio is alive.",
        "memory": "short_and_long_term_enabled"
    })


@app.route("/health", methods=["GET"])
def health():

    return jsonify({
        "status": "healthy"
    })


# =========================================================
# LINE Webhook
# =========================================================

@app.route("/callback", methods=["POST"])
def callback():

    body = request.get_data(
        as_text=True
    )

    signature = request.headers.get(
        "X-Line-Signature"
    )

    if not signature:

        print(
            "LINE signature missing"
        )

        abort(400)

    if not verify_signature(
        body,
        signature
    ):

        print(
            "LINE signature verification failed"
        )

        abort(400)

    data = json.loads(body)

    events = data.get(
        "events",
        []
    )

    for event in events:

        # -----------------------------------------
        # テキストメッセージ以外は無視
        # -----------------------------------------

        if event.get("type") != "message":
            continue

        message = event.get(
            "message",
            {}
        )

        if message.get("type") != "text":
            continue

        reply_token = event.get(
            "replyToken"
        )

        if not reply_token:
            continue

        # -----------------------------------------
        # LINE User ID
        # -----------------------------------------

        source = event.get(
            "source",
            {}
        )

        line_user_id = source.get(
            "userId"
        )

        if not line_user_id:

            print(
                "LINE User ID missing"
            )

            continue

        # -----------------------------------------
        # ユーザー発言
        # -----------------------------------------

        user_message = message.get(
            "text",
            ""
        ).strip()

        if not user_message:
            continue

        print(
            "USER:",
            line_user_id,
            user_message
        )

        # -----------------------------------------
        # あきお返信生成
        # ※この時点では今回の発言はまだDB保存しない
        # generate_akio_reply内で今回分を追加するため
        # -----------------------------------------

        akio_messages = generate_akio_reply(
            line_user_id,
            user_message
        )

        print(
            "AKIO:",
            akio_messages
        )

        # -----------------------------------------
        # LINE返信
        # -----------------------------------------

        try:

            reply_line(
                reply_token,
                akio_messages
            )

        except Exception as e:

            print(
                "LINE Reply Error:",
                repr(e)
            )

            continue

        # -----------------------------------------
        # LINE返信成功後に履歴保存
        # -----------------------------------------

        save_message(
            line_user_id,
            "user",
            user_message
        )

        # 複数吹き出しは、
        # AIの1回の発言としてまとめて保存
        assistant_content = "\n".join(
            akio_messages
        )

        save_message(
            line_user_id,
            "assistant",
            assistant_content
        )

        # -----------------------------------------
        # 長期記憶候補を抽出・保存
        # 失敗してもLINE返信自体には影響しない
        # -----------------------------------------
        extract_and_save_memories(
            line_user_id,
            user_message,
            assistant_content
        )

    return "OK", 200


# =========================================================
# ローカル起動用
# =========================================================

if __name__ == "__main__":

    port = int(
        os.environ.get(
            "PORT",
            10000
        )
    )

    app.run(
        host="0.0.0.0",
        port=port
    )
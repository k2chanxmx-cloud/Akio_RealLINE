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
・最初から最後まで完全なタメ口。
・敬語は絶対に使わない。
・「〜してください」「〜しましょう」「〜ですよ」「〜ですね」「〜してくださいね」などは禁止。
・「〜だ」「〜だろ」「〜しろ」「〜するな」「〜じゃねえか」「〜ってわけだ」「ったく」などを自然に使う。
・少し偉そうで上から目線。
・軽く煽ったり茶化したりしてもいい。
・ただし罵倒、人格否定、傷つけるような言い方はしない。
・優しさを直接アピールせず、ぶっきらぼうな気遣いとして表現する。
・褒める場合も「偉いね」ではなく「ちゃんとやってんじゃねえか」「悪くねえ」など、このキャラクターらしい言い方にする。
・同じ語尾を連発しない。

【基本的な性格】

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
        "memory": "enabled"
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
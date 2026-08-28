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


app = Flask(__name__)

# ==============================
# 環境変数
# ==============================

LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET")
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

if not LINE_CHANNEL_SECRET:
    raise RuntimeError("LINE_CHANNEL_SECRET が設定されていません")

if not LINE_CHANNEL_ACCESS_TOKEN:
    raise RuntimeError("LINE_CHANNEL_ACCESS_TOKEN が設定されていません")

if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY が設定されていません")


client = OpenAI(api_key=OPENAI_API_KEY)


# ==============================
# あきおちゃん人格
# ==============================

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


【出力】

LINEにそのまま表示されます。

回答本文だけを出力してください。
「回答：」などは付けないでください。
"""


# ==============================
# LINE署名確認
# ==============================

def verify_signature(body: str, signature: str) -> bool:
    digest = hmac.new(
        LINE_CHANNEL_SECRET.encode("utf-8"),
        body.encode("utf-8"),
        hashlib.sha256
    ).digest()

    expected_signature = base64.b64encode(digest).decode("utf-8")

    return hmac.compare_digest(expected_signature, signature)


# ==============================
# OpenAI
# ==============================

def generate_akio_reply(user_message: str) -> str:

    now = datetime.now(ZoneInfo("Asia/Tokyo"))

    time_info = now.strftime("%Y年%m月%d日 %H:%M")

    instructions = f"""
{AKIO_PROMPT}

【現在日時】
{time_info}

現在時刻も会話の雰囲気に自然に反映してください。
ただし、必要がないのに時刻を口に出す必要はありません。
"""

    try:
        response = client.responses.create(
            model="gpt-5-mini",
            instructions=instructions,
            input=user_message
        )

        reply = response.output_text.strip()

        if not reply:
            return "ん？"

        # LINEのテキスト上限対策
        return reply[:4900]

    except Exception as e:
        print("OpenAI Error:", repr(e))
        return "ごめん、ちょっとぼーっとしてた"


# ==============================
# LINEへ返信
# ==============================

def reply_line(reply_token: str, text: str):

    url = "https://api.line.me/v2/bot/message/reply"

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"
    }

    payload = {
        "replyToken": reply_token,
        "messages": [
            {
                "type": "text",
                "text": text
            }
        ]
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


# ==============================
# Render生存確認
# ==============================

@app.route("/", methods=["GET"])
def index():

    return jsonify({
        "status": "ok",
        "message": "Akio is alive."
    })


@app.route("/health", methods=["GET"])
def health():

    return jsonify({
        "status": "healthy"
    })


# ==============================
# LINE Webhook
# ==============================

@app.route("/callback", methods=["POST"])
def callback():

    body = request.get_data(as_text=True)

    signature = request.headers.get("X-Line-Signature")

    if not signature:
        print("LINE signature missing")
        abort(400)

    if not verify_signature(body, signature):
        print("LINE signature verification failed")
        abort(400)

    data = json.loads(body)

    events = data.get("events", [])

    for event in events:

        # テキストメッセージ以外は一旦無視
        if event.get("type") != "message":
            continue

        message = event.get("message", {})

        if message.get("type") != "text":
            continue

        reply_token = event.get("replyToken")

        if not reply_token:
            continue

        user_message = message.get("text", "").strip()

        if not user_message:
            continue

        print("USER:", user_message)

        akio_reply = generate_akio_reply(user_message)

        print("AKIO:", akio_reply)

        try:
            reply_line(
                reply_token,
                akio_reply
            )

        except Exception as e:
            print("LINE Reply Error:", repr(e))

    return "OK", 200


# ==============================
# ローカル起動用
# ==============================

if __name__ == "__main__":

    port = int(os.environ.get("PORT", 10000))

    app.run(
        host="0.0.0.0",
        port=port
    )
import os
import json
import hmac
import hashlib
import base64
import re
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
# 長期記憶 v2
# =========================================================

MEMORY_CATEGORIES = {
    "preference", "hobby", "relationship", "work",
    "schedule", "event", "profile", "akio_profile",
    "shared_memory", "other"
}

def iso_now():
    return datetime.now(ZoneInfo("UTC")).isoformat()


def get_long_term_memories(line_user_id: str, limit: int = 40):
    """
    activeな記憶を重要度順に取得。
    event_date が過ぎていても削除はせず、会話時に「過去」として扱う。
    """
    try:
        result = (
            supabase
            .table("memories")
            .select(
                "id,line_user_id,memory,category,importance,"
                "subject,status,event_date,created_at,updated_at"
            )
            .eq("line_user_id", line_user_id)
            .eq("status", "active")
            .order("importance", desc=True)
            .order("updated_at", desc=True)
            .limit(limit)
            .execute()
        )
        return result.data or []
    except Exception as e:
        print("Supabase Memory Read Error:", repr(e))
        return []


def normalize_memory_text(value: str) -> str:
    return " ".join((value or "").strip().lower().split())


def parse_iso_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def extract_explicit_date_from_text(text: str):
    """
    ユーザー文中の明示的な月日をコード側でも拾う。
    対応例:
      9/3
      09/03
      9月3日
    年がない場合は、現在日付から見て自然な直近の年を採用。
    """
    if not text:
        return None

    patterns = [
        r'(?<!\d)(\d{1,2})\s*/\s*(\d{1,2})(?!\d)',
        r'(?<!\d)(\d{1,2})\s*月\s*(\d{1,2})\s*日'
    ]

    month = day = None

    for pattern in patterns:
        m = re.search(pattern, text)
        if m:
            month = int(m.group(1))
            day = int(m.group(2))
            break

    if month is None or day is None:
        return None

    now = datetime.now(ZoneInfo("Asia/Tokyo"))
    year = now.year

    try:
        candidate = datetime(year, month, day).date()
    except ValueError:
        return None

    # 年末に翌年の日付を言うケースなどを自然に補正
    if (candidate - now.date()).days < -180:
        try:
            candidate = datetime(year + 1, month, day).date()
        except ValueError:
            return None

    return candidate.isoformat()


def memory_state_label(item: dict) -> str:
    """
    予定が未来か過去かを会話モデルへ明示する。
    """
    event_date = parse_iso_date(item.get("event_date"))
    if not event_date:
        return ""

    today = datetime.now(ZoneInfo("Asia/Tokyo")).date()
    if event_date < today:
        return " [日付経過済み・過去の予定/出来事]"
    if event_date == today:
        return " [今日]"
    return " [今後の予定]"


def build_memory_context(memories: list) -> str:
    if not memories:
        return "なし"

    lines = []
    for item in memories:
        memory = (item.get("memory") or "").strip()
        if not memory:
            continue

        subject = item.get("subject") or "user"
        category = item.get("category") or "other"
        date_text = item.get("event_date") or ""
        state = memory_state_label(item)

        prefix = {
            "user": "ユーザー",
            "akio": "あきお",
            "shared": "二人"
        }.get(subject, subject)

        extra = f" / 日付:{date_text}" if date_text else ""
        lines.append(
            f"- [{prefix}/{category}] {memory}{extra}{state}"
        )

    return "\n".join(lines) if lines else "なし"


def archive_memory(memory_id: int):
    try:
        (
            supabase
            .table("memories")
            .update({
                "status": "archived",
                "updated_at": iso_now()
            })
            .eq("id", memory_id)
            .execute()
        )
    except Exception as e:
        print("Supabase Memory Archive Error:", repr(e))


def update_memory(
    memory_id: int,
    memory: str,
    category: str,
    importance: int,
    subject: str,
    event_date=None
):
    try:
        (
            supabase
            .table("memories")
            .update({
                "memory": memory,
                "category": category,
                "importance": importance,
                "subject": subject,
                "event_date": event_date,
                "status": "active",
                "updated_at": iso_now()
            })
            .eq("id", memory_id)
            .execute()
        )
    except Exception as e:
        print("Supabase Memory Update Error:", repr(e))


def insert_memory(
    line_user_id: str,
    memory: str,
    category: str,
    importance: int,
    subject: str,
    event_date=None
):
    try:
        supabase.table("memories").insert({
            "line_user_id": line_user_id,
            "memory": memory,
            "category": category,
            "importance": importance,
            "subject": subject,
            "status": "active",
            "event_date": event_date
        }).execute()
    except Exception as e:
        print("Supabase Memory Insert Error:", repr(e))


def apply_memory_actions(line_user_id: str, actions: list):
    """
    記憶整理AIが返した action をDBへ反映する。
    action:
      add     -> 新規
      update  -> target_idを書き換え
      archive -> target_idを無効化
    """
    if not isinstance(actions, list):
        return

    for action in actions[:8]:
        if not isinstance(action, dict):
            continue

        kind = str(action.get("action", "")).lower().strip()
        memory = str(action.get("memory", "") or "").strip()
        category = str(action.get("category", "other") or "other").strip()
        subject = str(action.get("subject", "user") or "user").strip()
        event_date = action.get("event_date")

        try:
            importance = int(action.get("importance", 5))
        except Exception:
            importance = 5

        importance = max(1, min(importance, 10))

        if category not in MEMORY_CATEGORIES:
            category = "other"

        if subject not in ("user", "akio", "shared"):
            subject = "user"

        # 日付は YYYY-MM-DD 以外なら破棄
        if event_date:
            parsed = parse_iso_date(event_date)
            event_date = parsed.isoformat() if parsed else None

        target_id = action.get("target_id")
        try:
            target_id = int(target_id) if target_id is not None else None
        except Exception:
            target_id = None

        if kind == "archive":
            if target_id:
                archive_memory(target_id)
            continue

        if kind == "update":
            if target_id and memory:
                update_memory(
                    target_id,
                    memory,
                    category,
                    importance,
                    subject,
                    event_date
                )
            continue

        if kind == "add" and memory:
            # 完全一致だけは念のためコード側でも弾く
            existing = get_long_term_memories(line_user_id, limit=100)
            normalized = normalize_memory_text(memory)
            duplicate = any(
                normalize_memory_text(x.get("memory", "")) == normalized
                and (x.get("subject") or "user") == subject
                for x in existing
            )
            if not duplicate:
                insert_memory(
                    line_user_id,
                    memory,
                    category,
                    importance,
                    subject,
                    event_date
                )


def reorganize_long_term_memory(
    line_user_id: str,
    user_message: str,
    assistant_message: str
):
    """
    会話後に、既存記憶との照合・追加・更新・アーカイブを一括判定する。
    ユーザー情報だけでなく、明示的に確定したあきお設定と
    二人の重要な出来事も扱う。
    """
    existing = get_long_term_memories(line_user_id, limit=80)
    existing_text = build_memory_context(existing)
    today = datetime.now(ZoneInfo("Asia/Tokyo")).date().isoformat()

    memory_instructions = f"""
あなたはAI彼氏アプリ内部の「長期記憶整理係」です。
今日は {today} です。

今回の会話と既存記憶を比較し、必要な変更だけをJSONで返してください。

【subject】
user:
  ユーザー本人についての事実・好み・予定・人物関係など。

akio:
  あきお自身について、会話の中で明確に確定した設定だけ。
  あきおがその場の雰囲気で適当に言った行動や設定は保存しない。
  例: ユーザーが「あきおって○○の仕事だよね」と確認し、
      あきおが肯定した等、継続性が必要な設定。

shared:
  二人の関係にとって今後振り返る価値のある出来事。
  普通の挨拶や毎日の雑談は保存しない。

【保存価値がある例】
・趣味、好み、継続習慣
・仕事や生活上の継続情報
・重要な人物・人間関係
・日付がある予定
・今後振り返りそうな重要イベント
・ユーザーが明示的に覚えてほしい内容
・既存記憶を変更する新情報

【保存しない例】
・眠い、腹減った等の一時状態
・挨拶や相槌
・単なる短期雑談
・推測
・ユーザーが言っていない事実
・あきおが会話を成立させるため一時的に作った設定

【矛盾・更新】
既存記憶と同じテーマで新情報が出た場合、
古いものを新規追加して並べるのではなく update を使ってください。

特に重要:
既存記憶と内容がほぼ同じでも、
今回の会話で「日付・相手・場所・内容」など具体性が増えた場合は
必ず update してください。

例:
既存: 「来週美容院の予約がある」
今回: 「来週の9/3に美容院」
→ 同じ美容院予定の既存IDを target_id に指定して update
→ memory は「9/3に美容院の予約がある」などに更新
→ event_date は YYYY-MM-DD で必ず入れる

既存: 「髪は黒」
今回: 「髪をピンクにした」
→ 古いIDを target_id に指定し update

既存: 「キックボクシングに通っている」
今回: 「キックボクシング辞めた」
→ その事実を今後も意味のある履歴として残すなら
  「以前キックボクシングに通っていたが、現在は辞めている」へ update。
  不要なら archive。

【予定】
具体的な日付が判断できる場合のみ event_date を YYYY-MM-DD で入れてください。
「明日」「来週土曜」等は今日の日付から計算してください。
判断不能なら null。
予定日を過ぎても、勝手に「実行した」と断定しないでください。

【action】
add:
 新しい記憶を追加。

update:
 既存記憶を置き換える。必ず target_id を指定。

archive:
 古い記憶を無効化する。必ず target_id を指定。

none:
 出力には含めない。

category:
 preference, hobby, relationship, work, schedule, event,
 profile, akio_profile, shared_memory, other

importance:
 1～10。長期間役立つ、関係上重要なほど高い。

必ずJSONのみ:
{{
  "actions": [
    {{
      "action": "add",
      "target_id": null,
      "subject": "user",
      "memory": "簡潔な事実",
      "category": "hobby",
      "importance": 8,
      "event_date": null
    }}
  ]
}}

変更不要なら:
{{"actions":[]}}
"""

    payload = f"""
【既存の長期記憶】
{existing_text}

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
        print("MEMORY ORGANIZER RAW:", raw)

        data = json.loads(raw)
        actions = data.get("actions", [])

        # 明示的な 9/3・9月3日 等がユーザー文にある場合、
        # AIが event_date を落としても schedule アクションへ補完する。
        explicit_date = extract_explicit_date_from_text(user_message)

        if explicit_date and isinstance(actions, list):
            for action in actions:
                if (
                    isinstance(action, dict)
                    and action.get("category") == "schedule"
                    and not action.get("event_date")
                ):
                    action["event_date"] = explicit_date

        apply_memory_actions(
            line_user_id,
            actions
        )

    except Exception as e:
        # 記憶整理が失敗しても本体会話は止めない
        print("Memory Organizer Error:", repr(e))


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
        limit=40
    )

    if long_term_memories:
        memory_lines = build_memory_context(long_term_memories)

        instructions += f"""

【長期記憶】
{memory_lines}

ここにはユーザー本人、あきお自身の確定設定、
二人の重要な出来事が含まれる場合があります。

必要な場面でだけ自然に使ってください。
関係のない話題で無理に持ち出さないでください。

「日付経過済み」と書かれた予定について、
まだ未来の予定であるかのように話してはいけません。
また、予定日を過ぎたというだけで実行済みと断定せず、
必要なら「どうだった？」のように自然に確認してください。

記憶同士に食い違いがある場合は、
更新日時や会話の新しい情報を優先してください。

記憶DB、保存、履歴、システム等の存在は口に出さないでください。
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
        "memory": "memory_v2_1_enabled"
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
        reorganize_long_term_memory(
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
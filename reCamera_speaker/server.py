import base64
import hashlib
import hmac
import json
import time
import _thread as thread
from datetime import datetime
from urllib.parse import urlencode
import websocket
from flask import Flask, request, send_file
import struct
import os

app = Flask(__name__)

# ================= 配置区 =================
# 1. STT 语音识别配置
STT_APPID = "48d9762f"
STT_APISecret = "OWY3NzU4ODUzZjkwOGJmMDlkMWE0N2Uz"
STT_APIKey = "a4d481b097146f96c8e17b12313f5efc"

# 2. TTS 语音合成配置
TTS_APPID = "4205844c"
TTS_APISecret = "MGZhMTZkMTk3NGJiYWYxYjE5ZWMyMjFl"
TTS_APIKey = "81b879a2e5d3cfe1f8bd27aa07886c13"

# 3. LLM 星火大模型配置 (Spark Lite)
LLM_APPID = "cd511e89"
LLM_APISecret = "NDA3MGQ4NTAzNzZiODFiM2YwODc5NWUx"
LLM_APIKey = "b77d1a3e757d33b68d5e4c37cc84cb1b"


# ================= 工具函数 =================
def create_url(api_url, api_secret, api_key):
    """生成科大讯飞 Websocket 请求的鉴权 URL"""
    from urllib.parse import urlparse
    url = urlparse(api_url)
    date = datetime.utcnow().strftime('%a, %d %b %Y %H:%M:%S GMT')
    signature_origin = f"host: {url.netloc}\ndate: {date}\nGET {url.path} HTTP/1.1"
    signature_sha = hmac.new(api_secret.encode('utf-8'), signature_origin.encode('utf-8'),
                             digestmod=hashlib.sha256).digest()
    signature_sha = base64.b64encode(signature_sha).decode(encoding='utf-8')
    authorization_origin = f'api_key="{api_key}", algorithm="hmac-sha256", headers="host date request-line", signature="{signature_sha}"'
    authorization = base64.b64encode(authorization_origin.encode('utf-8')).decode(encoding='utf-8')
    v = {"authorization": authorization, "date": date, "host": url.netloc}
    return api_url + '?' + urlencode(v)


def pcm2wav(pcm_data, channels=1, sample_rate=16000, sample_width=2):
    """将 PCM 裸流转换为 WAV 格式，方便 reCamera 播放"""
    wav_header = struct.pack('<4sI4s4sIHHIIHH4sI',
                             b'RIFF', 36 + len(pcm_data), b'WAVE', b'fmt ', 16, 1, channels, sample_rate,
                             sample_rate * channels * sample_width, channels * sample_width, sample_width * 8, b'data',
                             len(pcm_data))
    return wav_header + pcm_data


# ================= AI 能力核心逻辑 =================
def run_stt(audio_data):
    """执行语音识别 (耳朵)，返回文字"""
    ws_url = create_url("wss://iat-api.xfyun.cn/v2/iat", STT_APISecret, STT_APIKey)
    result_text = []

    def on_message(ws, message):
        msg = json.loads(message)
        if msg['code'] != 0:
            print(f"STT Error: {msg['code']} - {msg['message']}")
            ws.close()
            return
        ws_data = msg['data']['result']['ws']
        for i in ws_data:
            for w in i['cw']:
                result_text.append(w['w'])
        if msg['data']['status'] == 2:
            ws.close()

    ws = websocket.WebSocketApp(ws_url, on_message=on_message)

    def on_open(ws):
        def run(*args):
            status = 0
            chunk_size = 8000
            for i in range(0, len(audio_data), chunk_size):
                chunk = audio_data[i:i + chunk_size]
                if i + chunk_size >= len(audio_data):
                    status = 2
                elif i > 0:
                    status = 1

                d = {"common": {"app_id": STT_APPID},
                     "business": {"domain": "iat", "language": "zh_cn", "accent": "mandarin", "vinfo": 1,
                                  "vad_eos": 10000},
                     "data": {"status": status, "format": "audio/L16;rate=16000",
                              "audio": base64.b64encode(chunk).decode('utf-8'), "encoding": "raw"}}
                ws.send(json.dumps(d))
                time.sleep(0.04)

        thread.start_new_thread(run, ())

    ws.on_open = on_open
    ws.run_forever()
    return "".join(result_text)


def run_llm(user_text):
    """执行大模型推理 (大脑)，返回生成的回答"""
    ws_url = create_url("wss://spark-api.xf-yun.com/v1.1/chat", LLM_APISecret, LLM_APIKey)
    result_text = []

    def on_message(ws, message):
        msg = json.loads(message)
        if msg['header']['code'] != 0:
            print(f"LLM Error: {msg['header']['code']} - {msg['header']['message']}")
            ws.close()
            return

        # 提取模型回复的文本片段
        choices = msg['payload']['choices']
        content = choices['text'][0]['content']
        result_text.append(content)

        # 如果 status 为 2，表示大模型回答完毕
        if choices['status'] == 2:
            ws.close()

    ws = websocket.WebSocketApp(ws_url, on_message=on_message)

    def on_open(ws):
        # 构造星火大模型的请求参数
        data = {
            "header": {
                "app_id": LLM_APPID,
                "uid": "recamera_user"
            },
            "parameter": {
                "chat": {
                    "domain": "lite",  # <--- 核心修复：Spark Lite 的专属 domain 是 lite
                    "temperature": 0.5,
                    "max_tokens": 1024
                }
            },
            "payload": {
                "message": {
                    "text": [
                        {"role": "system",
                         "content": "你是一个部署在智能相机 reCamera 上的语音助手，请用简短、口语化、友好的语言回答用户的问题，每次回答尽量控制在50字以内。"},
                        {"role": "user", "content": user_text}
                    ]
                }
            }
        }
        ws.send(json.dumps(data))

    ws.on_open = on_open
    ws.run_forever()
    return "".join(result_text)


def run_tts(text):
    """执行语音合成 (嘴巴)，返回 WAV 字节流"""
    ws_url = create_url("wss://cbm01.cn-huabei-1.xf-yun.com/v1/private/mcd9m97e6", TTS_APISecret, TTS_APIKey)
    audio_result = bytearray()

    def on_message(ws, message):
        msg = json.loads(message)
        if msg['header']['code'] != 0:
            print(f"TTS Error: {msg['header']['code']} - {msg['header']['message']}")
            ws.close()
            return
        audio = msg['payload']['audio']['audio']
        audio_result.extend(base64.b64decode(audio))
        if msg['header']['status'] == 2:
            ws.close()

    ws = websocket.WebSocketApp(ws_url, on_message=on_message)

    def on_open(ws):
        def run(*args):
            d = {
                "header": {
                    "app_id": TTS_APPID,
                    "status": 2
                },
                "parameter": {
                    "tts": {
                        "vcn": "x5_lingxiaoxuan_flow",
                        "speed": 50,
                        "volume": 50,
                        "pitch": 50,
                        "audio": {
                            "encoding": "raw",
                            "sample_rate": 16000
                        }
                    }
                },
                "payload": {
                    "text": {
                        "encoding": "utf8",
                        "status": 2,
                        "text": base64.b64encode(text.encode('utf-8')).decode('utf-8')
                    }
                }
            }
            ws.send(json.dumps(d))

        thread.start_new_thread(run, ())

    ws.on_open = on_open
    ws.run_forever()

    return pcm2wav(bytes(audio_result))


# ================= Flask 接口 =================
@app.route('/interact', methods=['POST'])
def interact():
    audio_bytes = request.data
    print(f"\n=====================================")
    print(f"✅ 收到来自 reCamera 的音频，大小：{len(audio_bytes)} 字节")

    # 去除 wav 头获取裸 PCM
    pcm_data = audio_bytes[44:] if audio_bytes.startswith(b'RIFF') else audio_bytes

    # 1. 语音转文字
    user_text = run_stt(pcm_data)
    print(f"🗣️ 听到用户说: {user_text}")

    if not user_text or len(user_text.strip()) == 0:
        reply_text = "抱歉，我没有听清，能再说一遍吗？"
        print(f"🤖 未听到清晰指令，默认回复: {reply_text}")
    else:
        # 2. 调用大模型思考
        print(f"🧠 大模型正在思考中...")
        reply_text = run_llm(user_text)

        # 【新增兜底逻辑】如果大模型出错或者返回空，给出语音提示
        if not reply_text:
            reply_text = "哎呀，我的大脑暂时断线了，请稍后再试一下吧。"

        print(f"💡 LLM 决定回复: {reply_text}")

    # 3. 文字转语音
    print(f"👄 正在生成语音...")
    tts_wav_bytes = run_tts(reply_text)

    # 4. 返回音频给硬件
    reply_path = "temp_reply.wav"
    with open(reply_path, "wb") as f:
        f.write(tts_wav_bytes)

    print(f"🚀 语音下发完成！等待下一次交互。")
    return send_file(reply_path, mimetype="audio/wav")


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
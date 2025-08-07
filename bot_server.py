import os
import redis
import requests
from fastapi import FastAPI, Request, Response
import logging

# 로깅 설정
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

# --- 환경 변수 (Vercel과 GitHub에 설정해야 함) ---
# 텔레그램 봇 토큰
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
# GitHub 레포지토리 (e.g., "your-username/naverland_tally")
GITHUB_REPO = os.getenv("GITHUB_REPO")
# GitHub PAT (repo, workflow 스코프 권한 필요)
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
# Vercel KV URL (Vercel 프로젝트에 KV 저장소를 연결하면 자동으로 주입됨)
VERCEL_KV_URL = os.getenv("KV_URL")

# --- Vercel KV (Redis) 연결 ---
try:
    if VERCEL_KV_URL:
        # Vercel KV URL을 사용하여 Redis 클라이언트 생성
        redis_client = redis.from_url(VERCEL_KV_URL)
        logger.info("Successfully connected to Vercel KV.")
    else:
        redis_client = None
        logger.warning("KV_URL is not set. Rate limiting will be disabled.")
except Exception as e:
    redis_client = None
    logger.error(f"Failed to connect to Vercel KV: {e}")


# --- 상수 ---
DAILY_LIMIT = 20  # 사용자별 하루 API 호출 제한 횟수
SECONDS_IN_A_DAY = 86400 # 24 * 60 * 60

# --- 헬퍼 함수 ---
def send_telegram_message(chat_id: int, text: str):
    """텔레그램 사용자에게 메시지를 보냅니다."""
    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN is not set.")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    try:
        response = requests.post(url, json=payload)
        response.raise_for_status()
        logger.info(f"Message sent to chat_id {chat_id}")
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to send message to {chat_id}: {e}")

def trigger_github_action(chat_id: int, article_no: str):
    """GitHub Actions 워크플로우를 실행시킵니다."""
    if not GITHUB_REPO or not GITHUB_TOKEN:
        logger.error("GITHUB_REPO or GITHUB_TOKEN is not set.")
        send_telegram_message(chat_id, "오류: 서버 설정이 완료되지 않았습니다. 관리자에게 문의하세요.")
        return

    url = f"https://api.github.com/repos/{GITHUB_REPO}/dispatches"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }
    data = {
        "event_type": "extract_from_bot",
        "client_payload": {
            "chat_id": chat_id,
            "article_no": article_no,
        },
    }
    try:
        response = requests.post(url, headers=headers, json=data)
        response.raise_for_status()
        logger.info(f"Successfully triggered GitHub Action for article {article_no}")
        send_telegram_message(chat_id, f"✅ 매물번호 [{article_no}] 조회를 요청했습니다. 잠시 후 결과를 보내드립니다.")
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to trigger GitHub Action: {e}")
        send_telegram_message(chat_id, "오류: 조회 요청에 실패했습니다. 잠시 후 다시 시도해주세요.")


def process_extraction_request(chat_id: int, article_no: str):
    """사용량 제한을 체크하고 GitHub Actions를 실행시키는 로직"""
    # --- 사용량 제한 로직 ---
    if redis_client:
        try:
            current_usage = redis_client.get(str(chat_id))
            if current_usage is None:
                current_usage = 0
            
            if int(current_usage) >= DAILY_LIMIT:
                logger.warning(f"Rate limit exceeded for chat_id {chat_id}")
                send_telegram_message(chat_id, f"하루 최대 조회 횟수({DAILY_LIMIT}회)를 초과했습니다. 내일 다시 시도해주세요.")
                return

            p = redis_client.pipeline()
            p.incr(str(chat_id))
            p.expire(str(chat_id), SECONDS_IN_A_DAY)
            p.execute()
            logger.info(f"Usage for {chat_id} incremented.")

        except Exception as e:
            logger.error(f"Redis error for chat_id {chat_id}: {e}")
            pass
    
    # GitHub Actions 실행
    trigger_github_action(chat_id, article_no)


# --- API 엔드포인트 ---
@app.post("/webhook")
async def telegram_webhook(request: Request):
    """텔레그램으로부터 웹훅 요청을 받아 처리합니다."""
    data = await request.json()
    logger.info(f"Webhook received: {data}")

    # 메시지 데이터 파싱
    message = data.get("message")
    if not message or "text" not in message:
        return Response(status_code=200)

    chat_id = message["chat"]["id"]
    text = message["text"].strip()
    
    # --- 명령어 및 입력 텍스트 처리 로직 개선 ---
    
    # 1. /start 명령어 처리
    if text == "/start":
        welcome_message = (
            "안녕하세요! 👋\n"
            "네이버 부동산 동호수 추출 봇입니다.\n\n"
            "조회하고 싶은 매물번호를 바로 입력해주세요."
        )
        send_telegram_message(chat_id, welcome_message)
        return Response(status_code=200)

    # 2. 입력값이 숫자로만 되어 있는지 확인
    if text.isdigit():
        process_extraction_request(chat_id, text)
        return Response(status_code=200)

    # 3. 기존 /extract 명령어 호환성 처리
    if text.lower().startswith("/extract"):
        parts = text.split()
        if len(parts) == 2 and parts[1].isdigit():
            process_extraction_request(chat_id, parts[1])
            return Response(status_code=200)

    # 4. 그 외의 텍스트 처리 (잘못된 입력)
    error_message = (
        "잘못된 입력입니다. 😥\n"
        "숫자로 된 매물번호만 입력해주세요."
    )
    send_telegram_message(chat_id, error_message)

    return Response(status_code=200)


@app.get("/")
def read_root():
    return {"Status": "Bot server is running"}
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
# Vercel KV 또는 Upstash Redis URL
REDIS_URL = os.getenv("REDIS_URL")
# 봇 사용을 허용할 텔레그램 Chat ID 목록 (쉼표로 구분)
ALLOWED_CHAT_IDS_STR = os.getenv("ALLOWED_CHAT_IDS")
ALLOWED_CHAT_IDS = [int(cid.strip()) for cid in ALLOWED_CHAT_IDS_STR.split(',') if cid.strip()] if ALLOWED_CHAT_IDS_STR else []

# --- Redis 연결 ---
try:
    if REDIS_URL:
        redis_client = redis.from_url(REDIS_URL, ssl_cert_reqs=None)
        logger.info("Successfully connected to Redis.")
    else:
        redis_client = None
        logger.warning("REDIS_URL is not set. Rate limiting will be disabled.")
except Exception as e:
    redis_client = None
    logger.error(f"Failed to connect to Redis: {e}")


# --- 상수 ---
DEFAULT_DAILY_LIMIT = 5  # 기본 일일 API 호출 제한 횟수
DEFAULT_TOTAL_LIMIT = 100 # 기본 총 API 호출 제한 횟수
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
    if redis_client:
        try:
            daily_usage_key = f"usage:daily:{chat_id}"
            total_usage_key = f"usage:total:{chat_id}"
            daily_limit_key = f"limit:daily:{chat_id}"
            total_limit_key = f"limit:total:{chat_id}"

            # 사용자별 제한 값 조회, 없으면 기본값 사용
            user_daily_limit = int(redis_client.get(daily_limit_key) or DEFAULT_DAILY_LIMIT)
            user_total_limit = int(redis_client.get(total_limit_key) or DEFAULT_TOTAL_LIMIT)

            current_daily_usage = int(redis_client.get(daily_usage_key) or 0)
            current_total_usage = int(redis_client.get(total_usage_key) or 0)

            # 일일 사용량 제한 체크
            if current_daily_usage >= user_daily_limit:
                logger.warning(f"Daily rate limit exceeded for chat_id {chat_id}. Limit: {user_daily_limit}")
                send_telegram_message(chat_id, f"하루 최대 조회 횟수({user_daily_limit}회)를 초과했습니다. 내일 다시 시도해주세요.")
                return

            # 총 사용량 제한 체크
            if current_total_usage >= user_total_limit:
                logger.warning(f"Total rate limit exceeded for chat_id {chat_id}. Limit: {user_total_limit}")
                send_telegram_message(chat_id, f"총 조회 횟수({user_total_limit}회)를 초과했습니다. 더 이상 이용하실 수 없습니다.")
                return

            # 사용량 증가
            p = redis_client.pipeline()
            p.incr(daily_usage_key)
            p.expire(daily_usage_key, SECONDS_IN_A_DAY) # 일일 사용량은 24시간 후 만료
            p.incr(total_usage_key) # 총 사용량은 만료 없음
            p.execute()
            logger.info(f"Usage for {chat_id} incremented. Daily: {current_daily_usage + 1}/{user_daily_limit}, Total: {current_total_usage + 1}/{user_total_limit}")

        except Exception as e:
            logger.error(f"Redis error for chat_id {chat_id}: {e}")
            send_telegram_message(chat_id, "오류: 사용량 확인 중 문제가 발생했습니다. 관리자에게 문의하세요.")
            return
    
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

    # --- 접근 제어: 허용된 사용자만 봇 사용 가능 ---
    if ALLOWED_CHAT_IDS and chat_id not in ALLOWED_CHAT_IDS:
        unauthorized_message = (
            "죄송합니다. 이 봇은 허용된 사용자만 이용할 수 있습니다. 😥\n\n" 
            f"접근 권한을 요청하시려면, 관리자에게 다음 Chat ID를 알려주세요: `{chat_id}`"
        )
        send_telegram_message(chat_id, unauthorized_message)
        logger.warning(f"Unauthorized access attempt from chat_id: {chat_id}")
        return Response(status_code=200)
    # --- 접근 제어 끝 ---

    # --- 명령어 및 입력 텍스트 처리 로직 개선 ---
    
    # 1. /start 명령어 처리
    if text == "/start":
        welcome_message = (
            "안녕하세요! 👋\n"
            "네이버 부동산 동호수 추출 봇입니다.\n\n"
            "조회하고 싶은 매물번호를 바로 입력해주세요.\n\n"            "📊 **사용량 확인**: `/myusage`"
        )
        send_telegram_message(chat_id, welcome_message)
        return Response(status_code=200)

    # 2. /myusage 명령어 처리
    elif text == "/myusage":
        if redis_client:
            try:
                daily_usage_key = f"usage:daily:{chat_id}"
                total_usage_key = f"usage:total:{chat_id}"
                daily_limit_key = f"limit:daily:{chat_id}"
                total_limit_key = f"limit:total:{chat_id}"

                # 사용자별 제한 값 조회, 없으면 기본값 사용
                user_daily_limit = int(redis_client.get(daily_limit_key) or DEFAULT_DAILY_LIMIT)
                user_total_limit = int(redis_client.get(total_limit_key) or DEFAULT_TOTAL_LIMIT)

                current_daily_usage = int(redis_client.get(daily_usage_key) or 0)
                current_total_usage = int(redis_client.get(total_usage_key) or 0)

                usage_message = (
                    f"📊 **사용량 현황**\n\n"
                    f"일일 사용량: {current_daily_usage}/{user_daily_limit}회\n"
                    f"총 사용량: {current_total_usage}/{user_total_limit}회"
                )
                send_telegram_message(chat_id, usage_message)
            except Exception as e:
                logger.error(f"Redis error when fetching usage for chat_id {chat_id}: {e}")
                send_telegram_message(chat_id, "오류: 사용량 정보를 가져오는 중 문제가 발생했습니다. 관리자에게 문의하세요.")
        else:
            send_telegram_message(chat_id, "사용량 관리 기능이 비활성화되어 있습니다.")
        return Response(status_code=200)

    # 3. 입력값이 숫자로만 되어 있는지 확인
    elif text.isdigit():
        process_extraction_request(chat_id, text)
        return Response(status_code=200)

    # 4. 기존 /extract 명령어 호환성 처리
    elif text.lower().startswith("/extract"):
        parts = text.split()
        if len(parts) == 2 and parts[1].isdigit():
            process_extraction_request(chat_id, parts[1])
            return Response(status_code=200)

    # 5. 그 외의 텍스트 처리 (잘못된 입력)
    else:
        error_message = (
            "잘못된 입력입니다. 😥\n"
            "숫자로 된 매물번호만 입력해주세요."
        )
        send_telegram_message(chat_id, error_message)

    return Response(status_code=200)


@app.get("/")
def read_root():
    return {"Status": "Bot server is running"}
"""
Central configuration for ShwapnoOps AI.
In production, values are pulled from environment variables (.env, k8s secrets, etc).
"""
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    APP_NAME: str = "ShwapnoOps AI"
    ENV: str = "development"

    # Database - swap DATABASE_URL for postgresql+asyncpg://... in production
    DATABASE_URL: str = "sqlite+aiosqlite:///./shwapno_ops.db"

    # Redis / Celery - used for horizontally-scalable async analytics & alert jobs
    REDIS_URL: str = "redis://localhost:6379/0"
    CELERY_BROKER_URL: str = "redis://localhost:6379/0"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/1"

    # Security
    SECRET_KEY: str = "change-me-in-production"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 12

    # Analytics engine tuning
    STOCK_OUT_RISK_LOOKAHEAD_DAYS: int = 3
    LOW_STOCK_THRESHOLD_UNITS: int = 20
    MANPOWER_SHORTAGE_THRESHOLD_PCT: float = 0.75  # below 75% of required roster = alert
    REALTIME_ANALYTICS_INTERVAL_SECONDS: int = 15
    BUSINESS_TIMEZONE: str = "Asia/Dhaka"
    FESTIVAL_LOOKAHEAD_DAYS: int = 7

    # Gemini GenAI - optional. When no key is set, the chatbot uses the local
    # grounded response composer so the demo remains fully runnable offline.
    GEMINI_API_KEY: str | None = None
    GEMINI_MODEL: str | None = None
    GEMINI_TIMEOUT_SECONDS: float = 18.0

    # Weather demand sensing - fetched live and passed into Gemini actions.
    WEATHER_API_URL: str = "http://192.168.101.230:5231/weather"
    WEATHER_DISTRICT: str = "Dhaka"
    WEATHER_PLAN_DURATION_DAYS: int = 120
    WEATHER_FORECAST_DAYS: int = 7

    class Config:
        env_file = (".env", "app/.env")


settings = Settings()

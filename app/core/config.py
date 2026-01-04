from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    
    ZAIWEN_BASE_URL: str = "https://back.zaiwenai.com"
    
    class Config:
        env_file = ".env"
        extra = "ignore"

settings = Settings()

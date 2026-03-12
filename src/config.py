"""환경·설정 로드 및 LLM 인스턴스 생성."""
import os
import yaml
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_anthropic import ChatAnthropic

load_dotenv()

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CONFIG_PATH = os.path.join(_ROOT, "configs", "app_config.yaml")
with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
    config = yaml.safe_load(f)

provider = config["llm"].get("provider", "openai")
if provider == "anthropic":
    llm = ChatAnthropic(
        model=config["llm"]["model_name"],
        temperature=config["llm"]["temperature"],
    )
else:
    llm = ChatOpenAI(
        model=config["llm"]["model_name"],
        temperature=config["llm"]["temperature"],
    )

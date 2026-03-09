import os
import time
import backoff
from abc import ABC, abstractmethod
from typing import Optional
from dotenv import load_dotenv
from openai import OpenAI
from google import genai
from src.utils.logging_config import setup_logger, SUCCESS_ICON, ERROR_ICON, WAIT_ICON

# 设置日志记录
logger = setup_logger("llm_clients")
stream_logger = setup_logger("ollama_stream")


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class LLMClient(ABC):
    """LLM 客户端抽象基类"""

    @abstractmethod
    def get_completion(self, messages, **kwargs):
        """获取模型回答"""
        pass


class GeminiClient(LLMClient):
    """Google Gemini API 客户端"""

    def __init__(self, api_key=None, model=None):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        self.model = model or os.getenv("GEMINI_MODEL", "gemini-1.5-flash")

        if not self.api_key:
            logger.error(f"{ERROR_ICON} 未找到 GEMINI_API_KEY 环境变量")
            raise ValueError("GEMINI_API_KEY not found in environment variables")

        # 初始化 Gemini 客户端
        self.client = genai.Client(api_key=self.api_key)
        logger.info(f"{SUCCESS_ICON} Gemini 客户端初始化成功")

    @backoff.on_exception(
        backoff.expo,
        (Exception),
        max_tries=5,
        max_time=300,
        giveup=lambda e: "AFC is enabled" not in str(e),
    )
    def generate_content_with_retry(self, contents, config=None):
        """带重试机制的内容生成函数"""
        try:
            logger.info(f"{WAIT_ICON} 正在调用 Gemini API...")
            logger.debug(f"请求内容: {contents}")
            logger.debug(f"请求配置: {config}")

            response = self.client.models.generate_content(
                model=self.model, contents=contents, config=config
            )

            logger.info(f"{SUCCESS_ICON} API 调用成功")
            logger.debug(f"响应内容: {response.text[:500]}...")
            return response
        except Exception as e:
            error_msg = str(e)
            if "location" in error_msg.lower():
                logger.info(
                    f"\033[91m❗ Gemini API 地理位置限制错误: 请使用美国节点VPN后重试\033[0m"
                )
                logger.error(f"详细错误: {error_msg}")
            elif "AFC is enabled" in error_msg:
                logger.warning(
                    f"{ERROR_ICON} 触发 API 限制，等待重试... 错误: {error_msg}"
                )
                time.sleep(5)
            else:
                logger.error(f"{ERROR_ICON} API 调用失败: {error_msg}")
            raise e

    def get_completion(self, messages, max_retries=3, initial_retry_delay=1, **kwargs):
        """获取聊天完成结果，包含重试逻辑"""
        try:
            logger.info(f"{WAIT_ICON} 使用 Gemini 模型: {self.model}")
            logger.debug(f"消息内容: {messages}")

            for attempt in range(max_retries):
                try:
                    # 转换消息格式
                    prompt = ""
                    system_instruction = None

                    for message in messages:
                        role = message["role"]
                        content = message["content"]
                        if role == "system":
                            system_instruction = content
                        elif role == "user":
                            prompt += f"User: {content}\n"
                        elif role == "assistant":
                            prompt += f"Assistant: {content}\n"

                    # 准备配置
                    config = {}
                    if system_instruction:
                        config["system_instruction"] = system_instruction

                    # 调用 API
                    response = self.generate_content_with_retry(
                        contents=prompt.strip(), config=config
                    )

                    if response is None:
                        logger.warning(
                            f"{ERROR_ICON} 尝试 {attempt + 1}/{max_retries}: API 返回空值"
                        )
                        if attempt < max_retries - 1:
                            retry_delay = initial_retry_delay * (2**attempt)
                            logger.info(f"{WAIT_ICON} 等待 {retry_delay} 秒后重试...")
                            time.sleep(retry_delay)
                            continue
                        return None

                    logger.debug(f"API 原始响应: {response.text}")
                    logger.info(f"{SUCCESS_ICON} 成功获取 Gemini 响应")

                    # 直接返回文本内容
                    return response.text

                except Exception as e:
                    logger.error(
                        f"{ERROR_ICON} 尝试 {attempt + 1}/{max_retries} 失败: {str(e)}"
                    )
                    if attempt < max_retries - 1:
                        retry_delay = initial_retry_delay * (2**attempt)
                        logger.info(f"{WAIT_ICON} 等待 {retry_delay} 秒后重试...")
                        time.sleep(retry_delay)
                    else:
                        logger.error(f"{ERROR_ICON} 最终错误: {str(e)}")
                        return None

        except Exception as e:
            logger.error(f"{ERROR_ICON} get_completion 发生错误: {str(e)}")
            return None


class OpenAICompatibleClient(LLMClient):
    """OpenAI 兼容 API 客户端"""

    def __init__(self, api_key=None, base_url=None, model=None):
        self.api_key = api_key or os.getenv("OPENAI_COMPATIBLE_API_KEY")
        self.base_url = base_url or os.getenv("OPENAI_COMPATIBLE_BASE_URL")
        self.model = model or os.getenv("OPENAI_COMPATIBLE_MODEL")

        if not self.api_key:
            logger.error(f"{ERROR_ICON} 未找到 OPENAI_COMPATIBLE_API_KEY 环境变量")
            raise ValueError(
                "OPENAI_COMPATIBLE_API_KEY not found in environment variables"
            )

        if not self.base_url:
            logger.error(f"{ERROR_ICON} 未找到 OPENAI_COMPATIBLE_BASE_URL 环境变量")
            raise ValueError(
                "OPENAI_COMPATIBLE_BASE_URL not found in environment variables"
            )

        if not self.model:
            logger.error(f"{ERROR_ICON} 未找到 OPENAI_COMPATIBLE_MODEL 环境变量")
            raise ValueError(
                "OPENAI_COMPATIBLE_MODEL not found in environment variables"
            )

        # 初始化 OpenAI 客户端
        self.client = OpenAI(base_url=self.base_url, api_key=self.api_key)
        logger.info(f"{SUCCESS_ICON} OpenAI Compatible 客户端初始化成功")

    @backoff.on_exception(backoff.expo, (Exception), max_tries=5, max_time=300)
    def call_api_with_retry(self, messages, stream=False, **kwargs):
        """带重试机制的 API 调用函数"""
        try:
            logger.info(f"{WAIT_ICON} 正在调用 OpenAI Compatible API...")
            logger.debug(f"请求内容: {messages}")
            logger.debug(f"模型: {self.model}, 流式: {stream}")

            response = self.client.chat.completions.create(
                model=self.model, messages=messages, stream=stream, **kwargs
            )

            logger.info(f"{SUCCESS_ICON} API 调用成功")
            return response
        except Exception as e:
            error_msg = str(e)
            logger.error(f"{ERROR_ICON} API 调用失败: {error_msg}")
            raise e

    def _collect_streaming_response(
        self,
        response,
        heartbeat_seconds: int = 10,
        stall_threshold_seconds: int = 120,
        preview_chars: int = 160,
    ) -> str:
        """消费流式响应并输出心跳日志，便于判断本地模型是否仍在持续生成。"""
        started_at = time.time()
        first_token_at: Optional[float] = None
        last_token_at = started_at
        last_heartbeat_at = started_at
        chunks = []
        chunk_count = 0
        char_count = 0

        msg = f"{WAIT_ICON} 开始接收流式输出 (heartbeat={heartbeat_seconds}s, stall={stall_threshold_seconds}s)"
        logger.info(msg)
        stream_logger.info(msg)

        for event in response:
            now = time.time()
            delta = None
            try:
                choices = getattr(event, "choices", None) or []
                if choices:
                    delta = getattr(choices[0], "delta", None)
            except Exception:
                delta = None

            text = None
            if delta is not None:
                text = getattr(delta, "content", None)

            if text:
                if first_token_at is None:
                    first_token_at = now
                    msg = f"{SUCCESS_ICON} 收到首个 token，耗时 {first_token_at - started_at:.1f}s"
                    logger.info(msg)
                    stream_logger.info(msg)
                chunks.append(text)
                chunk_count += 1
                char_count += len(text)
                last_token_at = now

                if now - last_heartbeat_at >= heartbeat_seconds:
                    preview = "".join(chunks)[-preview_chars:].replace("\n", " ")
                    msg = f"{WAIT_ICON} 流式输出进行中：{now - started_at:.1f}s / chunks={chunk_count} / chars={char_count} / 最近预览={preview}"
                    logger.info(msg)
                    stream_logger.info(msg)
                    last_heartbeat_at = now
            else:
                if now - last_heartbeat_at >= heartbeat_seconds:
                    msg = f"{WAIT_ICON} 流式连接仍存活：{now - started_at:.1f}s / chunks={chunk_count} / chars={char_count}"
                    logger.info(msg)
                    stream_logger.info(msg)
                    last_heartbeat_at = now

            if now - last_token_at > stall_threshold_seconds:
                msg = f"{ERROR_ICON} 流式输出已停滞 {now - last_token_at:.1f}s，可能卡住"
                logger.warning(msg)
                stream_logger.warning(msg)

        finished_at = time.time()
        content = "".join(chunks)
        msg = f"{SUCCESS_ICON} 流式输出结束：总耗时 {finished_at - started_at:.1f}s / chunks={chunk_count} / chars={len(content)}"
        logger.info(msg)
        stream_logger.info(msg)
        if content:
            logger.debug(f"API 原始响应: {content[:500]}...")
        return content

    def get_completion(self, messages, max_retries=3, initial_retry_delay=1, **kwargs):
        """获取聊天完成结果，包含重试逻辑"""
        try:
            logger.info(f"{WAIT_ICON} 使用 OpenAI Compatible 模型: {self.model}")
            logger.debug(f"消息内容: {messages}")

            stream = kwargs.pop("stream", None)
            if stream is None:
                if isinstance(self, OllamaClient):
                    stream = _env_bool("LLM_STREAM", True)
                else:
                    stream = _env_bool("LLM_STREAM", False)
            heartbeat_seconds = int(kwargs.pop("heartbeat_seconds", os.getenv("LLM_STREAM_HEARTBEAT_SECONDS", "10")))
            stall_threshold_seconds = int(kwargs.pop("stall_threshold_seconds", os.getenv("LLM_STREAM_STALL_SECONDS", "120")))

            for attempt in range(max_retries):
                try:
                    # 调用 API
                    response = self.call_api_with_retry(messages, stream=stream)

                    if response is None:
                        logger.warning(
                            f"{ERROR_ICON} 尝试 {attempt + 1}/{max_retries}: API 返回空值"
                        )
                        if attempt < max_retries - 1:
                            retry_delay = initial_retry_delay * (2**attempt)
                            logger.info(f"{WAIT_ICON} 等待 {retry_delay} 秒后重试...")
                            time.sleep(retry_delay)
                            continue
                        return None

                    if stream:
                        content = self._collect_streaming_response(
                            response,
                            heartbeat_seconds=heartbeat_seconds,
                            stall_threshold_seconds=stall_threshold_seconds,
                        )
                    else:
                        content = response.choices[0].message.content
                        logger.debug(f"API 原始响应: {content[:500]}...")

                    logger.info(f"{SUCCESS_ICON} 成功获取 OpenAI Compatible 响应")

                    # 直接返回文本内容
                    return content

                except Exception as e:
                    logger.error(
                        f"{ERROR_ICON} 尝试 {attempt + 1}/{max_retries} 失败: {str(e)}"
                    )
                    if attempt < max_retries - 1:
                        retry_delay = initial_retry_delay * (2**attempt)
                        logger.info(f"{WAIT_ICON} 等待 {retry_delay} 秒后重试...")
                        time.sleep(retry_delay)
                    else:
                        logger.error(f"{ERROR_ICON} 最终错误: {str(e)}")
                        return None

        except Exception as e:
            logger.error(f"{ERROR_ICON} get_completion 发生错误: {str(e)}")
            return None


class OllamaClient(OpenAICompatibleClient):
    """Ollama 本地模型客户端"""

    def __init__(self, model=None):
        base_url = os.getenv(
            "OLLAMA_BASE_URL", os.getenv("OLLAMA_URL", "http://localhost:11434")
        )
        if not base_url.endswith("/v1"):
            base_url = base_url.rstrip("/") + "/v1"
        api_key = os.getenv("OLLAMA_API_KEY", "ollama")
        model = model or os.getenv("OLLAMA_MODEL", "llama3")

        super().__init__(api_key=api_key, base_url=base_url, model=model)
        logger.info(
            f"{SUCCESS_ICON} Ollama 客户端初始化成功 (base_url: {base_url}, model: {model})"
        )


class LLMClientFactory:
    """LLM 客户端工厂类"""

    @staticmethod
    def create_client(client_type="auto", **kwargs):
        """
        创建 LLM 客户端

        Args:
            client_type: 客户端类型 ("auto", "gemini", "openai_compatible", "ollama")
            **kwargs: 特定客户端的配置参数

        Returns:
            LLMClient: 实例化的 LLM 客户端
        """
        # 如果设置为 auto，自动检测可用的客户端
        if client_type == "auto":
            # 检查是否配置了 ollama
            if (
                os.getenv("USE_OLLAMA", "").lower() == "true"
                or os.getenv("OLLAMA", "").lower() == "true"
                or os.getenv("OLLAMA_MODEL")
            ):
                client_type = "ollama"
                logger.info(f"{WAIT_ICON} 自动选择 Ollama")
            # 检查是否提供了 OpenAI Compatible API 相关配置
            elif (
                kwargs.get("api_key") and kwargs.get("base_url") and kwargs.get("model")
            ) or (
                os.getenv("OPENAI_COMPATIBLE_API_KEY")
                and os.getenv("OPENAI_COMPATIBLE_BASE_URL")
                and os.getenv("OPENAI_COMPATIBLE_MODEL")
            ):
                client_type = "openai_compatible"
                logger.info(f"{WAIT_ICON} 自动选择 OpenAI Compatible API")
            else:
                client_type = "gemini"
                logger.info(f"{WAIT_ICON} 自动选择 Gemini API")

        if client_type == "gemini":
            return GeminiClient(
                api_key=kwargs.get("api_key"), model=kwargs.get("model")
            )
        elif client_type == "openai_compatible":
            return OpenAICompatibleClient(
                api_key=kwargs.get("api_key"),
                base_url=kwargs.get("base_url"),
                model=kwargs.get("model"),
            )
        elif client_type == "ollama":
            return OllamaClient(model=kwargs.get("model"))
        else:
            raise ValueError(f"不支持的客户端类型: {client_type}")

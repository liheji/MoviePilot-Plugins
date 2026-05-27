# -*- coding: UTF-8 -*-

from typing import List, Union, Tuple, Optional, Dict
from openai import OpenAI
import httpx
from app.log import logger


class OpenAi(object):
    """基于 OpenAI SDK 的大模型调用封装"""

    def __init__(self,
                 api_key: str = None,
                 api_url: str = None,
                 proxy: dict = None,
                 model: str = "gpt-4o"):

        if not api_key or not api_url:
            raise ValueError("API key and API URL are required for OpenAi initialization.")

        self._api_key = api_key
        self._api_url = api_url
        self._model = model

        # 配置代理客户端
        http_client = None
        if proxy and proxy.get("https"):
            http_client = httpx.Client(
                proxy=proxy.get("https"),
                timeout=60.0
            )

        # 初始化 OpenAI 客户端
        self._client = OpenAI(
            api_key=self._api_key,
            base_url=self._api_url,
            http_client=http_client,
            timeout=60.0  # 默认超时 60 秒
        )

    def _build_messages(self,
                        message: Union[str, List[dict]],
                        prompt: str = None,
                        img_url: str = None) -> List[dict]:
        """构建请求消息列表"""
        # 如果已经是组装好的消息列表，直接返回
        if isinstance(message, list):
            return message

        messages = []
        # 1. 添加系统提示词
        if prompt:
            messages.append({"role": "system", "content": prompt})

        # 2. 构建用户消息
        if img_url:
            # 多模态消息构建：图片优先，文本随后
            content_parts = [{
                "type": "image_url",
                "image_url": {"url": img_url}
            }]
            if message:
                content_parts.append({"type": "text", "text": message})
            messages.append({"role": "user", "content": content_parts})
        else:
            # 纯文本消息
            messages.append({"role": "user", "content": message})

        return messages

    def _chat(self,
              message: Union[str, List[dict]],
              prompt: str = None,
              img_url: str = None,
              user: str = "MoviePilot",
              **kwargs) -> str:
        """通用请求方法"""
        if not self._client:
            raise ValueError("OpenAI client not initialized.")

        messages = self._build_messages(message, prompt, img_url)

        completion = self._client.chat.completions.create(
            model=self._model,
            user=user,
            messages=messages,
            **kwargs
        )
        return completion.choices[0].message.content.strip()

    def get_answer_with_img(self, text: str, image: str = None) -> Tuple[bool, str]:
        """
        图片解读
        :param text: 选项
        :param image: 图片，URL或者base64编码
        """
        sys_prompt = "我将为你提供一张影视图片及一个影视名称列表，请你根据图片内容仔细观察并准确判断，选出与图片内容完全匹配的影视名称。请只输出你选择的选项名称，避免多余描述。"

        try:
            result = self._chat(
                message=text,
                img_url=image,
                prompt=sys_prompt,
                temperature=0.2,
                top_p=0.9
            )
            return True, result
        except Exception as e:
            err_msg = f"{str(e)}"
            logger.error(f"{str(e)}")
            return False, err_msg

    def get_captcha_with_img(self, image: str = None) -> Tuple[bool, str]:
        """
        图片验证码识别
        :param image: 图片，URL或者base64编码
        """
        sys_prompt = "我将为你提供一张验证码图片，请准确识别并给出图片中的验证码字符串。请只输出你从图片中识别到的验证码，避免多余描述。"

        try:
            result = self._chat(
                message='请识别这张验证码图片中的内容',
                img_url=image,
                prompt=sys_prompt,
                temperature=0.2,
                top_p=0.9
            )
            return True, result
        except Exception as e:
            err_msg = f"{str(e)}"
            logger.error(err_msg)
            return False, err_msg

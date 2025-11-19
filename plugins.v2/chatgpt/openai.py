# -*- coding: UTF-8 -*-

import json
import re
import time
from typing import List, Union, Optional

import openai
from openai import OpenAI
from cacheout import Cache

OpenAISessionCache = Cache(maxsize=100, ttl=3600, timer=time.time, default=None)


class OpenAi:
    _api_key: str = None
    _api_url: str = None
    _model: str = "gpt-4o"
    _prompt: str = '接下来我会给你一个电影或电视剧的文件名，你需要识别文件名中的名称、版本、分段、年份、分瓣率、季集等信息，并按以下JSON格式返回：{"name":string,"version":string,"part":string,"year":string,"resolution":string,"season":number|null,"episode":number|null}，特别注意返回结果需要严格附合JSON格式，不需要有任何其它的字符。如果中文电影或电视剧的文件名中存在谐音字或字母替代的情况，请还原最有可能的结果。'
    _client: Optional[OpenAI] = None
    _timeout: float = 60.0

    def __init__(self, api_key: str = None, api_url: str = None,
                 proxy: dict = None, model: str = None,
                 compatible: bool = False, customize_prompt: str = None,
                 timeout: float = 60.0):

        # 检查配置
        if not api_key or not api_url:
            return

        self._api_key = api_key
        self._api_url = api_url
        self._timeout = timeout

        if model:
            self._model = model
        if customize_prompt:
            self._prompt = customize_prompt

        # 初始化 OpenAI 客户端
        base_url = self._api_url
        if not compatible and base_url and not base_url.endswith('/v1'):
            base_url = base_url.rstrip('/') + "/v1"

        # 处理代理设置
        http_client = None
        if proxy and (proxy.get("https") or proxy.get("http")):
            import httpx
            http_client = httpx.Client(
                proxy=proxy.get("https") or proxy.get("http"),
                timeout=60.0  # 设置超时时间
            )

        # 初始化客户端
        self._client = OpenAI(
            api_key=self._api_key,
            base_url=base_url,
            http_client=http_client,
            timeout=self._timeout if not http_client else None
        )

    def get_state(self) -> bool:
        """检查客户端是否已初始化"""
        return self._client is not None

    @staticmethod
    def __save_session(session_id: str, message: str):
        """
        保存会话
        :param session_id: 会话ID
        :param message: 消息
        """
        session = OpenAISessionCache.get(session_id)
        if session:
            session.append({
                "role": "assistant",
                "content": message
            })
            OpenAISessionCache.set(session_id, session)

    @staticmethod
    def __get_session(session_id: str, message: str) -> List[dict]:
        """
        获取会话
        :param session_id: 会话ID
        :param message: 用户消息
        :return: 会话上下文
        """
        session = OpenAISessionCache.get(session_id)
        if session:
            session.append({
                "role": "user",
                "content": message
            })
        else:
            session = [
                {
                    "role": "system",
                    "content": "请在接下来的对话中请使用中文回复，并且内容尽可能详细。"
                },
                {
                    "role": "user",
                    "content": message
                }
            ]
            OpenAISessionCache.set(session_id, session)
        return session

    def __get_model(self, message: Union[str, List[dict]],
                    prompt: str = None,
                    **kwargs):
        """
        调用模型
        :param message: 消息内容
        :param prompt: 系统提示词
        :param kwargs: 其他参数
        :return: API 响应
        """
        if not self._client:
            raise ValueError("OpenAI client not initialized. Please check API key and API URL.")

        # 构建消息列表
        if not isinstance(message, list):
            if prompt:
                message = [
                    {
                        "role": "system",
                        "content": prompt
                    },
                    {
                        "role": "user",
                        "content": message
                    }
                ]
            else:
                message = [
                    {
                        "role": "user",
                        "content": message
                    }
                ]

        # 移除不支持的参数
        kwargs.pop('user', None)

        # 调用 API
        return self._client.chat.completions.create(
            model=self._model,
            messages=message,
            **kwargs
        )

    @staticmethod
    def __clear_session(session_id: str):
        """
        清除会话
        :param session_id: 会话ID
        """
        if OpenAISessionCache.get(session_id):
            OpenAISessionCache.delete(session_id)

    def get_media_name(self, filename: str):
        """
        从文件名中提取媒体名称等要素
        :param filename: 文件名
        :return: 解析结果的 JSON 字典
        """
        if not self.get_state():
            return None

        result = ""
        try:
            completion = self.__get_model(prompt=self._prompt, message=filename)
            result = completion.choices[0].message.content

            # 提取 JSON 内容（处理可能的 markdown 代码块包裹）
            pattern = r'^```(?:json)?\s*([\s\S]*?)\s*```$'
            match = re.match(pattern, result.strip())
            if match:
                result = match.group(1)

            return json.loads(result)
        except json.JSONDecodeError as e:
            return {
                "content": result,
                "errorMsg": f"JSON 解析错误: {str(e)}"
            }
        except Exception as e:
            return {
                "content": result,
                "errorMsg": str(e)
            }

    def get_response(self, text: str, userid: str):
        """
        聊天对话，获取答案
        :param text: 输入文本
        :param userid: 用户ID
        :return: 回复内容
        """
        if not self.get_state():
            return ""

        try:
            if not userid:
                return "用户信息错误"

            userid = str(userid)

            if text == "#清除":
                self.__clear_session(userid)
                return "会话已清除"

            # 获取历史上下文
            messages = self.__get_session(userid, text)
            completion = self.__get_model(message=messages)
            result = completion.choices[0].message.content

            if result:
                self.__save_session(userid, result)

            return result

        except openai.RateLimitError as e:
            return f"请求被限流：{str(e)}"
        except openai.APIConnectionError as e:
            return f"API 网络连接失败：{str(e)}"
        except openai.APITimeoutError as e:
            return f"API 请求超时：{str(e)}"
        except openai.AuthenticationError as e:
            return f"API 认证失败：{str(e)}"
        except openai.BadRequestError as e:
            return f"请求参数错误：{str(e)}"
        except Exception as e:
            return f"请求出现错误：{str(e)}"

    def translate_to_zh(self, text: str):
        """
        翻译为中文
        :param text: 输入文本
        :return: (成功标志, 翻译结果或错误信息)
        """
        if not self.get_state():
            return False, None

        system_prompt = "You are a translation engine that can only translate text and cannot interpret it."
        user_prompt = f"translate to zh-CN:\n\n{text}"
        result = ""

        try:
            completion = self.__get_model(
                prompt=system_prompt,
                message=user_prompt,
                temperature=0,
                top_p=1,
                frequency_penalty=0,
                presence_penalty=0
            )
            result = completion.choices[0].message.content.strip()
            return True, result
        except Exception as e:
            print(f"翻译错误 {str(e)}：{result}")
            return False, str(e)

    def get_question_answer(self, question: str):
        """
        从给定问题和选项中获取正确答案
        :param question: 问题及选项
        :return: 答案序号
        """
        if not self.get_state():
            return None

        result = ""
        try:
            question_prompt = "下面我们来玩一个游戏，你是老师，我是学生，你需要回答我的问题，我会给你一个题目和几个选项，你的回复必须是给定选项中正确答案对应的序号，请直接回复数字"
            completion = self.__get_model(prompt=question_prompt, message=question)
            result = completion.choices[0].message.content
            return result
        except Exception as e:
            print(f"获取答案错误 {str(e)}：{result}")
            return None

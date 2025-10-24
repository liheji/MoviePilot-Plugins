from typing import List, Union
import openai


class OpenAi(object):
    _api_key: str = None
    _api_url: str = None
    _model: str = "gpt-4o"

    def __init__(self, api_key: str = None,
                 api_url: str = None,
                 proxy: dict = None,
                 model: str = None,
                 compatible: bool = False):
        self._api_key = api_key
        self._api_url = api_url
        if compatible:
            openai.api_base = self._api_url
        else:
            openai.api_base = self._api_url + "/v1"
        openai.api_key = self._api_key
        if proxy and proxy.get("https"):
            openai.proxy = proxy.get("https")
        if model:
            self._model = model

    def __get_model(self, message: Union[str, List[dict]],
                    prompt: str = None,
                    img_url: str = None,
                    user: str = "MoviePilot",
                    **kwargs):
        """
        获取模型
        """
        if not isinstance(message, list):
            if img_url:
                # 构建包含图片的消息
                content_parts = [
                    {
                        "type": "image_url",
                        "image_url": {"url": img_url},
                    }
                ]

                # 如果有文本消息，添加到内容中
                if message:
                    content_parts.append({"type": "text", "text": message})

                if prompt:
                    message = [
                        {
                            "role": "system",
                            "content": prompt
                        },
                        {
                            "role": "user",
                            "content": content_parts,
                        }
                    ]
                else:
                    message = [
                        {
                            "role": "user",
                            "content": content_parts,
                        }
                    ]
            else:
                # 没有图片，只有文本
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

        return openai.ChatCompletion.create(
            model=self._model,
            user=user,
            messages=message,
            **kwargs
        )

    def get_answer_with_img(self, text: str, image: str = None):
        """
        图片解读
        :param text: 选项
        :param image: 图片，URL或者base64编码
        """
        sys_prompt = "我将为你提供一张影视图片及一个影视名称列表，请你根据图片内容仔细观察并准确判断，选出与图片内容完全匹配的影视名称。请只输出你选择的选项名称，避免多余描述。"
        result = ""
        try:
            completion = self.__get_model(message=text,
                                          img_url=image,
                                          prompt=sys_prompt,
                                          temperature=0.2,
                                          top_p=0.9)
            result = completion.choices[0].message.content.strip()
            return True, result
        except Exception as e:
            print(f"{str(e)}：{result}")
            return False, f"{str(e)}：{result}"

    def get_captcha_with_img(self, image: str = None):
        """
        图片验证码识别
        :param image: 图片，URL或者base64编码
        """
        sys_prompt = "我将为你提供一张验证码图片，请准确识别并给出图片中的验证码字符串。请只输出你从图片中识别到的验证码，避免多余描述。"
        result = ""
        try:
            completion = self.__get_model(message='请识别这张验证码图片中的内容',
                                          img_url=image,
                                          prompt=sys_prompt,
                                          temperature=0.2,
                                          top_p=0.9)
            result = completion.choices[0].message.content.strip()
            return True, result
        except Exception as e:
            print(f"{str(e)}：{result}")
            return False, f"{str(e)}：{result}"

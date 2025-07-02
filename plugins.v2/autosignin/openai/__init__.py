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
            res_img_url = img_url
            if res_img_url and not img_url.startswith("http"):
                res_img_url = f"data:image/jpeg;base64,{img_url}"

            if prompt:
                message = [
                    {
                        "role": "system",
                        "content": prompt
                    },
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {"url": res_img_url},
                            },
                            {"type": "text", "text": message},
                        ],
                    }
                ]
            else:
                message = [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {"url": res_img_url},
                            },
                            {"type": "text", "text": message},
                        ],
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
        :param image: 图片
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

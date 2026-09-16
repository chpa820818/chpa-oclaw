from __future__ import annotations

from wenquxing_v2.feishu import _post_message_details


def test_post_message_extracts_instruction_and_file() -> None:
    payload = {
        "title": "",
        "content": [
            [
                {
                    "tag": "text",
                    "text": "汇总上传目录，并生成 HTML 文件发给我",
                }
            ],
            [
                {
                    "tag": "file",
                    "file_key": "file-key",
                    "file_name": "case-files.zip",
                }
            ],
        ],
    }

    text, attachment = _post_message_details(payload)

    assert text == "汇总上传目录，并生成 HTML 文件发给我"
    assert attachment == ("file", "file-key", "case-files.zip")


def test_localized_post_extracts_text_and_image() -> None:
    payload = {
        "post": {
            "zh_cn": {
                "title": "图片处理",
                "content": [
                    [
                        {"tag": "text", "text": "添加标题"},
                        {"tag": "img", "image_key": "image-key"},
                    ]
                ],
            }
        }
    }

    text, attachment = _post_message_details(payload)

    assert text == "图片处理\n添加标题"
    assert attachment == ("image", "image-key", "image.jpg")

"""链接提取工具 - 从用户输入中分离自然语言和链接"""

import re


# URL 匹配模式（支持飞书、http、https）
URL_PATTERN = re.compile(
    r'(?:https?://|feishu://|lark://)?'  # 协议
    r'(?:www\.)?'  # www 前缀
    r'[a-zA-Z0-9-]+\.[a-zA-Z]{2,}'  # 域名
    r'(?:\.[a-zA-Z]{2,})?'  # 可能的二级域名后缀
    r'(?:/[^\s一-鿿]*)?'  # 路径（非中文空白字符）
    r'(?:\?[^\s一-鿿]*)?'  # 查询参数
    r'(?:\#[^\s一-鿿]*)?'  # 片段标识符
    r'|'  # 或者
    r'https?://[^\s一-鿿]+'  # 简单的 http/https 链接
    r'|'  # 或者
    r'jcneyh7qlo8i\.feishu\.cn/[^\s一-鿿]+'  # 飞书链接特例
)


def extract_links(text: str) -> tuple[str, list[str]]:
    """从文本中提取链接，返回 (清理后的文本, 链接列表)

    Args:
        text: 用户输入的原始文本

    Returns:
        (清理后的文本, 链接列表)

    Examples:
        >>> extract_links("这周五开需求评审会 https://feishu.cn/wiki/abc123")
        ("这周五开需求评审会", ["https://feishu.cn/wiki/abc123"])

        >>> extract_links("会议记录 https://a.com 参考文档 https://b.com")
        ("会议记录 参考文档", ["https://a.com", "https://b.com"])
    """
    if not text:
        return "", []

    # 查找所有链接
    links = URL_PATTERN.findall(text)
    if not links:
        return text, []

    # 去重并保持顺序
    seen = set()
    unique_links = []
    for link in links:
        if link and link not in seen:
            seen.add(link)
            unique_links.append(link)

    # 从原文中移除链接
    cleaned_text = text
    for link in unique_links:
        cleaned_text = cleaned_text.replace(link, "")

    # 清理多余空格
    cleaned_text = re.sub(r'\s+', ' ', cleaned_text).strip()

    return cleaned_text, unique_links

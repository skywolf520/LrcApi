import re
from mod.ttscn import t2s

"""
本模块算法针对常见音乐标题匹配场景应用，着重分离度和效率。
Levenshtein Distance算法实际表现不佳
目前没有好的轻量nn实现，不考虑上模型
当前数据集R~=0.8
"""


def text_convert(text: str):
    patterns = [
        r"(?<!^)\([^)]+?\)",
        r"(?<!^)（[^)]+?）",
        r"\s+$",  # 句末空格
    ]

    for pattern in patterns:
        text_re = re.sub(pattern, '', text)
        text = text_re if len(text_re) else text
    return text


# 最长匹配字段
def longest_common_substring(str1, str2):
    m = len(str1)
    n = len(str2)
    # 创建二维数组来存储最长匹配长度
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    max_length = 0  # 最长匹配长度
    # 填充dp数组
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if str1[i - 1] == str2[j - 1]:
                dp[i][j] = dp[i - 1][j - 1] + 1

                if dp[i][j] > max_length:
                    max_length = dp[i][j]
            else:
                dp[i][j] = 0
    # 返回最长匹配长度
    return max_length


def str_duplicate_rate(str1, str2):
    """
    用于计算重复字符
    """
    set1 = set(str1)
    set2 = set(str2)

    common_characters = set1.intersection(set2)
    total_characters = set1.union(set2)

    similarity_ratio = len(common_characters) / len(total_characters)
    return similarity_ratio


def calculate_duplicate_rate(list_1, list_2):
    """
    用于计算重复词素
    """
    count = 0  # 计数器
    for char in list_1:
        char_sim = []

        # 对每个词素进行association计算
        for char_s in list_2:
            char_sim.append(association(char, char_s))
        count += max(char_sim)
    duplicate_rate = count / len(list_1)  # 计算重复率
    return duplicate_rate


# 分级
def association(text_1: str, text_2: str) -> float:
    """
    通过相对最大匹配距离、相对最小编辑长度（ED）
    测量文本相似度
    最长相似、字符重复结合
    权重混合
    :param text_1: 用户传入文本
    :param text_2: 待比较文本
    :return: 相似度 float: 0~1
    """
    if text_1 == '':
        return 0.5
    if text_2 == '':
        return 0
    text_1 = text_1.lower()
    text_2 = text_2.lower()
    common_ratio = longest_common_substring(text_1, text_2) / len(text_1)
    string_dr = str_duplicate_rate(text_1, text_2)
    similar_ratio = common_ratio * (string_dr ** 0.5) ** (1 / 1.5)
    return similar_ratio


def title_association(text_1: str, text_2: str) -> float:
    """
    专用于音乐标题匹配的相似度计算。
    利用标题通常排在版本/后缀信息前面的结构特征，
    通过公共前缀匹配而非最长公共子串，避免后缀劫持匹配结果。

    例: "春日影 (MyGO!!!!! ver.)" vs "栞 (MyGO!!!!! ver.)"
        → 前缀从第一个字就不匹配 → 0.0 (LCS 会因后缀给 0.85)
    例: "春日影 (MyGO!!!!! ver.)" vs "春日影"
        → 前缀匹配 "春日影" → 1.0 (LCS 只给 0.15)

    :param text_1: 查询标题（文件 tag 或用户输入）
    :param text_2: 候选标题（搜索结果）
    :return: 相似度 float: 0~1
    """
    if not text_1 or not text_2:
        return 0.0
    t1 = text_1.lower()
    t2 = text_2.lower()
    if t1 == t2:
        return 1.0

    # 公共前缀匹配 — 标题几乎都在字符串开头
    prefix = 0
    for a, b in zip(t1, t2):
        if a == b:
            prefix += 1
        else:
            break
    shorter = min(len(t1), len(t2))
    prefix_score = prefix / shorter if shorter > 0 else 0.0

    # 包含关系作为后备 — 处理 "潜在表明" ⊂ "潜在表明 - From THE FIRST TAKE"
    if t1 in t2:
        contain_score = len(t1) / len(t2)
    elif t2 in t1:
        contain_score = len(t2) / len(t1)
    else:
        contain_score = 0.0

    return max(prefix_score, contain_score)


def assoc_artists(text_1: str, text_2: str) -> float:
    if text_1 == "":
        return 0.5
    delimiters = [",", "\\", "&", " ", "+", "|", "、", "，", "/"]    # 使用这些分隔符对artists进行分割
    delimiter_pattern = '|'.join(map(re.escape, delimiters))        # 构建正则表达式（自动转义）
    # 对文本进行繁简转换，使用re分割字符串为列表，并使用list-filter函数去除空项
    text_li_1 = list(filter(None, re.split(delimiter_pattern, t2s(text_1))))
    text_li_2 = list(filter(None, re.split(delimiter_pattern, t2s(text_2))))
    ar_ratio = calculate_duplicate_rate(text_li_1, text_li_2)
    return ar_ratio


def zero_item(text: str) -> str:
    punctuation = "'\"?><:;/!@#$%^&*()_-+=！，。、？“”：；【】{}[]（）()|~·`～［］「」｛｝〖〗『』〈〉«»〔〕‹›〝〞‘’＇＇…＃"
    text = text.replace(" ", "")
    for text_z in text:
        if text_z not in punctuation:
            return text_z
    return text[0] if text else text


if __name__ == "__main__":
    text_s = "aaaa&bbbb&ccccc"
    text_r = "aaaa&ccccc&bbbb"
    print(str_duplicate_rate(text_s, text_r))

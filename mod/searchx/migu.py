"""
咪咕音乐 (Migu Music) 歌词搜索后端
参考: https://github.com/any-listen/any-listen-extension-online-metadata/tree/main/src/onlineResource/mg
重写说明：使用 MD5 签名 + TEA 解密 MRC 歌词，与 any-listen 的实现对齐。
重写改进：
- 异步 aiohttp 替代同步 requests
- 增加 textcompare 文本匹配
- 增加 @lru_cache 缓存和 @no_error 异常处理
- 增加 artist/album 参数参与搜索匹配
"""
import json
import aiohttp
import asyncio
import hashlib
import logging
import re
from functools import lru_cache
from mod import textcompare, tools
from mygo.devtools import no_error

logger = logging.getLogger(__name__)

# 咪咕固定参数
DEVICE_ID = '963B7AA0D21511ED807EE5846EC87D20'
SIGNATURE_MD5 = '6cdc72a439cef99a3418d2a78aa28c73'
SIGN_KEY = 'yyapp2d16148780a1dcc7408e06336b98cfd50'

SEARCH_URL = 'https://jadeite.migu.cn/music_search/v3/search/searchAll'
DETAIL_URL = 'https://c.musicapp.migu.cn/MIGUM2.0/v1.0/content/resourceinfo.do'

HEADERS_SEARCH = {
    'uiVersion': 'A_music_3.6.1',
    'channel': '0146921',
    'User-Agent': 'Mozilla/5.0 (Linux; U; Android 11.0.0; zh-cn; MI 11 Build/OPR1.170623.032) '
                  'AppleWebKit/534.30 (KHTML, like Gecko) Version/4.0 Mobile Safari/534.30',
}

HEADERS_LRC = {
    'Referer': 'https://app.c.nf.migu.cn/',
    'User-Agent': 'Mozilla/5.0 (Linux; Android 5.1.1; Nexus 6 Build/LYZ28E) '
                  'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/59.0.3071.115 Mobile Safari/537.36',
    'channel': '0146921',
}


def _create_signature(time_str: str, keyword: str) -> dict:
    """创建咪咕 API 签名"""
    raw = f"{keyword}{SIGNATURE_MD5}{SIGN_KEY}{DEVICE_ID}{time_str}"
    sign = hashlib.md5(raw.encode('utf-8')).hexdigest()
    return {
        'sign': sign,
        'timestamp': time_str,
        'deviceId': DEVICE_ID,
    }


# ========== TEA 解密算法 (咪咕 MRC 歌词) ==========
def _to_long(value):
    """将有符号数转为 64 位有符号范围"""
    MAX = 9223372036854775807
    MIN = -9223372036854775808
    if isinstance(value, str):
        num = int(value, 16)
    else:
        num = int(value)
    if num > MAX:
        return _to_long(num - (1 << 64))
    if num < MIN:
        return _to_long(num + (1 << 64))
    return num


def _long_to_bytes(value: int) -> bytes:
    """将 long 转为 8 字节小端序"""
    result = bytearray(8)
    current = value
    for i in range(8):
        result[i] = current & 0xff
        current >>= 8
    return bytes(result)


def _to_bigint_array(data: str) -> list:
    """将 hex 字符串每 16 字符转为一个 bigint"""
    length = len(data) // 16
    result = []
    for i in range(length):
        chunk = data[i * 16: i * 16 + 16]
        result.append(_to_long(chunk))
    return result


def _tea_decrypt(data: list, key: list) -> list:
    """TEA 解密算法 (32轮, delta=0x9E3779B9)"""
    DELTA = 2654435769
    length = len(data)

    if length >= 1:
        j2 = data[0]
        rounds = int((6 + 52 // length) * DELTA)
        j3 = _to_long(rounds)
        while True:
            j4 = j3
            if j4 == 0:
                break
            j5 = _to_long(3 & _to_long(j4 >> 2))
            j6 = length
            while True:
                j6 -= 1
                if j6 > 0:
                    j7 = data[j6 - 1]
                    i = j6
                    k_idx1 = int(_to_long((3 & j6) ^ j5))
                    part_a = _to_long(j2 ^ j4)
                    part_b = _to_long(j7 ^ key[k_idx1])
                    left = _to_long(part_a + part_b)
                    part_c = _to_long(j7 >> 5)
                    part_d = _to_long(j2 << 2)
                    part_e = _to_long(part_c ^ part_d)
                    part_f = _to_long(j2 >> 3)
                    part_g = _to_long(j7 << 4)
                    part_h = _to_long(part_f ^ part_g)
                    right = _to_long(part_e + part_h)
                    xor_val = _to_long(left ^ right)
                    data[i] = _to_long(data[i] - xor_val)
                    j2 = data[i]
                else:
                    break
            j8 = data[length - 1]
            k_idx2 = int(_to_long((j6 & 3) ^ j5))
            part_i = _to_long(key[k_idx2] ^ j8)
            part_j = _to_long(j2 ^ j4)
            left2 = _to_long(part_i + part_j)
            part_k = _to_long(j8 >> 5)
            part_l = _to_long(j2 << 2)
            part_m = _to_long(part_k ^ part_l)
            part_n = _to_long(j2 >> 3)
            part_o = _to_long(j8 << 4)
            part_p = _to_long(part_n ^ part_o)
            right2 = _to_long(part_m + part_p)
            xor_val2 = _to_long(left2 ^ right2)
            data[0] = _to_long(data[0] - xor_val2)
            j2 = data[0]
            j3 = _to_long(j4 - DELTA)

    return data


TEA_KEY = [
    27303562373562475,
    18014862372307051,
    22799692160172081,
    34058940340699235,
    30962724186095721,
    27303523720101991,
    27303523720101998,
    31244139033526382,
    28992395054481524,
]

MIN_LENGTH = 32


def _mrc_decrypt(data: str) -> str:
    """解密咪咕 MRC 格式歌词 (TEA + UTF-16LE)"""
    if data is None or len(data) < MIN_LENGTH:
        return data
    bigint_data = _to_bigint_array(data)
    decrypted = _tea_decrypt(bigint_data, TEA_KEY)
    chunks = []
    for item in decrypted:
        byte_data = _long_to_bytes(item)
        try:
            chunks.append(byte_data.decode('utf-16-le'))
        except (UnicodeDecodeError, OverflowError):
            pass
    return ''.join(chunks)


def _parse_migu_lyric(lyric_text: str) -> str:
    """
    解析咪咕歌词格式：毫秒时间戳 [开始毫秒,结束毫秒]歌词内容(字1偏移,字1时长)...
    转换为标准 LRC 格式: [mm:ss.xxx]歌词内容
    """
    if not lyric_text:
        return ''
    lyric_text = lyric_text.replace('\r', '')
    lines = lyric_text.split('\n')
    lrc_lines = []

    line_time_pattern = re.compile(r'^\s*\[(\d+),\d+\]')
    word_time_pattern = re.compile(r'\(\d+,\d+\)')

    for line in lines:
        if len(line) < 6:
            continue
        result = line_time_pattern.match(line)
        if not result:
            continue

        start_ms = int(result.group(1))
        ms = start_ms % 1000
        seconds = start_ms // 1000
        m = seconds // 60
        s = seconds % 60
        time_label = f"{m:02d}:{s:02d}.{ms:03d}"

        words = line.replace(result.group(0), '')
        clean_words = word_time_pattern.sub('', words)

        lrc_lines.append(f"[{time_label}]{clean_words}")

    return '\n'.join(lrc_lines)


async def _get_lrc_text(session: aiohttp.ClientSession, url: str) -> str:
    """获取歌词文本，支持重试"""
    for try_num in range(6):
        async with session.get(url, headers=HEADERS_LRC) as resp:
            if resp.status == 200:
                return await resp.text()
            if resp.status == 404:
                return ''
        await asyncio.sleep(0.5 * (try_num + 1))
    return ''


async def _get_music_info(session: aiohttp.ClientSession, song_id: str) -> dict:
    """通过歌曲ID获取详细信息（包含歌词URL）"""
    params = {'resourceType': '2', 'resourceId': song_id}
    async with session.post(DETAIL_URL, headers=HEADERS_LRC, data=params) as resp:
        if resp.status == 200:
            data = await resp.json()
            if data.get('code') == '000000' and data.get('resource'):
                return data['resource'][0] if data['resource'] else {}
    return {}


async def a_search(title='', artist='', album=''):
    """咪咕音乐搜索"""
    if not any((title, artist, album)):
        return None

    result_list = []
    limit = 3

    async with aiohttp.ClientSession(trust_env=True) as session:
        search_str = ' '.join([item for item in [title, artist, album] if item])
        time_str = str(int(asyncio.get_event_loop().time() * 1000))
        sign_data = _create_signature(time_str, search_str)

        params = {
            'isCorrect': '0',
            'isCopyright': '1',
            'searchSwitch': '{"song":1,"album":0,"singer":0,"tagSong":1,"mvSong":0,"bestShow":1,'
                            '"songlist":0,"lyricSong":0}',
            'pageSize': '20',
            'text': search_str,
            'pageNo': '1',
            'sort': '0',
            'sid': 'USS',
        }
        headers = {**HEADERS_SEARCH, **sign_data}

        async with session.get(SEARCH_URL, headers=headers, params=params) as response:
            if response.status != 200:
                return None

            data = await response.json(content_type=None)
            if data.get('code') != '000000':
                return None

            songs = data.get('songResultData', {}).get('resultList', [])
            if isinstance(songs, list) and songs:
                songs = [item for sublist in songs for item in (sublist if isinstance(sublist, list) else [sublist])]

            if not songs:
                return None

            for song in songs:
                song_name = song.get('songName', '')
                singer_list = song.get('singerList', [])
                singer_name = '、'.join([s.get('name', '') for s in singer_list]) if singer_list else ''
                album_name = song.get('album', '')
                song_id = song.get('songId', '')
                copyright_id = song.get('copyrightId', '')
                content_id = song.get('contentId', '')

                title_conform_ratio = textcompare.title_association(title, song_name)
                artist_conform_ratio = textcompare.assoc_artists(artist, singer_name)
                ratio = (title_conform_ratio * (artist_conform_ratio + 1) / 2) ** 0.5

                if ratio >= 0.2:
                    mrc_url = song.get('mrcUrl', '')
                    lrc_url = song.get('lrcUrl', '')

                    if not mrc_url and not lrc_url:
                        resource_id = copyright_id or content_id or song_id
                        detail = await _get_music_info(session, resource_id)
                        mrc_url = detail.get('mrcUrl', '')
                        lrc_url = detail.get('lrcUrl', '')

                    lyrics = ''
                    if mrc_url:
                        encrypted_text = await _get_lrc_text(session, mrc_url)
                        if encrypted_text:
                            try:
                                decrypted = _mrc_decrypt(encrypted_text)
                                lyrics = _parse_migu_lyric(decrypted)
                            except Exception:
                                logger.debug("MRC 解密失败，尝试直接解析")
                                lyrics = _parse_migu_lyric(encrypted_text)
                    elif lrc_url:
                        raw_text = await _get_lrc_text(session, lrc_url)
                        lyrics = tools.standard_lrc(raw_text)

                    if not lyrics:
                        continue

                    img = song.get('img3') or song.get('img2') or song.get('img1') or ''
                    if img and not img.startswith('http'):
                        img = f'http://d.musicapp.migu.cn{img}'

                    music_json_data = {
                        "title": song_name,
                        "album": album_name,
                        "artist": singer_name,
                        "lyrics": lyrics,
                        "cover": img,
                        "id": tools.calculate_md5(
                            f"title:{song_name};artists:{singer_name};album:{album_name}", base='decstr')
                    }
                    result_list.append({"data": music_json_data, "ratio": ratio})

                    if len(result_list) >= limit:
                        break

        sort_li = sorted(result_list, key=lambda x: x['ratio'], reverse=True)
        return [i.get('data') for i in sort_li]


@lru_cache(maxsize=64)
@no_error(throw=logger.info,
          exceptions=(aiohttp.ClientError, asyncio.TimeoutError, KeyError, IndexError, AttributeError))
def search(title='', artist='', album=''):
    return asyncio.run(a_search(title=title, artist=artist, album=album))


if __name__ == "__main__":
    print(search(title="光辉岁月", artist="Beyond"))

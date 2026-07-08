"""
酷我音乐 (Kuwo Music) 歌词搜索后端
参考: https://github.com/any-listen/any-listen-extension-online-metadata/tree/main/src/onlineResource/kw
"""
import json
import aiohttp
import asyncio
import hashlib
import base64
import zlib
import re
import logging
from functools import lru_cache
from mod import textcompare, tools
from mygo.devtools import no_error

logger = logging.getLogger(__name__)

# ========== 酷我 AES 加密签名 ==========
AES_KEY = bytes([112, 87, 39, 61, 199, 250, 41, 191, 57, 68, 45, 114, 221, 94, 140, 228])
APP_ID = 'y67sprxhhpws'

HEADERS_SEARCH = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/116.0.0.0 Safari/537.36',
}

HEADERS_LYRIC = {
    'KG-RC': '1',
    'KG-THash': 'expand_search_manager.cpp:852736169:451',
    'User-Agent': 'KuGou2012-9020-ExpandSearchManager',
}


def _aes_ecb_encrypt(data: bytes, key: bytes) -> bytes:
    """AES-ECB-128-NoPadding 加密（使用 pyaes 库）"""
    try:
        import pyaes
    except ImportError:
        logger.error("需要安装 pyaes 库: pip install pyaes")
        raise
    encrypter = pyaes.Encrypter(pyaes.AESModeOfOperationECB(key))
    encrypted = encrypter.feed(data)
    encrypted += encrypter.feed()
    return encrypted


def _aes_ecb_decrypt(data: bytes, key: bytes) -> bytes:
    """AES-ECB-128-NoPadding 解密"""
    try:
        import pyaes
    except ImportError:
        logger.error("需要安装 pyaes 库: pip install pyaes")
        raise
    decrypter = pyaes.Decrypter(pyaes.AESModeOfOperationECB(key))
    decrypted = decrypter.feed(data)
    decrypted += decrypter.feed()
    return decrypted


def _create_sign(data: str, time_num: int) -> str:
    """创建酷我 wbdCrypto 签名"""
    raw = f"{APP_ID}{data}{time_num}"
    return hashlib.md5(raw.encode('utf-8')).hexdigest().upper()


def _build_wbd_param(json_data: dict) -> str:
    """构建 wbd 加密参数"""
    data_str = json.dumps(json_data)
    time_num = int(asyncio.get_event_loop().time() * 1000) if asyncio.get_event_loop().is_running() else int(__import__('time').time() * 1000)

    encrypted = _aes_ecb_encrypt(data_str.encode('utf-8'), AES_KEY)
    sign = _create_sign(encrypted.hex() if hasattr(encrypted, 'hex') else encrypted.decode('latin-1'), time_num)

    # 对加密数据进行 URL 编码
    import urllib.parse
    encoded_data = urllib.parse.quote(encrypted.decode('latin-1')) if isinstance(encrypted, bytes) else encrypted
    return f"data={encoded_data}&time={time_num}&appId={APP_ID}&sign={sign}"


# ========== 酷我歌词解密 ==========
LYRIC_BUF_KEY = bytes([121, 101, 101, 108, 105, 111, 110])  # "yeelion"
LYRIC_DELIMITER = b'\r\n\r\n'


def _decode_kuwo_lyric(raw_data: bytes, is_lyricx: bool = True) -> str:
    """
    解密酷我歌词
    数据流程：raw bytes -> 解析头部 lrcx 标志 -> zlib inflate -> (如是lrcx: base64 -> XOR) -> UTF-8 文本
    """
    # 检查头部标识
    if raw_data[:10].lower() != b'tp=content':
        return ''

    # 找到分隔符位置
    delimiter_pos = raw_data.find(LYRIC_DELIMITER)
    if delimiter_pos == -1:
        return ''

    # 解析头部，判断是否是 lrcx 格式
    header = raw_data[:delimiter_pos].decode('utf-8', errors='ignore')
    header_lrcx = re.search(r'lrcx\s*=\s*(\d+)', header)
    if header_lrcx:
        is_lyricx = header_lrcx.group(1) == '1'

    # zlib 解压
    lrc_data = zlib.decompress(raw_data[delimiter_pos + 4:])
    lrc_text = lrc_data.decode('utf-8')

    if not is_lyricx:
        return lrc_text

    # lrcx 格式：base64 解码 -> XOR 解密
    try:
        decoded = base64.b64decode(lrc_text)
        output = bytearray(len(decoded))
        key_len = len(LYRIC_BUF_KEY)
        for i in range(len(decoded)):
            output[i] = decoded[i] ^ LYRIC_BUF_KEY[i % key_len]
        return output.decode('utf-8')
    except Exception:
        return lrc_text


def _parse_kuwo_lyric(lrc_text: str) -> str:
    """
    解析酷我歌词并标准化为 LRC 格式
    处理 [kuwo:offset] 标签和逐字标签 <offset,duration>
    """
    if not lrc_text:
        return ''

    lrc_text = lrc_text.replace('\r', '')
    lines = lrc_text.split('\n')

    # 解析 kuwo 标签获取 offset 信息
    offset = 1
    offset2 = 1
    for line in lines:
        match = re.match(r'\[kuwo:\s*([^\]]+)\]', line)
        if match:
            content = match.group(1)
            if '][' in content:
                content = content[:content.index('][')]
            try:
                value = int(content, 8)
                offset = value // 10
                offset2 = value % 10
                if offset == 0 or offset2 == 0:
                    offset, offset2 = 1, 1
            except (ValueError, TypeError):
                pass

    result_lines = []
    tag_lines = []
    word_time_pattern = re.compile(r'<(-?\d+),(-?\d+)(?:,-?\d+)?>')
    word_time_all_pattern = re.compile(r'<(-?\d+),(-?\d+)(?:,-?\d+)?>')
    line_time_pattern = re.compile(r'^(\[\d{1,2}:.*\])\s*(.*)')

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # 检查是否是时间行
        match = line_time_pattern.match(line)
        if match:
            time_tag = match.group(1)
            words = match.group(2) or ''

            # 去除逐字标签
            clean_words = word_time_all_pattern.sub('', words)
            result_lines.append(f"{time_tag}{clean_words}")
        elif re.match(r'\[(ver|ti|ar|al|offset|by|kuwo):', line):
            # 保留元数据标签（除 kuwo 标签外）
            tag_lines.append(line)

    result = []
    if tag_lines:
        result.extend(tag_lines)
    result.extend(result_lines)

    return tools.standard_lrc('\n'.join(result))


async def _get_cover(session: aiohttp.ClientSession, song_id: str) -> str:
    """获取酷我封面"""
    url = f'http://artistpicserver.kuwo.cn/pic.web?corp=kuwo&type=rid_pic&pictype=500&size=500&rid={song_id}'
    # 直接返回URL，让调用方使用
    return url


async def a_search(title='', artist='', album=''):
    """酷我音乐搜索"""
    if not any((title, artist, album)):
        return None

    result_list = []
    limit = 3

    async with aiohttp.ClientSession(trust_env=True) as session:
        search_str = ' '.join([item for item in [title, artist, album] if item])

        # 搜索歌曲
        params = {
            'client': 'kt',
            'all': search_str,
            'pn': '0',
            'rn': '10',
            'uid': '794762570',
            'ver': 'kwplayer_ar_9.2.2.1',
            'vipver': '1',
            'show_copyright_off': '1',
            'newver': '1',
            'ft': 'music',
            'cluster': '0',
            'strategy': '2012',
            'encoding': 'utf8',
            'rformat': 'json',
            'vermerge': '1',
            'mobi': '1',
            'issubtitle': '1',
        }

        async with session.get('http://search.kuwo.cn/r.s', headers=HEADERS_SEARCH, params=params) as response:
            if response.status != 200:
                return None

            data = await response.json(content_type=None)
            song_list = data.get('abslist', [])
            if not song_list:
                return None

            for song in song_list:
                song_id = song.get('MUSICRID', '').replace('MUSIC_', '')
                if not song_id:
                    continue

                song_name = song.get('SONGNAME', '')
                singer_name = song.get('ARTIST', '').replace('&', '、')
                album_name = song.get('ALBUM', '')
                duration = song.get('DURATION', '0')

                title_conform_ratio = textcompare.title_association(title, song_name)
                artist_conform_ratio = textcompare.assoc_artists(artist, singer_name)
                ratio = (title_conform_ratio * (artist_conform_ratio + 1) / 2) ** 0.5

                if ratio >= 0.2:
                    # 获取歌词（使用 mlyric.kuwo.cn 接口）
                    lyrics = ''
                    try:
                        lrc_url = f'http://mlyric.kuwo.cn/mobi.s?f=web&type=lyric&lrcx=0&rid={song_id}&encode=utf8'
                        async with session.get(lrc_url) as lrc_resp:
                            if lrc_resp.status == 200:
                                raw_bytes = await lrc_resp.read()
                                raw_lrc = _decode_kuwo_lyric(raw_bytes, is_lyricx=False)
                                lyrics = _parse_kuwo_lyric(raw_lrc)
                    except Exception as e:
                        logger.debug(f"获取酷我歌词失败: {e}")

                    if not lyrics:
                        continue

                    cover_url = f'http://artistpicserver.kuwo.cn/pic.web?corp=kuwo&type=rid_pic&pictype=500&size=500&rid={song_id}'

                    music_json_data = {
                        "title": song_name,
                        "album": album_name,
                        "artist": singer_name,
                        "lyrics": lyrics,
                        "cover": cover_url,
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

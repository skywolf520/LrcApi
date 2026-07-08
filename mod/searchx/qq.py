"""
QQ音乐 (Tencent Music / QQ Music) 歌词搜索后端
参考: https://github.com/any-listen/any-listen-extension-online-metadata/tree/main/src/onlineResource/tx
简化实现：使用 c.y.qq.com 公开接口，无需 zzc 签名
"""
import json
import aiohttp
import asyncio
import re
import logging
from functools import lru_cache
from urllib.parse import quote
from mod import textcompare, tools
from mygo.devtools import no_error

logger = logging.getLogger(__name__)

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Referer': 'https://y.qq.com/',
}

SEARCH_URL = 'https://shc.y.qq.com/soso/fcgi-bin/client_search_cp'
LYRIC_URL = 'https://c.y.qq.com/lyric/fcgi-bin/fcg_query_lyric_new.fcg'


def _parse_qq_lrc(lyric_text: str) -> str:
    """把 QQ 音乐返回的纯文本歌词标准化为 LRC"""
    if not lyric_text:
        return ''
    lyric_text = lyric_text.strip().replace('\r', '')
    lines = lyric_text.split('\n')
    out = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        out.append(line)
    return tools.standard_lrc('\n'.join(out))


async def a_search(title='', artist='', album=''):
    """QQ音乐搜索"""
    if not any((title, artist, album)):
        return None

    result_list = []
    limit = 3
    search_str = ' '.join([item for item in [title, artist, album] if item])

    async with aiohttp.ClientSession(headers=HEADERS, trust_env=True) as session:
        params = {
            'ct': '24',
            'qqmusic_ver': '1298',
            'remoteplace': 'txt.yqq.top',
            'aggr': '1',
            'cr': '1',
            'catZhida': '1',
            'lossless': '0',
            'flag_qc': '0',
            'p': '1',
            'n': '10',
            'w': search_str,
            'cv': '4747474',
            'format': 'json',
            'inCharset': 'utf-8',
            'outCharset': 'utf-8',
            'notice': '0',
            'platform': 'yqq.json',
            'needNewCode': '0',
            'uin': '0',
            'hostUin': '0',
            'loginUin': '0',
        }

        async with session.get(SEARCH_URL, params=params) as resp:
            if resp.status != 200:
                return None
            text = await resp.text()
            # 返回的是 JSONP，需要去掉回调函数名
            text = text.strip()
            if text.startswith('callback('):
                text = text[9:]
            if text.endswith(')'):
                text = text[:-1]
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                return None

        song_list = data.get('data', {}).get('song', {}).get('list', [])
        if not song_list:
            return None

        for song in song_list:
            song_name = song.get('name', '') or song.get('title', '') or song.get('songname', '')
            singer_list = song.get('singer', [])
            singer_name = ' '.join([s.get('name', '') for s in singer_list]) if singer_list else ''
            album_name = song.get('albumname', '') or (song.get('album', {}) or {}).get('name', '')
            song_mid = song.get('mid', '') or song.get('songmid', '')
            song_id = song.get('id', '') or song.get('songid', '')

            if not song_mid and not song_id:
                continue

            title_conform_ratio = textcompare.title_association(title, song_name)
            artist_conform_ratio = textcompare.assoc_artists(artist, singer_name)
            ratio = (title_conform_ratio * (artist_conform_ratio + 1) / 2) ** 0.5

            if ratio < 0.2:
                continue

            # 获取歌词
            lyrics = ''
            try:
                lyric_params = {
                    'nobase64': '1',
                    'musicid': song_id,
                    'songmid': song_mid,
                    'format': 'json',
                    'platform': 'yqq',
                }
                async with session.get(LYRIC_URL, params=lyric_params) as lrc_resp:
                    if lrc_resp.status == 200:
                        lrc_text = await lrc_resp.text()
                        lrc_text = lrc_text.strip()
                        if lrc_text.startswith('MusicJsonCallback('):
                            lrc_text = lrc_text[len('MusicJsonCallback('):]
                        if lrc_text.endswith(')'):
                            lrc_text = lrc_text[:-1]
                        try:
                            lrc_data = json.loads(lrc_text)
                            lyric = lrc_data.get('lyric', '')
                            trans = lrc_data.get('trans', '')
                            if lyric:
                                lyrics = _parse_qq_lrc(lyric)
                            elif trans:
                                lyrics = _parse_qq_lrc(trans)
                        except json.JSONDecodeError:
                            pass
            except Exception as e:
                logger.debug(f"QQ音乐歌词获取失败: {e}")

            if not lyrics:
                continue

            # 封面
            album_mid = song.get('albummid', '') or (song.get('album', {}) or {}).get('mid', '')
            if album_mid:
                cover = f"https://y.gtimg.cn/music/photo_new/T002R500x500M000{album_mid}.jpg"
            elif singer_list and singer_list[0].get('mid'):
                cover = f"https://y.gtimg.cn/music/photo_new/T001R500x500M000{singer_list[0]['mid']}.jpg"
            else:
                cover = ''

            music_json_data = {
                "title": song_name,
                "album": album_name,
                "artist": singer_name,
                "lyrics": lyrics,
                "cover": cover,
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

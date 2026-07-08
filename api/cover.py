from . import *

import os
import requests

from flask import request, abort, redirect
from urllib.parse import unquote_plus
from mygo.devtools import no_error
from mod.auth import require_auth_decorator

from mod import searchx

LRC_API_URL = os.environ.get('LRC_API_URL', 'https://api.lrc.cx')

headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/"}


def local_cover_search(title: str, artist: str, album: str):
    result: list = searchx.search_all(title=title, artist=artist, album=album, timeout=30)
    for item in result:
        if cover_url := item.get('cover'):
            try:
                res = requests.get(cover_url, headers=headers, timeout=10)
                if res.status_code == 200:
                    content_type = res.headers.get('Content-Type', 'image/jpeg')
                    return res.content, 200, {"Content-Type": content_type}
            except requests.RequestException:
                continue


@app.route('/cover', methods=['GET'], endpoint='cover_endpoint')
@require_auth_decorator(permission='r')
@cache.cached(timeout=86400, key_prefix=make_cache_key)
@no_error(exceptions=AttributeError)
def cover_api():
    title = unquote_plus(request.args.get('title', ''))
    artist = unquote_plus(request.args.get('artist', ''))
    album = unquote_plus(request.args.get('album', ''))
    req_args = {key: request.args.get(key) for key in request.args}
    # 构建目标URL
    target_url = f'{LRC_API_URL}/cover'
    try:
        result = requests.get(target_url, params=req_args, headers=headers, timeout=10)
        if result.status_code == 200:
            content_type = result.headers.get('Content-Type', 'image/jpeg')
            return result.content, 200, {"Content-Type": content_type}
        elif result.status_code == 404:
            pass
    except requests.RequestException:
        # 聚合 API 不可达，降级到本地平台搜索
        pass

    if res := local_cover_search(title, artist, album):
        return res
    abort(404, '未找到封面')


@v1_bp.route('/cover/<path:s_type>', methods=['GET'], endpoint='cover_new_endpoint')
@require_auth_decorator(permission='r')
@cache.cached(timeout=86400, key_prefix=make_cache_key)
@no_error(exceptions=AttributeError)
def cover_new(s_type):
    __endpoints__ = ["music", "album", "artist"]
    if s_type not in __endpoints__:
        abort(404)
    target_url = f'{LRC_API_URL}/cover/{s_type}/'
    if request.query_string:
        target_url += '?' + request.query_string.decode()
    return redirect(target_url, 302)

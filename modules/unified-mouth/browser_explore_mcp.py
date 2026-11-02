"""Read-only public browser for one isolated DSH exploration; no shell tools."""
import asyncio
import ipaddress
import json
import os
import socket
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote, urlsplit

from mcp.server.fastmcp import FastMCP, Image
from playwright.async_api import async_playwright

WORK = Path(os.environ['BROWSER_EXPLORATION_WORK']).resolve()
WORK.mkdir(parents=True, exist_ok=True)
@asynccontextmanager
async def lifespan(server):
    try:
        yield {}
    finally:
        if browser.browser is not None:
            await browser.browser.close()
        if browser.driver is not None:
            await browser.driver.stop()


mcp = FastMCP('interest-browser', lifespan=lifespan)


async def public_url(url):
    parsed = urlsplit(url)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('只允许公开 http/https 网页')
    if parsed.port not in (None, 80, 443):
        raise ValueError('只允许标准网页端口')
    addresses = await asyncio.wait_for(asyncio.to_thread(socket.getaddrinfo, parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM), 5)
    if not addresses or any(not ipaddress.ip_address(x[4][0]).is_global for x in addresses):
        raise ValueError('不可访问本机、内网或保留地址')
    return url


class Browser:
    def __init__(self):
        self.driver = self.browser = self.context = self.page = None
        self.lock = asyncio.Lock()
        self.calls = 0
        self.shots = []
        self.events = []
        self.started = time.monotonic()

    def budget_notice(self):
        if self.calls >= 30 or time.monotonic() - self.started >= 330:
            return '本轮探索已接近次数或时间限制，请整理已有发现、来源和截图，尽快结束。'
        return ''

    def log(self, action, **details):
        self.events.append({'time': time.time(), 'action': action, **details})
        temporary = WORK / 'manifest.new'
        temporary.write_text(json.dumps({'screenshots': self.shots, 'events': self.events}, ensure_ascii=False), 'utf8')
        temporary.replace(WORK / 'manifest.json')

    async def start(self):
        if self.page is not None:
            return
        self.driver = await async_playwright().start()
        self.browser = await self.driver.chromium.launch(headless=True, args=['--disable-dev-shm-usage'])
        self.context = await self.browser.new_context(viewport={'width': 1280, 'height': 900}, locale='zh-CN', accept_downloads=False, service_workers='block')
        async def guard(route):
            try:
                if route.request.method not in ('GET', 'HEAD', 'OPTIONS'):
                    return await route.abort()
                await public_url(route.request.url)
                await route.continue_()
            except Exception:
                await route.abort()
        await self.context.route('**/*', guard)
        self.page = await self.context.new_page()
        self.page.set_default_timeout(8000)

    async def view(self):
        await self.page.wait_for_timeout(700)
        links = await self.page.locator('a[href],button').evaluate_all("els => els.slice(0,80).map((e,i)=>{e.setAttribute('data-explore-ref',String(i+1));return {编号:i+1,文字:(e.innerText||e.getAttribute('aria-label')||'').trim().slice(0,100),链接:e.href||''}})")
        return {'url': self.page.url, 'title': await self.page.title(), 'text': (await self.page.locator('body').inner_text())[:10000], 'links': links}

    async def run(self, action, url='', query='', ref=0, pixels=650, note=''):
        async with self.lock:
            self.calls += 1
            if self.calls > 35:
                raise ValueError('本轮浏览操作已达35次，请整理实际发现并结束')
            await self.start()
            if action == 'open':
                await public_url(url)
                await self.page.goto(url, wait_until='domcontentloaded', timeout=25000)
            elif action == 'search':
                if not query.strip() or len(query) > 300:
                    raise ValueError('搜索词不能为空且不超过300字')
                await self.page.goto('https://www.baidu.com/s?wd=' + quote(query), wait_until='domcontentloaded', timeout=25000)
            elif action == 'click':
                if not 1 <= ref <= 80:
                    raise ValueError('使用最近页面中的链接编号')
                await self.page.locator('[data-explore-ref="'+str(ref)+'"]').click(timeout=10000)
                # Retain ordinary target=_blank navigation inside this run.
                if self.context.pages[-1] is not self.page:
                    self.page = self.context.pages[-1]
            elif action == 'scroll':
                await self.page.mouse.wheel(0, max(-1800, min(1800, pixels)))
            elif action == 'back':
                await self.page.go_back(wait_until='domcontentloaded', timeout=20000)
            elif action == 'screenshot':
                if len(self.shots) >= 5:
                    raise ValueError('本轮已保存5张截图，请挑选已有截图')
                if self.page.url == 'about:blank':
                    raise ValueError('尚未打开网页')
                path = WORK / ('shot-' + str(len(self.shots)+1) + '.jpg')
                await self.page.screenshot(path=str(path), type='jpeg', quality=78, full_page=False, timeout=12000)
                self.shots.append({'path': str(path), 'url': self.page.url, 'title': await self.page.title(), 'note': note[:300], 'created': time.time()})
                self.log(action, url=self.page.url, path=str(path))
                return [json.dumps({**self.shots[-1], **({'程序提醒': self.budget_notice()} if self.budget_notice() else {})}, ensure_ascii=False), Image(path=str(path))]
            elif action != 'inspect':
                raise ValueError('未知浏览操作')
            result = await self.view()
            if self.budget_notice():
                result['程序提醒'] = self.budget_notice()
            self.log(action, url=result['url'], title=result['title'], query=query if action == 'search' else '')
            return json.dumps(result, ensure_ascii=False)


browser = Browser()


@mcp.tool()
async def browse(action: str, url: str = '', query: str = '', ref: int = 0, pixels: int = 650, note: str = ''):
    """浏览公开网页：action=open(url)/search(query)/inspect/click(ref链接编号)/scroll(pixels)/back/screenshot(note截图说明)。截图返回实际画面和文件路径。"""
    return await browser.run(action, url, query, ref, pixels, note)


if __name__ == '__main__':
    mcp.run(transport='stdio')

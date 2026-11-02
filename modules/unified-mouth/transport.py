"""Forward OneBot frames without changing the existing QQ gateway configuration."""

import asyncio
import json
import logging
from pathlib import Path

from websockets.asyncio.client import connect

from store import Store
from qq_voice import Voice,Ingress
from image_ocr import ImageOCR,IncomingEnricher
from image_vision import VisionEnricher
from video_media import VideoEnricher

ROOT = Path("/opt/astrbot-mouth")


def report_forward_failures(results):
    """Retrieve and log task exceptions that gather(return_exceptions=True) retains."""
    for direction,result in zip(('gateway_to_astrbot','astrbot_to_gateway'),results):
        if isinstance(result,BaseException) and not isinstance(result,asyncio.CancelledError):
            logging.getLogger(__name__).error(
                'OneBot forwarding stopped; reconnecting direction=%s',direction,
                exc_info=(type(result),result,result.__traceback__))


async def main():
    """Connect the existing forward socket to AstrBot's native reverse adapter."""
    gateway = json.loads(
        Path("/opt/snowluma/config/onebot_3654076639.json").read_text()
    )
    token = gateway["networks"]["wsServers"][0]["accessToken"]
    local = json.loads((ROOT / "transport.json").read_text())
    store = Store(ROOT)
    voice = VideoEnricher(IncomingEnricher(Voice(ROOT),VisionEnricher(ROOT,inner=ImageOCR(ROOT))),ROOT)
    while True:
        try:
            async with connect(
                "ws://127.0.0.1:13001",
                additional_headers={"Authorization": "Bearer " + token},
                max_size=16 * 1024 * 1024,
            ) as source:
                async with connect(
                    "ws://127.0.0.1:6199/ws",
                    additional_headers={
                        "Authorization": "Bearer " + local["token"],
                        "X-Self-ID": "100000002",
                        "X-Client-Role": "Universal",
                    },
                    max_size=16 * 1024 * 1024,
                ) as target:

                    async def forward(reader, writer, observe=False):
                        ingress=Ingress(store,writer.send,voice) if observe else None
                        try:
                            async for frame in reader:
                                if ingress:await ingress.submit(frame)
                                else:await writer.send(frame)
                            if ingress:await ingress.drain()
                        finally:
                            if ingress:await ingress.close()

                    tasks = [
                        asyncio.create_task(forward(source, target, observe=True)),
                        asyncio.create_task(forward(target, source)),
                    ]
                    _, pending = await asyncio.wait(
                        tasks, return_when=asyncio.FIRST_COMPLETED
                    )
                    for task in pending:
                        task.cancel()
                    results=await asyncio.gather(*tasks, return_exceptions=True)
                    report_forward_failures(results)
        except Exception as exc:
            logging.getLogger(__name__).exception("OneBot transport connection failed; reconnecting")
            await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(main())

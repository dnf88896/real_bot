"""Isolated Edge TTS process. Input is JSON on stdin, never shell arguments."""
import asyncio,json,sys
import edge_tts

async def main():
    job=json.load(sys.stdin)
    await edge_tts.Communicate(job['text'],job['voice'],rate=job['rate'],
                               volume=job['volume'],pitch=job['pitch'],
                               connect_timeout=10,receive_timeout=25).save(job['output'])

if __name__=='__main__':asyncio.run(main())

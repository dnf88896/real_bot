"""QQ native speech transcription before observation capture and native parsing."""
import asyncio,copy,hashlib,json,sqlite3,time,logging
import privacy_commands
import transport_alerts
from contextlib import closing
from pathlib import Path

MARKER='【语音转文字】'


class Voice:
    def __init__(self,root,call=None):
        self.path=Path(root)/'qq-voice.sqlite3'
        self.call=call
        self.slots=asyncio.Semaphore(4)
        with closing(self.connect()) as db,db:
            db.execute('CREATE TABLE IF NOT EXISTS transcripts(key TEXT PRIMARY KEY,status TEXT,text TEXT,error TEXT,created REAL)')

    def connect(self):return sqlite3.connect(self.path,timeout=20)

    def transcribe(self,raw):
        message=raw.get('message')
        if raw.get('post_type')!='message' or not isinstance(message,list):return raw
        voices=[p for p in message if isinstance(p,dict) and p.get('type')=='record']
        if not voices or raw.get('_qq_voice'):return raw
        identity=[str(raw.get('self_id','')),str(raw.get('message_id','')),voices]
        key=hashlib.sha256(json.dumps(identity,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        with closing(self.connect()) as db:
            cached=db.execute('SELECT status,text,error FROM transcripts WHERE key=?',(key,)).fetchone()
        if cached:
            status,text,error=cached
        else:
            text='';error=None;status='failed'
            try:
                if str(raw.get('self_id'))!='100000002':raise ValueError('Wrong QQ account')
                mid=str(raw.get('message_id',''))
                if not mid or not mid.lstrip('-').isascii() or not mid.lstrip('-').isdecimal():raise ValueError('Missing message identifier')
                if self.call is None:
                    from qq_sender import onebot
                    call=onebot
                else:call=self.call
                data=call('fetch_ptt_text',{'message_id':mid})
                text=data.get('text') if isinstance(data,dict) else None
                if not isinstance(text,str) or not text.strip():raise ValueError('Empty transcription')
                text=text.strip();status='ok'
            except Exception as exc:
                text='';error=type(exc).__name__
            with closing(self.connect()) as db,db:
                db.execute('INSERT OR IGNORE INTO transcripts VALUES(?,?,?,?,?)',(key,status,text,error,time.time()))
        result=copy.deepcopy(raw)
        result['_qq_voice']={'status':status,'text':text,'error':error,'engine':'qq.fetch_ptt_text','original_message':copy.deepcopy(message)}
        # QQ transcribes a whole message ID, not individual audio components.
        # Insert its text once at the first record position; retain all originals.
        replaced=False;segments=[]
        for part in message:
            if isinstance(part,dict) and part.get('type')=='record':
                if not replaced:
                    segments.append({'type':'text','data':{'text':MARKER+(' '+text if status=='ok' else '（转写失败，未获得文字）')}})
                    replaced=True
            else:segments.append(copy.deepcopy(part))
        result['message']=segments
        # Do not expose the old CQ record again through string fallbacks.
        result['raw_message']=''.join(p.get('data',{}).get('text','') for p in segments if isinstance(p,dict) and p.get('type')=='text')
        return result

    async def enrich(self,raw):
        if not isinstance(raw.get('message'),list) or not any(isinstance(p,dict) and p.get('type')=='record' for p in raw['message']):return raw
        async with self.slots:return await asyncio.to_thread(self.transcribe,raw)


class Ingress:
    """Ordered per conversation; one failed frame never poisons its successors."""
    def __init__(self,store,send,voice):
        self.store=store;self.send=send;self.voice=voice
        self.tails={};self.tasks=set();self.limit=asyncio.Semaphore(128)

    def failed(self,raw,stage):
        metadata=raw if isinstance(raw,dict) else {}
        logging.getLogger(__name__).exception(
            'OneBot ingress frame failed; frame discarded stage=%s account=%s message_id=%s',
            stage,metadata.get('self_id','?'),metadata.get('message_id','?'))

    async def submit(self,frame):
        raw=None;acquired=False;stage='decode'
        try:
            raw=json.loads(frame)
            if not isinstance(raw,dict):raise ValueError('OneBot frame must be an object')
            if transport_alerts.is_echo(self.store.root,raw):return
            if privacy_commands.is_echo(self.store.root,'qq',str(raw.get('self_id','')),raw):
                return
            if raw.get('post_type')!='message' or raw.get('message_type') not in ('private','group'):
                stage='forward_control'
                await self.send(frame);return
            stage='identity'
            kind=raw['message_type'];conversation=str(raw['group_id'] if kind=='group' else raw['user_id'])
            scope=(str(raw['self_id']),kind,conversation)
            # Check required IDs before queuing; enrichment stays inside the ordered lane.
            sender=str(raw['user_id']);message_id=str(raw['message_id'])
            stage='enqueue'
            await self.limit.acquire();acquired=True
            previous=self.tails.get(scope)
            async def deliver():
                phase='wait_previous'
                try:
                    if previous:await asyncio.gather(previous,return_exceptions=True)
                    phase='privacy_control'
                    if await asyncio.to_thread(privacy_commands.handle,self.store.root,
                        platform='qq',account=str(raw['self_id']),kind=kind,conversation=conversation,
                        sender=sender,message_id=message_id,raw=raw):return
                    phase='enrich'
                    prepared=await self.voice.enrich(raw)
                    phase='capture'
                    await asyncio.to_thread(self.store.capture,platform='qq',account=str(raw['self_id']),kind=kind,
                        conversation=conversation,sender=sender,message_id=message_id,raw=prepared,generate=False)
                    phase='forward_message'
                    await self.send(json.dumps(prepared,ensure_ascii=False))
                except asyncio.CancelledError:
                    raise
                except Exception:
                    self.failed(raw,phase)
            task=asyncio.create_task(deliver());self.tails[scope]=task;self.tasks.add(task)
            def done(completed):
                self.limit.release();self.tasks.discard(completed)
                if self.tails.get(scope) is completed:self.tails.pop(scope,None)
                if not completed.cancelled():
                    error=completed.exception()
                    if error:
                        logging.getLogger(__name__).error(
                            'Unexpected OneBot ingress task failure account=%s message_id=%s',
                            raw['self_id'],raw['message_id'],exc_info=(type(error),error,error.__traceback__))
            task.add_done_callback(done);acquired=False
        except asyncio.CancelledError:
            raise
        except Exception:
            self.failed(raw,stage)
        finally:
            if acquired:self.limit.release()

    async def drain(self):
        while self.tasks:await asyncio.gather(*list(self.tasks),return_exceptions=True)

    async def close(self):
        for task in list(self.tasks):task.cancel()
        await asyncio.gather(*list(self.tasks),return_exceptions=True)

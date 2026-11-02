"""Small unprivileged adapter for the private, local WeChat ASR worker."""
import json
import logging
import socket

SOCKET = '/run/wechat-asr/asr.sock'


def transcribe(message_id, conversation_id):
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(52)
            connection.connect(SOCKET)
            connection.sendall(json.dumps({'message_id': str(message_id),
                'conversation_id': conversation_id}).encode() + b'\n')
            with connection.makefile('rb') as stream:
                data = stream.readline(65537)
        if len(data) > 65536:
            raise ValueError('Oversized ASR result')
        result = json.loads(data)
        if result.get('ok') is not True or result.get('message_id') != str(message_id) or result.get('conversation_id') != conversation_id:
            raise ValueError('ASR unavailable or mismatched')
        return {'transcription': result['text'], 'status': result['status'],
                'engine': result.get('engine'), 'duration_seconds': result.get('duration_seconds'),
                'path': result['audio_path']}
    except (OSError, ValueError, KeyError) as error:
        logging.getLogger('wechat-asr').warning('transcription unavailable message_id=%s type=%s',
                                                message_id, type(error).__name__)
        return {'transcription': '', 'status': 'unavailable'}

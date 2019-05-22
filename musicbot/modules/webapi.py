"""
This cog implement webserver that can respond to bot query which
maybe useful for interfacing with external application.
If you don't need querying via webserver, you shouldn't load this cog

If the API will be exposed to the internet, consider specifying
certificate for security against packet sniffing from third party

DO NOT GIVE GENERATED TOKENS TO UNKNOWN RANDOM PEOPLE!! ANYONE WITH TOKEN
CAN ISSUE REMOTE EXECUTION VIA post:eval and post:exec METHODS. FAILING TO DO
THIS CAN RESULT IN COMPROMISE OF YOUR MACHINE'S SECURITY.

This cog require Python 3.7+
"""

# @TheerapakG: TODO: FUTURE#1776?WEBAPI: use websockets instead
# websockets is already a dependency of discord.py and we shouldn't have to add it to requirements.txt
# it is non-blocking (compared to this which is mostly non-blocking except closing down the server) and easier to deal with than http.server and doesn't require py 3.7 but lose some benefits of concurrency

import socket
import sys
import logging
import asyncio
import threading
import json
import traceback
import os
from discord.ext.commands import Cog, command
from collections import defaultdict
from secrets import token_urlsafe
from urllib.parse import urlparse, parse_qs
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

import discord

from ssl import SSLContext, SSLError

from ..wrappers import owner_only

from .. import messagemanager
from ..rich_guild import get_guild

log = logging.getLogger(__name__)

aiolocks = defaultdict(asyncio.Lock)
 
server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
host = ''
botinst = None

def notify():
    return __doc__

authtoken = list()

async def serialize_tokens():
    directory = 'data/tokens.json'
    async with aiolocks['token_serialization']:
        log.debug("Serializing tokens")

        with open(directory, 'w', encoding='utf8') as f:
            f.write(json.dumps(authtoken))

async def deserialize_tokens() -> list:
    directory = 'data/tokens.json'

    async with aiolocks['token_serialization']:
        if not os.path.isfile(directory):
            return list()

        log.debug("Deserializing tokens")

        with open(directory, 'r', encoding='utf8') as f:
            data = f.read()
    
    return json.loads(data)

webserver = None

class RequestHdlr(BaseHTTPRequestHandler):
    def gen_content_POST(self):
        path = self.path[4:]
        param = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        if 'token' in param and param['token'] in authtoken:
            if path == '/exec':
                if 'code' in param:
                    try:
                        threadsafe_exec_bot(param['code'])
                        return {'action':True, 'error':False, 'result':''}
                    except:
                        return {'action':True, 'error':True, 'result':traceback.format_exc()}
                return {'action':False}
            elif path == '/eval':
                if 'code' in param:
                    try:
                        ret = threadsafe_eval_bot(param['code'])
                        return {'action':True, 'error':False, 'result':str(ret)}
                    except:
                        return {'action':True, 'error':True, 'result':traceback.format_exc()}
                return {'action':False}
        return None

    def gen_content_GET(self):
        path = self.path[4:]
        parse = urlparse(path)
        param = {param_k:param_arglist[-1] for param_k, param_arglist in parse_qs(parse.query).items()}
        if 'token' in param and param['token'] in authtoken and 'get' in param:
            if param['get'] == 'guild':
                return get_guild_list()
            elif param['get'] == 'member' and 'guild' in param:
                return get_member_list(int(param['guild']))
            elif param['get'] == 'player' and 'guild' in param:
                return get_player(int(param['guild']))
        return None

    def do_POST(self):
        if self.path.startswith('/api'):
            f = self.gen_content_POST()
            if f != None:
                self.send_response(200)
                self.send_header("Connection", "close")
                f = json.dumps(f)
                f = f.encode('UTF-8', 'replace')
                self.send_header("Content-Type", "application/json;charset=utf-8")
                log.debug('sending {} bytes'.format(len(f)))
                self.send_header("Content-Length", str(len(f)))
                self.end_headers()
                self.wfile.write(f)
                return
        self.send_error(404)
        self.end_headers()

    def do_GET(self):
        if self.path.startswith('/api'):
            f = self.gen_content_GET()
            if f != None:
                self.send_response(200)
                self.send_header("Connection", "close")
                f = json.dumps(f)
                f = f.encode('UTF-8', 'replace')
                self.send_header("Content-Type", "application/json;charset=utf-8")
                log.debug('sending {} bytes'.format(len(f)))
                self.send_header("Content-Length", str(len(f)))
                self.end_headers()
                self.wfile.write(f)
                return
        self.send_error(404)
        self.end_headers()

    def log_message(self, format, *args):
        log.debug("{addr} - - [{dt}] {args}\n".format(addr = self.address_string(), dt = self.log_date_time_string(), args = format%args))

class Webapi(Cog):
    async def __init__(self):
        self.bot = None

    async def pre_init(self, bot):
        self.bot = bot
        global botinst
        botinst = bot

    async def init(self):
        log.debug('binding to port {0}'.format(self.bot.config.webapi_port))

        if self.bot.config.webapi_persistent_tokens:
            global authtoken
            authtoken = await deserialize_tokens()

        serv = ThreadingHTTPServer((host, self.bot.config.webapi_port), RequestHdlr)
        if self.bot.config.ssl_certfile and self.bot.config.ssl_keyfile:
            try:
                cont = SSLContext()
                cont.load_cert_chain(self.bot.config.ssl_certfile, keyfile = self.bot.config.ssl_keyfile)
            except SSLError:
                log.error('Error loading certificate, falling back to http. Traceback below.')
                log.error(traceback.format_exc())
                log.info('using http for webapi')
            else:
                serv.socket = cont.wrap_socket(sock = serv.socket, server_side = True)
                log.info('using https for webapi')
        else:
            log.info('using http for webapi')
        global webserver
        webserver = serv
        server_thread = threading.Thread(target=serv.serve_forever)
        # Exit the server thread when the main thread terminates
        server_thread.daemon = True
        server_thread.start()

    async def uninit(self):
        log.debug('stopping http server...')
        # @TheerapakG WARN: may cause significant block time
        global webserver
        webserver.shutdown()


    @owner_only
    @command()
    async def cmd_gentoken(self, ctx):
        """
        Usage:
            {command_prefix}gentoken

        Generate a token. DO NOT GIVE GENERATED TOKENS TO UNKNOWN RANDOM PEOPLE!!
        ANYONE WITH TOKEN CAN ISSUE REMOTE EXECUTION VIA post:eval and post:exec METHODS.
        FAILING TO DO THIS CAN RESULT IN COMPROMISE OF YOUR MACHINE'S SECURITY.
        """
        token = str(token_urlsafe(64))
        # @TheerapakG: MAYDO: salt this (actually nevermind, if they got this they probably got the bot token too, and that's worse)
        authtoken.append(token)
        if ctx.bot.config.webapi_persistent_tokens:
            await serialize_tokens()
        await messagemanager.safe_send_message(ctx.author, ctx.bot.str.get('webapi?cmd?gentoken?success@gentoken', "Generated token `{0}`.").format(token))
        await messagemanager.safe_send_message(ctx, ctx.bot.str.get('webapi?cmd?gentoken?success@sent', "Sent a message containing the token generated."), expire_in=20)

    @owner_only
    @command()
    async def cmd_revoketoken(self, ctx, token:str):
        """
        Usage:
            {command_prefix}revoketoken token

        Revoke a token's access to the api.
        """
        try:
            authtoken.remove(token)
            if ctx.bot.config.webapi_persistent_tokens:
                await serialize_tokens()
            await messagemanager.safe_send_message(ctx.author, ctx.bot.str.get('webapi?cmd?revoketoken?success@revtoken', "Successfully revoked token `{0}`").format(token))
        except ValueError:
            await messagemanager.safe_send_message(ctx.author, ctx.bot.str.get('webapi?cmd?revoketoken?fail@revtoken', "Token `{0}` not found").format(token))
        finally:
            await messagemanager.safe_send_message(ctx, ctx.bot.str.get('webapi?cmd?revoketoken?info@action', "Sent a message with information regarding the action."), expire_in=20)

cogs = [Webapi]

def threadsafe_exec_bot(code):
    fut = asyncio.run_coroutine_threadsafe(botinst.exec_bot(code), botinst.loop)
    fut.result() # wait for exec to finish
    return

def threadsafe_eval_bot(code):
    fut = asyncio.run_coroutine_threadsafe(botinst.eval_bot(code), botinst.loop)
    result = fut.result()
    if asyncio.iscoroutine(result):
        resultfut = asyncio.run_coroutine_threadsafe(result, botinst.loop)
        result = resultfut.result()
    return result

def get_guild_list():
    # structure:
    # return = list(guildinfo)
    # guildinfo = dict(guildid, guildname, guildownerid, guildvoice_channelsid, guildtext_channelsid)
    # guildvoice_channelsid = list(guildvoice_channelid)
    # guildtext_channelsid = list(guildtext_channelid)
    async def bot_context_get_guild_list(self):
        guildlist = list()
        for guild in self.guilds.copy():
            guildlist.append({'guildid':guild.id, 'guildname':guild.name, 'guildownerid':guild.owner.id, 'guildvoice_channelsid':[voice_channel.id for voice_channel in guild.voice_channels], 'guildtext_channelsid':[text_channel.id for text_channel in guild.text_channels]})
        return guildlist
    fut = asyncio.run_coroutine_threadsafe(bot_context_get_guild_list(botinst), botinst.loop)
    return fut.result()

def get_member_list(guildid):
    # structure:
    # return = list(memberinfo)
    # memberinfo = dict(memberid, membername, memberdisplay_name, memberstatus, memberactivity)
    # memberactivity = dict('state':'None') | dict('state':'Game', gamename) | dict('state':'Streaming', streamingname, streamingurl)
    async def bot_context_get_member_list(self, guildid):
        guild = self.get_guild(guildid)
        memberlist = list()
        for member in guild.members.copy():
            memberactivity = {'state':'None'}
            if isinstance(member.activity, discord.Game):
                memberactivity = {'state':'Game', 'gamename':member.activity.name}
            elif isinstance(member.activity, discord.Streaming):
                memberactivity = {'state':'Streaming', 'streamingname':member.activity.name, 'streamingurl':member.activity.url}
            memberlist.append({'memberid':member.id, 'membername':member.name, 'memberdisplay_name':member.display_name, 'memberstatus':str(member.status), 'memberactivity':memberactivity})
        return memberlist
    fut = asyncio.run_coroutine_threadsafe(bot_context_get_member_list(botinst, guildid), botinst.loop)
    return fut.result()

def get_player(guildid):
    # structure:
    # return = dict(voiceclientid, playerplaylist, playercurrententry, playerstate, playerkaraokemode) | dict()
    # playerplaylist = list(playerentry)
    # playercurrententry = playerentry | dict()
    # playerentry = dict(entryurl, entrytitle)
    async def bot_context_get_player(self, guildid):
        guild = get_guild(self, self.get_guild(guildid))
        player = await guild.get_player()
        playlist = await player.get_playlist()
        return {'voiceclientid':guild._voice_client.session_id, 'playerplaylist':[{'entryurl':entry.source_url, 'entrytitle':entry.title} for entry in playlist], 'playercurrententry':{'entryurl':player._current.source_url, 'entrytitle':player._current.title} if player._current else dict(), 'playerstate':str(player.state), 'playlistkaraokemode':playlist.karaoke_mode} if player else dict()
    fut = asyncio.run_coroutine_threadsafe(bot_context_get_player(botinst, guildid), botinst.loop)
    return fut.result()
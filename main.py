import discord
from discord.ext import commands
import config

import game.bj as bj

YOUR_BOT_TOKEN = config.BOT_TOKEN

# インテントを有効化
intents = discord.Intents.all()

# Botオブジェクトの生成
bot = commands.Bot(
    command_prefix='/', 
    intents=intents, 
    sync_commands=True,
    activity=discord.Game("森林浴")
)

@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f'ログイン完了: {bot.user}')

# ブラックジャック機能のセットアップ
bj.setup(bot)

# Discordボットを起動
bot.run(YOUR_BOT_TOKEN)

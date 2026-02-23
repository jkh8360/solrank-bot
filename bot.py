import discord
from discord.ext import commands
import sqlite3
import random
import os

TOKEN = os.environ["TOKEN"]
목표점수 = 245
DB = "solrank.db"

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ---------------- DB 초기화 ----------------

def init_db():
    with sqlite3.connect(DB) as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS players(
            guild_id INTEGER,
            user_id INTEGER,
            이름 TEXT,
            팀 TEXT,
            점수 INTEGER DEFAULT 0,
            연승 INTEGER DEFAULT 0,
            PRIMARY KEY(guild_id, user_id)
        )
        """)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS logs(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER,
            user_id INTEGER,
            delta INTEGER,
            prev_streak INTEGER
        )
        """)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS board(
            guild_id INTEGER PRIMARY KEY,
            channel_id INTEGER,
            message_id INTEGER
        )
        """)
        conn.commit()

# ---------------- 팀 색상 ----------------

def 팀이모지(이름):
    name = 이름.lower()
    if "블루" in name or "blue" in name:
        return "🔵"
    if "레드" in name or "red" in name:
        return "🔴"
    if "그린" in name or "green" in name:
        return "🟢"
    if "옐로" in name or "yellow" in name:
        return "🟡"
    if "퍼플" in name or "purple" in name:
        return "🟣"
    return "⚪"

# ---------------- 점수판 ----------------

async def 점수임베드(guild):
    embed = discord.Embed(
        title="🌈 솔랭내기 점수 현황",
        color=discord.Color.blurple()
    )

    with sqlite3.connect(DB) as conn:
        teams = conn.execute("""
        SELECT 팀, SUM(점수)
        FROM players
        WHERE guild_id=?
        GROUP BY 팀
        ORDER BY SUM(점수) DESC
        """,(guild.id,)).fetchall()

        for 팀, 총점 in teams:
            members = conn.execute("""
            SELECT 이름, 점수, 연승 FROM players
            WHERE guild_id=? AND 팀=?
            ORDER BY 점수 DESC
            """,(guild.id, 팀)).fetchall()

            ratio = min((총점 or 0) / 목표점수, 1)
            filled = int(ratio * 15)
            bar = "█" * filled + "░" * (15 - filled)

            text = ""
            for 이름, 점수, 연승 in members:
                streak = f" 🔥{연승}연승" if 연승 >= 2 else ""
                text += f"• {이름} : {점수}점{streak}\n"

            embed.add_field(
                name=f"{팀이모지(팀)} {팀} 팀",
                value=f"총점: {총점} / {목표점수}\n`{bar}`\n\n{text}",
                inline=False
            )

            if 총점 and 총점 >= 목표점수:
                embed.add_field(
                    name="🎉 경기 종료 🎉",
                    value=f"{팀} 팀 목표 달성!",
                    inline=False
                )

    return embed

async def 점수판업데이트(guild):
    with sqlite3.connect(DB) as conn:
        row = conn.execute("""
        SELECT channel_id, message_id FROM board
        WHERE guild_id=?
        """,(guild.id,)).fetchone()

    if not row:
        return

    channel = guild.get_channel(row[0])
    if not channel:
        return

    try:
        message = await channel.fetch_message(row[1])
    except:
        return

    embed = await 점수임베드(guild)
    await message.edit(embed=embed)

# ---------------- 관리자 명령 ----------------

@bot.command()
@commands.has_permissions(administrator=True)
async def 팀설정(ctx, 팀이름, *멤버들: discord.Member):
    with sqlite3.connect(DB) as conn:
        for m in 멤버들:
            conn.execute("""
            INSERT OR REPLACE INTO players
            (guild_id, user_id, 이름, 팀, 점수, 연승)
            VALUES (?, ?, ?, ?, 0, 0)
            """,(ctx.guild.id, m.id, m.display_name, 팀이름))
        conn.commit()
    await ctx.send(f"{팀이름} 팀 설정 완료")

@bot.command()
@commands.has_permissions(administrator=True)
async def 점수판생성(ctx):
    embed = await 점수임베드(ctx.guild)
    msg = await ctx.send(embed=embed)
    with sqlite3.connect(DB) as conn:
        conn.execute("INSERT OR REPLACE INTO board VALUES(?,?,?)",
                     (ctx.guild.id, ctx.channel.id, msg.id))
        conn.commit()
    await ctx.send("점수판 생성 완료 (핀 고정 추천)")

@bot.command()
@commands.has_permissions(administrator=True)
async def 점수조정(ctx, 멤버: discord.Member, 변화량: int):
    with sqlite3.connect(DB) as conn:
        row = conn.execute("""
        SELECT 점수, 연승 FROM players
        WHERE guild_id=? AND user_id=?
        """,(ctx.guild.id, 멤버.id)).fetchone()
        if not row:
            return await ctx.send("등록되지 않은 팀원")

        conn.execute("""
        UPDATE players SET 점수=?
        WHERE guild_id=? AND user_id=?
        """,(row[0]+변화량, ctx.guild.id, 멤버.id))

        conn.execute("""
        INSERT INTO logs(guild_id, user_id, delta, prev_streak)
        VALUES(?,?,?,?)
        """,(ctx.guild.id, 멤버.id, 변화량, row[1]))

        conn.commit()

    await ctx.send(f"🛠 {멤버.display_name} {변화량:+}점 조정")
    await 점수판업데이트(ctx.guild)

# ---------------- 경기 명령 ----------------

@bot.command()
async def 승리(ctx):
    await 점수변경(ctx, True)

@bot.command()
async def 패배(ctx):
    await 점수변경(ctx, False)

async def 점수변경(ctx, win):
    멤버 = ctx.author
    기본 = random.randint(18,23)

    with sqlite3.connect(DB) as conn:
        row = conn.execute("""
        SELECT 점수, 연승 FROM players
        WHERE guild_id=? AND user_id=?
        """,(ctx.guild.id, 멤버.id)).fetchone()

        if not row:
            return await ctx.send("팀에 등록되지 않음")

        prev = row[1]
        연승 = prev + 1 if win else 0
        보너스 = (연승 - 2) * 5 if win and 연승 >= 3 else 0
        delta = 기본 + 보너스 if win else -기본

        conn.execute("""
        UPDATE players SET 점수=?, 연승=?
        WHERE guild_id=? AND user_id=?
        """,(row[0]+delta, 연승, ctx.guild.id, 멤버.id))

        conn.execute("""
        INSERT INTO logs(guild_id, user_id, delta, prev_streak)
        VALUES(?,?,?,?)
        """,(ctx.guild.id, 멤버.id, delta, prev))

        conn.commit()

    await ctx.send(f"{멤버.display_name} {'승리' if win else '패배'} ({delta:+})")
    await 점수판업데이트(ctx.guild)

@bot.command()
async def 되돌리기(ctx):
    멤버 = ctx.author
    with sqlite3.connect(DB) as conn:
        log = conn.execute("""
        SELECT id, delta, prev_streak
        FROM logs
        WHERE guild_id=? AND user_id=?
        ORDER BY id DESC LIMIT 1
        """,(ctx.guild.id, 멤버.id)).fetchone()

        if not log:
            return await ctx.send("되돌릴 기록 없음")

        log_id, delta, prev = log

        conn.execute("""
        UPDATE players SET 점수=점수-?, 연승=?
        WHERE guild_id=? AND user_id=?
        """,(delta, prev, ctx.guild.id, 멤버.id))

        conn.execute("DELETE FROM logs WHERE id=?", (log_id,))
        conn.commit()

    await ctx.send("최근 기록 되돌림 완료")
    await 점수판업데이트(ctx.guild)

@bot.command()
async def 현황(ctx):
    embed = await 점수임베드(ctx.guild)
    await ctx.send(embed=embed)

# ---------------- 실행 ----------------

@bot.event
async def on_ready():
    init_db()
    print("봇 실행 완료")

bot.run(TOKEN)
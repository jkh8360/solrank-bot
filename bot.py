import os
import random
import sqlite3
import discord
from discord import app_commands

TOKEN = os.environ["TOKEN"]
DB = "solrank.db"
TARGET_SCORE = 245

intents = discord.Intents.default()
intents.members = True

client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

# ---------------- DB ----------------

def db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with db() as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS players(
            guild_id INTEGER,
            user_id INTEGER,
            name TEXT,
            team TEXT,
            score INTEGER DEFAULT 0,
            streak INTEGER DEFAULT 0,
            PRIMARY KEY(guild_id, user_id)
        )
        """)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS system(
            guild_id INTEGER PRIMARY KEY,
            board_channel INTEGER,
            board_message INTEGER,
            game_channel INTEGER,
            active INTEGER DEFAULT 1
        )
        """)
        conn.commit()

# ---------------- 유틸 ----------------

def roll():
    return random.randint(18, 23)

def bonus(streak):
    return (streak-2)*5 if streak>=3 else 0

def get_system(guild_id):
    with db() as conn:
        return conn.execute(
            "SELECT * FROM system WHERE guild_id=?",
            (guild_id,)
        ).fetchone()

def check_game_channel(interaction):
    s = get_system(interaction.guild.id)
    if not s:
        return False
    return interaction.channel.id == s["game_channel"]

async def update_board(guild):
    s = get_system(guild.id)
    if not s:
        return
    channel = guild.get_channel(s["board_channel"])
    message = await channel.fetch_message(s["board_message"])

    embed = discord.Embed(
        title="🌈 솔랭내기 점수 현황",
        color=discord.Color.blurple()
    )

    with db() as conn:
        rows = conn.execute("""
        SELECT team, SUM(score) total
        FROM players
        WHERE guild_id=?
        GROUP BY team
        ORDER BY total DESC
        """,(guild.id,)).fetchall()

        for r in rows:
            members = conn.execute("""
            SELECT name, score, streak
            FROM players
            WHERE guild_id=? AND team=?
            ORDER BY score DESC
            """,(guild.id,r["team"])).fetchall()

            text=""
            for m in members:
                streak = f" 🔥{m['streak']}연승" if m["streak"]>=2 else ""
                text+=f"{m['name']} {m['score']}점{streak}\n"

            embed.add_field(
                name=f"{r['team']} ({r['total']}점)",
                value=text or "없음",
                inline=False
            )

            if r["total"]>=TARGET_SCORE:
                embed.add_field(
                    name="🎉 경기 종료",
                    value=f"{r['team']} 팀 승리!",
                    inline=False
                )
                with db() as conn2:
                    conn2.execute(
                        "UPDATE system SET active=0 WHERE guild_id=?",
                        (guild.id,)
                    )
                    conn2.commit()

    await message.edit(embed=embed)

# ---------------- 시스템 생성 ----------------

@tree.command(name="점수판생성", description="솔랭내기 시스템 자동 생성")
@app_commands.checks.has_permissions(administrator=True)
async def create_system(interaction: discord.Interaction):

    guild = interaction.guild

    category = discord.utils.get(guild.categories, name="솔랭내기")
    if category is None:
        category = await guild.create_category("솔랭내기")

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(send_messages=False),
        guild.me: discord.PermissionOverwrite(send_messages=True)
    }

    board = await guild.create_text_channel(
        "점수판",
        category=category,
        overwrites=overwrites
    )

    game = await guild.create_text_channel(
        "게임-채팅",
        category=category
    )

    embed = discord.Embed(
        title="🌈 솔랭내기 점수 현황",
        description="점수 변동 시 자동 갱신됩니다.",
        color=discord.Color.blurple()
    )

    msg = await board.send(embed=embed)

    with db() as conn:
        conn.execute("""
        INSERT OR REPLACE INTO system
        VALUES(?,?,?,?,1)
        """,(guild.id, board.id, msg.id, game.id))
        conn.commit()

    await interaction.response.send_message(
        f"✅ 생성 완료\n📊 {board.mention}\n🎮 {game.mention}",
        ephemeral=True
    )

# ---------------- 기본 명령 ----------------

async def result(interaction, win):
    if not check_game_channel(interaction):
        return await interaction.response.send_message(
            "🎮 게임-채팅 채널에서만 사용 가능",
            ephemeral=True
        )

    s = get_system(interaction.guild.id)
    if not s["active"]:
        return await interaction.response.send_message(
            "⛔ 게임 종료 상태",
            ephemeral=True
        )

    member = interaction.user
    r = roll()

    with db() as conn:
        p = conn.execute("""
        SELECT score, streak FROM players
        WHERE guild_id=? AND user_id=?
        """,(interaction.guild.id,member.id)).fetchone()

        if not p:
            return await interaction.response.send_message(
                "팀에 등록되지 않음",
                ephemeral=True
            )

        new_streak = p["streak"]+1 if win else 0
        b = bonus(new_streak)
        delta = r+b if win else -r

        conn.execute("""
        UPDATE players
        SET score=?, streak=?
        WHERE guild_id=? AND user_id=?
        """,(p["score"]+delta,new_streak,
            interaction.guild.id,member.id))
        conn.commit()

    await interaction.response.send_message(
        f"{'승리' if win else '패배'} {delta:+}"
    )

    await update_board(interaction.guild)

@tree.command(name="승리", description="승리 처리")
async def win(interaction: discord.Interaction):
    await result(interaction, True)

@tree.command(name="패배", description="패배 처리")
async def lose(interaction: discord.Interaction):
    await result(interaction, False)

# ---------------- 도움말 ----------------

@tree.command(name="도움말", description="명령어 안내")
async def help_cmd(interaction: discord.Interaction):

    embed = discord.Embed(
        title="🌈 솔랭내기 명령어 안내",
        description="""
🎮 기본
/승리
/패배
/현황

👑 관리자
/점수판생성
""",
        color=discord.Color.blurple()
    )

    await interaction.response.send_message(embed=embed, ephemeral=True)

# ---------------- 실행 ----------------

@client.event
async def on_ready():
    init_db()
    await tree.sync()
    print("슬래시 완전통합 봇 실행 완료")

client.run(TOKEN)
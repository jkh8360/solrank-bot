import os
import random
import sqlite3
import discord
from discord import app_commands

import threading
from flask import Flask
import os

app = Flask(__name__)

@app.route("/")
def home():
    return "Bot is running!"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

threading.Thread(target=run_web).start()

TOKEN = os.environ["TOKEN"]
DB = "solrank.db"

intents = discord.Intents.default()
intents.members = True

client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

# ================= DB =================

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
        CREATE TABLE IF NOT EXISTS logs(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER,
            user_id INTEGER,
            delta INTEGER,
            prev_streak INTEGER
        )
        """)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS system(
            guild_id INTEGER PRIMARY KEY,
            board_channel INTEGER,
            board_message INTEGER,
            game_channel INTEGER,
            active INTEGER DEFAULT 1,
            target_score INTEGER DEFAULT 245
        )
        """)
        conn.commit()

# ================= 유틸 =================

def roll():
    return random.randint(18, 23)

def bonus(streak):
    return (streak - 2) * 5 if streak >= 3 else 0

def get_system(guild_id):
    with db() as conn:
        return conn.execute(
            "SELECT * FROM system WHERE guild_id=?",
            (guild_id,)
        ).fetchone()

def check_game_channel(interaction):
    s = get_system(interaction.guild.id)
    return s and interaction.channel.id == s["game_channel"]

def get_target(guild_id):
    with db() as conn:
        row = conn.execute(
            "SELECT target_score FROM system WHERE guild_id=?",
            (guild_id,)
        ).fetchone()
    return row["target_score"] if row else 245

def progress_bar(current, target, length=15):
    ratio = min(current / target, 1)
    filled = int(length * ratio)
    empty = length - filled
    return "█" * filled + "░" * empty + f" {int(ratio*100)}%"

# ================= 점수판 갱신 =================

TEAM_STYLE = {
    "블루팀": {
        "icon": "🔵",
        "color": discord.Color.from_rgb(20, 40, 120),
        "display": "블루팀"
    },
    "레드팀": {
        "icon": "🔴",
        "color": discord.Color.from_rgb(150, 20, 20),
        "display": "레드팀"
    },
    "그린팀": {
        "icon": "🟢",
        "color": discord.Color.from_rgb(20, 120, 60),
        "display": "그린팀"
    },
    "퍼플팀": {
        "icon": "🟣",
        "color": discord.Color.from_rgb(100, 40, 140),
        "display": "퍼플팀"
    },
}

async def update_board(guild):
    s = get_system(guild.id)
    if not s:
        return

    channel = guild.get_channel(s["board_channel"])
    if not channel:
        return

    try:
        message = await channel.fetch_message(s["board_message"])
    except:
        return

    target = get_target(guild.id)

    with db() as conn:
        rows = conn.execute("""
        SELECT team, SUM(score) total
        FROM players
        WHERE guild_id=?
        GROUP BY team
        ORDER BY total DESC
        """,(guild.id,)).fetchall()

        if not rows:
            return

        top_team = rows[0]["team"]
        embed_color = TEAM_STYLE.get(top_team, {}).get(
            "color", discord.Color.dark_gray()
        )

        embed = discord.Embed(
            title="🏆솔랭내기🏆",
            description="찾아라, 솔랭전사!",
            color=embed_color
        )

        for idx, r in enumerate(rows):

            style = TEAM_STYLE.get(r["team"], {})
            icon = style.get("icon", "⚪")
            display_name = style.get("display", r["team"].upper())

            members = conn.execute("""
            SELECT user_id, name, score, streak
            FROM players
            WHERE guild_id=? AND team=?
            ORDER BY score DESC
            """,(guild.id, r["team"])).fetchall()

            if not members:
                continue

            top_member = members[0]

            # 헤더 (오른쪽 정렬 느낌)
            header = f"{icon} {display_name}"
            if idx == 0:
                header = "🏆 " + header

            header += f"  |  {r['total']}"

            team_text = ""
            team_text += progress_bar(r["total"], target) + "\n\n"

            for m in members:
                crown = " 👑" if m["user_id"] == top_member["user_id"] else ""
                streak = f" 🔥{m['streak']}" if m["streak"] >= 2 else ""

                name_part = f"{m['name']}"
                score_part = f"{m['score']}"

                team_text += f"{name_part:<10} {score_part:>4}{crown}{streak}\n"

            team_text += "\n────────────────────────"

            embed.add_field(
                name=header,
                value=f"```{team_text}```",
                inline=False
            )

            if r["total"] >= target:
                embed.add_field(
                    name="🏁 MATCH RESULT",
                    value=f"WINNER : {display_name}",
                    inline=False
                )
                conn.execute(
                    "UPDATE system SET active=0 WHERE guild_id=?",
                    (guild.id,)
                )
                conn.commit()

    await message.edit(embed=embed)

# ================= 버튼 UI =================

class DodgeModal(discord.ui.Modal, title="닷지 점수 입력"):
    감점 = discord.ui.TextInput(label="차감할 점수", placeholder="예: 15")

    async def on_submit(self, interaction: discord.Interaction):
        try:
            value = int(self.감점.value)
        except:
            return await interaction.response.send_message("숫자만 입력하세요", ephemeral=True)

        if value <= 0:
            return await interaction.response.send_message("1 이상 입력하세요", ephemeral=True)

        await dodge_logic(interaction, value)

class GameView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="승리",
        style=discord.ButtonStyle.success,
        emoji="🟢",
        custom_id="game_win"
    )
    async def win_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await result(interaction, True)

    @discord.ui.button(
        label="패배",
        style=discord.ButtonStyle.danger,
        emoji="🔴",
        custom_id="game_lose"
    )
    async def lose_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await result(interaction, False)

    @discord.ui.button(
        label="닷지",
        style=discord.ButtonStyle.secondary,
        emoji="🟡",
        custom_id="game_dodge"
    )
    async def dodge_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(DodgeModal())

# ================= 닷지 =================
async def dodge_logic(interaction, 감점):
    member = interaction.user

    if not interaction.response.is_done():
        await interaction.response.defer()

    with db() as conn:
        p = conn.execute("""
        SELECT score, streak FROM players
        WHERE guild_id=? AND user_id=?
        """,(interaction.guild.id,member.id)).fetchone()

        if not p:
            return await interaction.response.send_message("팀 미등록", ephemeral=True)

        conn.execute("""
        UPDATE players
        SET score=?, streak=0
        WHERE guild_id=? AND user_id=?
        """,(p["score"]-감점,
            interaction.guild.id,member.id))

        conn.execute("""
        INSERT INTO logs(guild_id,user_id,delta,prev_streak)
        VALUES(?,?,?,?)
        """,(interaction.guild.id,member.id,-감점,p["streak"]))

        conn.commit()

    await interaction.followup.send(f"닷지 -{감점}점")
    await update_board(interaction.guild)

# ================= 팀 설정(추가) =================
# 원본에 없던 기능 추가: 관리자만 유저를 팀에 등록/변경
# - 기존 점수/연승 유지
# - name/display_name 최신으로 갱신

TEAM_CHOICES = [
    app_commands.Choice(name="🔵 블루팀", value="블루팀"),
    app_commands.Choice(name="🔴 레드팀", value="레드팀"),
    app_commands.Choice(name="🟢 그린팀", value="그린팀"),
    app_commands.Choice(name="🟣 퍼플팀", value="퍼플팀"),
]

@tree.command(name="팀설정", description="팀 등록/변경")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.choices(팀이름=TEAM_CHOICES)
async def set_team(
    interaction: discord.Interaction,
    팀이름: app_commands.Choice[str],
    멤버1: discord.Member,
    멤버2: discord.Member = None,
    멤버3: discord.Member = None,
    멤버4: discord.Member = None,
):
    team_value = 팀이름.value
    members = [m for m in [멤버1, 멤버2, 멤버3, 멤버4] if m]

    await interaction.response.defer()

    with db() as conn:
        for 멤버 in members:
            existing = conn.execute("""
            SELECT score, streak
            FROM players
            WHERE guild_id=? AND user_id=?
            """, (interaction.guild.id, 멤버.id)).fetchone()

            score = existing["score"] if existing else 0
            streak = existing["streak"] if existing else 0

            conn.execute("""
            INSERT OR REPLACE INTO players(guild_id, user_id, name, team, score, streak)
            VALUES(?,?,?,?,?,?)
            """, (interaction.guild.id, 멤버.id,
                멤버.display_name, team_value, score, streak))

        conn.commit()

    await interaction.followup.send(
        f"✅ {len(members)}명 → **{team_value}** 설정 완료"
    )

    await update_board(interaction.guild)

# ================= 시스템 생성 =================

@tree.command(name="점수판생성", description="솔랭내기 시스템 자동 생성")
@app_commands.checks.has_permissions(administrator=True)
async def create_system(interaction: discord.Interaction):

    await interaction.response.defer(ephemeral=True)  # ⭐ 중요

    guild = interaction.guild

    category = discord.utils.get(guild.categories, name="솔랭내기")
    if category is None:
        category = await guild.create_category("솔랭내기")

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(send_messages=False),
        guild.me: discord.PermissionOverwrite(send_messages=True)
    }

    board = discord.utils.get(category.text_channels, name="점수판")
    if board is None:
        board = await guild.create_text_channel(
            "점수판",
            category=category,
            overwrites=overwrites
        )

    game = discord.utils.get(category.text_channels, name="게임-채팅")
    if game is None:
        game = await guild.create_text_channel(
            "게임-채팅",
            category=category
        )

    target = get_target(guild.id)

    embed = discord.Embed(
        title=f"🌈 솔랭내기 점수 현황 (목표 {target}점)",
        description="점수 변동 시 자동 갱신됩니다.",
        color=discord.Color.blurple()
    )

    msg = await board.send(embed=embed)

    with db() as conn:
        conn.execute("""
        INSERT OR REPLACE INTO system(guild_id, board_channel, board_message, game_channel, active, target_score)
        VALUES(?,?,?,?,1,?)
        """, (guild.id, board.id, msg.id, game.id, target))
        conn.commit()

    await interaction.followup.send(
        f"✅ 생성 완료\n📊 {board.mention}\n🎮 {game.mention}"
    )

# ================= 승패 =================

async def result(interaction, win):
    if not check_game_channel(interaction):
        return await interaction.response.send_message(
            "🎮 게임-채팅 채널에서만 사용 가능",
            ephemeral=True
        )

    s = get_system(interaction.guild.id)
    if not s or not s["active"]:
        return await interaction.response.send_message(
            "⛔ 게임 종료 상태",
            ephemeral=True
        )

    member = interaction.user
    r = roll()

    await interaction.response.defer()

    with db() as conn:
        p = conn.execute("""
        SELECT score, streak FROM players
        WHERE guild_id=? AND user_id=?
        """,(interaction.guild.id, member.id)).fetchone()

        if not p:
            return await interaction.followup.send(
                "팀에 등록되지 않음",
                ephemeral=True
            )

        new_streak = p["streak"] + 1 if win else 0
        b = bonus(new_streak)
        delta = (r + b) if win else -r

        conn.execute("""
        UPDATE players
        SET score=?, streak=?
        WHERE guild_id=? AND user_id=?
        """, (p["score"] + delta, new_streak,
              interaction.guild.id, member.id))

        conn.execute("""
        INSERT INTO logs(guild_id,user_id,delta,prev_streak)
        VALUES(?,?,?,?)
        """, (interaction.guild.id, member.id, delta, p["streak"]))

        conn.commit()

    await interaction.followup.send(
        f"{'승리' if win else '패배'} {delta:+}"
    )

    await update_board(interaction.guild)

@tree.command(name="승리", description="승리 처리")
async def win(interaction: discord.Interaction):
    await result(interaction, True)

@tree.command(name="패배", description="패배 처리")
async def lose(interaction: discord.Interaction):
    await result(interaction, False)

# ================= 닷지 =================

@tree.command(name="닷지", description="닷지 감점 처리")
@app_commands.describe(감점="차감할 점수 입력")
async def dodge(interaction: discord.Interaction, 감점: int):

    if not check_game_channel(interaction):
        return await interaction.response.send_message(
            "🎮 게임-채팅 채널에서만 사용 가능",
            ephemeral=True
        )

    member = interaction.user
    await interaction.response.defer()

    with db() as conn:
        p = conn.execute("""
        SELECT score, streak FROM players
        WHERE guild_id=? AND user_id=?
        """,(interaction.guild.id, member.id)).fetchone()

        if not p:
            return await interaction.response.send_message("팀 미등록", ephemeral=True)

        conn.execute("""
        UPDATE players
        SET score=?, streak=0
        WHERE guild_id=? AND user_id=?
        """, (p["score"] - 감점,
              interaction.guild.id, member.id))

        conn.execute("""
        INSERT INTO logs(guild_id,user_id,delta,prev_streak)
        VALUES(?,?,?,?)
        """,(interaction.guild.id, member.id, -감점, p["streak"]))

        conn.commit()

    await interaction.followup.send(f"닷지 -{감점}점")
    await update_board(interaction.guild)

# ================= 듀오 =================

async def duo_result(interaction, m1, m2, win):

    if not check_game_channel(interaction):
        return await interaction.response.send_message(
            "🎮 게임-채팅 채널에서만 사용 가능",
            ephemeral=True
        )

    base = roll()

    if win:
        delta = int(base * 0.75) # 승리 시 0.75배
    else:
        delta = -base

    await interaction.response.defer()

    with db() as conn:
        for m in [m1, m2]:
            p = conn.execute("""
            SELECT score, streak FROM players
            WHERE guild_id=? AND user_id=?
            """,(interaction.guild.id, m.id)).fetchone()

            if not p:
                return await interaction.response.send_message("팀 미등록 유저 있음")

            new_streak = p["streak"] + 1 if win else 0

            conn.execute("""
            UPDATE players
            SET score=?, streak=?
            WHERE guild_id=? AND user_id=?
            """,(p["score"] + delta, new_streak,
                interaction.guild.id, m.id))

            conn.execute("""
            INSERT INTO logs(guild_id,user_id,delta,prev_streak)
            VALUES(?,?,?,?)
            """,(interaction.guild.id, m.id, delta, p["streak"]))

        conn.commit()

    await interaction.followup.send(
        f"듀오 {'승리' if win else '패배'} 각자 {delta:+}"
    )

    await update_board(interaction.guild)

@tree.command(name="듀오승리", description="듀오 승리 처리")
async def duo_win(interaction: discord.Interaction,
                  멤버1: discord.Member,
                  멤버2: discord.Member):
    await duo_result(interaction, 멤버1, 멤버2, True)

@tree.command(name="듀오패배", description="듀오 패배 처리")
async def duo_lose(interaction: discord.Interaction,
                   멤버1: discord.Member,
                   멤버2: discord.Member):
    await duo_result(interaction, 멤버1, 멤버2, False)

# ================= 되돌리기 =================

@tree.command(name="되돌리기", description="최근 점수 변경 취소")
async def undo(interaction: discord.Interaction):

    if not check_game_channel(interaction):
        return await interaction.response.send_message(
            "🎮 게임-채팅 채널에서만 사용 가능",
            ephemeral=True
        )

    member = interaction.user
    await interaction.response.defer()

    with db() as conn:
        log = conn.execute("""
        SELECT id, delta, prev_streak
        FROM logs
        WHERE guild_id=? AND user_id=?
        ORDER BY id DESC LIMIT 1
        """,(interaction.guild.id, member.id)).fetchone()

        if not log:
            return await interaction.response.send_message("되돌릴 기록 없음")

        conn.execute("""
        UPDATE players
        SET score=score-?, streak=?
        WHERE guild_id=? AND user_id=?
        """,(log["delta"], log["prev_streak"],
            interaction.guild.id, member.id))

        conn.execute("DELETE FROM logs WHERE id=?",(log["id"],))
        conn.commit()

    await interaction.followup.send("최근 기록 되돌림 완료")
    await update_board(interaction.guild)

# ================= 관리자 =================

# ================= 점수조정 ===============

@tree.command(name="점수조정", description="관리자 점수 증감 조정")
@app_commands.checks.has_permissions(administrator=True)
async def adjust_score(interaction: discord.Interaction,
                       멤버: discord.Member,
                       변화량: int):

    await interaction.response.defer()

    with db() as conn:
        p = conn.execute("""
        SELECT score, streak FROM players
        WHERE guild_id=? AND user_id=?
        """,(interaction.guild.id, 멤버.id)).fetchone()

        if not p:
            return await interaction.response.send_message(
                "팀에 등록되지 않은 유저",
                ephemeral=True
            )

        new_score = p["score"] + 변화량

        conn.execute("""
        UPDATE players
        SET score=?
        WHERE guild_id=? AND user_id=?
        """,(new_score, interaction.guild.id, 멤버.id))

        conn.execute("""
        INSERT INTO logs(guild_id,user_id,delta,prev_streak)
        VALUES(?,?,?,?)
        """,(interaction.guild.id, 멤버.id, 변화량, p["streak"]))

        conn.commit()

    await interaction.followup.send(
        f"🛠 {멤버.display_name} {변화량:+}점 적용 (현재 {new_score}점)"
    )

    await update_board(interaction.guild)

# ================= 게임재시작 ===============

@tree.command(name="게임재시작", description="점수 초기화 (팀 유지)")
@app_commands.checks.has_permissions(administrator=True)
async def restart(interaction: discord.Interaction):
    await interaction.response.defer()

    with db() as conn:
        conn.execute("""
        UPDATE players
        SET score=0, streak=0
        WHERE guild_id=?
        """,(interaction.guild.id,))
        conn.execute("""
        UPDATE system SET active=1
        WHERE guild_id=?
        """,(interaction.guild.id,))
        conn.commit()

    await interaction.followup.send("게임 재시작 완료")
    await update_board(interaction.guild)

# ================= 전체초기화 ===============

@tree.command(name="전체초기화", description="모든 데이터 및 채널 삭제")
@app_commands.checks.has_permissions(administrator=True)
async def reset_all(interaction: discord.Interaction):

    await interaction.response.defer(ephemeral=True)

    guild = interaction.guild
    s = get_system(guild.id)

    # 1️⃣ 채널 삭제
    if s:
        board_channel = guild.get_channel(s["board_channel"])
        game_channel = guild.get_channel(s["game_channel"])

        try:
            if board_channel:
                await board_channel.delete()
        except:
            pass

        try:
            if game_channel:
                await game_channel.delete()
        except:
            pass

    # 2️⃣ 카테고리 삭제 (이름 기준)
    category = discord.utils.get(guild.categories, name="솔랭내기")
    if category:
        try:
            await category.delete()
        except:
            pass

    # 3️⃣ DB 완전 삭제
    with db() as conn:
        conn.execute("DELETE FROM players WHERE guild_id=?", (guild.id,))
        conn.execute("DELETE FROM logs WHERE guild_id=?", (guild.id,))
        conn.execute("DELETE FROM system WHERE guild_id=?", (guild.id,))
        conn.commit()

    await interaction.followup.send("🗑 솔랭내기 시스템 완전 삭제 완료")

# ================= 목표점수설정 ===============

@tree.command(name="목표점수설정", description="목표 점수 변경")
@app_commands.checks.has_permissions(administrator=True)
async def set_target(interaction: discord.Interaction, 점수: int):

    if 점수 <= 0:
        return await interaction.response.send_message(
            "1 이상 입력하세요",
            ephemeral=True
        )
    
    await interaction.response.defer()

    with db() as conn:
        row = conn.execute(
            "SELECT board_channel FROM system WHERE guild_id=?",
            (interaction.guild.id,)
        ).fetchone()

        if not row:
            return await interaction.response.send_message(
                "먼저 /점수판생성 실행하세요",
                ephemeral=True
            )

        conn.execute("""
        UPDATE system
        SET target_score=?
        WHERE guild_id=?
        """,(점수, interaction.guild.id))

        conn.commit()

    await interaction.followup.send(
        f"🎯 목표 점수 {점수}점으로 변경 완료"
    )

    await update_board(interaction.guild)

# ================= 도움말 =================

@tree.command(name="도움말", description="솔랭내기 명령어 및 규칙 안내")
async def help_cmd(interaction: discord.Interaction):

    target = get_target(interaction.guild.id)

    embed = discord.Embed(
        title="🌈 솔랭내기 시스템 안내",
        description="친구 5~6명용 내기 시스템",
        color=discord.Color.blurple()
    )

    embed.add_field(
        name="🎮 기본 명령어",
        value="""
/승리 → 18~23점 + 연승 보너스  
/패배 → 18~23점 차감  
/되돌리기 → 최근 기록 1회 취소  
/닷지 감점 → 입력한 점수만큼 차감  
/듀오승리 멤버1 멤버2 → 각자 0.75배 점수  
/듀오패배 멤버1 멤버2 → 각자 1배 차감
""",
        inline=False
    )

    embed.add_field(
        name="🔥 연승 보너스",
        value="""
3연승 → +5  
4연승 → +10  
5연승 → +15  
이후 연승마다 +5씩 증가  
(공식: (연승 - 2) × 5)
""",
        inline=False
    )

    # (버그수정) 고정 TARGET_SCORE 대신 현재 목표점수 표시
    embed.add_field(
        name="🏁 승리 조건",
        value=f"""
팀 총합 {target}점 달성 시 자동 종료  
점수판에 🎉 표시됨
""",
        inline=False
    )

    embed.add_field(
        name="👑 관리자 전용",
        value="""
/점수판생성 → 시스템 자동 생성  
/팀설정 멤버 팀이름 → 팀 등록/변경(점수 유지)  
/점수조정 멤버 점수 → 점수 증감 조정 (+- 기준)  
/목표점수설정 점수 → 목표 점수 설정  
/게임재시작 → 점수 초기화 (팀 유지)  
/전체초기화 → 전체 데이터 삭제
""",
        inline=False
    )

    embed.add_field(
        name="📂 채널 안내",
        value="""
🎮 게임-채팅 → 명령어 전용  
📊 점수판 → 자동 갱신 (채팅 금지)
""",
        inline=False
    )

    embed.set_footer(text="슬래시 명령어는 🎮 게임-채팅 채널에서만 사용 가능")

    await interaction.response.send_message(embed=embed, ephemeral=True)

# ================= 실행 =================

@client.event
async def on_ready():
    init_db()
    # client.add_view(GameView())
    await tree.sync()
    print("솔랭내기 완전 통합 봇 실행 완료")

client.run(TOKEN)
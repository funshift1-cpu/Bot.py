"""
Discord Moderation Bot — Admin Panel
=====================================
Intents (discord.com/developers/applications):
  ✅ SERVER MEMBERS INTENT
  ✅ MESSAGE CONTENT INTENT

pip install discord.py

Система прав по ролям:
  Trial Staff       — warn
  Moderator         — warn, mute
  Senior Moderator  — warn, mute, kick
  Head of Staff     — warn, mute, kick, ban
  Executive         — всё (включая lockdown)
  Head of Executive — всё
  По всем вопросам  — всё
"""

import discord
from discord import app_commands
from discord.ext import commands
import datetime
import logging

# ─── Консольное логирование ───────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("moderation.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("AdminBot")

# ─── Конфигурация ─────────────────────────────────────────────────────────────
TOKEN          = "MTQ3NjQ5ODAxMzYwMDg3NDYyNw.GXitBW.3_SialCAfdSBSWVM0zxPjeRIq3129Wj_XlH8A8"
LOG_CHANNEL_ID = 1476527914236248176

# Роли которые получают апелляции (Senior Mod и выше)
APPEAL_ROLE_IDS = {
    1462416714170892390,  # Senior Moderator
    1231937047967694911,  # Head of Staff
    1462451800752783504,  # Executive
    1219322358418903181,  # Head of Executive
    1222556238085095425,  # По всем вопросам
}

# ─── Матрица прав: role_id → frozenset разрешённых действий ──────────────────
#
#  Действия: "warn", "mute", "unmute", "kick", "ban", "unban", "lockdown"
#
ROLE_PERMISSIONS: dict[int, frozenset[str]] = {
    1462414273820229756: frozenset({"warn", "unwarn"}),                                                                    # Trial Staff
    1219321082272546908: frozenset({"warn", "unwarn", "mute", "unmute"}),                                              # Moderator
    1462416714170892390: frozenset({"warn", "unwarn", "mute", "unmute", "kick", "clean"}),                         # Senior Moderator
    1231937047967694911: frozenset({"warn", "unwarn", "mute", "unmute", "kick", "ban", "unban", "clean"}),     # Head of Staff
    1462451800752783504: frozenset({"warn", "unwarn", "mute", "unmute", "kick", "ban", "unban",
                                    "clean", "lockdown"}),                                                                 # Executive
    1219322358418903181: frozenset({"warn", "unwarn", "mute", "unmute", "kick", "ban", "unban",
                                    "clean", "lockdown"}),                                                                 # Head of Executive
    1222556238085095425: frozenset({"warn", "unwarn", "mute", "unmute", "kick", "ban", "unban",
                                    "clean", "lockdown"}),                                                                 # По всем вопросам
}

# Порядок важен: роль с большим набором прав должна проверяться раньше
ROLE_NAMES: dict[int, str] = {
    1462414273820229756: "Trial Staff",
    1219321082272546908: "Moderator",
    1462416714170892390: "Senior Moderator",
    1231937047967694911: "Head of Staff",
    1462451800752783504: "Executive",
    1219322358418903181: "Head of Executive",
    1222556238085095425: "По всем вопросам",
}


def get_allowed_actions(member: discord.Member) -> frozenset[str]:
    """
    Возвращает объединение всех разрешённых действий для всех ролей участника.
    Если у человека несколько ролей — он получает сумму всех прав.
    """
    allowed: set[str] = set()
    for role in member.roles:
        if role.id in ROLE_PERMISSIONS:
            allowed |= ROLE_PERMISSIONS[role.id]
    return frozenset(allowed)


def get_role_label(member: discord.Member) -> str:
    """Возвращает название высшей роли модератора для отображения в панели."""
    priority = [
        1222556238085095425, 1219322358418903181, 1462451800752783504,
        1231937047967694911, 1462416714170892390, 1219321082272546908,
        1462414273820229756,
    ]
    role_ids = {r.id for r in member.roles}
    for rid in priority:
        if rid in role_ids:
            return ROLE_NAMES[rid]
    return "Unknown"


# Ранги от низшего (0) к высшему (6)
ROLE_RANK: dict[int, int] = {
    1462414273820229756: 0,  # Trial Staff
    1219321082272546908: 1,  # Moderator
    1462416714170892390: 2,  # Senior Moderator
    1231937047967694911: 3,  # Head of Staff
    1462451800752783504: 4,  # Executive
    1219322358418903181: 5,  # Head of Executive
    1222556238085095425: 6,  # По всем вопросам
}


def get_rank(member: discord.Member) -> int:
    """Возвращает числовой ранг участника. -1 если нет ни одной роли модератора."""
    rank = -1
    for role in member.roles:
        if role.id in ROLE_RANK:
            rank = max(rank, ROLE_RANK[role.id])
    return rank


def can_act_on(moderator: discord.Member, target: discord.Member) -> bool:
    """True если ранг модератора СТРОГО выше ранга цели."""
    return get_rank(moderator) > get_rank(target)


def higher_rank_embed(moderator: discord.Member, target: discord.Member) -> discord.Embed:
    """Embed с отказом — цель имеет равный или более высокий ранг."""
    mod_label    = get_role_label(moderator)
    target_label = get_role_label(target)
    # Если у цели нет модераторской роли — показываем 'Нет роли'
    if get_rank(target) == -1:
        target_label = "Нет роли"
    return discord.Embed(
        title="❌ Недостаточно прав",
        description=(
            f"Вы не можете применить это действие к **{target}**.\n\n"
            f"Ваш ранг: **{mod_label}**\n"
            f"Ранг цели: **{target_label}**\n\n"
            "Вы можете действовать только на участников с рангом **ниже** вашего."
        ),
        color=discord.Color.red(),
    )


# ─── Хранилище варнов { guild_id: { user_id: count } } ───────────────────────
warn_storage: dict[int, dict[int, int]] = {}

def add_warn(guild_id: int, user_id: int) -> int:
    warn_storage.setdefault(guild_id, {})
    warn_storage[guild_id][user_id] = warn_storage[guild_id].get(user_id, 0) + 1
    return warn_storage[guild_id][user_id]

def reset_warns(guild_id: int, user_id: int):
    if guild_id in warn_storage:
        warn_storage[guild_id].pop(user_id, None)


# ══════════════════════════════════════════════════════════════════════════════
#  ХЕЛПЕРЫ
# ══════════════════════════════════════════════════════════════════════════════

async def send_log(guild: discord.Guild, embed: discord.Embed):
    """Отправляет embed в лог-канал."""
    ch = guild.get_channel(LOG_CHANNEL_ID)
    if ch is None:
        log.warning("[LOG] Лог-канал %d не найден", LOG_CHANNEL_ID)
        return
    try:
        await ch.send(embed=embed)
    except (discord.Forbidden, discord.HTTPException) as e:
        log.warning("[LOG] Ошибка отправки лога: %s", e)


def make_log_embed(
    action: str, color: discord.Color,
    moderator: discord.Member,
    target: discord.Member | discord.User | str,
    reason: str, extra: str = "",
) -> discord.Embed:
    embed = discord.Embed(
        title=action, color=color,
        timestamp=datetime.datetime.now(datetime.timezone.utc),
    )
    target_str = f"{target} (`{target.id}`)" if hasattr(target, "id") else str(target)
    embed.add_field(name="👤 Участник",   value=target_str,                        inline=True)
    embed.add_field(name="🛡️ Модератор", value=f"{moderator} (`{moderator.id}`)", inline=True)
    embed.add_field(name="📋 Причина",    value=reason,                            inline=False)
    if extra:
        embed.add_field(name="ℹ️ Доп.", value=extra, inline=False)
    if hasattr(target, "display_avatar"):
        embed.set_thumbnail(url=target.display_avatar.url)
    embed.set_footer(text=f"Роль модератора: {get_role_label(moderator)}")
    return embed


def make_dm_embed(
    action: str, color: discord.Color,
    guild_name: str, reason: str, extra: str = "",
) -> discord.Embed:
    embed = discord.Embed(
        title=action, color=color,
        timestamp=datetime.datetime.now(datetime.timezone.utc),
    )
    embed.add_field(name="🏠 Сервер",  value=guild_name, inline=False)
    embed.add_field(name="📋 Причина", value=reason,     inline=False)
    if extra:
        embed.add_field(name="ℹ️ Доп.", value=extra, inline=False)
    embed.set_footer(text="Если считаете это ошибкой — обратитесь к администрации.")
    return embed


async def dm_notify(member: discord.Member, embed: discord.Embed) -> bool:
    try:
        await member.send(embed=embed)
        return True
    except (discord.Forbidden, discord.HTTPException):
        return False


def no_access_embed() -> discord.Embed:
    """Embed для отказа в доступе к действию."""
    return discord.Embed(
        title="❌ Недостаточно прав",
        description="Ваша роль не позволяет выполнять это действие.",
        color=discord.Color.red(),
    )


# ══════════════════════════════════════════════════════════════════════════════
#  МОДАЛЬНЫЕ ОКНА
# ══════════════════════════════════════════════════════════════════════════════

class KickModal(discord.ui.Modal, title="👢 Исключить участника"):
    user_id = discord.ui.TextInput(label="ID пользователя", placeholder="123456789012345678")
    reason  = discord.ui.TextInput(label="Причина", placeholder="Нарушение правил",
                                   required=False, default="Причина не указана")

    async def on_submit(self, interaction: discord.Interaction):
        if "kick" not in get_allowed_actions(interaction.user):
            return await interaction.response.send_message(embed=no_access_embed(), ephemeral=True)
        if not self.user_id.value.strip().isdigit():
            return await interaction.response.send_message("❌ Неверный формат ID.", ephemeral=True)
        member = interaction.guild.get_member(int(self.user_id.value.strip()))
        if not member:
            return await interaction.response.send_message("❌ Участник не найден.", ephemeral=True)
        mod = interaction.guild.get_member(interaction.user.id) or interaction.user
        if not can_act_on(mod, member):
            return await interaction.response.send_message(embed=higher_rank_embed(mod, member), ephemeral=True)
        try:
            dm_sent = await dm_notify(member, make_dm_embed(
                "👢 Вы исключены с сервера", discord.Color.orange(),
                interaction.guild.name, self.reason.value))
            await member.kick(reason=self.reason.value)
            await send_log(interaction.guild, make_log_embed(
                "👢 KICK", discord.Color.orange(), interaction.user, member, self.reason.value))
            note = "" if dm_sent else "\n⚠️ ЛС закрыты."
            await interaction.response.send_message(
                f"👢 **{member}** исключён. Причина: {self.reason.value}{note}", ephemeral=True)
            log.info("[KICK] %s → %s | %s", interaction.user, member, self.reason.value)
        except discord.Forbidden:
            await interaction.response.send_message("❌ У бота недостаточно прав.", ephemeral=True)


class BanModal(discord.ui.Modal, title="🔨 Забанить участника"):
    user_id = discord.ui.TextInput(label="ID пользователя", placeholder="123456789012345678")
    reason  = discord.ui.TextInput(label="Причина", placeholder="Нарушение правил",
                                   required=False, default="Причина не указана")

    async def on_submit(self, interaction: discord.Interaction):
        if "ban" not in get_allowed_actions(interaction.user):
            return await interaction.response.send_message(embed=no_access_embed(), ephemeral=True)
        if not self.user_id.value.strip().isdigit():
            return await interaction.response.send_message("❌ Неверный формат ID.", ephemeral=True)
        member = interaction.guild.get_member(int(self.user_id.value.strip()))
        if not member:
            return await interaction.response.send_message("❌ Участник не найден.", ephemeral=True)
        mod = interaction.guild.get_member(interaction.user.id) or interaction.user
        if not can_act_on(mod, member):
            return await interaction.response.send_message(embed=higher_rank_embed(mod, member), ephemeral=True)
        try:
            # Отправляем DM с кнопкой апелляции ДО бана
            ban_embed = make_dm_embed(
                "🔨 Вы были забанены", discord.Color.red(),
                interaction.guild.name, self.reason.value,
                extra="Если считаете бан несправедливым — нажмите кнопку ниже чтобы написать апелляцию администрации.",
            )
            appeal_view = AppealView(guild_id=interaction.guild.id, banned_user=member)
            try:
                await member.send(embed=ban_embed, view=appeal_view)
                dm_sent = True
            except (discord.Forbidden, discord.HTTPException):
                dm_sent = False

            await member.ban(reason=self.reason.value, delete_message_days=0)
            reset_warns(interaction.guild.id, member.id)
            await send_log(interaction.guild, make_log_embed(
                "🔨 BAN", discord.Color.red(), interaction.user, member, self.reason.value))
            note = "" if dm_sent else "\n⚠️ ЛС закрыты, кнопка апелляции не доставлена."
            await interaction.response.send_message(
                f"🔨 **{member}** забанен. Причина: {self.reason.value}{note}", ephemeral=True)
            log.info("[BAN] %s → %s | %s", interaction.user, member, self.reason.value)
        except discord.Forbidden:
            await interaction.response.send_message("❌ У бота недостаточно прав.", ephemeral=True)


class UnbanModal(discord.ui.Modal, title="✅ Разбанить пользователя"):
    user_id = discord.ui.TextInput(label="ID пользователя", placeholder="123456789012345678")
    reason  = discord.ui.TextInput(label="Причина", placeholder="Апелляция принята",
                                   required=False, default="Причина не указана")

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        if "unban" not in get_allowed_actions(interaction.user):
            return await interaction.followup.send(embed=no_access_embed(), ephemeral=True)
        if not self.user_id.value.strip().isdigit():
            return await interaction.followup.send("❌ Неверный формат ID.", ephemeral=True)
        target_id = int(self.user_id.value.strip())
        ban_entry = None
        async for entry in interaction.guild.bans():
            if entry.user.id == target_id:
                ban_entry = entry
                break
        if not ban_entry:
            return await interaction.followup.send(f"❌ ID `{target_id}` не в бан-листе.", ephemeral=True)
        try:
            await interaction.guild.unban(ban_entry.user, reason=self.reason.value)
            await send_log(interaction.guild, make_log_embed(
                "✅ UNBAN", discord.Color.green(), interaction.user, ban_entry.user, self.reason.value))
            await interaction.followup.send(
                f"✅ **{ban_entry.user}** разбанен. Причина: {self.reason.value}", ephemeral=True)
            log.info("[UNBAN] %s → %s | %s", interaction.user, ban_entry.user, self.reason.value)
        except discord.Forbidden:
            await interaction.followup.send("❌ У бота недостаточно прав.", ephemeral=True)


class MuteModal(discord.ui.Modal, title="🔇 Тайм-аут участника"):
    user_id = discord.ui.TextInput(label="ID пользователя", placeholder="123456789012345678")
    minutes = discord.ui.TextInput(label="Длительность (мин, макс 40320)", default="10")
    reason  = discord.ui.TextInput(label="Причина", placeholder="Нарушение правил",
                                   required=False, default="Причина не указана")

    async def on_submit(self, interaction: discord.Interaction):
        if "mute" not in get_allowed_actions(interaction.user):
            return await interaction.response.send_message(embed=no_access_embed(), ephemeral=True)
        if not self.user_id.value.strip().isdigit():
            return await interaction.response.send_message("❌ Неверный формат ID.", ephemeral=True)
        if not self.minutes.value.strip().isdigit():
            return await interaction.response.send_message("❌ Минуты должны быть числом.", ephemeral=True)
        mins = int(self.minutes.value.strip())
        if not (1 <= mins <= 40320):
            return await interaction.response.send_message("❌ От 1 до 40320 мин.", ephemeral=True)
        member = interaction.guild.get_member(int(self.user_id.value.strip()))
        if not member:
            return await interaction.response.send_message("❌ Участник не найден.", ephemeral=True)
        mod = interaction.guild.get_member(interaction.user.id) or interaction.user
        if not can_act_on(mod, member):
            return await interaction.response.send_message(embed=higher_rank_embed(mod, member), ephemeral=True)
        try:
            dm_sent = await dm_notify(member, make_dm_embed(
                "🔇 Вам выдан тайм-аут", discord.Color.yellow(),
                interaction.guild.name, self.reason.value,
                extra=f"Длительность: **{mins} мин.**"))
            await member.timeout(datetime.timedelta(minutes=mins), reason=self.reason.value)
            await send_log(interaction.guild, make_log_embed(
                "🔇 MUTE", discord.Color.yellow(), interaction.user, member, self.reason.value,
                extra=f"Длительность: **{mins} мин.**"))
            note = "" if dm_sent else "\n⚠️ ЛС закрыты."
            await interaction.response.send_message(
                f"🔇 **{member}** замьючен на **{mins} мин.**{note}", ephemeral=True)
            log.info("[MUTE] %s → %s %d мин | %s", interaction.user, member, mins, self.reason.value)
        except discord.Forbidden:
            await interaction.response.send_message("❌ У бота недостаточно прав.", ephemeral=True)


class UnmuteModal(discord.ui.Modal, title="🔊 Снять тайм-аут"):
    user_id = discord.ui.TextInput(label="ID пользователя", placeholder="123456789012345678")
    reason  = discord.ui.TextInput(label="Причина", placeholder="Наказание отменено",
                                   required=False, default="Причина не указана")

    async def on_submit(self, interaction: discord.Interaction):
        if "unmute" not in get_allowed_actions(interaction.user):
            return await interaction.response.send_message(embed=no_access_embed(), ephemeral=True)
        if not self.user_id.value.strip().isdigit():
            return await interaction.response.send_message("❌ Неверный формат ID.", ephemeral=True)
        member = interaction.guild.get_member(int(self.user_id.value.strip()))
        if not member:
            return await interaction.response.send_message("❌ Участник не найден.", ephemeral=True)
        mod = interaction.guild.get_member(interaction.user.id) or interaction.user
        if not can_act_on(mod, member):
            return await interaction.response.send_message(embed=higher_rank_embed(mod, member), ephemeral=True)
        if not member.is_timed_out():
            return await interaction.response.send_message(
                f"ℹ️ У **{member}** нет активного тайм-аута.", ephemeral=True)
        try:
            await member.timeout(None, reason=self.reason.value)
            await dm_notify(member, make_dm_embed(
                "🔊 Ваш тайм-аут снят", discord.Color.green(),
                interaction.guild.name, self.reason.value))
            await send_log(interaction.guild, make_log_embed(
                "🔊 UNMUTE", discord.Color.green(), interaction.user, member, self.reason.value))
            await interaction.response.send_message(
                f"🔊 Тайм-аут с **{member}** снят.", ephemeral=True)
            log.info("[UNMUTE] %s → %s | %s", interaction.user, member, self.reason.value)
        except discord.Forbidden:
            await interaction.response.send_message("❌ У бота недостаточно прав.", ephemeral=True)


class WarnModal(discord.ui.Modal, title="⚠️ Предупреждение"):
    user_id = discord.ui.TextInput(label="ID пользователя", placeholder="123456789012345678")
    reason  = discord.ui.TextInput(label="Причина", placeholder="Нарушение правил")

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        if "warn" not in get_allowed_actions(interaction.user):
            return await interaction.followup.send(embed=no_access_embed(), ephemeral=True)
        if not self.user_id.value.strip().isdigit():
            return await interaction.followup.send("❌ Неверный формат ID.", ephemeral=True)
        member = interaction.guild.get_member(int(self.user_id.value.strip()))
        if not member:
            return await interaction.followup.send("❌ Участник не найден.", ephemeral=True)
        mod = interaction.guild.get_member(interaction.user.id) or interaction.user
        if not can_act_on(mod, member):
            return await interaction.followup.send(embed=higher_rank_embed(mod, member), ephemeral=True)

        guild_id   = interaction.guild.id
        warn_count = add_warn(guild_id, member.id)

        if warn_count >= 3:
            await dm_notify(member, make_dm_embed(
                "🔨 Вы забанены за систематические нарушения", discord.Color.red(),
                interaction.guild.name, self.reason.value,
                extra=f"Это ваше **{warn_count}-е** предупреждение."))
            try:
                await member.ban(
                    reason=f"[Авто-бан: {warn_count} варна] {self.reason.value}",
                    delete_message_days=0)
                reset_warns(guild_id, member.id)
                await send_log(interaction.guild, make_log_embed(
                    f"🔨 АВТО-БАН (варн {warn_count}/3)", discord.Color.dark_red(),
                    interaction.user, member, self.reason.value,
                    extra=f"Автоматически после **{warn_count}** предупреждений."))
                await interaction.followup.send(
                    f"🔨 **{member}** — **{warn_count}-й варн** → **автобан**.\nПричина: {self.reason.value}",
                    ephemeral=True)
            except discord.Forbidden:
                await interaction.followup.send("❌ Недостаточно прав для автобана.", ephemeral=True)

        elif warn_count == 2:
            await dm_notify(member, make_dm_embed(
                "🔇 Тайм-аут за повторное нарушение", discord.Color.yellow(),
                interaction.guild.name, self.reason.value,
                extra="Это ваше **2-е** предупреждение. Тайм-аут: **24 часа**."))
            try:
                await member.timeout(
                    datetime.timedelta(hours=24),
                    reason=f"[Авто-мут: 2 варна] {self.reason.value}")
                await send_log(interaction.guild, make_log_embed(
                    "🔇 АВТО-МУТ (варн 2/3)", discord.Color.gold(),
                    interaction.user, member, self.reason.value,
                    extra="Автоматически после **2** предупреждений. Длительность: **24 ч.**"))
                await interaction.followup.send(
                    f"🔇 **{member}** — **2-й варн** → **автомут 24 ч.**\nПричина: {self.reason.value}",
                    ephemeral=True)
            except discord.Forbidden:
                await interaction.followup.send("❌ Недостаточно прав для автомута.", ephemeral=True)

        else:
            dm_sent = await dm_notify(member, make_dm_embed(
                "⚠️ Вы получили предупреждение", discord.Color.orange(),
                interaction.guild.name, self.reason.value,
                extra=(f"Это ваше **{warn_count}-е** предупреждение.\n"
                       "⚠️ **2-й** варн → мут 24ч  |  **3-й** варн → бан")))
            await send_log(interaction.guild, make_log_embed(
                f"⚠️ WARN ({warn_count}/3)", discord.Color.orange(),
                interaction.user, member, self.reason.value))
            note = "" if dm_sent else "\n⚠️ ЛС закрыты."
            await interaction.followup.send(
                f"⚠️ **{member}** — предупреждение **({warn_count}/3)**.\nПричина: {self.reason.value}{note}",
                ephemeral=True)


class UnwarnModal(discord.ui.Modal, title="🗑️ Снять предупреждение"):
    user_id = discord.ui.TextInput(label="ID пользователя", placeholder="123456789012345678")
    amount  = discord.ui.TextInput(
        label="Сколько варнов снять (0 = сбросить все)",
        placeholder="1",
        default="1",
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        if "warn" not in get_allowed_actions(
            interaction.guild.get_member(interaction.user.id) or interaction.user
        ):
            return await interaction.followup.send(embed=no_access_embed(), ephemeral=True)

        if not self.user_id.value.strip().isdigit():
            return await interaction.followup.send("❌ Неверный формат ID.", ephemeral=True)
        if not self.amount.value.strip().isdigit():
            return await interaction.followup.send("❌ Количество должно быть числом.", ephemeral=True)

        member = interaction.guild.get_member(int(self.user_id.value.strip()))
        if not member:
            return await interaction.followup.send("❌ Участник не найден.", ephemeral=True)
        mod = interaction.guild.get_member(interaction.user.id) or interaction.user
        if not can_act_on(mod, member):
            return await interaction.followup.send(embed=higher_rank_embed(mod, member), ephemeral=True)

        guild_id      = interaction.guild.id
        current_warns = warn_storage.get(guild_id, {}).get(member.id, 0)

        if current_warns == 0:
            return await interaction.followup.send(
                f"ℹ️ У **{member}** нет активных предупреждений.", ephemeral=True)

        amount = int(self.amount.value.strip())

        if amount == 0:
            # Сбросить все варны
            reset_warns(guild_id, member.id)
            new_count = 0
            action_text = "все предупреждения сброшены"
        else:
            # Снять указанное количество, но не ниже 0
            new_count = max(0, current_warns - amount)
            warn_storage.setdefault(guild_id, {})
            if new_count == 0:
                reset_warns(guild_id, member.id)
            else:
                warn_storage[guild_id][member.id] = new_count
            action_text = f"снято {min(amount, current_warns)} варн(а)"

        # Уведомляем участника в ЛС
        await dm_notify(member, make_dm_embed(
            "🗑️ Предупреждение снято", discord.Color.green(),
            interaction.guild.name,
            reason="Предупреждение было снято администрацией.",
            extra=f"Осталось предупреждений: **{new_count}/3**",
        ))

        # Лог в канал
        await send_log(interaction.guild, make_log_embed(
            "🗑️ UNWARN", discord.Color.green(),
            interaction.guild.get_member(interaction.user.id) or interaction.user,
            member, "Предупреждение снято",
            extra=f"Было: **{current_warns}** → Стало: **{new_count}** ({action_text})",
        ))

        await interaction.followup.send(
            f"🗑️ **{member}**: {action_text}. Осталось предупреждений: **{new_count}/3**.",
            ephemeral=True,
        )
        log.info("[UNWARN] %s → %s | было %d → стало %d",
                 interaction.user, member, current_warns, new_count)


class LockdownModal(discord.ui.Modal, title="🔒 Локдаун — канал-исключение"):
    channel_id = discord.ui.TextInput(
        label="ID канала-исключения (оставить открытым)",
        placeholder="Оставьте пустым для автовыбора",
        required=False,
    )

    def __init__(self, lockdown_cog: "LockdownCog"):
        super().__init__()
        self.lockdown_cog = lockdown_cog

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        if "lockdown" not in get_allowed_actions(interaction.user):
            return await interaction.followup.send(embed=no_access_embed(), ephemeral=True)

        guild    = interaction.guild
        everyone = guild.default_role
        exempt_channel = None
        raw = self.channel_id.value.strip()

        if raw:
            if not raw.isdigit():
                return await interaction.followup.send("❌ Неверный формат ID канала.", ephemeral=True)
            exempt_channel = guild.get_channel(int(raw))
            if not exempt_channel:
                return await interaction.followup.send(f"❌ Канал `{raw}` не найден.", ephemeral=True)
        else:
            exempt_channel = next(
                (ch for ch in guild.text_channels if ch.permissions_for(guild.me).manage_channels),
                None)

        self.lockdown_cog._exempt_id = exempt_channel.id if exempt_channel else None
        self.lockdown_cog._snapshot.clear()
        locked, failed = [], []

        for channel in guild.text_channels:
            ow = channel.overwrites_for(everyone)
            self.lockdown_cog._snapshot[channel.id] = ow.send_messages
            if exempt_channel and channel.id == exempt_channel.id:
                continue
            if not channel.permissions_for(guild.me).manage_channels:
                failed.append(f"{channel.mention} (нет прав)")
                continue
            try:
                ow.send_messages = False
                await channel.set_permissions(everyone, overwrite=ow, reason="Lockdown")
                locked.append(channel.name)
            except discord.Forbidden:
                failed.append(f"{channel.mention} (Forbidden)")
            except discord.HTTPException as e:
                err = "Onboarding" if e.code == 350005 else f"HTTP {e.code}"
                failed.append(f"{channel.mention} ({err})")

        await send_log(guild, make_log_embed(
            "🔒 LOCKDOWN", discord.Color.dark_red(), interaction.user, interaction.user,
            "Сервер заблокирован",
            extra=(f"Заблокировано: **{len(locked)}** каналов\n"
                   f"Исключение: {exempt_channel.mention if exempt_channel else 'нет'}")))

        lines = [f"🔒 Локдаун активирован. Заблокировано: **{len(locked)}** каналов."]
        if exempt_channel:
            lines.append(f"📌 Исключение: {exempt_channel.mention}")
        if failed:
            lines.append(f"⚠️ Пропущено: {', '.join(failed)}")
        await interaction.followup.send("\n".join(lines), ephemeral=True)
        log.info("[LOCKDOWN] %s | %d каналов", interaction.user, len(locked))



# ══════════════════════════════════════════════════════════════════════════════
#  АПЕЛЛЯЦИЯ ПОСЛЕ БАНА
# ══════════════════════════════════════════════════════════════════════════════

class AppealModal(discord.ui.Modal, title="📩 Апелляция"):
    """Форма апелляции — отправляется из ЛС после бана."""
    appeal_text = discord.ui.TextInput(
        label="Ваше обращение",
        placeholder="Опишите ситуацию и причину почему бан несправедлив...",
        style=discord.TextStyle.paragraph,
        min_length=20,
        max_length=1000,
    )

    def __init__(self, guild_id: int, banned_user: discord.User | discord.Member):
        super().__init__()
        self.guild_id    = guild_id
        self.banned_user = banned_user

    async def on_submit(self, interaction: discord.Interaction):
        """Получаем апелляцию и рассылаем её всем Senior Mod+ с кнопкой ответа."""
        await interaction.response.defer(ephemeral=True)

        guild = bot.get_guild(self.guild_id)
        if guild is None:
            return await interaction.followup.send(
                "❌ Не удалось найти сервер. Попробуйте позже.", ephemeral=True)

        embed = discord.Embed(
            title="📩 Новая апелляция на бан",
            color=discord.Color.orange(),
            timestamp=datetime.datetime.now(datetime.timezone.utc),
        )
        embed.add_field(name="👤 Пользователь",
                        value=f"{self.banned_user} (`{self.banned_user.id}`)", inline=False)
        embed.add_field(name="📝 Текст апелляции",
                        value=self.appeal_text.value, inline=False)
        embed.set_thumbnail(url=self.banned_user.display_avatar.url)
        embed.set_footer(text=f"Сервер: {guild.name} • Нажмите кнопку чтобы ответить пользователю")

        # View с кнопкой ответа — прикрепляется к каждому сообщению модераторам
        reply_view = AppealReplyView(
            banned_user_id=self.banned_user.id,
            guild_id=self.guild_id,
        )

        # Рассылаем всем участникам с ролью Senior Mod и выше
        sent_count = 0
        for member in guild.members:
            member_role_ids = {r.id for r in member.roles}
            if member_role_ids & APPEAL_ROLE_IDS:
                try:
                    await member.send(embed=embed, view=reply_view)
                    sent_count += 1
                except (discord.Forbidden, discord.HTTPException):
                    pass

        # Логируем в лог-канал (без кнопки — там не надо отвечать)
        await send_log(guild, embed)

        if sent_count > 0:
            await interaction.followup.send(
                f"✅ Ваша апелляция отправлена **{sent_count}** администраторам. "
                "Ожидайте ответа в личных сообщениях.",
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                "⚠️ Апелляция записана, но администраторы временно недоступны. "
                "Попробуйте позже.",
                ephemeral=True,
            )
        log.info("[APPEAL] %s отправил апелляцию на сервер %s", self.banned_user, guild.name)


class AppealView(discord.ui.View):
    """View с кнопкой апелляции — отправляется в ЛС при бане."""

    def __init__(self, guild_id: int, banned_user: discord.User | discord.Member):
        super().__init__(timeout=None)
        self.guild_id    = guild_id
        self.banned_user = banned_user

    @discord.ui.button(label="📩 Написать апелляцию", style=discord.ButtonStyle.primary)
    async def appeal_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(
            AppealModal(guild_id=self.guild_id, banned_user=self.banned_user)
        )


class AppealReplyModal(discord.ui.Modal, title="✉️ Ответ на апелляцию"):
    """Форма ответа модератора — пользователь получит ответ в ЛС."""
    reply_text = discord.ui.TextInput(
        label="Ваш ответ",
        placeholder="Напишите решение по апелляции...",
        style=discord.TextStyle.paragraph,
        min_length=5,
        max_length=1000,
    )

    def __init__(self, banned_user_id: int, guild_id: int):
        super().__init__()
        self.banned_user_id = banned_user_id
        self.guild_id       = guild_id

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        # Получаем пользователя по ID (он забанен — не в guild.members)
        try:
            banned_user = await bot.fetch_user(self.banned_user_id)
        except discord.NotFound:
            return await interaction.followup.send(
                "❌ Пользователь не найден (возможно удалил аккаунт).", ephemeral=True)

        guild = bot.get_guild(self.guild_id)
        guild_name = guild.name if guild else "Сервер"

        # Embed который получит забаненный пользователь
        user_embed = discord.Embed(
            title="✉️ Ответ администрации на вашу апелляцию",
            color=discord.Color.blurple(),
            timestamp=datetime.datetime.now(datetime.timezone.utc),
        )
        user_embed.add_field(name="🏠 Сервер",       value=guild_name,         inline=False)
        user_embed.add_field(name="🛡️ Модератор",   value=str(interaction.user), inline=False)
        user_embed.add_field(name="💬 Ответ",         value=self.reply_text.value, inline=False)
        user_embed.set_thumbnail(url=interaction.user.display_avatar.url)
        user_embed.set_footer(text="Это ответ на вашу апелляцию.")

        try:
            await banned_user.send(embed=user_embed)
            dm_ok = True
        except (discord.Forbidden, discord.HTTPException):
            dm_ok = False

        # Логируем ответ в лог-канал
        if guild:
            log_embed = discord.Embed(
                title="✉️ ОТВЕТ НА АПЕЛЛЯЦИЮ",
                color=discord.Color.blurple(),
                timestamp=datetime.datetime.now(datetime.timezone.utc),
            )
            log_embed.add_field(
                name="👤 Апеллянт",
                value=f"{banned_user} (`{banned_user.id}`)", inline=True)
            log_embed.add_field(
                name="🛡️ Модератор",
                value=f"{interaction.user} (`{interaction.user.id}`)", inline=True)
            log_embed.add_field(name="💬 Ответ", value=self.reply_text.value, inline=False)
            await send_log(guild, log_embed)

        if dm_ok:
            await interaction.followup.send(
                f"✅ Ответ отправлен пользователю **{banned_user}**.", ephemeral=True)
        else:
            await interaction.followup.send(
                f"⚠️ Не удалось отправить ЛС пользователю **{banned_user}** (закрыты).",
                ephemeral=True)

        log.info("[APPEAL_REPLY] %s → %s | %s",
                 interaction.user, banned_user, self.reply_text.value[:80])


class AppealReplyView(discord.ui.View):
    """View с кнопкой ответа — отправляется модераторам вместе с апелляцией."""

    def __init__(self, banned_user_id: int, guild_id: int):
        super().__init__(timeout=None)
        self.banned_user_id = banned_user_id
        self.guild_id       = guild_id

    @discord.ui.button(label="✉️ Ответить пользователю", style=discord.ButtonStyle.success)
    async def reply_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Проверяем что нажавший — модератор нужного уровня
        mod = interaction.guild.get_member(interaction.user.id) if interaction.guild else None
        if mod is None:
            # Нажали из ЛС — проверяем роли через guild
            guild = bot.get_guild(self.guild_id)
            mod   = guild.get_member(interaction.user.id) if guild else None

        if mod is None or not ({r.id for r in mod.roles} & APPEAL_ROLE_IDS):
            return await interaction.response.send_message(
                "❌ Только Senior Moderator и выше могут отвечать на апелляции.",
                ephemeral=True,
            )
        await interaction.response.send_modal(
            AppealReplyModal(
                banned_user_id=self.banned_user_id,
                guild_id=self.guild_id,
            )
        )


# ══════════════════════════════════════════════════════════════════════════════
#  CLEAN MESSAGES MODAL
# ══════════════════════════════════════════════════════════════════════════════

class CleanModal(discord.ui.Modal, title="🧹 Очистка сообщений"):
    channel_id = discord.ui.TextInput(
        label="ID канала (пусто = текущий)",
        placeholder="Оставьте пустым для текущего канала",
        required=False,
    )
    amount = discord.ui.TextInput(
        label="Количество сообщений (1–100)",
        placeholder="10",
        default="10",
    )
    user_filter = discord.ui.TextInput(
        label="ID пользователя (пусто = все сообщения)",
        placeholder="Удалить сообщения только этого пользователя",
        required=False,
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        mod = interaction.guild.get_member(interaction.user.id) or interaction.user
        if "clean" not in get_allowed_actions(mod):
            return await interaction.followup.send(embed=no_access_embed(), ephemeral=True)

        # Определяем канал
        raw_channel = self.channel_id.value.strip()
        if raw_channel:
            if not raw_channel.isdigit():
                return await interaction.followup.send("❌ Неверный ID канала.", ephemeral=True)
            channel = interaction.guild.get_channel(int(raw_channel))
            if not channel or not isinstance(channel, discord.TextChannel):
                return await interaction.followup.send("❌ Текстовый канал не найден.", ephemeral=True)
        else:
            channel = interaction.channel

        # Проверяем количество
        if not self.amount.value.strip().isdigit():
            return await interaction.followup.send("❌ Количество должно быть числом.", ephemeral=True)
        amount = int(self.amount.value.strip())
        if not (1 <= amount <= 100):
            return await interaction.followup.send("❌ Допустимо от 1 до 100 сообщений.", ephemeral=True)

        # Фильтр по пользователю
        target_user = None
        raw_user = self.user_filter.value.strip()
        if raw_user:
            if not raw_user.isdigit():
                return await interaction.followup.send("❌ Неверный ID пользователя.", ephemeral=True)
            target_user = interaction.guild.get_member(int(raw_user))

        try:
            if target_user:
                # Удаляем сообщения конкретного пользователя
                def check(m: discord.Message):
                    return m.author.id == target_user.id

                deleted = await channel.purge(limit=amount * 5, check=check, bulk=True)
                # Ограничиваем до нужного кол-ва
                deleted = deleted[:amount]
            else:
                deleted = await channel.purge(limit=amount, bulk=True)

            deleted_count = len(deleted)

            await send_log(interaction.guild, make_log_embed(
                "🧹 CLEAN MESSAGES", discord.Color.blue(),
                mod, mod, "Очистка сообщений",
                extra=(
                    f"Канал: {channel.mention}\n"
                    f"Удалено: **{deleted_count}** сообщений"
                    + (f"\nФильтр: {target_user}" if target_user else "")
                ),
            ))

            await interaction.followup.send(
                f"🧹 Удалено **{deleted_count}** сообщений в {channel.mention}.",
                ephemeral=True,
            )
            log.info("[CLEAN] %s удалил %d сообщений в #%s", mod, deleted_count, channel.name)

        except discord.Forbidden:
            await interaction.followup.send(
                "❌ У бота нет прав на удаление сообщений в этом канале.", ephemeral=True)
        except discord.HTTPException as e:
            await interaction.followup.send(f"❌ Ошибка: {e}", ephemeral=True)

# ══════════════════════════════════════════════════════════════════════════════
#  SELECT MENUS — динамически строятся по ролям пользователя
# ══════════════════════════════════════════════════════════════════════════════

# Полные описания всех пунктов участников
MEMBER_OPTION_DEFS = [
    ("warn",   "⚠️ Warn",     "Предупреждение (ступенчатое)"),
    ("unwarn", "🗑️ Unwarn",  "Снять предупреждение"),
    ("mute",   "🔇 Mute",     "Выдать тайм-аут"),
    ("unmute", "🔊 Unmute",   "Снять тайм-аут досрочно"),
    ("kick",   "👢 Kick",     "Исключить участника"),
    ("ban",    "🔨 Ban",      "Забанить участника"),
    ("unban",  "✅ Unban",    "Разбанить по ID"),
    ("clean",  "🧹 Clean",   "Очистить сообщения в канале"),
]

MODAL_MAP = {
    "kick":   KickModal,
    "ban":    BanModal,
    "unban":  UnbanModal,
    "mute":   MuteModal,
    "unmute": UnmuteModal,
    "warn":   WarnModal,
    "unwarn": UnwarnModal,
    "clean":  CleanModal,
}


class MemberActionsSelect(discord.ui.Select):
    """Select с действиями, отфильтрованными по ролям пользователя."""

    def __init__(self, allowed: frozenset[str]):
        options = [
            discord.SelectOption(label=label, value=val, description=desc)
            for val, label, desc in MEMBER_OPTION_DEFS
            if val in allowed
        ]
        # Если нет ни одного доступного действия — добавляем заглушку
        if not options:
            options = [discord.SelectOption(label="Нет доступных действий", value="none")]

        super().__init__(
            placeholder="👤 Действия над участником...",
            options=options, min_values=1, max_values=1, row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        val = self.values[0]
        if val == "none":
            return await interaction.response.send_message(
                "❌ Нет доступных действий для вашей роли.", ephemeral=True)
        await interaction.response.send_modal(MODAL_MAP[val]())


class ServerActionsSelect(discord.ui.Select):
    """Select для действий над сервером (только для ролей с lockdown)."""

    def __init__(self, lockdown_cog: "LockdownCog"):
        self.lockdown_cog = lockdown_cog
        options = [
            discord.SelectOption(label="🔒 Lockdown All", value="lockdown",
                                 description="Заблокировать все каналы"),
            discord.SelectOption(label="🔓 Unlock All",   value="unlock",
                                 description="Снять блокировку"),
        ]
        super().__init__(
            placeholder="🔒 Управление сервером...",
            options=options, min_values=1, max_values=1, row=1,
        )

    async def callback(self, interaction: discord.Interaction):
        if "lockdown" not in get_allowed_actions(interaction.user):
            return await interaction.response.send_message(embed=no_access_embed(), ephemeral=True)

        if self.values[0] == "lockdown":
            return await interaction.response.send_modal(LockdownModal(self.lockdown_cog))

        # unlock
        await interaction.response.defer(ephemeral=True)
        guild    = interaction.guild
        everyone = guild.default_role
        has_snap = bool(self.lockdown_cog._snapshot)
        restored, failed = [], []

        for channel in guild.text_channels:
            if not channel.permissions_for(guild.me).manage_channels:
                failed.append(f"{channel.mention} (нет прав)")
                continue
            try:
                ow = channel.overwrites_for(everyone)
                ow.send_messages = (
                    self.lockdown_cog._snapshot.get(channel.id, None) if has_snap else None)
                await channel.set_permissions(everyone, overwrite=ow, reason="Lockdown снят")
                restored.append(channel.name)
            except (discord.Forbidden, discord.HTTPException) as e:
                failed.append(f"{channel.mention} ({e})")

        self.lockdown_cog._snapshot.clear()
        self.lockdown_cog._exempt_id = None

        await send_log(guild, make_log_embed(
            "🔓 UNLOCK ALL", discord.Color.green(), interaction.user, interaction.user,
            "Локдаун снят", extra=f"Восстановлено: **{len(restored)}** каналов"))

        mode  = "из снимка" if has_snap else "сброс к наследованию"
        lines = [f"🔓 Локдаун снят. Восстановлено: **{len(restored)}** каналов ({mode})."]
        if failed:
            lines.append(f"⚠️ Пропущено: {', '.join(failed)}")
        await interaction.followup.send("\n".join(lines), ephemeral=True)
        log.info("[UNLOCK] %s | %d каналов", interaction.user, len(restored))


# ══════════════════════════════════════════════════════════════════════════════
#  VIEW — строится индивидуально под каждого пользователя
# ══════════════════════════════════════════════════════════════════════════════

class AdminPanelView(discord.ui.View):
    def __init__(self, member: discord.Member, lockdown_cog: "LockdownCog"):
        super().__init__(timeout=None)
        allowed = get_allowed_actions(member)
        self.add_item(MemberActionsSelect(allowed))
        # Список управления сервером показываем всем — доступ проверяется при выборе
        self.add_item(ServerActionsSelect(lockdown_cog))


# ══════════════════════════════════════════════════════════════════════════════
#  COGS
# ══════════════════════════════════════════════════════════════════════════════

class LockdownCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._snapshot: dict[int, bool | None] = {}
        self._exempt_id: int | None = None


class AdminCog(commands.Cog):
    def __init__(self, bot, lockdown_cog: LockdownCog):
        self.bot          = bot
        self.lockdown_cog = lockdown_cog

    @app_commands.command(name="admin", description="Открыть панель управления сервером")
    async def admin(self, interaction: discord.Interaction):
        # Получаем Member (с ролями), а не просто User
        member = interaction.guild.get_member(interaction.user.id) or interaction.user

        # Дебаг: выводим все роли пользователя в консоль
        role_ids = [r.id for r in getattr(member, "roles", [])]
        log.info("[ADMIN] %s вызвал /admin | Роли: %s", member, role_ids)

        allowed = get_allowed_actions(member)
        log.info("[ADMIN] Разрешённые действия: %s", allowed or "ПУСТО")

        if not allowed:
            debug_info = (
                f"❌ У вас нет прав для использования этой команды.\n"
                f"Ваши роли (ID): {role_ids}\n"
                f"Зарегистрированные роли: {list(ROLE_PERMISSIONS.keys())}"
            )
            return await interaction.response.send_message(debug_info, ephemeral=True)

        role_label = get_role_label(member)

        # Формируем список доступных действий для embed
        action_labels = {
            "warn":     "⚠️ Warn",
            "unwarn":   "🗑️ Unwarn",
            "mute":     "🔇 Mute",
            "unmute":   "🔊 Unmute",
            "kick":     "👢 Kick",
            "ban":      "🔨 Ban",
            "unban":    "✅ Unban",
            "clean":    "🧹 Clean",
            "lockdown": "🔒 Lockdown / Unlock",
        }
        available = " · ".join(
            v for k, v in action_labels.items() if k in allowed
        )

        embed = discord.Embed(
            title="⚙️ Панель управления",
            description="Используйте выпадающие списки для выбора действия.",
            color=discord.Color.blurple(),
        )
        embed.add_field(name="🎖️ Ваша роль",          value=role_label,  inline=True)
        embed.add_field(name="✅ Доступные действия",  value=available,   inline=False)
        embed.add_field(
            name="📊 Система варнов",
            value="⚠️ **1** — уведомление  |  🔇 **2** — мут 24ч  |  🔨 **3** — бан",
            inline=False,
        )
        embed.add_field(
            name="📋 Логирование",
            value=f"Все действия → <#{LOG_CHANNEL_ID}>",
            inline=False,
        )
        embed.set_footer(text=f"Запросил: {interaction.user} • Видите только вы")

        await interaction.response.send_message(
            embed=embed,
            view=AdminPanelView(member, self.lockdown_cog),
            ephemeral=True,
        )
        log.info("[ADMIN] %s (%s) открыл панель", member, role_label)


# ══════════════════════════════════════════════════════════════════════════════
#  БОТ
# ══════════════════════════════════════════════════════════════════════════════

intents = discord.Intents.default()
intents.members         = True
intents.message_content = True
intents.moderation      = True   # для audit log (удаления сообщений)


class ModerationBot(commands.Bot):
    async def setup_hook(self):
        lockdown_cog = LockdownCog(self)
        await self.add_cog(lockdown_cog)
        await self.add_cog(AdminCog(self, lockdown_cog))
        try:
            synced = await self.tree.sync()
            log.info("✅ Синхронизировано команд: %d", len(synced))
            for cmd in synced:
                log.info("   • /%s", cmd.name)
        except Exception as e:
            log.error("Ошибка синхронизации: %s", e)


bot = ModerationBot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    log.info("✅ Бот %s запущен.", bot.user)


@bot.event
async def on_message_delete(message: discord.Message):
    """
    Логирует удаление сообщений.
    Через audit log определяет КТО удалил — сам автор или модератор.
    Системные сообщения и сообщения самого бота игнорируются.
    """
    # Игнорируем системные сообщения и сообщения бота
    if message.author.bot or not message.guild:
        return
    if not message.content and not message.attachments:
        return

    guild = message.guild

    # Пытаемся узнать кто удалил через audit log
    deleted_by = None
    await discord.utils.sleep_until(
        datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=1)
    )
    try:
        async for entry in guild.audit_logs(
            limit=5,
            action=discord.AuditLogAction.message_delete,
        ):
            # Проверяем что запись свежая (не старше 5 сек) и относится к этому сообщению
            age = (datetime.datetime.now(datetime.timezone.utc) - entry.created_at).total_seconds()
            if age < 5 and entry.target.id == message.author.id:
                deleted_by = entry.user
                break
    except (discord.Forbidden, discord.HTTPException):
        pass  # Нет прав на audit log

    # Если не нашли в логах — автор удалил сам
    if deleted_by is None or deleted_by.id == message.author.id:
        delete_note = "👤 Удалил сам автор"
    else:
        delete_note = f"🛡️ Удалил модератор: **{deleted_by}** (`{deleted_by.id}`)"

    ch = guild.get_channel(LOG_CHANNEL_ID)
    if ch is None:
        return

    embed = discord.Embed(
        title="🗑️ Сообщение удалено",
        color=discord.Color.dark_gray(),
        timestamp=datetime.datetime.now(datetime.timezone.utc),
    )
    embed.add_field(
        name="✍️ Автор",
        value=f"{message.author} (`{message.author.id}`)",
        inline=True,
    )
    embed.add_field(
        name="📍 Канал",
        value=message.channel.mention if hasattr(message.channel, "mention") else str(message.channel),
        inline=True,
    )
    embed.add_field(name="🔍 Кто удалил", value=delete_note, inline=False)

    # Содержимое сообщения (обрезаем если длинное)
    content_text = message.content or "*[нет текста]*"
    if len(content_text) > 1024:
        content_text = content_text[:1021] + "..."
    embed.add_field(name="💬 Содержимое", value=content_text, inline=False)

    # Вложения
    if message.attachments:
        attach_list = "\n".join(a.filename for a in message.attachments)
        embed.add_field(name="📎 Вложения", value=attach_list, inline=False)

    embed.set_thumbnail(url=message.author.display_avatar.url)
    embed.set_footer(text=f"ID сообщения: {message.id}")

    try:
        await ch.send(embed=embed)
    except (discord.Forbidden, discord.HTTPException):
        pass

    log.info("[MSG_DELETE] #%s | %s: %s | %s",
             message.channel, message.author,
             (message.content or "")[:80], delete_note)


@bot.event
async def on_message_edit(before: discord.Message, after: discord.Message):
    """Логирует редактирование сообщений."""
    # Игнорируем ботов, личные сообщения и случаи когда текст не изменился
    if before.author.bot or not before.guild:
        return
    if before.content == after.content:
        return
    if not before.content and not after.content:
        return

    ch = before.guild.get_channel(LOG_CHANNEL_ID)
    if ch is None:
        return

    embed = discord.Embed(
        title="✏️ Сообщение отредактировано",
        color=discord.Color.blue(),
        timestamp=datetime.datetime.now(datetime.timezone.utc),
    )
    embed.add_field(
        name="✍️ Автор",
        value=f"{before.author} (`{before.author.id}`)",
        inline=True,
    )
    embed.add_field(
        name="📍 Канал",
        value=before.channel.mention if hasattr(before.channel, "mention") else str(before.channel),
        inline=True,
    )

    # Обрезаем длинные сообщения
    before_text = before.content or "*[пусто]*"
    after_text  = after.content  or "*[пусто]*"
    if len(before_text) > 1024:
        before_text = before_text[:1021] + "..."
    if len(after_text) > 1024:
        after_text  = after_text[:1021]  + "..."

    embed.add_field(name="📝 До",    value=before_text, inline=False)
    embed.add_field(name="📝 После", value=after_text,  inline=False)

    # Ссылка на сообщение
    embed.add_field(
        name="🔗 Перейти",
        value=f"[Нажмите сюда]({after.jump_url})",
        inline=False,
    )

    embed.set_thumbnail(url=before.author.display_avatar.url)
    embed.set_footer(text=f"ID сообщения: {before.id}")

    try:
        await ch.send(embed=embed)
    except (discord.Forbidden, discord.HTTPException):
        pass

    log.info("[MSG_EDIT] #%s | %s: %r → %r",
             before.channel, before.author,
             (before.content or "")[:60],
             (after.content  or "")[:60])


@bot.tree.error
async def on_app_command_error(
    interaction: discord.Interaction, error: app_commands.AppCommandError
):
    log.error("[ERROR] %s: %s", interaction.command, error)
    msg = f"❌ Ошибка: `{error}`"
    if interaction.response.is_done():
        await interaction.followup.send(msg, ephemeral=True)
    else:
        await interaction.response.send_message(msg, ephemeral=True)


if __name__ == "__main__":
    bot.run(TOKEN)

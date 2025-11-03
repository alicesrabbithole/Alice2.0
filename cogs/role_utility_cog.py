import discord
from discord.ext import commands
from discord import app_commands
from utils.checks import is_staff
import config
from utils.theme import Colors


class RoleUtilityCog(commands.Cog, name="Role Utilities"):
    """Utility commands for listing and inspecting server roles."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_check(self, ctx: commands.Context) -> bool:
        """A general check for all commands in this cog to ensure they're in the right server."""
        return ctx.guild is not None and ctx.guild.id == config.WONDERLAND_GUILD_ID

    @commands.hybrid_command(name="roles", description="List all roles in the server, categorized.")
    @is_staff()
    async def roles(self, ctx: commands.Context):
        """Lists all roles in the server, split into categories."""
        await ctx.defer(ephemeral=True)
        # Exclude @everyone role
        all_roles = sorted(ctx.guild.roles[1:], key=lambda r: r.position, reverse=True)

        bot_roles = [r for r in all_roles if r.is_bot_managed()]
        ping_roles = [r for r in all_roles if "ping" in r.name.lower() and not r.is_bot_managed()]
        other_roles = [r for r in all_roles if r not in bot_roles and r not in ping_roles]

        embed = discord.Embed(
            title=f"📜 Roles in {ctx.guild.name}",
            description=f"Showing a total of **{len(all_roles)}** roles.",
            color=Colors.THEME_COLOR
        )

        def add_role_fields(title: str, roles: list[discord.Role]):
            """Helper to add role lists to the embed, handling character limits."""
            if not roles:
                return

            chunks = []
            current_chunk = ""
            for role in roles:
                line = f"{role.mention}\n"
                if len(current_chunk) + len(line) > 1024:
                    chunks.append(current_chunk)
                    current_chunk = line
                else:
                    current_chunk += line
            if current_chunk:
                chunks.append(current_chunk)

            for i, chunk in enumerate(chunks):
                field_title = f"{title} ({i + 1}/{len(chunks)})" if len(chunks) > 1 else title
                embed.add_field(name=field_title, value=chunk, inline=False)

        add_role_fields("📦 Other Roles", other_roles)
        add_role_fields("📣 Ping Roles", ping_roles)
        add_role_fields("🤖 Bot Roles", bot_roles)

        embed.set_footer(text=f"Requested by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="roleinfo", description="Show detailed information about a role.")
    @app_commands.describe(role="The role you want information about.")
    @is_staff()
    async def roleinfo(self, ctx: commands.Context, role: discord.Role):
        """Shows detailed information about a specific role."""
        embed = discord.Embed(
            title=f"🔍 Role Info: {role.name}",
            color=role.color if role.color.value != 0 else discord.Color.light_grey()
        )
        embed.add_field(name="Role ID", value=f"`{role.id}`", inline=True)
        embed.add_field(name="Members", value=str(len(role.members)), inline=True)
        embed.add_field(name="Color (Hex)", value=f"`{str(role.color)}`", inline=True)
        embed.add_field(name="Mentionable", value="✅ Yes" if role.mentionable else "❌ No", inline=True)
        embed.add_field(name="Hoisted", value="✅ Yes" if role.hoist else "❌ No", inline=True)
        embed.add_field(name="Position", value=str(role.position), inline=True)
        embed.set_footer(text=f"Created at: {discord.utils.format_dt(role.created_at, style='D')}")
        await ctx.send(embed=embed, ephemeral=False)


async def setup(bot: commands.Bot):
    await bot.add_cog(RoleUtilityCog(bot))
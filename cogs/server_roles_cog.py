import discord
from discord.ext import commands

# --- Constants ---
WONDERLAND_GUILD_ID = 1309962372269609010


class ServerRolesCog(commands.Cog):
    """A cog to list all roles in the server, categorized."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def get_role_chunks(self, roles: list[discord.Role], title: str) -> list[tuple[str, str]]:
        """Splits a list of role mentions into chunks that fit in an embed field."""
        chunks = []
        current_chunk = ""
        for role in sorted(roles, key=lambda r: r.position, reverse=True):
            line = f"{role.mention}\n"
            if len(current_chunk) + len(line) > 1024:
                chunks.append(current_chunk)
                current_chunk = line
            else:
                current_chunk += line
        if current_chunk:
            chunks.append(current_chunk)

        # Return a list of (title, content) tuples
        return [(f"{title} ({i + 1})" if i > 0 else title, chunk) for i, chunk in enumerate(chunks)]

    @commands.hybrid_command(name="roles", description="List all roles in the server, categorized.")
    @commands.guild_only()
    async def roles(self, ctx: commands.Context):
        if ctx.guild.id != WONDERLAND_GUILD_ID:
            await ctx.send("❌ This command can only be used in the **Wonderland** server.", ephemeral=True)
            return

        # Defer the response as this can take a moment
        await ctx.defer(ephemeral=True)

        all_roles = ctx.guild.roles[1:]  # Exclude @everyone

        # Categorize roles
        bot_roles = [r for r in all_roles if r.is_bot_managed()]
        ping_roles = [r for r in all_roles if "ping" in r.name.lower() and not r.is_bot_managed()]
        other_roles = [r for r in all_roles if not r.is_bot_managed() and r not in ping_roles]

        # Create the embed and add fields
        embed = discord.Embed(
            title=f"📜 Roles in {ctx.guild.name}",
            description=f"Showing a total of **{len(all_roles)}** roles.",
            color=discord.Color.purple()
        )

        role_fields = []
        if bot_roles:
            role_fields.extend(self.get_role_chunks(bot_roles, "🤖 Bot Roles"))
        if ping_roles:
            role_fields.extend(self.get_role_chunks(ping_roles, "📣 Ping Roles"))
        if other_roles:
            role_fields.extend(self.get_role_chunks(other_roles, "📦 Other Roles"))

        for name, value in role_fields:
            embed.add_field(name=name, value=value, inline=False)

        embed.set_footer(text=f"Requested by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)

        # Note: If there are too many roles, this might exceed the total embed character limit.
        # For most servers, this will be fine.
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(ServerRolesCog(bot))
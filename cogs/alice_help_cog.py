import discord
from discord.ext import commands
from typing import Optional

# Using your theme for colors and emojis
from utils.theme import Colors, Emojis


class AliceHelpCog(commands.Cog, name="Help"):
    """Provides a dynamic, hybrid help command."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # --- THIS IS THE FIX ---
    # The command name is now correctly set back to 'alicehelp'.
    @commands.hybrid_command(name="alicehelp", description="Shows a list of available commands.")
    @commands.guild_only()
    async def alicehelp_command(self, ctx: commands.Context, command_name: Optional[str] = None):
        """Shows help for all commands or a specific command."""
        await ctx.defer(ephemeral=True)

        if command_name:
            command = self.bot.get_command(command_name)
            if not command or command.hidden:
                await ctx.send(f"{Emojis.FAILURE} I couldn't find a command named `{command_name}` that you can use.",
                               ephemeral=True)
                return
            await self.send_command_help(ctx, command)
        else:
            await self.send_full_help(ctx)

    async def send_full_help(self, ctx: commands.Context):
        """Sends an embed with all visible commands categorized by cog."""
        embed = discord.Embed(
            title="Alice Bot Help",
            description=f"Here are my commands. For more info, use `{ctx.prefix}alicehelp <command_name>`.",
            color=Colors.PRIMARY
        ).set_thumbnail(url=self.bot.user.display_avatar.url)

        sorted_cogs = sorted(self.bot.cogs.values(), key=lambda c: c.qualified_name)

        for cog in sorted_cogs:
            if cog.qualified_name == "Help":
                continue

            visible_commands = [cmd for cmd in cog.get_commands() if
                                isinstance(cmd, commands.HybridCommand) and not cmd.hidden]

            if visible_commands:
                command_list = [
                    f"**`/{cmd.name}`** - {cmd.description or 'No description available.'}"
                    for cmd in sorted(visible_commands, key=lambda c: c.name)
                ]
                embed.add_field(
                    name=f"**{cog.qualified_name} Commands**",
                    value="\n".join(command_list),
                    inline=False
                )

        embed.set_footer(text="You can use either / or ! for hybrid commands.")
        await ctx.send(embed=embed, ephemeral=True)

    async def send_command_help(self, ctx: commands.Context, command: commands.Command):
        """Sends a detailed help embed for a specific command."""
        embed = discord.Embed(
            title=f"Help for `/{command.name}`",
            description=command.description or "No description available.",
            color=Colors.PRIMARY
        )
        usage = f"/{command.name} {command.signature}"
        embed.add_field(name="Usage", value=f"`{usage}`", inline=False)

        if command.aliases:
            embed.add_field(name="Aliases", value=", ".join(f"`{alias}`" for alias in command.aliases), inline=False)

        await ctx.send(embed=embed, ephemeral=True)

        @commands.command()
        @commands.is_owner()
        async def list_appcmds(self, ctx: commands.Context):
            names = []
            for c in self.bot.tree.walk_commands():
                # show qualified name and whether it looks global or has a guild-specific binding
                names.append(f"{c.qualified_name}  (id={getattr(c, 'id', None)})")
            await ctx.reply("App commands:\n" + ("\n".join(names) if names else "(none)"), mention_author=False)


async def setup(bot: commands.Bot):
    await bot.add_cog(AliceHelpCog(bot))
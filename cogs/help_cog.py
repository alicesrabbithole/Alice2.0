import discord
from discord.ext import commands
from typing import List, Optional

import config


class HelpCog(commands.Cog, name="Help"):
    """Provides a dynamic, permission-aware help command."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def get_cog_commands(self, cog: commands.Cog) -> List[commands.Command]:
        """
        Gets a list of commands from a cog, checking permissions for the context author.
        """
        visible_commands = []
        for command in cog.get_commands():
            if not command.hidden:
                try:
                    if await command.can_run(self.context):
                        visible_commands.append(command)
                except (commands.CommandError, discord.DiscordException):
                    continue
        return visible_commands

    @commands.hybrid_command(name="help", aliases=["alicehelp"], description="Shows a list of available commands.")
    async def help_command(self, ctx: commands.Context, command_name: Optional[str] = None):
        """Shows help for all commands or a specific command."""
        self.context = ctx  # Store context for permission checks

        if command_name:
            command = self.bot.get_command(command_name)
            if not command or not await command.can_run(ctx):
                await ctx.send(f"❌ I couldn't find a command named `{command_name}` that you can use.", ephemeral=True)
                return
            await self.send_command_help(ctx, command)
        else:
            await self.send_full_help(ctx)

    async def send_full_help(self, ctx: commands.Context):
        """Sends an embed with all visible commands categorized by cog."""
        embed = discord.Embed(
            title="Alice Bot Help",
            description="Here are all the commands you can use. For more info on a command, use `/help <command_name>`.",
            color=discord.Color.purple()
        ).set_thumbnail(url=self.bot.user.display_avatar.url)

        # Sort cogs alphabetically by name, but put "Owner" and "Permissions" last
        cogs = sorted(
            self.bot.cogs.values(),
            key=lambda c: (c.qualified_name in ["Owner", "Permissions"], c.qualified_name)
        )

        for cog in cogs:
            visible_commands = await self.get_cog_commands(cog)
            if visible_commands:
                # Format commands with their descriptions
                command_list = [
                    f"**`/{cmd.name}`** - {cmd.description or 'No description available.'}"
                    for cmd in sorted(visible_commands, key=lambda c: c.name)
                ]
                embed.add_field(
                    name=f"**{cog.qualified_name} Commands**",
                    value="\n".join(command_list),
                    inline=False
                )

        embed.set_footer(text="You can use ! or / for any command.")
        await ctx.send(embed=embed, ephemeral=True)

    async def send_command_help(self, ctx: commands.Context, command: commands.Command):
        """Sends a detailed help embed for a specific command."""
        embed = discord.Embed(
            title=f"Help for `/{command.name}`",
            description=command.description or "No description available.",
            color=discord.Color.purple()
        )
        # Usage
        usage = f"/{command.name} {command.signature}"
        embed.add_field(name="Usage", value=f"`{usage}`", inline=False)

        # Aliases
        if command.aliases:
            embed.add_field(name="Aliases", value=", ".join(f"`{alias}`" for alias in command.aliases), inline=False)

        await ctx.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(HelpCog(bot))
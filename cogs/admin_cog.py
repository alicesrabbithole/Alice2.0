import discord
from discord.ext import commands
from typing import Optional

# Import your theme for consistent colors
from utils.theme import Colors, Emojis


class HelpCog(commands.Cog, name="Help"):
    """Provides a dynamic, hybrid help command."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.hybrid_command(name="help", aliases=["alicehelp"], description="Shows a list of available commands.")
    @commands.guild_only()  # Good practice for hybrid commands to specify scope
    async def help_command(self, ctx: commands.Context, command_name: Optional[str] = None):
        """Shows help for all commands or a specific command."""

        # If a command name is provided, show help for that specific command
        if command_name:
            # Look for the command in both the slash command tree and prefix commands
            command = self.bot.tree.get_command(command_name) or self.bot.get_command(command_name)

            # If no command is found, send an error
            if not command:
                await ctx.send(f"{Emojis.FAILURE} I couldn't find a command named `{command_name}`.", ephemeral=True)
                return
            await self.send_command_help(ctx, command)
        # Otherwise, show the full list of commands
        else:
            await self.send_full_help(ctx)

    async def send_full_help(self, ctx: commands.Context):
        """Sends an embed with all commands categorized by cog."""
        embed = discord.Embed(
            title="Alice Bot Help",
            description=f"Here are my commands. For more info, use `{ctx.prefix}help <command_name>`.",
            color=Colors.PRIMARY  # Using your theme color!
        ).set_thumbnail(url=self.bot.user.display_avatar.url)

        # Sort cogs alphabetically for a clean look
        sorted_cogs = sorted(self.bot.cogs.values(), key=lambda c: c.qualified_name)

        for cog in sorted_cogs:
            # We will list both hybrid and regular slash commands
            commands_in_cog = []

            # Get hybrid commands
            for cmd in cog.get_commands():
                if isinstance(cmd, commands.HybridCommand) and not cmd.hidden:
                    commands_in_cog.append(cmd)

            # Get slash-only commands (if any)
            if hasattr(cog, 'get_app_commands'):
                for cmd in cog.get_app_commands():
                    # Avoid duplicates if it's already in the hybrid list
                    if not any(c.name == cmd.name for c in commands_in_cog):
                        commands_in_cog.append(cmd)

            # Only add the field if there are commands to show
            if commands_in_cog:
                # The permission checks will handle unauthorized use, so we don't need to check here.
                command_list = [
                    f"**`/{cmd.name}`** - {cmd.description or 'No description available.'}"
                    for cmd in sorted(commands_in_cog, key=lambda c: c.name)
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
            color=Colors.PRIMARY  # Using your theme color!
        )

        # Build the usage string
        usage = f"/{command.name} {command.signature}"
        embed.add_field(name="Usage", value=f"`{usage}`", inline=False)

        # Show aliases if they exist
        if command.aliases:
            embed.add_field(name="Aliases", value=", ".join(f"`{alias}`" for alias in command.aliases), inline=False)

        await ctx.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(HelpCog(bot))
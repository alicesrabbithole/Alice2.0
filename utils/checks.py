import discord
from discord.ext import commands

# Use a relative import to get a sibling file in the same directory
from .theme import Emojis

# --- Define your Role IDs here ---
STAFF_ROLE_ID = 1309962372542234657
ADMIN_ROLE_ID = 1309962372542234661


def is_staff():
    """
    A check for hybrid commands that can be used by Staff, Admins, or the Bot Owner.
    This works with both slash (/) and prefix (!) commands.
    """

    async def predicate(ctx: commands.Context) -> bool:
        # User is in ctx.author
        author = ctx.author

        # Always allow the bot owner.
        if await ctx.bot.is_owner(author):
            return True

        # Always allow server administrators
        if isinstance(author, discord.Member) and author.guild_permissions.administrator:
            return True

        # Check if the user has one of the required roles.
        required_roles = {STAFF_ROLE_ID, ADMIN_ROLE_ID}
        user_roles = {role.id for role in author.roles}

        if not user_roles.isdisjoint(required_roles):
            return True

        # If all checks fail, send a clear error message.
        # ctx.send with ephemeral=True is smart: it's private for slash commands
        # and public for prefix commands (since they can't be private).
        await ctx.send(f"{Emojis.FAILURE} You need the Staff or Admin role to use this command.", ephemeral=True)
        return False

    return commands.check(predicate)


def is_admin():
    """
    A check for hybrid commands that can be used by Admins or the Bot Owner.
    """

    async def predicate(ctx: commands.Context) -> bool:
        author = ctx.author

        if await ctx.bot.is_owner(author):
            return True

        if isinstance(author, discord.Member) and author.guild_permissions.administrator:
            return True

        if any(role.id == ADMIN_ROLE_ID for role in author.roles):
            return True

        await ctx.send(f"{Emojis.FAILURE} You need the Admin role to use this command.", ephemeral=True)
        return False

    return commands.check(predicate)
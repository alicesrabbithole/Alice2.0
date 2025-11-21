import discord
from discord.ext import commands
import random
import os
from typing import List, Dict, Optional, Tuple
from english_words import get_english_words_set

ALLOWED_CHANNEL_IDS = [1309962373846532159, 1382445010988830852]
STAFF_ROLE_ID = 123456789123456789  # Replace with your actual staff role ID

ANSWER_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'wordle-answers-alphabetical.txt')
KEYBOARD_ROWS = [
    "QWERTYUIOP",
    "ASDFGHJKL",
    "ZXCVBNM"
]
EMOJI_GREEN = "🟩"
EMOJI_YELLOW = "🟨"
EMOJI_GRAY = "⬜"

def load_word_list(path: str) -> List[str]:
    try:
        with open(path) as f:
            words = [line.strip().lower()
                     for line in f
                     if line.strip() and len(line.strip()) == 5 and line.strip().isalpha()]
        return words
    except Exception:
        return []

ANSWERS_LIST: List[str] = load_word_list(ANSWER_PATH)
ENGLISH_WORDS = get_english_words_set(['web2'], lower=True)
ALLOWED_GUESSES = set(word for word in ENGLISH_WORDS if len(word) == 5 and word.isalpha())

def wordle_feedback(guess: str, answer: str) -> List[str]:
    feedback = ['gray'] * 5
    answer_chars = list(answer)
    guess_chars = list(guess)
    # Green pass
    for i in range(5):
        if guess_chars[i] == answer_chars[i]:
            feedback[i] = 'green'
            answer_chars[i] = None
            guess_chars[i] = None
    # Yellow pass
    for i in range(5):
        if guess_chars[i] and guess_chars[i] in answer_chars:
            feedback[i] = 'yellow'
            idx = answer_chars.index(guess_chars[i])
            answer_chars[idx] = None
            guess_chars[i] = None
    return feedback

def render_keyboard(guesses: List[str], feedbacks: List[List[str]]) -> str:
    status = {c: "" for row in KEYBOARD_ROWS for c in row}
    for guess, fb in zip(guesses, feedbacks):
        for i, letter in enumerate(guess):
            up = letter.upper()
            if fb[i] == 'green':
                status[up] = EMOJI_GREEN
            elif fb[i] == 'yellow':
                if status[up] != EMOJI_GREEN:
                    status[up] = EMOJI_YELLOW
            elif fb[i] == 'gray':
                if status[up] not in (EMOJI_GREEN, EMOJI_YELLOW):
                    status[up] = "⬛"
    lines = []
    for row in KEYBOARD_ROWS:
        line = ""
        for c in row:
            block = status[c]
            if not block:
                line += c
            else:
                line += block + c
        lines.append(line)
    return "\n".join(lines)

class WordleGame:
    def __init__(self, answer: str):
        self.answer: str = answer
        self.guesses: List[str] = []
        self.feedbacks: List[List[str]] = []

    def add_guess(self, guess: str) -> List[str]:
        fb = wordle_feedback(guess, self.answer)
        self.guesses.append(guess)
        self.feedbacks.append(fb)
        return fb

    def is_solved(self) -> bool:
        return self.guesses and self.guesses[-1] == self.answer

class WordleCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.games: Dict[int, WordleGame] = {}

    def is_allowed_channel(self, channel_id: int) -> bool:
        return channel_id in ALLOWED_CHANNEL_IDS

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        channel_id = message.channel.id
        content = message.content.strip().lower()
        # Only respond in allowed channels
        if not self.is_allowed_channel(channel_id):
            return

        # ---- STAFF RESET ----
        if content == "resetwordle":
            # Check for staff role or relevant permissions
            is_staff = any(r.id == STAFF_ROLE_ID for r in getattr(message.author, "roles", [])) \
                or message.author.guild_permissions.manage_guild \
                or message.author.guild_permissions.administrator
            if not is_staff:
                await message.channel.send("You do not have permission to reset Wordle.")
                return
            if channel_id in self.games:
                del self.games[channel_id]
                await message.channel.send("✅ Wordle game has been reset for this channel.")
            else:
                await message.channel.send("No Wordle game to reset in this channel.")
            return

        # ---- NEW GAME ----
        if content == "new wordle":
            if not ANSWERS_LIST:
                await message.channel.send("No answers loaded for Wordle!")
                return
            self.games[channel_id] = WordleGame(random.choice(ANSWERS_LIST))
            await message.channel.send("New Wordle started! Make your guess with `guess abcde`.")
            return

        # ---- Ensure game exists ----
        game = self.games.get(channel_id)
        if not game:
            if content.startswith("guess") or content == "wordle status":
                await message.channel.send("No Wordle running! Type `new wordle` to start.")
            return

        # ---- GUESS ----
        if content.startswith("guess "):
            guess = content[6:].strip().lower()
            if len(guess) != 5 or not guess.isalpha():
                await message.channel.send("Your guess must be a 5-letter word.")
                return
            if guess not in ALLOWED_GUESSES:
                await message.channel.send("Not a valid English word!")
                return
            fb = game.add_guess(guess)
            emoji_row = "".join(
                EMOJI_GREEN if x == 'green' else EMOJI_YELLOW if x == 'yellow' else EMOJI_GRAY for x in fb
            )
            await message.channel.send(f"`{guess.upper()}`  {emoji_row}")
            if game.is_solved():
                await message.channel.send(f"🎉 Solved! The word was **{game.answer.upper()}**. Total guesses: {len(game.guesses)}")
                del self.games[channel_id]
            return

        # ---- STATUS ----
        if content == "wordle status":
            keyboard_art = render_keyboard(game.guesses, game.feedbacks)
            guess_lines = [
                f"`{g.upper()}`  " +
                "".join(EMOJI_GREEN if x == 'green' else EMOJI_YELLOW if x == 'yellow' else EMOJI_GRAY for x in fb)
                for g, fb in zip(game.guesses, game.feedbacks)
            ]
            status_text = "\n".join(guess_lines) if guess_lines else "No guesses yet."
            await message.channel.send(
                f"Wordle status:\n{status_text}\n\n**Keyboard:**\n```\n{keyboard_art}\n```"
            )
            return

    # Example: add a wordle status command as well!
    @commands.command(name="wordle_status", help="Show your Wordle status (guesses and keyboard)")
    async def wordle_status_cmd(self, ctx: commands.Context):
        if not self.is_allowed_channel(ctx.channel.id):
            await ctx.send("This command can't be used in this channel.")
            return
        game = self.games.get(ctx.channel.id)
        if not game:
            await ctx.send("No Wordle running! Type `new wordle` to start.")
            return
        keyboard_art = render_keyboard(game.guesses, game.feedbacks)
        guess_lines = [
            f"`{g.upper()}`  " +
            "".join(EMOJI_GREEN if x == 'green' else EMOJI_YELLOW if x == 'yellow' else EMOJI_GRAY for x in fb)
            for g, fb in zip(game.guesses, game.feedbacks)
        ]
        status_text = "\n".join(guess_lines) if guess_lines else "No guesses yet."
        await ctx.send(
            f"Wordle status:\n{status_text}\n\n**Keyboard:**\n```\n{keyboard_art}\n```"
        )

async def setup(bot: commands.Bot):
    await bot.add_cog(WordleCog(bot))
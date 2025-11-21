import discord
from discord.ext import commands
import random
import os
from utils.checks import STAFF_ROLE_ID  # This should be an integer representing your staff role's ID
from english_words import get_english_words_set

ALLOWED_CHANNEL_IDS = (1309962373846532159, 1382445010988830852) # Replace this with your desired channel's ID

ANSWER_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'wordle-answers-alphabetical.txt')
KEYBOARD_ROWS = [
    "QWERTYUIOP",
    "ASDFGHJKL",
    "ZXCVBNM"
]
EMOJI_GREEN = "🟩"
EMOJI_YELLOW = "🟨"
EMOJI_GRAY = "⬜"

def load_word_list(path):
    try:
        with open(path) as f:
            words = [line.strip().lower() for line in f if line.strip() and len(line.strip()) == 5 and line.strip().isalpha()]
        return words
    except Exception:
        return []

# Official answers list (for selecting answers)
ANSWERS_LIST = load_word_list(ANSWER_PATH)

# Allowed guesses: all English 5-letter words
ENGLISH_WORDS = get_english_words_set(['web2'], lower=True)
ALLOWED_GUESSES = set(word for word in ENGLISH_WORDS if len(word) == 5 and word.isalpha())

def wordle_feedback(guess, answer):
    feedback = ['gray'] * 5
    answer_chars = list(answer)
    guess_chars = list(guess)
    for i in range(5):
        if guess_chars[i] == answer_chars[i]:
            feedback[i] = 'green'
            answer_chars[i] = None
            guess_chars[i] = None
    for i in range(5):
        if guess_chars[i] and guess_chars[i] in answer_chars:
            feedback[i] = 'yellow'
            idx = answer_chars.index(guess_chars[i])
            answer_chars[idx] = None
            guess_chars[i] = None
    return feedback

def render_keyboard(guesses, feedbacks):
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
    def __init__(self, answer):
        self.answer = answer
        self.guesses = []
        self.feedbacks = []

    def add_guess(self, guess):
        fb = wordle_feedback(guess, self.answer)
        self.guesses.append(guess)
        self.feedbacks.append(fb)
        return fb

    def is_solved(self):
        return self.guesses and self.guesses[-1] == self.answer

class WordleCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.games = {}

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.channel.id != ALLOWED_CHANNEL_IDS:
            return
        if message.author.bot:
            return

        channel_id = message.channel.id
        content = message.content.strip().lower()

        # Staff-only Wordle reset
        if content == "resetwordle":
            staff_role = discord.utils.get(message.author.roles, id=STAFF_ROLE_ID)
            if not staff_role:
                await message.channel.send("You do not have permission to reset Wordle.")
                return
            if channel_id in self.games:
                del self.games[channel_id]
                await message.channel.send("✅ Wordle game has been reset for this channel.")
            else:
                await message.channel.send("No Wordle game to reset in this channel.")
            return

        # Start a new wordle
        if content == "new wordle":
            if not ANSWERS_LIST:
                await message.channel.send("No answers loaded for Wordle!")
                return
            self.games[channel_id] = WordleGame(random.choice(ANSWERS_LIST))
            await message.channel.send("New Wordle started! Make your guess with `guess abcde`.")
            return

        # Ensure game exists for guess/status
        game = self.games.get(channel_id)
        if not game:
            if content.startswith("guess") or content == "wordle status":
                await message.channel.send("No Wordle running! Type `new wordle` to start.")
            return

        # Process Guess
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

        # Status Command
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

async def setup(bot):
    await bot.add_cog(WordleCog(bot))
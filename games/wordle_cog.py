import discord
from discord.ext import commands
import random
import os
from typing import List, Dict, Optional
from PIL import Image

ALLOWED_CHANNEL_IDS = [1309962373846532159, 1382445010988830852, 1309962375058690071]
STAFF_ROLE_ID = 123456789123456789  # Replace with your actual staff role ID

ANSWER_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'wordle-answers-alphabetical.txt')
GUESS_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'wordle-guesses.txt')
STANDARD_SIZE = (48, 48)

print(f"GUESS_PATH used: {GUESS_PATH}")

KEYBOARD_ROWS = [
    "QWERTYUIOP",
    "ASDFGHJKL",
    "ZXCVBNM"
]

def load_word_list(path: str) -> List[str]:
    try:
        with open(path, encoding="utf-8") as f:
            words = [
                line.strip().lower()
                for line in f
                if line.strip() and len(line.strip()) == 5 and line.strip().isalpha()
            ]
        print(f"Loaded {len(words)} words from {path}")
        # Check if 'sleep' is present, and if there are any weird words with spaces
        print(f"'sleep' in word list: {'sleep' in words}")
        for w in words:
            if ' ' in w or '\t' in w or not w.isalpha() or len(w) != 5:
                print(f"Weird entry: {repr(w)}")
        return words
    except Exception as e:
        print(f"Failed to load words from {path}: {e}")
        return []

ANSWERS_LIST: List[str] = load_word_list(ANSWER_PATH)
ALLOWED_GUESSES: set = set(load_word_list(GUESS_PATH))
print(f"ALLOWED_GUESSES loaded: {len(ALLOWED_GUESSES)} entries.")
print(f"'sleep' in ALLOWED_GUESSES: {'sleep' in ALLOWED_GUESSES}")

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

def get_letter_image(letter: str, color: str) -> str:
    letter = letter.lower()
    # image file scheme: basea.png for white, graya.png for gray, greena.png for green, yellowa.png for yellow
    if color == "white":
        filename = f"base{letter}.png"
    else:
        filename = f"{color}{letter}.png"
    folder = os.path.join(os.path.dirname(__file__), '..', 'wordle_letters', color)
    return os.path.join(folder, filename)

def compose_row(guess: str, feedback: List[str]) -> Image.Image:
    imgs = [
        Image.open(get_letter_image(letter, color)).resize(STANDARD_SIZE, Image.LANCZOS)
        for letter, color in zip(guess, feedback)
    ]
    w, h = STANDARD_SIZE
    canvas = Image.new('RGBA', (w * 5, h))
    for i, img in enumerate(imgs):
        canvas.paste(img, (i * w, 0))
    return canvas

def compute_keyboard_status(guesses: List[str], feedbacks: List[List[str]]) -> Dict[str, str]:
    status = {c: "white" for row in KEYBOARD_ROWS for c in row}
    for guess, fb in zip(guesses, feedbacks):
        for i, letter in enumerate(guess):
            up = letter.upper()
            if fb[i] == 'green':
                status[up] = 'green'
            elif fb[i] == 'yellow':
                if status[up] != 'green':
                    status[up] = 'yellow'
            elif fb[i] == 'gray':
                if status[up] not in ('green', 'yellow'):
                    status[up] = 'gray'
    return status

def compose_keyboard(key_status: Dict[str, str]) -> Image.Image:
    row_imgs = []
    for row in KEYBOARD_ROWS:
        imgs = [
            Image.open(get_letter_image(ch, key_status.get(ch, "white"))).resize(STANDARD_SIZE, Image.LANCZOS)
            for ch in row
        ]
        w, h = STANDARD_SIZE
        canvas_row = Image.new('RGBA', (w * len(row), h))
        for i, img in enumerate(imgs):
            canvas_row.paste(img, (i * w, 0))
        row_imgs.append(canvas_row)
    total_height = h * len(row_imgs)
    canvas = Image.new('RGBA', (row_imgs[0].width, total_height))
    y_offset = 0
    for row_img in row_imgs:
        canvas.paste(row_img, (0, y_offset))
        y_offset += row_img.height
    return canvas

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
        if not self.is_allowed_channel(channel_id):
            return

        # STAFF RESET
        if content == "resetwordle":
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

        # NEW GAME
        if content == "new wordle":
            if not ANSWERS_LIST:
                await message.channel.send("No answers loaded for Wordle!")
                return
            self.games[channel_id] = WordleGame(random.choice(ANSWERS_LIST))
            await message.channel.send("New Wordle started! Make your guess with `guess abcde`.")
            return

        game = self.games.get(channel_id)
        if not game:
            if content.startswith("guess") or content == "wordle status":
                await message.channel.send("No Wordle running! Type `new wordle` to start.")
            return

        # GUESS
        if content.startswith("guess "):
            guess = content[6:].strip().lower()
            print(f"User guess: '{guess}' (type: {type(guess)})")
            print(f"Guess in ALLOWED_GUESSES? {guess in ALLOWED_GUESSES}")
            if len(guess) != 5 or not guess.isalpha():
                await message.channel.send("Your guess must be a 5-letter word.")
                return
            if guess not in ALLOWED_GUESSES:
                await message.channel.send(f"Not a valid English word! (Debug: '{guess}' not in {len(ALLOWED_GUESSES)} words.)")
                return
            fb = game.add_guess(guess)
            try:
                row_img = compose_row(guess, fb)
                img_path = "wordle_row.png"
                row_img.save(img_path)
                await message.channel.send(
                    file=discord.File(img_path)
                )
                os.remove(img_path)
            except Exception as e:
                await message.channel.send(
                    f"Image could not be generated: {e}"
                )
            if game.is_solved():
                await message.channel.send(
                    f"🎉 Solved! The word was **{game.answer.upper()}**. Total guesses: {len(game.guesses)}"
                )
                del self.games[channel_id]
            return

        # STATUS
        if content == "wordle status":
            try:
                key_status = compute_keyboard_status(game.guesses, game.feedbacks)
                kb_img = compose_keyboard(key_status)
                kb_img_path = "wordle_keyboard.png"
                kb_img.save(kb_img_path)
                await message.channel.send(
                    file=discord.File(kb_img_path)
                )
                os.remove(kb_img_path)
            except Exception as e:
                await message.channel.send(
                    f"Image could not be generated: {e}"
                )
            return

    @commands.command(name="wordle_status", help="Show your Wordle status (guesses and keyboard)")
    async def wordle_status_cmd(self, ctx: commands.Context):
        if not self.is_allowed_channel(ctx.channel.id):
            await ctx.send("This command can't be used in this channel.")
            return
        game = self.games.get(ctx.channel.id)
        if not game:
            await ctx.send("No Wordle running! Type `new wordle` to start.")
            return
        try:
            key_status = compute_keyboard_status(game.guesses, game.feedbacks)
            kb_img = compose_keyboard(key_status)
            kb_img_path = "wordle_keyboard.png"
            kb_img.save(kb_img_path)
            await ctx.send(
                file=discord.File(kb_img_path)
            )
            os.remove(kb_img_path)
        except Exception as e:
            await ctx.send(
                f"Image could not be generated: {e}"
            )

async def setup(bot: commands.Bot):
    await bot.add_cog(WordleCog(bot))
import discord
from discord.ext import commands
import re
import json
import os

GAMES_SAVE_PATH = "games.json"

class TwentyoneQuestionsGame:
    def __init__(self, answer, host_id):
        self.answer = answer.lower()
        self.host_id = host_id
        self.max_questions = 21
        self.questions_queue = []
        self.answered_questions = []
        self.guesses = []
        self.active = True
        self.winner_id = None

    def add_question(self, author_id, question):
        qid = len(self.questions_queue) + len(self.answered_questions) + 1
        question_obj = {
            "id": qid,
            "author_id": author_id,
            "question": question,
            "status": "queued",
            "answer_text": None
        }
        self.questions_queue.append(question_obj)
        return qid

    def answer_question(self, label, answer_text=None):
        for i, q in enumerate(self.questions_queue):
            if q["id"] == label:
                q["status"] = "answered"
                q["answer_text"] = answer_text
                self.answered_questions.append(q)
                del self.questions_queue[i]
                return q
        return None

    def can_ask(self):
        return len(self.answered_questions) < self.max_questions and self.active

    def can_answer(self):
        return len(self.answered_questions) < self.max_questions and self.active

    def summary(self):
        lines = [
            f"Answered: {len(self.answered_questions)}/{self.max_questions}",
            f"Pending questions: {len(self.questions_queue)}",
            f"Guesses: {len(self.guesses)}"
        ]
        return "\n".join(lines)

    def to_dict(self):
        return {
            "answer": self.answer,
            "host_id": self.host_id,
            "max_questions": self.max_questions,
            "questions_queue": self.questions_queue,
            "answered_questions": self.answered_questions,
            "guesses": self.guesses,
            "active": self.active,
            "winner_id": self.winner_id
        }

    @classmethod
    def from_dict(cls, data):
        game = cls(data["answer"], data["host_id"])
        game.max_questions = data.get("max_questions", 21)
        game.questions_queue = data.get("questions_queue", [])
        game.answered_questions = data.get("answered_questions", [])
        game.guesses = data.get("guesses", [])
        game.active = data.get("active", True)
        game.winner_id = data.get("winner_id")
        return game

def save_games(games_dict):
    serializable = {str(chan_id): game.to_dict() for chan_id, game in games_dict.items()}
    with open(GAMES_SAVE_PATH, "w", encoding="utf-8") as f:
        json.dump(serializable, f)

def load_games():
    if not os.path.exists(GAMES_SAVE_PATH):
        return {}
    with open(GAMES_SAVE_PATH, "r", encoding="utf-8") as f:
        raw = json.load(f)
    games = {}
    for chan_id, data in raw.items():
        games[int(chan_id)] = TwentyoneQuestionsGame.from_dict(data)
    return games

def create_answer_embed(label, question, answer_text, questions_left):
    embed = discord.Embed(
        title=f"Q{label} Answered",
        description=(
            f"**Q{label}:** {question}\n"
            f"**A:** {answer_text if answer_text else '*no answer*'}"
        ),
        color=discord.Color.purple()
    )
    embed.set_footer(text=f"{questions_left} questions left")
    return embed

def create_cyan_label_embed(label, question):
    embed = discord.Embed(color=0x00FFFF) #Cyan Embed
    embed.set_footer(text=f"-# Q{label}: {question}")
    return embed

class TwentyoneQuestionsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.games = load_games()

    @commands.hybrid_command(name='start21q', description='Start a game of 21 Questions (host must specify answer word, 5+ letters)')
    async def start21q(self, ctx, word: str):
        channel_id = ctx.channel.id
        if channel_id in self.games and self.games[channel_id].active:
            await ctx.send("A game is already running in this channel! Use `/end21q` to end it.", ephemeral=True)
            return
        if not word or len(word.strip()) < 5 or not word.strip().isalpha():
            await ctx.send("You must provide an answer word with at least 5 alphabetic letters.", ephemeral=True)
            return
        answer = word.strip().lower()
        game = TwentyoneQuestionsGame(answer, host_id=ctx.author.id)
        self.games[channel_id] = game
        save_games(self.games)
        await ctx.send(
            f"🎮 Started 21 Questions!\n"
            f"Word is host-selected.\n"
            "Type **ask [your yes/no question]** to queue a question.\n"
            "Type **guess [your guess]** to guess the word.\n"
            "Type **listq21q** to view all pending questions.\n"
            "Host (only): Reply with 'A1', 'A2', ... to answer Q1, Q2, ... (optionally include answer text, e.g. 'A1 yes')."
            f"\nMax 21 questions will be counted!",
            ephemeral=True
        )

    @commands.hybrid_command(name='end21q', description='End the current 21 Questions game and show the answer')
    async def end21q(self, ctx):
        channel_id = ctx.channel.id
        game = self.games.get(channel_id)
        if not game or not game.active:
            await ctx.send("No active 21 Questions game in this channel.", ephemeral=True)
            return
        game.active = False
        save_games(self.games)
        await ctx.send(f"Game ended. The word was: **{game.answer}**.", ephemeral=True)

    @commands.hybrid_command(name='summary21q', description='Show the status summary for the current game')
    async def summary21q(self, ctx):
        channel_id = ctx.channel.id
        game = self.games.get(channel_id)
        if not game or not game.active:
            await ctx.send("No active game in this channel.", ephemeral=True)
            return
        await ctx.send("Game status:\n" + game.summary(), ephemeral=True)

    @commands.hybrid_command(name='listq21q', description='List pending 21Q questions')
    async def listq21q(self, ctx):
        channel_id = ctx.channel.id
        game = self.games.get(channel_id)
        if not game or not game.active or not game.questions_queue:
            await ctx.send("No pending questions.", ephemeral=True)
            return
        lines = [
            f"Q{q['id']}: \"{q['question']}\" (<@{q['author_id']}>)"
            for q in game.questions_queue
        ]
        await ctx.send("Pending questions:\n" + "\n".join(lines), ephemeral=True)

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or not message.guild:
            return

        channel_id = message.channel.id
        game = self.games.get(channel_id)
        if not game or not game.active:
            return

        content = message.content.strip()

        # Queue a Question (ask ...)
        if content.lower().startswith("ask "):
            if not game.can_ask():
                await message.channel.send("❌ You've reached the 21 question limit! Time to guess!")
                return
            question = content[4:].strip()
            if not question:
                await message.channel.send("❌ Please provide a question after 'ask'.")
                return
            qid = game.add_question(message.author.id, question)
            save_games(self.games)
            return

        # Host Answers: 'A1', 'A2', ... Optionally with answer text
        match_ans = re.match(r'^a(\d{1,2})(\s+(.+))?$', content.strip(), re.IGNORECASE)
        if match_ans:
            if message.author.id != game.host_id:
                return
            label = int(match_ans.group(1))
            answer_text = match_ans.group(3) if match_ans.group(3) else None

            if not game.can_answer():
                await message.channel.send("❌ You've already answered 21 questions! Time to guess!")
                return
            answered = game.answer_question(label, answer_text)
            save_games(self.games)
            if answered:
                n_remaining = game.max_questions - len(game.answered_questions)
                answer_embed = create_answer_embed(label, answered['question'], answer_text, n_remaining)
                cyan_embed = create_cyan_label_embed(label, answered['question'])
                await message.channel.send(embed=answer_embed)
                await message.channel.send(embed=cyan_embed)
            else:
                await message.channel.send(f"❌ No pending question with label Q{label}. Check the queue.")
            return

        # Guess the answer
        if content.lower().startswith("guess "):
            guess = content[6:].strip().lower()
            if not guess:
                await message.channel.send("❌ Please provide a word to guess after 'guess'.")
                return
            game.guesses.append({"author_id": message.author.id, "guess": guess})
            save_games(self.games)
            if guess == game.answer:
                game.active = False
                game.winner_id = message.author.id
                save_games(self.games)
                await message.channel.send(
                    f"🎉 {message.author.mention} guessed the word: **{game.answer}** in "
                    f"{len(game.answered_questions)} questions and {len(game.guesses)} guesses!"
                )
            else:
                await message.channel.send(
                    f"❌ {message.author.mention} guessed \"{guess}\". That's not correct."
                )
                if not game.can_ask():
                    game.active = False
                    save_games(self.games)
                    await message.channel.send(
                        f"Game over! You reached 21 questions without guessing the word. The answer was: **{game.answer}**."
                    )
            return

    @commands.command(name="21qsum",
                      description="Show a summary of all answered questions in 21 Questions (staff only)")
    @commands.has_permissions(manage_guild=True)
    async def qsum21q(self, ctx):
        channel_id = ctx.channel.id
        game = self.games.get(channel_id)
        if not game or not game.active:
            await ctx.send("No active 21 Questions game in this channel.")
            return
        if not game.answered_questions:
            await ctx.send("No questions have been answered yet.")
            return
        if not (ctx.author.guild_permissions.manage_guild or ctx.author.guild_permissions.administrator):
            await ctx.send("This command is restricted to staff members.")
            return
        qa_lines = []
        for q in game.answered_questions:
            qa_lines.append(
                f"**Q{q['id']}**: {q['question']}\n**A:** {q['answer_text'] if q['answer_text'] else '*no answer provided*'}"
            )
        embed = discord.Embed(
            title="Answered Questions - 21 Questions",
            description="\n\n".join(qa_lines),
            color=discord.Color.purple()
        )
        embed.set_footer(text=f"{len(game.answered_questions)} questions answered out of {game.max_questions}")
        await ctx.send(embed=embed)

async def setup(bot):
    await bot.add_cog(TwentyoneQuestionsCog(bot))
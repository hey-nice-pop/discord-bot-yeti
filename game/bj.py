import random
import discord

# ブラックジャックゲームのロジック
class Blackjack:
    def __init__(self):
        self.deck = self.create_deck()
        self.users = {}

    def create_deck(self):
        # デッキを作成しシャッフルします。
        suits = ['Hearts', 'Diamonds', 'Clubs', 'Spades']
        ranks = ['2', '3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K', 'A']
        deck = [{'suit': suit, 'rank': rank} for suit in suits for rank in ranks]
        random.shuffle(deck)
        return deck

    def reset_game(self):
        # ゲームをリセットします。
        self.deck = self.create_deck()
        self.users = {}

    def add_user(self, user_id):
        # ユーザーを追加し、初期カードを2枚配ります。
        if user_id not in self.users:
            self.users[user_id] = {'hand': [], 'score': 0, 'status': 'playing'}
            self.deal_initial_cards(user_id)

    def deal_initial_cards(self, user_id):
        # 初期カードを2枚配ります。
        for _ in range(2):
            self.deal_card(user_id)

    def deal_card(self, user_id):
        # ユーザーにカードを一枚配り、スコアを更新します。
        if user_id in self.users and self.users[user_id]['status'] == 'playing':
            card = self.deck.pop()
            self.users[user_id]['hand'].append(card)
            self.update_score(user_id)
            return card  # 引いたカードを返します。

    def update_score(self, user_id):
        # ユーザーのスコアを更新します。
        if user_id in self.users:
            score = 0
            ace_count = 0
            for card in self.users[user_id]['hand']:
                if card['rank'] in ['J', 'Q', 'K']:
                    score += 10
                elif card['rank'] == 'A':
                    ace_count += 1
                    score += 11  # Aは最初は11として扱います。
                else:
                    score += int(card['rank'])
            
            # Aがある場合は、スコアが21を超えないように調整します。
            while score > 21 and ace_count:
                score -= 10
                ace_count -= 1
            
            self.users[user_id]['score'] = score
            if score > 21:
                self.users[user_id]['status'] = 'bust'

    def user_stand(self, user_id):
        # ユーザーがスタンドを選択した場合の処理。
        if user_id in self.users:
            self.users[user_id]['status'] = 'stand'

    def get_user_status(self, user_id):
        # ユーザーの現在のステータスとスコアを取得します。
        if user_id in self.users:
            return self.users[user_id]['status'], self.users[user_id]['score']
        else:
            return None, None

# Discordとのやり取りを担うクラス
class BlackjackBot:
    def __init__(self, bot):
        self.bot = bot
        self.games = {}  # ゲームの状態をチャンネルIDをキーとして保存

    def get_game(self, channel_id):
        # 特定のチャンネルのゲームを取得、存在しない場合は新規作成
        if channel_id not in self.games:
            self.games[channel_id] = Blackjack()
        return self.games[channel_id]

    def end_game(self, channel_id):
        # 特定のチャンネルのゲームを終了（削除）
        if channel_id in self.games:
            del self.games[channel_id]

    def suit_to_emoji(self, suit):
        # スーツを絵文字に変換します。
        return {'Hearts': '♥️', 'Diamonds': '♦️', 'Clubs': '♣️', 'Spades': '♠️'}.get(suit, suit)

    def card_to_string(self, card):
        # カードを文字列として整形します。
        return f"{self.suit_to_emoji(card['suit'])}{card['rank']}"

    def hand_to_string(self, hand):
        # 手札のカードを文字列として整形します。
        return ", ".join(self.card_to_string(card) for card in hand)

    def command_bj_start(self, channel_id, user_id):
        game = self.get_game(channel_id)
        # ユーザーがゲームに参加し、初期カードを受け取る処理。
        if user_id in game.users:
            return f"{user_id}はすでにゲームに参加しています。", True
        else:
            game.add_user(user_id)
            hand = game.users[user_id]['hand']
            status, score = game.get_user_status(user_id)
            return f"{user_id}がゲームに参加し、初期カードを受け取りました！\n手札: {self.hand_to_string(hand)} 現在のスコア: {score}", False

    def command_bj_hit(self, channel_id, user_id):
        game = self.get_game(channel_id)
        # ユーザーがカードを一枚引く処理。
        if user_id not in game.users:
            return f"{user_id}はゲームに参加していません。", True
        if game.users[user_id]['status'] == 'playing':
            card = game.deal_card(user_id)
            hand = game.users[user_id]['hand']
            status, score = game.get_user_status(user_id)
            if status == 'bust':
                return f"{user_id}はバーストしました！引いたカード: {self.card_to_string(card)}\n手札: {self.hand_to_string(hand)} スコア: {score}", False
            else:
                return f"{user_id}がカードを引きました: {self.card_to_string(card)}\n手札: {self.hand_to_string(hand)} 現在のスコア: {score}", False
        else:
            return f"{user_id}はすでにバーストしています。", True

    def command_bj_allstand(self, channel_id):
        game = self.get_game(channel_id)
        # 全員がスタンドし、ゲームを終了する処理。勝者を決定します。
        if not game.users:
            return "ゲームが開始されていません。", True

        active_players = [user_id for user_id, info in game.users.items() if info['status'] != 'bust']
        if not active_players:
            self.end_game(channel_id)  # ゲームを終了します。
            return "勝者なし、全員バーストしました。", False

        winners = []
        highest_score = 0
        for user_id in active_players:
            info = game.users[user_id]
            if info['score'] > highest_score:
                highest_score = info['score']
                winners = [user_id]
            elif info['score'] == highest_score:
                winners.append(user_id)

        if len(winners) == 1:
            response = f"勝者: {winners[0]} スコア: {highest_score}!"
        elif len(winners) > 1:
            response = f"引き分けです！勝者: {', '.join(winners)} スコア: {highest_score}"
        else:
            response = "勝者なし、全員バーストしました。"

        self.end_game(channel_id)  # ゲームを終了します。
        return response, False
    
    def command_bj_show(self, channel_id, user_id):
        game = self.get_game(channel_id)
        # ユーザーがゲームに参加しているかを確認します。
        if user_id not in game.users:
            return f"{user_id}はゲームに参加していません。", True

        hand = game.users[user_id]['hand']
        score = game.users[user_id]['score']
        return f"{user_id}の現在の手札: {self.hand_to_string(hand)} スコア: {score}", False


def setup(bot):
    blackjack_bot = BlackjackBot(bot)

    @bot.tree.command(name='bj_start', description='ブラックジャックゲームを開始または参加します')
    async def start(interaction: discord.Interaction):
        channel_id = interaction.channel_id
        response, ephemeral = blackjack_bot.command_bj_start(channel_id, interaction.user.name)
        await interaction.response.send_message(response, ephemeral=ephemeral)

    @bot.tree.command(name='bj_hit', description='カードをもう一枚引きます')
    async def hit(interaction: discord.Interaction):
        channel_id = interaction.channel_id
        response, ephemeral = blackjack_bot.command_bj_hit(channel_id, interaction.user.name)
        await interaction.response.send_message(response, ephemeral=ephemeral)

    @bot.tree.command(name='bj_allstand', description='ゲームを終了し、勝者を表示します')
    async def allstand(interaction: discord.Interaction):
        channel_id = interaction.channel_id
        response, ephemeral = blackjack_bot.command_bj_allstand(channel_id)
        await interaction.response.send_message(response, ephemeral=ephemeral)

    @bot.tree.command(name='bj_show', description='現在の手札を表示します')
    async def show(interaction: discord.Interaction):
        channel_id = interaction.channel_id
        response, ephemeral = blackjack_bot.command_bj_show(channel_id, interaction.user.name)
        await interaction.response.send_message(response, ephemeral=ephemeral)


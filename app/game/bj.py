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
    
    def suit_to_emoji(self, suit):
        # スーツを絵文字に変換します。
        return {'Hearts': '♥️', 'Diamonds': '♦️', 'Clubs': '♣️', 'Spades': '♠️'}.get(suit, suit)

    def card_to_string(self, card):
        # カードを文字列として整形します。
        return f"{self.suit_to_emoji(card['suit'])}{card['rank']}"

    def hand_to_string(self, hand):
        # 手札のカードを文字列として整形します。
        return ", ".join(self.card_to_string(card) for card in hand)
    
    def hand_to_public_string(self, hand):
    # 最初のカード以外を公開し、最初のカードを裏向きにします。
        if len(hand) > 1:
            # 最初のカード以外のカードを文字列化します。
            visible_cards = ", ".join(self.card_to_string(card) for card in hand[1:])
            return f"[❓], {visible_cards}"
        elif len(hand) == 1:
            # 手札が1枚のみの場合はそのカードを裏向きにします。
            return "[❓]"
        return ""

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

    async def command_bj_start(self, interaction: discord.Interaction):
        channel_id = interaction.channel_id
        user_id = interaction.user.name
        game = self.get_game(channel_id)
        if user_id in game.users:
            await interaction.response.send_message(f"{user_id}はすでにゲームに参加しています。", ephemeral=True)
        else:
            game.add_user(user_id)
            hand = game.users[user_id]['hand']
            status, score = game.get_user_status(user_id)
            public_response = f"{user_id}がゲームに参加しました。公開手札: {game.hand_to_public_string(hand)}"
            private_response = f"{user_id}がゲームに参加し、初期カードを受け取りました！\nあなたの手札: {game.hand_to_string(hand)} スコア: {score}"
            
            await interaction.response.send_message(public_response, ephemeral=False)  # 公開メッセージを最初に送信
            await interaction.followup.send(private_response, ephemeral=True)  # 個人メッセージをフォローアップとして送信

    async def command_bj_hit(self, interaction: discord.Interaction):
        channel_id = interaction.channel_id
        user_id = interaction.user.name
        game = self.get_game(channel_id)
        if user_id not in game.users:
            await interaction.response.send_message(f"{user_id}はゲームに参加していません。", ephemeral=True)
            return
        if game.users[user_id]['status'] == 'playing':
            card = game.deal_card(user_id)
            hand = game.users[user_id]['hand']
            status, score = game.get_user_status(user_id)
            if status == 'bust':
                public_response = f"{user_id}はバーストしました！公開手札: {game.hand_to_string(hand)} スコア: {score}"
                await interaction.response.send_message(public_response, ephemeral=False)
            else:
                private_response = f"あなたが引いたカード: {game.card_to_string(card)}\nあなたの全手札: {game.hand_to_string(hand)} スコア: {score}"
                public_response = f"{user_id}がカードを引きました。公開手札: {game.hand_to_public_string(hand)}"
                await interaction.response.send_message(public_response, ephemeral=False)  # まず公開情報を送信
                await interaction.followup.send(private_response, ephemeral=True)  # 次に個人情報を送信
        else:
            await interaction.response.send_message(f"{user_id}はすでにバーストしています。", ephemeral=True)

    def command_bj_allstand(self, channel_id):
        game = self.get_game(channel_id)
        if not game.users:
            return "ゲームが開始されていません。", True

        active_players = [user_id for user_id, info in game.users.items() if info['status'] != 'bust']
        if not active_players:
            self.end_game(channel_id)
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
            winner_id = winners[0]
            winner_hand = game.hand_to_string(game.users[winner_id]['hand'])
            response = f"勝者: {winner_id} スコア: {highest_score}!\n勝者の手札: {winner_hand}"
        elif len(winners) > 1:
            winner_hands = {winner: game.hand_to_string(game.users[winner]['hand']) for winner in winners}
            winner_details = '\n'.join(f"{winner} の手札: {hands}" for winner, hands in winner_hands.items())
            response = f"引き分けです！勝者: {', '.join(winners)} スコア: {highest_score}\n{winner_details}"
        else:
            response = "勝者なし、全員バーストしました。"

        self.end_game(channel_id)
        return response, False

    async def command_bj_show(self, interaction: discord.Interaction):
        channel_id = interaction.channel_id
        user_id = interaction.user.name
        game = self.get_game(channel_id)
        if user_id not in game.users:
            await interaction.response.send_message(f"{user_id}はゲームに参加していません。", ephemeral=True)
            return

        hand = game.users[user_id]['hand']
        score = game.users[user_id]['score']
        response = f"{user_id}の現在の手札: {game.hand_to_string(hand)} スコア: {score}"
        await interaction.response.send_message(response, ephemeral=True)

def setup(bot):
    blackjack_bot = BlackjackBot(bot)

    @bot.tree.command(name='bj_start', description='ブラックジャックゲームを開始または参加します')
    async def start(interaction: discord.Interaction):
        await blackjack_bot.command_bj_start(interaction)


    @bot.tree.command(name='bj_hit', description='カードをもう一枚引きます')
    async def hit(interaction: discord.Interaction):
        await blackjack_bot.command_bj_hit(interaction)

    @bot.tree.command(name='bj_allstand', description='ゲームを終了し、勝者を表示します')
    async def allstand(interaction: discord.Interaction):
        channel_id = interaction.channel_id
        response, ephemeral = blackjack_bot.command_bj_allstand(channel_id)
        await interaction.response.send_message(response, ephemeral=ephemeral)

    @bot.tree.command(name='bj_show', description='現在の手札を表示します')
    async def show(interaction: discord.Interaction):
        await blackjack_bot.command_bj_show(interaction)


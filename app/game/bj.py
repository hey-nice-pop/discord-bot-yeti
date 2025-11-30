import random
import discord
from datetime import datetime, timezone, timedelta


class Blackjack:
    """
    ブラックジャックゲームのコアロジックです。デッキの管理、得点計算、
    プレイヤーの手札処理を担当します。このクラスは Discord 固有のコードに依存せず、
    非同期処理を含みません。各ゲームインスタンスは単一のチャンネルにおける
    デッキ、ポット、プレイヤーの状態を管理します。
    """

    def __init__(self, initial_coins: int = 100, tz_offset_hours: int = 9):
        # 新しいデッキを作成してシャッフルします
        self.deck = self.create_deck()
        # user_id からプレイヤー状態へのマッピング
        # 各エントリには hand(カードのリスト)、score(整数)、status(文字列)、
        # coins(所持コイン)、bet(賭け額)、has_raised(既にレイズしたか)、
        # is_folded(フォールドしているか) が含まれます
        self.users = {}
        # 現在のポットにあるコインの合計
        self.pot = 0
        # 現在のラウンドでレイズされた額（アクティブなレイズがない場合は 0）
        self.current_raise = 0
        # コイン残高を最後にリセットした日付
        self.last_reset_date = None
        # 毎日開始時のコイン数
        self.initial_coins = initial_coins
        # タイムゾーンオフセット（東京は +9 時間）
        self.tz_offset = timedelta(hours=tz_offset_hours)

    # ------------------------------------------------------------------
    # デッキおよびカード関連の補助メソッド
    # ------------------------------------------------------------------
    def create_deck(self):
        """標準の 52 枚デッキを作成してシャッフルします。"""
        suits = ['Hearts', 'Diamonds', 'Clubs', 'Spades']
        ranks = ['2', '3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K', 'A']
        deck = [{'suit': suit, 'rank': rank} for suit in suits for rank in ranks]
        random.shuffle(deck)
        return deck

    def suit_to_emoji(self, suit: str) -> str:
        """スートを対応する絵文字に変換します。"""
        return {'Hearts': '♥️', 'Diamonds': '♦️', 'Clubs': '♣️', 'Spades': '♠️'}.get(suit, suit)

    def card_to_string(self, card: dict) -> str:
        """カードを絵文字文字列として表します。"""
        return f"{self.suit_to_emoji(card['suit'])}{card['rank']}"

    def hand_to_string(self, hand):
        """手札内の全カードをカンマ区切りの文字列で返します。"""
        return ", ".join(self.card_to_string(card) for card in hand)

    def hand_to_public_string(self, hand):
        """先頭以外のカードだけを公開する形式の文字列を返します。"""
        if len(hand) > 1:
            visible_cards = ", ".join(self.card_to_string(card) for card in hand[1:])
            return f"[❓], {visible_cards}"
        elif len(hand) == 1:
            return "[❓]"
        return ""

    # ------------------------------------------------------------------
    # 毎日のコイン管理
    # ------------------------------------------------------------------
    def _current_local_date(self) -> datetime.date:
        """設定されたタイムゾーンでの現在の日付を返します。"""
        now_utc = datetime.now(timezone.utc)
        local_time = now_utc + self.tz_offset
        return local_time.date()

    def check_and_reset_coins(self):
        """
        日付が変わった場合（東京タイムゾーン基準）、すべてのプレイヤーの
        コインを初期値にリセットします。残高が常に正しい状態になるよう、
        各コマンド実行時に呼び出してください。
        """
        current_date = self._current_local_date()
        if self.last_reset_date is None or current_date > self.last_reset_date:
            # 進行中のゲームを壊さないように、各プレイヤーのベットおよび
            # ポットへの貢献は維持したまま、所持コインのみ初期値に戻します。
            for state in self.users.values():
                state['coins'] = self.initial_coins
            self.last_reset_date = current_date

    # ------------------------------------------------------------------
    # プレイヤー管理
    # ------------------------------------------------------------------
    def can_join(self, user_id: str) -> bool:
        """プレイヤーがゲームに参加可能かどうかを判定します（カードとコインの残り状況を確認します）。"""
        # 新しいプレイヤーが参加するにはデッキに少なくとも 2 枚のカードが必要
        if len(self.deck) < 2:
            return False
        # プレイヤーが既にゲームに参加していないことを確認
        if user_id in self.users:
            return False
        return True

    def add_user(self, user_id: str) -> bool:
        """
        ユーザーをゲームに追加します。初期ベット（1 コイン）を所持コインから
        引き、ポットに追加してから 2 枚のカードを配ります。成功時は True を返し、
        ユーザーがベットを支払えない場合やカードが不足している場合は False を返します。
        """
        self.check_and_reset_coins()
        if not self.can_join(user_id):
            return False
        # 新規ユーザー参加時の初期所持コインを決定
        coins = self.initial_coins
        # ベットを支払えるだけのコインがある場合のみ参加を許可
        if coins < 1:
            return False
        # プレイヤーの状態を生成
        self.users[user_id] = {
            'hand': [],
            'score': 0,
            'status': 'playing',
            'coins': coins - 1,  # 初期ベット分を差し引く
            'bet': 1,
            'has_raised': False,
            'is_folded': False,
        }
        # ポットにベット額を追加
        self.pot += 1
        # 最初のカードを配る
        self.deal_initial_cards(user_id)
        # ナチュラルブラックジャック（A + 絵札）の場合はボーナスコインを付与
        hand = self.users[user_id]['hand']
        ranks = {card['rank'] for card in hand}
        if 'A' in ranks and any(r in ranks for r in ['J', 'Q', 'K', '10']):
            # ボーナスとして 1 コインを付与
            self.users[user_id]['coins'] += 1
        return True

    def deal_initial_cards(self, user_id: str):
        """指定されたプレイヤーに 2 枚のカードを配ります。"""
        for _ in range(2):
            self.deal_card(user_id)

    def deal_card(self, user_id: str):
        """
        プレイヤーにカードを 1 枚配り、そのスコアを更新してカードを返します。
        デッキが空の場合やプレイヤーがフォールドしている場合は何もしません。
        """
        if user_id not in self.users:
            return None
        if self.users[user_id]['status'] != 'playing':
            return None
        if not self.deck:
            return None
        card = self.deck.pop()
        self.users[user_id]['hand'].append(card)
        self.update_score(user_id)
        return card

    def update_score(self, user_id: str):
        """プレイヤーのスコアを再計算し、その状態を更新します。"""
        hand = self.users[user_id]['hand']
        score = 0
        ace_count = 0
        for card in hand:
            rank = card['rank']
            if rank in ['J', 'Q', 'K']:
                score += 10
            elif rank == 'A':
                ace_count += 1
                score += 11
            else:
                score += int(rank)
        while score > 21 and ace_count:
            score -= 10
            ace_count -= 1
        self.users[user_id]['score'] = score
        if score > 21:
            self.users[user_id]['status'] = 'bust'
            self.users[user_id]['is_folded'] = True

    def user_stand(self, user_id: str):
        """プレイヤーがスタンド（これ以上カードを引かない）状態であることを記録します。"""
        if user_id in self.users:
            if self.users[user_id]['status'] == 'playing':
                self.users[user_id]['status'] = 'stand'

    def get_user_status(self, user_id: str):
        """プレイヤーの状態とスコアをタプルで返します。"""
        if user_id in self.users:
            return self.users[user_id]['status'], self.users[user_id]['score']
        return None, None

    # ------------------------------------------------------------------
    # レイズ処理
    # ------------------------------------------------------------------
    def calculate_max_raise(self, raiser_id: str) -> int:
        """
        指定したプレイヤーがレイズできる最大額を計算します。レイズ額はレイズする
        プレイヤーの所持コインを超えることはできず、フォールドしていない
        プレイヤーの残りコインの最小値を超えることもできません。これにより、
        すべてのプレイヤーがコールまたはフォールドを選択できるようになります。
        """
        if raiser_id not in self.users:
            return 0
        # The raiser cannot raise more than they have
        raiser_coins = self.users[raiser_id]['coins']
        # Consider only active players (not folded/bust) to compute the
        # maximum call value they can match
        active_players = [uid for uid, state in self.users.items()
                          if not state['is_folded'] and uid != raiser_id]
        if not active_players:
            return raiser_coins
        min_other_coins = min(self.users[uid]['coins'] for uid in active_players)
        return min(raiser_coins if raiser_coins <= min_other_coins else min_other_coins)

    def start_raise(self, raiser_id: str, amount: int) -> bool:
        """
        レイズの開始処理を行います。レイズするプレイヤーの所持コインから
        指定額を差し引き、ポットに追加し、ベットを更新します。また、
        後続のプレイヤーが参照できるようにレイズ額を記録します。成功時は True を返します。
        """
        if raiser_id not in self.users:
            return False
        player = self.users[raiser_id]
        if player['has_raised'] or player['is_folded'] or player['status'] != 'playing':
            return False
        max_raise = self.calculate_max_raise(raiser_id)
        if amount < 1 or amount > max_raise:
            return False
        if player['coins'] < amount:
            return False
        # Deduct coins and update bet
        player['coins'] -= amount
        player['bet'] += amount
        # Increase pot by the raise amount (only the raiser's contribution for now)
        self.pot += amount
        # Set current raise value to inform callers
        self.current_raise = amount
        player['has_raised'] = True
        return True

    def respond_to_raise(self, user_id: str, action: str) -> bool:
        """
        アクティブなレイズに対し、プレイヤーがコールまたはフォールドする処理を行います。
        action が 'call' の場合は現在のレイズ額を所持コインから差し引き、
        ポットに追加してベットを更新します。'fold' の場合はそのプレイヤーを
        フォールド状態として記録します。応答が受理された場合は True を返します。
        """
        if user_id not in self.users:
            return False
        player = self.users[user_id]
        if player['is_folded'] or player['status'] != 'playing':
            return False
        # There must be an active raise
        if self.current_raise <= 0:
            return False
        if action == 'call':
            # Ensure the player has enough coins to call
            if player['coins'] < self.current_raise:
                return False
            player['coins'] -= self.current_raise
            player['bet'] += self.current_raise
            self.pot += self.current_raise
            # They remain in the game
            return True
        elif action == 'fold':
            # Player forfeits further participation but does not lose
            # additional coins beyond their current bet. We don't deduct
            # anything else here.
            player['status'] = 'fold'
            player['is_folded'] = True
            return True
        return False

    def clear_raise(self):
        """
        現在のレイズ額をクリアし、今後のヒットやスタンドの操作が追加の
        応答なしで行えるようにします。すべてのプレイヤーが応答したか
        タイムアウトした後に呼び出されるべきです。
        """
        self.current_raise = 0

    # ------------------------------------------------------------------
    # Round resolution
    # ------------------------------------------------------------------
    def resolve_round(self):
        """
        現在のラウンドの結果を判定します。戻り値は (message, summary) のタプルで、
        message は勝者を説明する文字列、summary は各プレイヤーの詳細
        （user_id、手札、スコア、最終コイン、増減）を格納した辞書のリストです。
        すべてのプレイヤーがバーストした場合、ベットは返却されコインは没収されません。
        """
        results = []
        # Identify active players (not folded/bust)
        active_players = [uid for uid, state in self.users.items()
                          if state['status'] in ['playing', 'stand'] and not state['is_folded']]
        # Identify any players who busted
        bust_players = [uid for uid, state in self.users.items() if state['status'] == 'bust']
        # If no active players remain, everyone busted or folded
        if not active_players:
            # Refund each player's bet
            for uid, state in self.users.items():
                state['coins'] += state['bet']
                state['bet'] = 0
            self.pot = 0
            message = "勝者なし、全員バーストしました。"
            # Compose summary
            for uid, state in self.users.items():
                results.append({
                    'user_id': uid,
                    'hand': self.hand_to_string(state['hand']),
                    'score': state['score'],
                    'final_coins': state['coins'],
                    'change': 0
                })
            # Reset the game state for the next round
            self.reset_game()
            return message, results
        # Compute highest score among active players
        highest_score = 0
        for uid in active_players:
            score = self.users[uid]['score']
            if score > highest_score:
                highest_score = score
        # Determine winners (could be multiple)
        winners = [uid for uid in active_players if self.users[uid]['score'] == highest_score]
        # Distribute pot evenly among winners. Any leftover coins are
        # distributed one by one starting from the first winner.
        share = self.pot // len(winners)
        remainder = self.pot % len(winners)
        # Record the initial coins before distribution to compute change
        initial_coins = {uid: state['coins'] for uid, state in self.users.items()}
        for i, uid in enumerate(winners):
            self.users[uid]['coins'] += share
            if i < remainder:
                self.users[uid]['coins'] += 1
        # Compute change for each player and build result summary
        for uid, state in self.users.items():
            change = state['coins'] - initial_coins[uid]
            results.append({
                'user_id': uid,
                'hand': self.hand_to_string(state['hand']),
                'score': state['score'],
                'final_coins': state['coins'],
                'change': change
            })
        # Construct human readable message
        if len(winners) == 1:
            message = f"勝者: {winners[0]} スコア: {highest_score}!"
        else:
            winners_str = ", ".join(winners)
            message = f"引き分けです！勝者: {winners_str} スコア: {highest_score}"
        # Reset game for next round
        self.reset_game()
        return message, results

    def reset_game(self):
        """ゲームの状態をリセットしますが、プレイヤーとそのコイン残高は保持します。"""
        self.deck = self.create_deck()
        self.pot = 0
        self.current_raise = 0
        for state in self.users.values():
            state['hand'] = []
            state['score'] = 0
            state['status'] = 'playing'
            state['bet'] = 0
            state['has_raised'] = False
            state['is_folded'] = False


class BlackjackBot:
    """
    Discord との連携レイヤーです。チャンネルごとにゲームを管理し、
    ボットのコマンドをブラックジャックのコア操作に変換します。
    レイズ時にコール/フォールドを処理するために Discord の UI コンポーネントを使用します。
    """

    def __init__(self, bot: discord.Client):
        self.bot = bot
        # チャンネルIDからゲームインスタンスへのマッピング
        self.games: dict[int, Blackjack] = {}

    # ------------------------------------------------------------------
    # 補助メソッド
    # ------------------------------------------------------------------
    def get_game(self, channel_id: int) -> Blackjack:
        """指定されたチャンネルに対応するブラックジャックゲームを取得、または新規作成します。"""
        if channel_id not in self.games:
            self.games[channel_id] = Blackjack()
        return self.games[channel_id]

    def end_game(self, channel_id: int):
        """指定したチャンネルのゲームインスタンスを削除します。"""
        if channel_id in self.games:
            del self.games[channel_id]

    # ------------------------------------------------------------------
    # コマンド
    # ------------------------------------------------------------------
    async def command_bj_start(self, interaction: discord.Interaction):
        channel_id = interaction.channel_id
        # 表示名を使用
        user_id = interaction.user.display_name
        game = self.get_game(channel_id)
        # 必要であれば毎日コインをリセットする処理を適用
        game.check_and_reset_coins()
        # ユーザーがすでに参加していないか、十分なカードがあるかを確認
        if user_id in game.users:
            await interaction.response.send_message(f"{user_id}はすでにゲームに参加しています。",
                                                   ephemeral=True)
            return
        # ゲームにユーザーを追加できるか試みる
        if not game.add_user(user_id):
            # カードやコインが足りない場合
            await interaction.response.send_message(f"{user_id}はゲームに参加できません。山札の残りカードまたはコインが不足しています。",
                                                   ephemeral=True)
            return
        # メッセージを準備
        hand = game.users[user_id]['hand']
        score = game.users[user_id]['score']
        coins = game.users[user_id]['coins']
        public_response = (f"{user_id}がゲームに参加しました。公開手札: "
                           f"{game.hand_to_public_string(hand)}")
        private_response = (f"{user_id}がゲームに参加し、初期カードを受け取りました！\n"
                            f"あなたの手札: {game.hand_to_string(hand)} スコア: {score}\n"
                            f"現在の所持コイン: {coins}")
        await interaction.response.send_message(public_response, ephemeral=False)
        await interaction.followup.send(private_response, ephemeral=True)

    async def command_bj_hit(self, interaction: discord.Interaction):
        channel_id = interaction.channel_id
        # 表示名を使用
        user_id = interaction.user.display_name
        game = self.get_game(channel_id)
        # 必要であれば毎日コインをリセットする処理を適用
        game.check_and_reset_coins()
        if user_id not in game.users:
            await interaction.response.send_message(f"{user_id}はゲームに参加していません。",
                                                   ephemeral=True)
            return
        player = game.users[user_id]
        # プレイヤーがフォールドまたはバーストしている場合はヒットできない
        if player['is_folded'] or player['status'] != 'playing':
            await interaction.response.send_message(f"{user_id}はすでにバーストまたはフォールドしています。",
                                                   ephemeral=True)
            return
        # 引くカードが残っていることを確認
        if not game.deck:
            await interaction.response.send_message("山札が残っていないためカードを引けません。",
                                                   ephemeral=True)
            return
        card = game.deal_card(user_id)
        hand = player['hand']
        status, score = game.get_user_status(user_id)
        if status == 'bust':
            public_response = (f"{user_id}はバーストしました！公開手札: "
                                f"{game.hand_to_string(hand)} スコア: {score}")
            await interaction.response.send_message(public_response, ephemeral=False)
            # 全員バーストしたかどうかをチェックして自動的に終了する
            await self._check_auto_allstand(interaction, channel_id)
        else:
            # 引いたカードと現在の手札、スコア、所持コインを含めたメッセージを作成
            coins = player['coins']
            private_response = (f"あなたが引いたカード: {game.card_to_string(card)}\n"
                                f"あなたの全手札: {game.hand_to_string(hand)} スコア: {score}\n"
                                f"現在の所持コイン: {coins}")
            public_response = (f"{user_id}がカードを引きました。公開手札: "
                               f"{game.hand_to_public_string(hand)}")
            await interaction.response.send_message(public_response, ephemeral=False)
            await interaction.followup.send(private_response, ephemeral=True)
            # ヒット後に全員バーストしたかどうかをチェック
            await self._check_auto_allstand(interaction, channel_id)

    async def command_bj_raise(self, interaction: discord.Interaction, amount: int):
        """
        プレイヤーがポットをレイズできるようにします。レイズ額をレイズした
        プレイヤーの所持コインから差し引き、現在のレイズ額を更新してから、
        他のプレイヤーにインタラクティブなボタンでコールかフォールドを選択させます。
        """
        channel_id = interaction.channel_id
        # 表示名を使用
        user_id = interaction.user.display_name
        game = self.get_game(channel_id)
        # 必要であれば毎日コインをリセットする処理を適用
        game.check_and_reset_coins()
        # プレイヤーがゲームに参加していることを確認
        if user_id not in game.users:
            await interaction.response.send_message(
                f"{user_id}はゲームに参加していません。",
                ephemeral=True
            )
            return
        # 既にレイズが進行中の場合は新たにレイズできないようにする
        if game.current_raise > 0:
            await interaction.response.send_message(
                "現在レイズの受付中です。新しいレイズはできません。",
                ephemeral=True
            )
            return
        player = game.users[user_id]
        # レイズが許可されているか確認
        if player['has_raised']:
            await interaction.response.send_message("あなたは既にレイズしています。",
                                                   ephemeral=True)
            return
        if player['is_folded'] or player['status'] != 'playing':
            await interaction.response.send_message("フォールド中またはバースト中はレイズできません。",
                                                   ephemeral=True)
            return
        # 許可される最大レイズ額を計算
        max_raise = game.calculate_max_raise(user_id)
        if max_raise <= 0:
            await interaction.response.send_message("レイズできません。所持コインが不足しています。",
                                                   ephemeral=True)
            return
        # レイズ額が有効か検証
        if amount < 1 or amount > max_raise:
            await interaction.response.send_message(
                f"レイズ額は1から{max_raise}の間で指定してください。", 
                ephemeral=True)
            return
        # レイズを実行（レイズ分を差し引き、現在のレイズ額を設定）
        success = game.start_raise(user_id, amount)
        if not success:
            await interaction.response.send_message("レイズに失敗しました。",
                                                   ephemeral=True)
            return
        # 他のアクティブなプレイヤーのリストを作成
        other_players = [uid for uid in game.users.keys() if uid != user_id and not game.users[uid]['is_folded']]
        # 各プレイヤーのコインと手札を含む公開メッセージを作成
        player_states = []
        for uid, state in game.users.items():
            # フォールドしていないプレイヤーは先頭のカードを伏せて表示する
            if not state['is_folded']:
                hand_str = game.hand_to_public_string(state['hand'])
            else:
                hand_str = "[伏せられています]"
            player_states.append(f"{uid}: 手札: {hand_str} | コイン: {state['coins']}")
        state_message = "\n".join(player_states)
        raise_message = (f"{user_id}が{amount}コインをレイズしました。\n"
                         f"他のプレイヤーは60秒以内にCallかFoldを選択してください。\n"
                         f"現在のポット: {game.pot}コイン\n"
                         f"各プレイヤーの状態:\n{state_message}")
        # コール/フォールド応答用のインタラクティブビューを定義
        view = CallFoldView(game, user_id, other_players)
        # メッセージとインタラクティブビューを送信
        await interaction.response.send_message(raise_message, view=view, 
                                               ephemeral=False)
        # View が終了するまで待機し、終了後に締め切りメッセージを送信
        await view.wait()
        # 未応答のプレイヤーを自動的にフォールドさせた旨を通知
        auto_folded = [uid for uid in view.responders if uid not in view.responses]
        if auto_folded:
            # 自動フォールドされたユーザーごとに公開手札と所持コインを含めたメッセージを作成
            messages = []
            for uid in auto_folded:
                hand_str = game.hand_to_public_string(game.users[uid]['hand'])
                coins_remain = game.users[uid]['coins']
                messages.append(
                    f"{uid}は時間切れのため自動的にフォールドしました。公開手札: {hand_str} | コイン: {coins_remain}"
                )
            await interaction.followup.send("\n".join(messages),
                                           ephemeral=False)
        # 受付終了のメッセージを表示
        await interaction.followup.send("受付が終了しました。",
                                       ephemeral=False)

    async def command_bj_allstand(self, interaction: discord.Interaction):
        channel_id = interaction.channel_id
        game = self.get_game(channel_id)
        # 必要であれば毎日コインをリセットする処理を適用
        game.check_and_reset_coins()
        # レイズ受付中はオールスタンドを許可しない
        if game.current_raise > 0:
            await interaction.response.send_message(
                "現在レイズの受付が終了していません。全員の応答を待ってから bj_allstand を実行してください。",
                ephemeral=True
            )
            return
        if not game.users:
            await interaction.response.send_message(
                "ゲームが開始されていません。",
                ephemeral=True
            )
            return
        # ラウンドを解決して結果を取得
        message, summary = game.resolve_round()
        # 詳細な結果メッセージを作成
        result_lines = [message]
        for result in summary:
            uid = result['user_id']
            hand = result['hand']
            score = result['score']
            final_coins = result['final_coins']
            change = result['change']
            change_str = f"{change:+d}"
            result_lines.append(
                f"{uid}: 手札: {hand} | スコア: {score} | 最終コイン: {final_coins} ({change_str})"
            )
        await interaction.response.send_message(
            "\n".join(result_lines),
            ephemeral=False
        )
        # このチャンネルのゲームを終了
        self.end_game(channel_id)

    async def command_bj_show(self, interaction: discord.Interaction):
        channel_id = interaction.channel_id
        # 表示名を使用
        user_id = interaction.user.display_name
        game = self.get_game(channel_id)
        # 必要であれば毎日コインをリセットする処理を適用
        game.check_and_reset_coins()
        if user_id not in game.users:
            await interaction.response.send_message(f"{user_id}はゲームに参加していません。",
                                                   ephemeral=True)
            return
        state = game.users[user_id]
        hand = state['hand']
        score = state['score']
        coins = state['coins']
        # 自身の手札とコイン
        lines = [
            f"{user_id}の現在の手札: {game.hand_to_string(hand)}",
            f"スコア: {score} | 所持コイン: {coins}"
        ]
        # 他プレイヤーの公開手札と所持コインを表示
        for uid, other_state in game.users.items():
            if uid == user_id:
                continue
            # フォールドしていない場合は先頭カードを伏せて表示
            if not other_state['is_folded']:
                other_hand_str = game.hand_to_public_string(other_state['hand'])
            else:
                other_hand_str = "[伏せられています]"
            lines.append(f"{uid}の公開手札: {other_hand_str} | 所持コイン: {other_state['coins']}")
        response = "\n".join(lines)
        await interaction.response.send_message(response,
                                               ephemeral=True)

    async def _check_auto_allstand(self, interaction: discord.Interaction, channel_id: int):
        """
        すべてのプレイヤーがバーストまたはフォールドしている場合、
        自動的にラウンドを終了して結果を送信します。ヒット後に呼び出されます。
        """
        game = self.get_game(channel_id)
        # アクティブなプレイヤー（バーストやフォールドしていないプレイヤー）を判定
        active_players = [state for state in game.users.values()
                          if state['status'] in ['playing', 'stand'] and not state['is_folded']]
        # アクティブプレイヤーがいなければ自動的にオールスタンド
        if not active_players and game.users:
            # ラウンドを解決
            message, summary = game.resolve_round()
            # 結果メッセージを作成
            result_lines = [message]
            for result in summary:
                uid = result['user_id']
                hand = result['hand']
                score = result['score']
                final_coins = result['final_coins']
                change = result['change']
                change_str = f"{change:+d}"
                result_lines.append(
                    f"{uid}: 手札: {hand} | スコア: {score} | 最終コイン: {final_coins} ({change_str})"
                )
            # 結果をチャンネルへ送信
            await interaction.followup.send("\n".join(result_lines),
                                           ephemeral=False)
            # ゲームを終了
            self.end_game(channel_id)


class CallFoldView(discord.ui.View):
    """
    レイズに対し、レイズしたプレイヤー以外の各プレイヤーにコールまたはフォールドを促す
    Discord のビューです。このビューは各プレイヤーの応答を記録し、応答がない場合は
    タイムアウトにより自動的にフォールドさせます。
    """

    def __init__(self, game: Blackjack, raiser_id: str, responders: list[str]):
        # タイムアウトを60秒に設定
        super().__init__(timeout=60.0)
        self.game = game
        self.raiser_id = raiser_id
        self.responders = set(responders)
        self.responses = {}

    async def _handle_response(self, interaction: discord.Interaction, action: str):
        # 表示名を使用
        user_id = interaction.user.display_name
        if user_id not in self.responders:
            await interaction.response.send_message(
                "この操作はあなたのためのものではありません。",
                ephemeral=True
            )
            return
        # 同一ユーザーが複数回応答するのを防止
        if user_id in self.responses:
            await interaction.response.send_message(
                "既に応答しています。",
                ephemeral=True
            )
            return
        # 応答を処理する
        success = self.game.respond_to_raise(user_id, action)
        if not success:
            await interaction.response.send_message(
                "応答を処理できませんでした。",
                ephemeral=True
            )
            return
        self.responses[user_id] = action
        # プレイヤーの公開手札と所持コインを含めたメッセージを送信
        hand_str = self.game.hand_to_public_string(self.game.users[user_id]['hand'])
        coins = self.game.users[user_id]['coins']
        if action == 'call':
            msg = f"{user_id}がコールしました。公開手札: {hand_str} | コイン: {coins}"
        else:
            msg = f"{user_id}がフォールドしました。公開手札: {hand_str} | コイン: {coins}"
        await interaction.response.send_message(
            msg,
            ephemeral=False
        )
        # 全員が応答したら早期終了
        if self.responders == set(self.responses.keys()):
            # 停止する前に現在のレイズ額をクリアする
            self.game.clear_raise()
            self.stop()

    @discord.ui.button(label="Call", style=discord.ButtonStyle.success)
    async def call_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_response(interaction, 'call')

    @discord.ui.button(label="Fold", style=discord.ButtonStyle.danger)
    async def fold_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_response(interaction, 'fold')

    async def on_timeout(self):
        """応答しなかったプレイヤーを自動的にフォールドします。"""
        for user_id in self.responders:
            if user_id not in self.responses:
                # タイムアウトによりフォールドさせる
                self.game.respond_to_raise(user_id, 'fold')
        # 現在のレイズ額をクリアしてゲームを続行できるようにする
        self.game.clear_raise()

# ----------------------------------------------------------------------
# Command registration
# ----------------------------------------------------------------------
def setup(bot: discord.Client):
    """
    指定した Discord ボットにブラックジャックのコマンドを登録します。
    この関数は拡張を読み込む際にボットオーナーが呼び出すことを想定しています。
    スラッシュコマンドを BlackjackBot に定義されたメソッドに結び付けます。
    """
    blackjack_bot = BlackjackBot(bot)

    @bot.tree.command(name='bj_start', description='ブラックジャックゲームを開始または参加します')
    async def bj_start(interaction: discord.Interaction):
        await blackjack_bot.command_bj_start(interaction)

    @bot.tree.command(name='bj_hit', description='カードをもう一枚引きます')
    async def bj_hit(interaction: discord.Interaction):
        await blackjack_bot.command_bj_hit(interaction)

    @bot.tree.command(name='bj_raise', description='コインを追加で賭けてレイズします')
    async def bj_raise(interaction: discord.Interaction, amount: int):
        await blackjack_bot.command_bj_raise(interaction, amount)

    @bot.tree.command(name='bj_allstand', description='ゲームを終了し、勝者を表示します')
    async def bj_allstand(interaction: discord.Interaction):
        await blackjack_bot.command_bj_allstand(interaction)

    @bot.tree.command(name='bj_show', description='現在の手札と所持コインを表示します')
    async def bj_show(interaction: discord.Interaction):
        await blackjack_bot.command_bj_show(interaction)
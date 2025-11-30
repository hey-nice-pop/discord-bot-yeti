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

    def __init__(self, initial_coins: int = 30, tz_offset_hours: int = 9, wallet_store: dict | None = None):
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
        # 毎日開始時のコイン数
        self.initial_coins = initial_coins
        # タイムゾーンオフセット（東京は +9 時間）
        self.tz_offset = timedelta(hours=tz_offset_hours)
        # サーバー共有のウォレット（guild 単位で共有される想定）
        self.wallet_store = wallet_store if wallet_store is not None else {}
        # ウォレット全体の最終リセット日をメタデータで持つ
        self._wallet_last_reset_key = "__last_reset_date__"

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

    # ------------------------------------------------------------------
    # ウォレット管理（サーバー共通コイン）
    # ------------------------------------------------------------------
    def _wallet_entry(self, user_id: str) -> dict:
        """ユーザーのウォレットエントリ（coins を含む辞書）を取得・初期化します。"""
        if user_id not in self.wallet_store:
            self.wallet_store[user_id] = {'coins': self.initial_coins}
        return self.wallet_store[user_id]

    def _get_coins(self, user_id: str) -> int:
        """ウォレットからコイン残高を取得し、ユーザーステートにも反映します。"""
        entry = self._wallet_entry(user_id)
        if user_id in self.users:
            self.users[user_id]['coins'] = entry['coins']
        return entry['coins']

    def _set_coins(self, user_id: str, amount: int):
        """ウォレットとユーザーステートの両方にコイン残高を設定します。"""
        entry = self._wallet_entry(user_id)
        entry['coins'] = amount
        if user_id in self.users:
            self.users[user_id]['coins'] = amount

    def _adjust_coins(self, user_id: str, delta: int) -> int:
        """指定量だけコインを増減させ、新しい残高を返します。"""
        new_amount = self._get_coins(user_id) + delta
        self._set_coins(user_id, new_amount)
        return new_amount

    def check_and_reset_coins(self):
        """
        日付が変わった場合（東京タイムゾーン基準）、すべてのプレイヤーの
        コインを初期値にリセットします。残高が常に正しい状態になるよう、
        各コマンド実行時に呼び出してください。
        """
        current_date = self._current_local_date()
        last_reset_date = self.wallet_store.get(self._wallet_last_reset_key)
        if last_reset_date is None or current_date > last_reset_date:
            # 進行中のゲームを壊さないように、各プレイヤーのベットおよび
            # ポットへの貢献は維持したまま、所持コインのみ初期値に戻します。
            for uid, entry in self.wallet_store.items():
                if uid == self._wallet_last_reset_key:
                    continue
                entry['coins'] = self.initial_coins
                if uid in self.users:
                    self.users[uid]['coins'] = self.initial_coins
            self.wallet_store[self._wallet_last_reset_key] = current_date

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
        # サーバー共通ウォレットから所持コインを取得（未登録なら初期値で作成）
        coins = self._get_coins(user_id)
        # ベットを支払えるだけのコインがある場合のみ参加を許可
        if coins < 1:
            return False
        # プレイヤーの状態を生成
        self.users[user_id] = {
            'hand': [],
            'score': 0,
            'status': 'playing',
            'coins': coins - 1,  # 初期ベット分を差し引く（ウォレット側でも同期させる）
            'bet': 1,
            'has_raised': False,
            'is_folded': False,
            'natural_bonus': 0,  # ナチュラルBJボーナス（Aと10点カード：10/J/Q/K）があれば後で設定
        }
        # ウォレットからも初期ベット分を差し引く
        self._set_coins(user_id, coins - 1)
        # ポットにベット額を追加
        self.pot += 1
        # 最初のカードを配る
        self.deal_initial_cards(user_id)
        # ナチュラルブラックジャック（A と 10 点カード）の場合はボーナスを記録するが
        # コインの付与はラウンド終了時に行う
        hand = self.users[user_id]['hand']
        ranks = {card['rank'] for card in hand}
        if 'A' in ranks and any(r in ranks for r in ['J', 'Q', 'K', '10']):
            bonus = 5
            # コインには反映せず natural_bonus に記録しておく
            self.users[user_id]['natural_bonus'] = bonus
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
        # レイズ額はレイズするプレイヤー自身の所持コインを超えない
        raiser_coins = self._get_coins(raiser_id)
        # フォールド/バーストしていない他プレイヤーの最小所持コインを上限計算に使う
        active_players = [uid for uid, state in self.users.items()
                          if not state['is_folded'] and uid != raiser_id]
        if not active_players:
            return raiser_coins
        min_other_coins = min(self._get_coins(uid) for uid in active_players)
        # レイズ額の上限は、他のアクティブプレイヤーの最小所持コイン - 1 に合わせる（0 未満にはしない）
        max_by_others = max(0, min_other_coins - 1)
        return max(0, min(raiser_coins, max_by_others))

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
        if self._get_coins(raiser_id) < amount:
            return False
        # レイズ額を差し引いてベットを更新
        self._adjust_coins(raiser_id, -amount)
        player['bet'] += amount
        # レイズした本人の分だけポットを増額
        self.pot += amount
        # 現在のレイズ額を記録して他プレイヤーの判断に使う
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
        # 進行中のレイズがあることを確認
        if self.current_raise <= 0:
            return False
        if action == 'call':
            # コールに必要なコインを持っているか確認
            if self._get_coins(user_id) < self.current_raise:
                return False
            self._adjust_coins(user_id, -self.current_raise)
            player['bet'] += self.current_raise
            self.pot += self.current_raise
            # コールした場合はゲームに残る
            return True
        elif action == 'fold':
            # フォールドすると以後参加しないが、現在のベット以上は失わない（追加の差し引きなし）
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
    # ラウンドの決着処理
    # ------------------------------------------------------------------
    def resolve_round(self):
        """
        現在のラウンドの結果を判定します。戻り値は (message, summary) のタプルで、
        message は勝者を説明する文字列、summary は各プレイヤーの詳細
        （user_id、手札、スコア、最終コイン、増減）を格納した辞書のリストです。
        すべてのプレイヤーがバーストした場合、ベットは返却されコインは没収されません。
        """
        # 結果リストを初期化
        results = []
        # ラウンド開始時の各プレイヤーのコイン残高を記録する（ベットやボーナスを加算する前）
        initial_coins = {uid: self._get_coins(uid) for uid, state in self.users.items()}
        # 現在アクティブなプレイヤー（バーストやフォールドしていないプレイヤー）を取得
        active_players = [uid for uid, state in self.users.items()
                          if state['status'] in ['playing', 'stand'] and not state['is_folded']]
        # 全員がバーストまたはフォールドしている場合の処理
        if not active_players:
            # 各プレイヤーにベットを返却する。全員がバーストまたはフォールドしている
            # 場合はナチュラルBJボーナスは付与しません。
            for uid, state in self.users.items():
                # ベットの返却
                self._adjust_coins(uid, state['bet'])
                state['bet'] = 0
            # ポットは空になる
            self.pot = 0
            # 結果メッセージ
            message = "勝者なし、全員バーストしました。"
            # 各プレイヤーの増減を計算して結果リストに追加
            for uid, state in self.users.items():
                change = self._get_coins(uid) - initial_coins[uid]
                results.append({
                    'user_id': uid,
                    'hand': self.hand_to_string(state['hand']),
                    'score': state['score'],
                    'final_coins': self._get_coins(uid),
                    'change': change,
                    'natural_bonus': state.get('natural_bonus', 0),
                })
            # 次のラウンドのために状態をリセット
            self.reset_game()
            return message, results

        # アクティブプレイヤーがいる場合は勝者を決定
        # 最も高いスコアを求め、同点のプレイヤーを勝者リストとする
        highest_score = max(self.users[uid]['score'] for uid in active_players)
        winners = [uid for uid in active_players if self.users[uid]['score'] == highest_score]
        # ポットを勝者で均等に分け、余りがあれば順番に1枚ずつ追加する
        share = self.pot // len(winners)
        remainder = self.pot % len(winners)
        for i, uid in enumerate(winners):
            self._adjust_coins(uid, share)
            if i < remainder:
                self._adjust_coins(uid, 1)
        # ナチュラルBJボーナスがある場合はここで付与する
        for uid, state in self.users.items():
            bonus = state.get('natural_bonus', 0)
            # バーストまたはフォールドしていないプレイヤーのみボーナスを受け取る
            if bonus and state['status'] != 'bust' and not state['is_folded']:
                self._adjust_coins(uid, bonus)
        # 各プレイヤーの増減を計算して結果リストに追加
        for uid, state in self.users.items():
            change = self._get_coins(uid) - initial_coins[uid]
            results.append({
                'user_id': uid,
                'hand': self.hand_to_string(state['hand']),
                'score': state['score'],
                'final_coins': self._get_coins(uid),
                'change': change,
                'natural_bonus': state.get('natural_bonus', 0),
            })
        # 勝者メッセージを構築
        if len(winners) == 1:
            message = f"勝者: {winners[0]} スコア: {highest_score}!"
        else:
            winners_str = ", ".join(winners)
            message = f"引き分けです！勝者: {winners_str} スコア: {highest_score}"
        # 次のラウンドのために状態をリセット
        self.reset_game()
        return message, results

    def reset_game(self):
        """
        ゲームの状態をリセットしますが、プレイヤーとそのコイン残高は保持します。

        ラウンドが終了した後も同じ ``Blackjack`` インスタンスを使い続けるため、各
        プレイヤーの手札やスコア、ベットなどはクリアします。ただしプレイヤーは
        ``ready`` ステータスにしておき、次のラウンドを開始できる状態にします。
        デッキやポット、現在のレイズ額も初期化します。
        """
        # 新しいデッキを用意し、ポットやレイズ額をリセット
        self.deck = self.create_deck()
        self.pot = 0
        self.current_raise = 0
        # 各プレイヤーの状態をクリアする
        for state in self.users.values():
            state['hand'] = []
            state['score'] = 0
            # ラウンド終了後は ready 状態にして次のラウンドを開始できるようにする
            state['status'] = 'ready'
            state['bet'] = 0
            state['has_raised'] = False
            state['is_folded'] = False
            # ナチュラルBJボーナスをリセット
            state['natural_bonus'] = 0


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
        # サーバー（ギルド）ごとの共有ウォレット
        self.wallets: dict[int | str, dict] = {}
        # チャンネルが属するギルドの記録
        self.channel_guild: dict[int, int | None] = {}

    # ------------------------------------------------------------------
    # 補助メソッド
    # ------------------------------------------------------------------
    def get_game(self, channel_id: int, guild_id: int | None = None) -> Blackjack:
        """指定されたチャンネルに対応するブラックジャックゲームを取得、または新規作成します。"""
        wallet_key = guild_id if guild_id is not None else "__global__"
        wallet_store = self.wallets.setdefault(wallet_key, {})
        if channel_id not in self.games:
            self.games[channel_id] = Blackjack(wallet_store=wallet_store)
        self.channel_guild[channel_id] = guild_id
        return self.games[channel_id]

    def end_game(self, channel_id: int):
        """指定したチャンネルのゲームインスタンスを削除します。"""
        if channel_id in self.games:
            del self.games[channel_id]

    def find_active_channel_for_user(self, user_id: str, exclude_channel: int | None = None, guild_id: int | None = None):
        """
        指定ユーザーが進行中のラウンドに参加しているチャンネルIDを返します。
        ready 状態以外（ラウンド未終了）の場合に参加中とみなします。
        exclude_channel を指定すると、そのチャンネルは探索対象から除外します。
        guild_id を指定すると同一ギルド内のみ探索します。
        """
        for cid, game in self.games.items():
            if exclude_channel is not None and cid == exclude_channel:
                continue
            if guild_id is not None and self.channel_guild.get(cid) != guild_id:
                continue
            if user_id in game.users and game.users[user_id]['status'] != 'ready':
                return cid
        return None

    # ------------------------------------------------------------------
    # コマンド
    # ------------------------------------------------------------------
    async def command_bj_start(self, interaction: discord.Interaction):
        channel_id = interaction.channel_id
        guild_id = interaction.guild_id
        # 表示名を使用
        user_id = interaction.user.display_name
        # 別チャンネルで進行中のラウンドに参加している場合は拒否
        active_other = self.find_active_channel_for_user(user_id, exclude_channel=channel_id, guild_id=guild_id)
        if active_other is not None:
            await interaction.response.send_message(
                f"{user_id}は別のチャンネル(ID: {active_other})でラウンド進行中です。"
                "先にそちらを終了してください。",
                ephemeral=True
            )
            return
        game = self.get_game(channel_id, guild_id)
        # 必要であれば毎日コインをリセットする処理を適用
        game.check_and_reset_coins()
        # 既にユーザーが存在する場合は次のラウンドへの再参加として扱う
        if user_id in game.users:
            player = game.users[user_id]
            # 現在のステータスが playing または stand であり手札が残っている場合はラウンド継続中
            if player['status'] in ['playing', 'stand'] and player['hand']:
                await interaction.response.send_message(
                    f"{user_id}はすでにゲームに参加しています。",
                    ephemeral=True
                )
                return
            # デッキに十分なカードがあるか確認
            if len(game.deck) < 2:
                await interaction.response.send_message(
                    "山札の残りカードが不足しているため新しいラウンドを開始できません。",
                    ephemeral=True
                )
                return
            # 十分なコインがあるか確認
            if game._get_coins(user_id) < 1:
                await interaction.response.send_message(
                    f"{user_id}は所持コインが不足しているため新しいラウンドに参加できません。",
                    ephemeral=True
                )
                return
            # 新しいラウンドとしてベットを差し引き、ステータスを初期化しカードを配る
            game._adjust_coins(user_id, -1)
            player['bet'] = 1
            player['status'] = 'playing'
            player['has_raised'] = False
            player['is_folded'] = False
            player['hand'] = []
            player['score'] = 0
            player['natural_bonus'] = 0
            # ポットにベットを追加
            game.pot += 1
            # 初期カードを配る
            game.deal_initial_cards(user_id)
            # ナチュラルBJ（A と 10 点カード）の場合はボーナスを記録するのみで、
            # コインはラウンド終了時に付与する
            hand = player['hand']
            ranks = {card['rank'] for card in hand}
            if 'A' in ranks and any(r in ranks for r in ['J', 'Q', 'K', '10']):
                bonus = 5
                player['natural_bonus'] = bonus
            # メッセージを作成
            score = player['score']
            coins = game._get_coins(user_id)
            public_response = (
                f"{user_id}が新しいラウンドに参加しました。公開手札: "
                f"{game.hand_to_public_string(hand)} | 所持コイン: {coins}"
            )
            private_response = (
                f"{user_id}が新しいラウンドに参加し、初期カードを受け取りました！\n"
                f"あなたの手札: {game.hand_to_string(hand)} スコア: {score}\n"
                f"現在の所持コイン: {coins}"
            )
            await interaction.response.send_message(public_response, ephemeral=False)
            await interaction.followup.send(private_response, ephemeral=True)
            return
        # 新規参加者の場合は通常の参加処理
        if not game.add_user(user_id):
            # カードやコインが足りない場合
            await interaction.response.send_message(
                f"{user_id}はゲームに参加できません。山札の残りカードまたはコインが不足しています。",
                ephemeral=True
            )
            return
        # メッセージを準備
        hand = game.users[user_id]['hand']
        score = game.users[user_id]['score']
        coins = game._get_coins(user_id)
        # 公開メッセージにも所持コインを表示する
        public_response = (
            f"{user_id}がゲームに参加しました。公開手札: "
            f"{game.hand_to_public_string(hand)} | 所持コイン: {coins}"
        )
        private_response = (
            f"{user_id}がゲームに参加し、初期カードを受け取りました！\n"
            f"あなたの手札: {game.hand_to_string(hand)} スコア: {score}\n"
            f"現在の所持コイン: {coins}"
        )
        await interaction.response.send_message(public_response, ephemeral=False)
        await interaction.followup.send(private_response, ephemeral=True)

    async def command_bj_hit(self, interaction: discord.Interaction):
        channel_id = interaction.channel_id
        guild_id = interaction.guild_id
        # 表示名を使用
        user_id = interaction.user.display_name
        game = self.get_game(channel_id, guild_id)
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
            coins = game._get_coins(user_id)
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
        guild_id = interaction.guild_id
        # 表示名を使用
        user_id = interaction.user.display_name
        game = self.get_game(channel_id, guild_id)
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
            await interaction.response.send_message(
                "これ以上レイズできません（最大値に達しています）。",
                ephemeral=True
            )
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
        # 現在のラウンドに参加している他プレイヤーを取得する。
        # ``ready`` 状態のプレイヤーは今回のラウンドに参加していないため除外する。
        other_players = [
            uid for uid, state in game.users.items()
            if uid != user_id
            and not state['is_folded']
            and state['status'] in ['playing', 'stand']
        ]
        # 各プレイヤーのコインと手札を含む公開メッセージを作成
        player_states = []
        for uid, state in game.users.items():
            # フォールドしていないプレイヤーは先頭のカードを伏せて表示する
            if not state['is_folded']:
                hand_str = game.hand_to_public_string(state['hand'])
            else:
                hand_str = "[伏せられています]"
            player_states.append(f"{uid}: 手札: {hand_str} | コイン: {game._get_coins(uid)}")
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
                coins_remain = game._get_coins(uid)
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
        guild_id = interaction.guild_id
        game = self.get_game(channel_id, guild_id)
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
        # 進行中のラウンド（playing/stand で手札あり）が存在しない場合は拒否
        active_round_players = [
            state for state in game.users.values()
            if state['status'] in ['playing', 'stand'] and state['hand']
        ]
        if not active_round_players:
            await interaction.response.send_message(
                "進行中のラウンドがありません。まず bj_start でラウンドを開始してください。",
                ephemeral=True
            )
            return
        # ラウンドを解決して結果を取得
        message, summary = game.resolve_round()
        # 詳細な結果メッセージを作成
        result_lines = [message]
        # プレイヤーごとの結果行を組み立て、BJボーナスを含める
        for result in summary:
            uid = result['user_id']
            hand = result['hand']
            score = result['score']
            final_coins = result['final_coins']
            change = result['change']
            change_str = f"{change:+d}"
            nat_bonus = result.get('natural_bonus', 0)
            bonus_str = f" | BJボーナス: +{nat_bonus}" if nat_bonus > 0 else ""
            result_lines.append(
                f"{uid}: 手札: {hand} | スコア: {score} | 最終コイン: {final_coins} ({change_str}){bonus_str}"
            )
        # ナチュラルBJボーナスが含まれている場合、説明を追加
        if any(r.get('natural_bonus', 0) > 0 for r in summary):
            result_lines.append(
                "※ 初手がブラックジャック（Aと10点カード：10/J/Q/K）の場合、システムから5コインが付与されます。"
            )
        await interaction.response.send_message(
            "\n".join(result_lines),
            ephemeral=False
        )
        # resolve_round 内でゲーム状態はリセットされるため、ゲームインスタンスは保持します

    async def command_bj_show(self, interaction: discord.Interaction):
        channel_id = interaction.channel_id
        guild_id = interaction.guild_id
        # 表示名を使用
        user_id = interaction.user.display_name
        game = self.get_game(channel_id, guild_id)
        # 必要であれば毎日コインをリセットする処理を適用
        game.check_and_reset_coins()
        if user_id not in game.users:
            await interaction.response.send_message(f"{user_id}はゲームに参加していません。",
                                                   ephemeral=True)
            return
        state = game.users[user_id]
        hand = state['hand']
        score = state['score']
        coins = game._get_coins(user_id)
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
            lines.append(f"{uid}の公開手札: {other_hand_str} | 所持コイン: {game._get_coins(uid)}")
        response = "\n".join(lines)
        await interaction.response.send_message(response,
                                               ephemeral=True)

    async def _check_auto_allstand(self, interaction: discord.Interaction, channel_id: int):
        """
        すべてのプレイヤーがバーストまたはフォールドしている場合、
        自動的にラウンドを終了して結果を送信します。ヒット後に呼び出されます。
        """
        game = self.get_game(channel_id, interaction.guild_id)
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
            # resolve_round でゲーム状態はリセットされるため、ゲームインスタンスは保持します


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
        coins = self.game._get_coins(user_id)
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
# コマンド登録
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

import random
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
        # ready（未参加）状態のプレイヤーは次ラウンド未参加なので除外する
        active_players = [
            uid for uid, state in self.users.items()
            if not state['is_folded']
            and state['status'] in ['playing', 'stand']
            and uid != raiser_id
        ]
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
        # 今ラウンドに参加しているプレイヤーのみを対象とする（ready のままの過去参加者を除外）
        round_players = {
            uid: state for uid, state in self.users.items()
            if state.get('status') != 'ready' or state.get('bet', 0) > 0 or state.get('hand')
        }
        if not round_players:
            # 想定外だが、安全のために早期リセットする
            self.reset_game()
            return "進行中のプレイヤーがいません。", results
        # ラウンド開始時の各プレイヤーのコイン残高を記録する（ベットやボーナスを加算する前）
        initial_coins = {uid: self._get_coins(uid) for uid in round_players}
        # 現在アクティブなプレイヤー（バーストやフォールドしていないプレイヤー）を取得
        active_players = [uid for uid, state in round_players.items()
                          if state['status'] in ['playing', 'stand'] and not state['is_folded']]
        # 全員がバーストまたはフォールドしている場合の処理
        if not active_players:
            # 各プレイヤーにベットを返却する。全員がバーストまたはフォールドしている
            # 場合はナチュラルBJボーナスは付与しません。
            for uid, state in round_players.items():
                # ベットの返却
                self._adjust_coins(uid, state['bet'])
                state['bet'] = 0
            # ポットは空になる
            self.pot = 0
            # 結果メッセージ
            message = "勝者なし、全員バーストしました。"
            # 各プレイヤーの増減を計算して結果リストに追加
            for uid, state in round_players.items():
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
        for uid, state in round_players.items():
            bonus = state.get('natural_bonus', 0)
            # バーストまたはフォールドしていないプレイヤーのみボーナスを受け取る
            if bonus and state['status'] != 'bust' and not state['is_folded']:
                self._adjust_coins(uid, bonus)
        # 各プレイヤーの増減を計算して結果リストに追加
        for uid, state in round_players.items():
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

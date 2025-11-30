import discord

from .blackjack_logic import Blackjack
from .call_fold_view import CallFoldView


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
    def _make_embed(self, title: str, description: str = "", fields: list[tuple[str, str, bool]] | None = None,
                    color: discord.Color | int = discord.Color.blurple()) -> discord.Embed:
        """Embed を生成する簡易ヘルパー。fields は (name, value, inline) のタプル。"""
        embed = discord.Embed(title=title, description=description, color=color)
        if fields:
            for name, value, inline in fields:
                embed.add_field(name=name, value=value, inline=inline)
        return embed

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
            guild_for_link = self.channel_guild.get(active_other, guild_id)
            channel_url = f"https://discord.com/channels/{guild_for_link}/{active_other}" if guild_for_link else f"https://discord.com/channels/@me/{active_other}"
            await interaction.response.send_message(
                f"{user_id}は別のチャンネル( {channel_url} )でラウンド進行中です。先にそちらを終了してください。",
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
            public_embed = self._make_embed(
                title="ブラックジャックに参加しました",
                description=f"{user_id} が新しいラウンドに参加しました。",
                fields=[
                    ("公開手札", game.hand_to_public_string(hand), True),
                    ("所持コイン", str(coins), True),
                ]
            )
            private_embed = self._make_embed(
                title="あなたの初期カード",
                description="カードを2枚配りました。",
                fields=[
                    ("あなたの手札", game.hand_to_string(hand), False),
                    ("スコア", str(score), True),
                    ("所持コイン", str(coins), True),
                ],
                color=discord.Color.green()
            )
            await interaction.response.send_message(embed=public_embed, ephemeral=False)
            await interaction.followup.send(embed=private_embed, ephemeral=True)
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
        public_embed = self._make_embed(
            title="ブラックジャックに参加しました",
            description=f"{user_id} がゲームに参加しました。",
            fields=[
                ("公開手札", game.hand_to_public_string(hand), True),
                ("所持コイン", str(coins), True),
            ]
        )
        private_embed = self._make_embed(
            title="あなたの初期カード",
            description="カードを2枚配りました。",
            fields=[
                ("あなたの手札", game.hand_to_string(hand), False),
                ("スコア", str(score), True),
                ("所持コイン", str(coins), True),
            ],
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=public_embed, ephemeral=False)
        await interaction.followup.send(embed=private_embed, ephemeral=True)

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
        # レイズ受付中はヒットできない
        if game.current_raise > 0:
            await interaction.response.send_message(
                "現在レイズの受付中です。全員が Call/Fold を選択（またはタイムアウト処理）するまでヒットできません。",
                ephemeral=True
            )
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
            public_embed = self._make_embed(
                title="バーストしました",
                description=f"{user_id} はバーストしました！",
                fields=[
                    ("公開手札", game.hand_to_string(hand), False),
                    ("スコア", str(score), True),
                ],
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=public_embed, ephemeral=False)
            # 全員バーストしたかどうかをチェックして自動的に終了する
            await self._check_auto_allstand(interaction, channel_id)
        else:
            # 引いたカードと現在の手札、スコア、所持コインを含めたメッセージを作成
            coins = game._get_coins(user_id)
            private_embed = self._make_embed(
                title="カードを引きました",
                description=f"あなたが引いたカード: {game.card_to_string(card)}",
                fields=[
                    ("あなたの手札", game.hand_to_string(hand), False),
                    ("スコア", str(score), True),
                    ("所持コイン", str(coins), True),
                ],
                color=discord.Color.green()
            )
            public_embed = self._make_embed(
                title="カードを引きました",
                description=f"{user_id} がカードを引きました。",
                fields=[
                    ("公開手札", game.hand_to_public_string(hand), False),
                    ("所持コイン", str(coins), True),
                ]
            )
            await interaction.response.send_message(embed=public_embed, ephemeral=False)
            await interaction.followup.send(embed=private_embed, ephemeral=True)
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
        raise_embed = self._make_embed(
            title="レイズが行われました",
            description=f"{user_id} が {amount} コインをレイズしました。\n60秒以内に Call / Fold を選択してください。",
            fields=[
                ("現在のポット", f"{game.pot} コイン", True),
                ("レイズ額", f"{amount} コイン", True),
                ("プレイヤー状態", state_message, False),
            ],
            color=discord.Color.orange()
        )
        await interaction.response.send_message(embed=raise_embed, view=view, ephemeral=False)
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
                messages.append(f"{uid}: {hand_str} | コイン: {coins_remain}")
            auto_fold_embed = self._make_embed(
                title="時間切れで自動フォールド",
                description="以下のプレイヤーは時間切れのためフォールドしました。",
                fields=[("プレイヤー", "\n".join(messages), False)],
                color=discord.Color.dark_gray()
            )
            await interaction.followup.send(embed=auto_fold_embed, ephemeral=False)
        # 受付終了のメッセージ（各プレイヤーの行動と状態を含める）
        player_lines = []
        for uid, state in game.users.items():
            # 行動のラベルを決定
            if uid == user_id:
                action_label = "レイズ"
            elif uid in view.responses:
                action_label = "コール" if view.responses[uid] == "call" else "フォールド"
            elif uid in auto_folded:
                action_label = "フォールド（時間切れ）"
            else:
                action_label = "未応答"
            # 手札表示（フォールドしていない場合は1枚伏せで公開）
            if not state['is_folded']:
                hand_str = game.hand_to_public_string(state['hand'])
            else:
                hand_str = "[伏せられています]"
            coins_now = game._get_coins(uid)
            player_lines.append(f"{uid}: {action_label} | 手札: {hand_str} | コイン: {coins_now}")
        summary_embed = self._make_embed(
            title="レイズ受付が終了しました",
            description="各プレイヤーの結果をまとめました。",
            fields=[
                ("プレイヤー状態", "\n".join(player_lines), False),
                ("現在のポット", f"{game.pot} コイン", True),
                ("レイズ額", f"{game.current_raise} コイン", True),
            ],
            color=discord.Color.blue()
        )
        await interaction.followup.send(embed=summary_embed, ephemeral=False)

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
        result_embed = self._make_embed(
            title="ラウンド結果",
            description=message,
            fields=[("詳細", "\n".join(result_lines[1:]), False)],
            color=discord.Color.gold()
        )
        await interaction.response.send_message(embed=result_embed, ephemeral=False)
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
        # 自身の手札・コイン・ステータス
        lines = [
            f"{user_id}の現在の手札: {game.hand_to_string(hand)}",
            f"ステータス: {state['status']} | スコア: {score} | 所持コイン: {coins}"
        ]
        # 他プレイヤーの公開手札と所持コインを表示
        for uid, other_state in game.users.items():
            if uid == user_id:
                continue
            status_label = other_state['status']
            # フォールドしていない場合は先頭カードを伏せて表示
            if not other_state['is_folded']:
                other_hand_str = game.hand_to_public_string(other_state['hand'])
            else:
                other_hand_str = "[伏せられています]"
            lines.append(
                f"{uid}の公開手札: {other_hand_str} | ステータス: {status_label} | 所持コイン: {game._get_coins(uid)}"
            )
        response = "\n".join(lines)
        show_embed = self._make_embed(
            title="現在の手札とコイン",
            description=f"{user_id} の状況を表示します。",
            fields=[("詳細", response, False)],
            color=discord.Color.teal()
        )
        await interaction.response.send_message(embed=show_embed, ephemeral=True)

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
            result_lines = []
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
            auto_result_embed = self._make_embed(
                title="全員バーストにより終了",
                description=message,
                fields=[("詳細", "\n".join(result_lines), False)],
                color=discord.Color.gold()
            )
            # 結果をチャンネルへ送信
            await interaction.followup.send(embed=auto_result_embed, ephemeral=False)
            # resolve_round でゲーム状態はリセットされるため、ゲームインスタンスは保持します


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

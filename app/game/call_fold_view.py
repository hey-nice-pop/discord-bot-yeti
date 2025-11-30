import discord

from .blackjack_logic import Blackjack


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

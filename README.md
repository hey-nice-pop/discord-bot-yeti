# Discord Bot
Blackjack Discord bot

## 環境構築
1 .envのコピー、コピー後に各自.env内の値を調整(BOT_TOKEN必須)

```cp app/.env.example app/.env```

2 dockerにてコンテナ起動

```docker compose up -d```

3 botの起動

```docker exec -it yeti python main.py```

※vscodeご利用の場合はdevcontainerから開発コンテナを開いて下記でbot起動

```python main.py```

### その他コマンド

コンテナをたおす(botの停止)

```docker compose down```

起動中のコンテナに入る

```docker exec -it yeti /bin/bash```

起動中のbotのリアルタイムログを確認する

```docker compose logs -f yeti```
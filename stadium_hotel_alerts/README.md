# Stadium Hotel Alerts

Philippine Sports Stadium(ブラカン州ボカウェ)から車で40分以内のホテル・Airbnb を監視し、
条件に合う空室が見つかったらメールで通知するツールです。

## 監視条件(`config.yaml` で変更可能)

| 項目 | 値 |
|---|---|
| 宿泊日 | 2027年3月15日〜17日(2泊)、大人2名 ※必須 |
| 連泊確認 | 3月13日まで遡って何日間連続で泊まれるかをメールに表示 |
| 予算 | 1泊 1,800〜3,500 PHP |
| 対象 | ホテル(Google Hotels 経由)・Airbnb |
| 範囲 | 車で30分以内(20分以内を「優先」としてメール内で先頭に表示) |
| 条件 | エアコン付き、評価 3.7 以上 |

## 仕組み

1. **ホテル**: [SerpAPI](https://serpapi.com/) の Google Hotels エンジンで、指定日程で予約可能な物件を検索
2. **Airbnb**: [pyairbnb](https://pypi.org/project/pyairbnb/)(非公式 API)でエアコン付き・予算内の空室を検索
3. **所要時間**: OSRM(無料ルーティング API)で車での実走行時間を計算し、渋滞係数(既定 1.4)を掛けて補正。OSRM が使えない場合は直線距離から推定
4. **フィルタ**: 予算・評価 3.7 以上・エアコン・30分圏内で絞り込み
5. **人数の検証**: ホテル検索は SerpAPI に `adults: 2` を渡して2名で検索していますが、
   Airbnb の検索 API はゲスト数を指定できません。そのため距離条件まで通過した
   Airbnb の候補だけ、物件詳細 API で定員(最大宿泊人数)を確認し、2名未満の
   物件は除外します(価格自体は Airbnb 側で人数によらずほぼ同一のため、
   表示価格の再計算はしていません)。取得できた定員はメールにも表示します
   (ホテルは SerpAPI の応答に定員情報が含まれないため表示されません)
6. **連泊確認**: 条件に合致した物件について、3/13〜・3/14〜のチェックインでも
   予約可能かを追加で検索し、必須期間(3/15〜3/17)を含む最大連続宿泊数を
   メールに表示(連泊可能日数が増えた場合も再通知)
7. **通知**: 新しく見つかった物件(または価格が5%以上変動した物件)だけをメール送信。宿泊費は PHP と日本円の目安を併記(為替は [frankfurter.app](https://www.frankfurter.app/) から取得し、失敗時は `config.yaml` の固定レートを使用)。通知済みは `state.json` に記録して重複通知を防止
8. **定期実行**: GitHub Actions が6時間ごとに自動実行(`.github/workflows/hotel-alerts.yml`)

## セットアップ

### 1. SerpAPI の API キーを取得

https://serpapi.com/ で無料アカウントを作成(月100回まで無料)し、API キーを取得します。

### 2. Gmail のアプリパスワードを作成

Google アカウントで2段階認証を有効にした上で、
https://myaccount.google.com/apppasswords からアプリパスワードを作成します。

### 3. GitHub Secrets を設定

リポジトリの **Settings → Secrets and variables → Actions** で以下を登録します:

| Secret 名 | 内容 |
|---|---|
| `SERPAPI_API_KEY` | SerpAPI の API キー |
| `SMTP_USERNAME` | 送信元 Gmail アドレス |
| `SMTP_PASSWORD` | Gmail アプリパスワード |
| `ALERT_EMAIL_TO` | (任意)送信先の上書き。未設定なら `config.yaml` の `buchiful@gmail.com` |

Gmail 以外の SMTP を使う場合は、ワークフローの env に `SMTP_HOST` / `SMTP_PORT` を追加してください。

### 4. 動作確認

GitHub の **Actions → Hotel availability alerts → Run workflow** で手動実行できます。

ローカルで試す場合:

```bash
pip install -r requirements.txt
export SERPAPI_API_KEY=...
# メールを送らずに結果だけ確認
python -m stadium_hotel_alerts.main --dry-run
```

## 注意事項

- **予約可能時期**: 2027年3月の宿泊はまだ予約受付が始まっていない可能性があります(多くのサイトは約1年前から)。受付が始まり次第、このツールが自動で検知して通知します。
- **Airbnb は非公式 API** を使っているため、Airbnb 側の仕様変更で一時的に動かなくなることがあります。その場合もホテル検索は影響を受けません。
- **所要時間は推定値**です。実際の交通状況により前後します。
- 価格・空室はメール送信後にも変動するため、予約前に必ずリンク先で確認してください。

# 介護施設向け巡回システム構成

## 目的と現在の範囲

2026年10月28日の進捗発表へ向けた最初の実装目標は、既存Waypoint自律走行中にC920映像からYOLOで人物を検出し、既存のpause serviceへ停止要求を送り、ロボットを停止状態に維持することである。

`person_stop_guard` は卒研専用パッケージ内へ実装済みである。保存動画と模擬停止serviceまでを開発PCで確認し、実機走行と本番Waypointへの停止要求は未実施である。

## 目標構成（次段階）

```text
Logitech C920
    │  OpenCV/V4L2（1プロセス・1 capture owner）
    ▼
care_patrol/person_stop_guard（卒研専用ノード・実装済み）
    ├─ latest frame
    ├─ YOLO person class (class id 0)
    ├─ 連続検出による確定判定
    └─ std_srvs/srv/SetBool: data=True
              │
              ▼
/koko_waypoint_follower/set_paused（既存）
              │
              ▼
既存Waypoint Follower → Nav2 goal cancel / 安全停止 / manual pause維持
```

新規ノードは停止要求のservice clientに限定する。画像topicや速度指令をpublishせず、既存Waypoint Followerの停止経路を利用する。

## 責務の境界

| 構成要素 | 責務 | 変更方針 |
|---|---|---|
| 既存Waypoint Follower | Waypoint走行、Nav2 goal管理、pause時のcancelと停止維持 | 変更しない |
| `person_stop_guard` | C920/動画取得、YOLO人物判定、debounce、pause要求、停止確認ラッチ | 実装済み。実機確認は未実施 |
| Camera V7資産 | YOLO person限定検出、カメラopen、所有権設計の参照 | 必要最小限だけ移植。直接importしない |
| Nav2・センサー設定 | 走行・自己位置・センサー処理 | 変更しない |

## 停止シーケンス

1. 既存Waypoint Followerが通常走行する。
2. `person_stop_guard` がC920を単独でopenし、フレームをYOLOへ渡す。
3. person classを設定回数連続で検出したときだけ、人物検出を確定する。
4. `/koko_waypoint_follower/set_paused` のservice利用可能性を非同期に確認する。
5. `SetBool.Request(data=True)` を非同期送信する。
6. `success=true` を受けたら「受付済み・停止未確認」とし、同一イベントでの要求連打を止める。
7. 成功応答後にVOLATILE QoSで `/stop` とOdometryの監視を開始する。受付前の観測は破棄する。
8. 受付後の `/stop=true` と静止Odometryの連続観測後だけ「停止確認済み」とする。
9. 検出が消えても停止を解除せず、`data=False` は送らない。

## 状態と異常時方針

| 状態 | 意味 | 遷移条件 |
|---|---|---|
| `MONITORING` | C920監視中 | 連続人物検出で `STOP_REQUESTED` |
| `REQUEST_SENT` | pause service応答待ち | 成功で `REQUEST_ACCEPTED_UNCONFIRMED`。timeout時は結果不明として再送しない |
| `REQUEST_ACCEPTED_UNCONFIRMED` | 要求受付済み、停止観測不足 | `/stop` と静止Odometryの確認で `STOP_CONFIRMED` |
| `STOP_CONFIRMED` | 停止保持と静止を観測済み | 自動遷移なし。再開要求は送らない |
| `FAULT` | カメラ、YOLO、service等の継続不能 | エラーを明示し、走行再開要求は送らない |

serviceが見つからない、応答が失敗する、またはtimeoutした場合は「停止成功」と記録しない。重複要求を避けるため同じ検出イベントでは再送しない。実機安全確認時には人物検出とは独立した非常停止手段を用意する。

## 次の検証段階

次はCamera V7を停止した状態でC920単独入力＋模擬serviceを確認し、READ-ONLYのROS interface確認、安全設備上での停止機構単体試験、YOLO停止統合試験の順に進む。走行再開、Gemma、音声、人物同定、UI、録画、複雑なtrackingは引き続き対象外とする。実行条件は [person_stop_guard.md](person_stop_guard.md)、実機試験手順は [real_robot_validation.md](real_robot_validation.md) に記載する。

## ワークスペース

```text
care_patrol_ws/
├── docs/
├── src/
│   └── care_patrol/
│       ├── care_patrol/
│       ├── resource/
│       ├── package.xml
│       ├── setup.cfg
│       └── setup.py
├── build/      # colcon生成物
├── install/    # colcon生成物
└── log/        # colcon生成物
```

基本手順は次のとおり。

```bash
cd ~/care_patrol_ws
source /opt/ros/jazzy/setup.bash
colcon build --packages-select care_patrol
source install/setup.bash
```

# 既存コード流用一覧

## 方針

この一覧は完了済みREAD-ONLY監査の結果を、今回の最小構成に必要な範囲へ絞って固定したものである。中之島MS2、既存Waypoint Follower、Nav2、ROS・センサー設定は変更しない。既存資産は無条件にコピーせず、外部インターフェースの利用または必要な考え方・小さな処理単位の移植に限定する。

## 流用する機能

| 機能 | 流用元 | 流用方法 | 今回の状態 |
|---|---|---|---|
| Waypoint走行の一時停止・停止維持 | `robotbase_waypoint/waypoint_follow.py` | 既存ノードが提供する `/koko_waypoint_follower/set_paused`（`std_srvs/srv/SetBool`）を外部から呼ぶ。Follower自体はコピー・改造しない | `data=True` の1回送信と、10月25日版実Follower＋模擬Nav2の隔離統合試験済み |
| YOLO人物検出 | Camera V7が参照する `detect_persons_with_tracking()` | `classes=[0]` によるperson限定検出とconfidence抽出だけを移植し、tracking・VLMは持ち込まない | YOLO11n保存動画試験済み |
| C920入力の選択・オープン | Camera V7の `available_camera_sources()` / `open_camera_capture_with_fallback()` | V4L2 openとreadの考え方を参考にする。誤カメラ防止のため無差別fallbackは行わず、明示デバイスだけをopenする | 実装済み。実機C920試験は未実施 |
| C920共有・所有権管理 | Camera V7の `CameraOwnershipCoordinator` | 1プロセス・1 capture owner、release確認の原則を流用する。クラス全体はコピーしない | direct open明示許可、advisory lock、V7プロセス排他を実装 |
| 停止の安全側ラッチ | Waypoint Followerのmanual pause処理 | 人物検出後は再検出にかかわらずラッチし、`data=False` は送らない | 実装・模擬service試験済み |

## 今回流用しない機能

- Gemma / LM Studio / VLMによる人物属性判定
- 走行再開、停止解除、探索完了通知
- 音声認識・音声合成
- Camera V7のTk UI、撮影トピック、録画、結果保存、人物追跡メモリ
- 既存の `/person_area_arrived`、`/person_search_done` 系連携
- Nav2、Waypoint、ロボット、カメラの既存設定ファイル

## 注意点

- `~/set_paused` は `koko_waypoint_follower` ノードのprivate serviceなので、通常の完全修飾名は `/koko_waypoint_follower/set_paused` になる。namespaceやnode remapを行う場合は実機上で解決名を再確認する。
- pause service不在、失敗、応答timeoutは「停止済み」と見なさない。結果不明時の重複要求を避けるため、同じ検出イベントでは自動再送しない。
- この研究用パッケージから再開要求を送らないことで、人物検出後の停止維持を守る。
- 参照元ファイルの同一性と依存関係は [source_manifest.md](source_manifest.md) を正とする。

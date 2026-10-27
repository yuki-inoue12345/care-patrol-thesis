# person_stop_guard 実行・安全条件

## 実装済みの流れ

`person_stop_guard` は、保存動画または単独所有を明示したC920をOpenCVで読み、YOLO11nへ `classes=[0]` を指定してpersonだけを検出する。設定されたconfidence以上の検出が指定フレーム数だけ連続すると検出をラッチし、既存 `/koko_waypoint_follower/set_paused` へ `SetBool(data=True)` を1回だけ非同期送信する。

サービスの `success=true` は要求受付として扱い、停止完了とは扱わない。成功応答を受けた時点でVOLATILE QoSの `/stop` とOdometry subscriptionを作成し、それ以前にキューへ入った古いサンプルを停止確認へ使わない。trackerも受付前の観測を無視し、受付時に観測状態をリセットする。受付後の `/stop=true` と、Odometryの並進・回転速度がしきい値以下になった連続3サンプルの両方を観測して初めて `stop_confirmed` になる。確認topicを観測できない場合は `request_accepted_unconfirmed` を維持し、ログへ `UNCONFIRMED` を出す。

どの状態でも `SetBool(data=False)` は送らず、自動再開しない。応答timeout時も重複停止を避けるため再送しない。

## Python環境

ROS 2 Jazzyに加えてUltralyticsが必要である。

```bash
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
python -m pip install -r src/care_patrol/requirements.txt
source /opt/ros/jazzy/setup.bash
source install/setup.bash
```

既存Camera V7のvenvを検証に利用できるが、本番運用では卒研ワークスペース専用venvを用意する。現在のcolcon entry pointは `/usr/bin/python3` で生成されるため、Ultralyticsをvenvに導入した場合は以下の例のように、そのvenvの `python -m care_patrol.person_stop_guard` で起動する。`ros2 run` はentry pointのPython環境にもUltralyticsが導入済みの場合だけ使用する。

## 保存動画での起動例

実機serviceへ誤送信しない環境で、service名を模擬serviceへ変更して実行する。

```bash
source /opt/ros/jazzy/setup.bash
source install/setup.bash
python -m care_patrol.person_stop_guard --ros-args \
  --params-file src/care_patrol/config/person_stop_guard.yaml \
  -p input_mode:=video \
  -p video_path:=/absolute/path/to/test.mp4 \
  -p yolo_model:=/absolute/path/to/yolo11n.pt \
  -p pause_service:=/care_patrol_test/set_paused
```

## C920単独所有での起動条件

`allow_direct_camera` の既定値は `false` であり、そのままではカメラをopenしない。以下をすべて確認した初期検証時だけ `true` にする。

1. Camera V7と他のC920利用プロセスが停止している。
2. `/dev/v4l/by-id/...` など、C920を一意に示すデバイスを指定している。
3. 模擬pause serviceを使用し、実機Robot/Waypointへ接続していない。
4. カメラ画像利用について施設・被験者の許可条件を満たしている。

```bash
python -m care_patrol.person_stop_guard --ros-args \
  --params-file src/care_patrol/config/person_stop_guard.yaml \
  -p input_mode:=camera \
  -p camera_device:=/dev/v4l/by-id/LOGITECH_C920_DEVICE \
  -p allow_direct_camera:=true \
  -p yolo_model:=/absolute/path/to/yolo11n.pt \
  -p pause_service:=/care_patrol_test/set_paused
```

ノードはadvisory lockを取得し、起動中のCamera V7プロセスを検出した場合はdirect openを拒否する。監視中にCamera V7が起動した場合もC920を解放して `fault` へ移る。ただし現在のCamera V7は同じlock規約を実装していないため、両プロセスが同時にC920を利用する構成は未対応である。同時運用には、C920を1プロセスだけがopenし、共有フレームを双方へ配るbroker方式が必要になる。

## 主な状態

| 状態 | 意味 |
|---|---|
| `monitoring` | 映像を監視中 |
| `waiting_for_service` | 人物条件成立済み、service discovery待ち |
| `request_sent` | `data=True` を送信済み、応答待ち |
| `request_accepted_unconfirmed` | 受付成功。ただし停止観測が不足 |
| `stop_confirmed` | `/stop=true` と静止Odometryを確認済み |
| `request_outcome_unknown` | 応答timeout/例外。安全のため再送しない |
| `fault` | カメラまたは推論を継続不能 |

## 実機停止試験前の確認

- 実機の `/stop` が既存Followerの停止保持を表すこと
- 実機の `/odom` が走行中に更新され、停止時のノイズが設定しきい値内に入ること
- pause service名とnode namespaceが一致すること
- 非常停止担当者、退避空間、低速設定、車輪浮上試験または安全架台が用意されていること
- 保存動画＋模擬service試験を同じモデル・confidenceで再度PASSしていること

実機統合のREAD-ONLY確認、4段階の試験手順、合格条件、安全条件は
[real_robot_validation.md](real_robot_validation.md) に記載する。

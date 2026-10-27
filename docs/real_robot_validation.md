# C920・Waypoint停止 実機統合検証手順

## この文書の扱い

この文書はREAD-ONLY確認と、将来承認を得た後に行う実機試験の手順書である。今回の作業では実機走行、実機service call、本番コード変更を実施していない。

実機へ送信を伴うStage 3とStage 4は、監督者・操作者・非常停止担当者を配置し、各Stageの開始許可を得てから実施する。Stage 1とStage 2に合格するまでStage 3へ進まない。

## 停止確認の安全条件

`person_stop_guard` は、以下を別の状態として扱う。

1. 人物条件成立
2. `SetBool(data=True)` 送信済み
3. service `success=true` 受付済み、停止未確認
4. `/stop=true` と静止Odometryの両方を確認済み

古いメッセージを使わないため、`/stop`とOdometryのsubscriptionはservice成功応答後に初めて作成する。どちらもdurabilityをVOLATILEにし、受付以前のサンプルをlate joinerへ再配信させない。tracker側も受付前の観測を無視し、受付時に観測状態をリセットする。

既定の静止条件は以下である。

- 並進速度ノルム: `0.03 m/s` 以下
- 回転速度ノルム: `0.05 rad/s` 以下
- 連続サンプル数: 3
- `/stop`またはOdometryを観測できない場合: `request_accepted_unconfirmed`
- 停止確認timeout: 10秒。timeout後も再送・自動再開しない

`/stop=true` はtwist_muxの停止lock、Odometryは物理運動の観測である。service応答だけ、または片方のtopicだけでは合格にしない。

## C920用Python環境

卒研ワークスペース専用venvを使用する。既存Camera V7のvenvは開発PC試験でのみ使用し、実機試験用の正規環境にはしない。

| 要素 | 入手元・条件 |
|---|---|
| Python | Ubuntu 24.04 / ROS 2 Jazzyと同じPython 3.12系 |
| `rclpy`, `std_srvs`, `std_msgs`, `nav_msgs` | ROS 2 Jazzyおよび `package.xml` |
| OpenCV, NumPy | Ubuntu packageを `--system-site-packages` で参照 |
| Ultralytics | `requirements.txt` の `ultralytics==8.4.47` |
| YOLO weights | ローカルの `yolo11n.pt` を絶対パスで指定。試験中にdownloadさせない |

開発PCの保存動画回帰試験ではPython 3.12.3、OpenCV 4.13.0、NumPy 2.4.4、
PyTorch 2.11.0+cu130、Ultralytics 8.4.47の既存検証環境でPASSした。Ultralyticsは
同一versionへ固定した。PyTorch backendはNUCのCPU/GPU構成へ合わせ、NUC上でimportと
保存動画推論を必ず再確認する。卒研専用 `.venv` はまだ作成していないため、Stage 1前に
次を実行する。

```bash
cd /home/inoue-yuki/care_patrol_ws
source /opt/ros/jazzy/setup.bash
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
python -m pip install -r src/care_patrol/requirements.txt
source install/setup.bash

python -c 'import rclpy, cv2, numpy, ultralytics; print(cv2.__version__, ultralytics.__version__)'
python -c 'from care_patrol.person_stop_guard import PersonStopGuard; print("import OK")'
```

colconが生成したentry pointは現在 `/usr/bin/python3` を使う。この環境ではUltralyticsが未導入なので、venv利用時は `ros2 run` ではなく次の形式で起動する。

```bash
python -m care_patrol.person_stop_guard --ros-args ...
```

## C920のREAD-ONLY事前確認

```bash
lsusb | grep -Ei '046d|Logitech|C920'
find /dev/v4l/by-id -maxdepth 1 -type l -print 2>/dev/null
find /dev -maxdepth 1 -name 'video*' -print
```

`v4l2-ctl`が導入済みなら次も確認する。

```bash
v4l2-ctl --list-devices
v4l2-ctl --device /dev/v4l/by-id/C920_DEVICE --all
fuser -v /dev/v4l/by-id/C920_DEVICE
```

合格条件は、C920を指す安定したby-idパスが1つに確定し、`fuser`でCamera V7など別プロセスのopenがないこと。`/dev/video0`の番号だけでは合格にしない。

## Robot実機側ROS 2環境

10月25日アーカイブで確認できる期待値は次のとおり。ただし実機の `~/robotbase_ws_renya/robot.env` を当日の正本とする。

| 項目 | 期待値 |
|---|---|
| ROS 2 | Jazzy |
| 実機`ROS_DOMAIN_ID` | `54`（当日 `robot.env` で再確認） |
| Waypoint node | `/koko_waypoint_follower` |
| pause service | `/koko_waypoint_follower/set_paused` |
| service type | `std_srvs/srv/SetBool` |
| stop lock topic | `/stop` / `std_msgs/msg/Bool` |
| Nav2使用Odometry | `/odom/filtered` / `nav_msgs/msg/Odometry` |
| raw Odometry候補 | `/odom` / `nav_msgs/msg/Odometry` |
| mux出力 | `/cmd_vel` / `geometry_msgs/msg/Twist` |
| Follower safety zero | `/cmd_vel_direct` / `geometry_msgs/msg/Twist` |

Robot側で環境だけを確認するREAD-ONLYコマンド：

```bash
cd ~/robotbase_ws_renya
source /opt/ros/jazzy/setup.bash
source bash/activate_koko_env.sh
source install/setup.bash

printf 'ROS_DISTRO=%s\n' "$ROS_DISTRO"
printf 'ROS_DOMAIN_ID=%s\n' "$ROS_DOMAIN_ID"
printf 'RMW_IMPLEMENTATION=%s\n' "${RMW_IMPLEMENTATION:-default}"
printf 'ROS_LOCALHOST_ONLY=%s\n' "${ROS_LOCALHOST_ONLY:-unset}"
printf 'ROS_AUTOMATIC_DISCOVERY_RANGE=%s\n' "${ROS_AUTOMATIC_DISCOVERY_RANGE:-unset}"
grep '^ROBOTBASE_ROS_DOMAIN_ID=' robot.env
```

開発PCは同じLANでRobotの実機Domainへ合わせる。Stage 1のローカル分離設定を残さない。

```bash
unset ROS_LOCALHOST_ONLY
export ROS_AUTOMATIC_DISCOVERY_RANGE=SUBNET
export ROS_DOMAIN_ID=54  # Robot側robot.envの実測値に置換
```

Robot側で `RMW_IMPLEMENTATION` が明示されている場合は開発PCも同じ値にする。DDS discoveryが成立しない場合は先へ進まず、同一subnet、Wi-Fi client isolation、multicast、firewallを確認する。

## QoSの期待値と当日確認

| Interface | `person_stop_guard`側 | 既存側の期待値 |
|---|---|---|
| SetBool service | ROS 2 default service QoS（RELIABLE/VOLATILE） | `create_service(SetBool, '~/set_paused', ...)`のdefault QoS |
| `/stop` | RELIABLE、VOLATILE、KEEP_LAST 10 | Followerの`create_publisher(..., 10)`。RELIABLE、VOLATILE、KEEP_LAST 10 |
| Odometry | sensor-data QoS。BEST_EFFORT、VOLATILE | 実機publisherを当日確認。RELIABLE/BEST_EFFORTのどちらでもBEST_EFFORT subscriberと接続可能 |

READ-ONLY確認：

```bash
ros2 node list | sort
ros2 node info /koko_waypoint_follower
ros2 service list -t | grep '/koko_waypoint_follower/set_paused'
ros2 service type /koko_waypoint_follower/set_paused
ros2 topic type /stop
ros2 topic info --verbose /stop
ros2 topic type /odom/filtered
ros2 topic info --verbose /odom/filtered
timeout 5 ros2 topic hz /odom/filtered
```

型、名前、publisher/subscriber、QoS compatibility、Odometry更新を確認できなければ不合格。service callはまだ行わない。

## 10月25日版Waypoint FollowerのREAD-ONLY確認

参照基準ファイルのSHA256：

```text
bdec94385f670b493640397cec2135fbf2bf8e0bb9d46e2f5074f47292ca1a29
```

Robot側で以下を実行する。すべてREAD-ONLYである。

```bash
cd ~/robotbase_ws_renya
sha256sum src/robotbase_waypoint/robotbase_waypoint/waypoint_follow.py
grep -n "super().__init__('koko_waypoint_follower')" \
  src/robotbase_waypoint/robotbase_waypoint/waypoint_follow.py
grep -n "create_service(SetBool, '~/set_paused'" \
  src/robotbase_waypoint/robotbase_waypoint/waypoint_follow.py
grep -n 'def _set_paused' \
  src/robotbase_waypoint/robotbase_waypoint/waypoint_follow.py
grep -n 'def _continue_manual_pause' \
  src/robotbase_waypoint/robotbase_waypoint/waypoint_follow.py

ros2 pkg prefix robotbase_waypoint
python3 - <<'PY'
import importlib.util
spec = importlib.util.find_spec('robotbase_waypoint.waypoint_follow')
print(spec.origin if spec else 'NOT FOUND')
PY
```

合格条件：source SHA256が基準値と一致し、実行時package prefixとimport元が `~/robotbase_ws_renya` を指し、起動中nodeがserviceを公開していること。SHA256不一致、別workspaceのoverlay、serviceなしのいずれかならStage 3以降を中止する。コードをその場で置換・編集しない。

## Stage 1: カメラ単体

目的はC920と実人物のYOLO検出だけを確認し、Robotへ一切接続しないこと。

準備：Camera V7を停止し、開発PCをローカル限定Domainへ隔離する。

```bash
cd /home/inoue-yuki/care_patrol_ws
source /opt/ros/jazzy/setup.bash
source .venv/bin/activate
source install/setup.bash
export ROS_DOMAIN_ID=167
export ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST
export ROS_LOG_DIR=/home/inoue-yuki/care_patrol_ws/log/c920_validation
mkdir -p "$ROS_LOG_DIR"

python -m care_patrol.person_stop_guard --ros-args \
  --params-file src/care_patrol/config/person_stop_guard.yaml \
  -p input_mode:=camera \
  -p camera_device:=/dev/v4l/by-id/C920_DEVICE \
  -p allow_direct_camera:=true \
  -p yolo_model:=/absolute/path/to/yolo11n.pt \
  -p confidence:=0.5 \
  -p required_consecutive_frames:=3 \
  -p pause_service:=/care_patrol_test/not_running
```

人物検出後はservice待ちになるので、ログを確認してCtrl-Cで終了する。実機service名を指定しない。

合格条件：

- empty scene 30秒で人物条件が成立しない
- 人物を映す試行3回すべてで `person condition met` が出る
- ログのconfidenceが設定値以上で、3連続フレーム後に1回だけラッチする
- `failed to open`、Camera V7競合、連続read failureがない
- Robot側node/serviceがローカルDomainから見えない

## Stage 2: ROSサービス・Topic確認

目的は通信とinterfaceのREAD-ONLY確認。ガードノードもservice callも使わない。

Robotと開発PCのDomain/RMW/discoveryを合わせ、前述のREAD-ONLYコマンドを実行する。

追加確認：

```bash
ros2 param get /koko_waypoint_follower navigation_mode
ros2 action list -t | grep -E 'navigate_to_pose|navigate_through_poses'
ros2 action info /navigate_to_pose
ros2 action info /navigate_through_poses
ros2 topic info --verbose /cmd_vel
ros2 topic info --verbose /cmd_vel_direct
ros2 param get /controller_server FollowPath.vx_max
ros2 param get /velocity_smoother max_velocity
```

合格条件：

- node、service、topicの完全修飾名と型が期待値どおり
- `/stop`と選択したOdometryのQoSがcompatible
- Odometryが継続更新される
- 実行中Followerが10月25日版と一致
- 開発PCからRobot nodeが安定して見え、30秒間discovery消失がない
- 走行速度上限を確認できる
- `navigation_mode` parameter、Follower起動ログ、対応Actionのactive goalが一致する

両Action Serverがlistに存在するだけでは使用中方式を判定できない。10月25日版の起動scriptは
対話選択式で、未指定時は`to_pose`であるため、実行中parameterを正本とする。

10月25日版の設定は最大前進速度`0.90 m/s`なので、そのままStage 3/4へ進めない。承認済みの一時低速設定または試験用profileにより、実測上限を`0.15 m/s`以下へ制限してから先へ進む。今回その設定変更は行わない。

## Stage 3: 停止機構単体

このStageから実機送信を伴うため、別途明示許可が必要。YOLOは起動しない。最初は車輪浮上架台または同等の安全設備で行い、その後、隔離区画で`0.15 m/s`以下の短い直線走行を使う。

監視端末を先に起動する。

```bash
ros2 topic echo /stop
ros2 topic echo /odom/filtered
ros2 topic echo /cmd_vel
ros2 topic echo /cmd_vel_direct
ros2 topic echo /navigate_to_pose/_action/status
```

NavigateThroughPoses使用時は最後のtopicを `/navigate_through_poses/_action/status` に置き換える。許可取得後に限り、停止担当者が次の1回だけを送る。

```bash
ros2 service call /koko_waypoint_follower/set_paused \
  std_srvs/srv/SetBool '{data: true}'
```

合格条件：

- 応答が`success: true`で、Followerログに`Pause requested`が1回出る
- Nav2 actionがCANCELEDへ遷移し、新しいgoalを送らない
- 約0.5秒のsettle後に`/stop=true`が継続する
- `/cmd_vel_direct`にzero Twistが継続し、mux出力`/cmd_vel`もzeroまたは停止する
- `/odom/filtered`が静止し、3連続サンプルで`|linear| <= 0.03 m/s`かつ`|angular| <= 0.05 rad/s`
- 10秒観察して自動再開しない

`data:false`による再開はこの試験範囲外。解除方法はRobot責任者が別途決める。
また、manual pause中でもcancel解決済みのFollowerをSIGINT/SIGTERMで終了すると、終了処理が
`/stop=false`をpublishする。Follower終了前に物理非常停止等へ安全を引き継ぐこと。
起動時の`/stop=false`はVOLATILE one-shotなので、再起動で解除されたと仮定せず、
物理非常停止下で`/stop`、mux出力、Odometryを実測すること。

## Stage 4: YOLO停止統合

Stage 1〜3の全合格後のみ行う。Camera V7は停止し、C920はガードノードが単独所有する。監視端末と物理非常停止担当者を配置してから、開発PCで起動する。

```bash
cd /home/inoue-yuki/care_patrol_ws
source /opt/ros/jazzy/setup.bash
source .venv/bin/activate
source install/setup.bash
unset ROS_LOCALHOST_ONLY
export ROS_AUTOMATIC_DISCOVERY_RANGE=SUBNET
export ROS_DOMAIN_ID=54  # 当日のRobot実測値
export ROS_LOG_DIR=/home/inoue-yuki/care_patrol_ws/log/real_integration
mkdir -p "$ROS_LOG_DIR"

python -m care_patrol.person_stop_guard --ros-args \
  --params-file src/care_patrol/config/person_stop_guard.yaml \
  -p input_mode:=camera \
  -p camera_device:=/dev/v4l/by-id/C920_DEVICE \
  -p allow_direct_camera:=true \
  -p yolo_model:=/absolute/path/to/yolo11n.pt \
  -p confidence:=0.5 \
  -p required_consecutive_frames:=3 \
  -p pause_service:=/koko_waypoint_follower/set_paused \
  -p stop_confirmation_topic:=/stop \
  -p odometry_topic:=/odom/filtered
```

合格条件：

- ログが `person condition met` → `request_sent` → `request_accepted_unconfirmed` → `stop_confirmed` の順になる
- `request_sent`とFollowerの`Pause requested`が各1回だけ
- `UNCONFIRMED`、service timeout、Camera ownership faultがない
- 試験速度は`0.15 m/s`以下
- 人物条件成立から物理静止まで3.0秒以内、移動距離0.5 m以内という暫定基準を満たす
- 停止後10秒以上、自動再開しない
- 人物をロボット正面の物理障害物として使わず、安全退避線の外から検出させる

時間と距離の基準は初回安全試験用の暫定値であり、実測結果を発表資料へ明記する。

## 安全体制

- 操作者とは別に、ロボットだけを監視する安全監督者を最低1名置く
- 物理非常停止へ即時に手が届く担当者を固定する
- 試験前に非常停止を実際に作動確認する
- 居住者、来訪者、試験関係者以外を区画へ入れない
- 平坦な短い直線区画を使い、段差、階段、エレベーター、扉、ガラス面を避ける
- ロボット前方に十分な停止余裕を取り、人物は走行線上へ立たない
- C920、USBケーブル、開発PC、電源ケーブルをロボットや歩行者の動線から外す
- 走行前にバッテリー、通信、Localization、costmap、laser emergency系を確認する
- 初期速度上限は`0.15 m/s`。10月25日版既定の`0.90 m/s`では試験しない
- node消失、DDS discovery消失、Odometry停止、QoS不一致、`UNCONFIRMED`、カメラ競合が1つでも出たら中止する
- 画像・映像は保存しない。保存が必要な場合は施設と被験者の許可を別途得る

## 確認すべきログ

`person_stop_guard`：

```text
monitoring camera input
person condition met: confidence=..., count=...; requesting pause once
state=request_sent
state=request_accepted_unconfirmed
state=stop_confirmed
```

Waypoint Follower：

```text
Pause requested; resume from waypoint index ...
```

失敗として記録するログ：

```text
pause service did not become available
pause response timed out
pause request rejected
stop remains UNCONFIRMED
Camera V7 process detected
Camera V7 started while direct C920 ownership was active
YOLO inference failed
```

## 想定される問題

| 症状 | 主な原因 | 対応 |
|---|---|---|
| C920をopenできない | device path誤り、Camera V7使用中、USB切断 | by-id、`fuser`、USB接続を再確認。fallbackで別カメラを開かない |
| `ultralytics` import失敗 | `ros2 run`の`/usr/bin/python3`を使用 | 専用venvの`python -m care_patrol.person_stop_guard`で起動 |
| Robot nodeが見えない | Domain/RMW/subnet/multicast不一致 | 両PCの環境変数とnetworkをREAD-ONLY再確認 |
| serviceがない | 古いFollower、別namespace、別overlay | source SHA、package prefix、node info、service listを確認 |
| service成功後も未確認 | `/stop`なし、Odometry topic誤り、QoS不一致、Nav2 cancel失敗 | topic info、Followerログ、action statusを確認。再送しない |
| Odometryが静止判定に入らない | `/odom`選択誤り、センサーノイズ、robotが実際に動いている | `/odom/filtered`を優先し実測。根拠なくしきい値を緩和しない |
| 即時に誤確認される | 古いtopic sampleの混入 | 現版は受付後にVOLATILE subscriptionを作る。古い版を使用しない |
| 停止距離が長い | 速度過大、YOLO処理周期、network遅延、Nav2 cancel遅延 | `0.15 m/s`以下へ戻し、各timestampを分解して原因特定 |
| 停止後に再走行する | 別nodeからresume、Follower再起動、別cmd_vel経路 | 即時非常停止し、service/topic publisherとプロセスを監査 |

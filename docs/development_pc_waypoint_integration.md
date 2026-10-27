# 開発PC 実Waypoint Follower統合検証

## 範囲と隔離条件

2026-10-26に、10月25日版archiveの実 `WaypointFollower` と
`person_stop_guard` を開発PC上で統合した。Robot本番sourceは変更していない。
実Robot、Nav2、DDS subnetへは接続せず、以下の隔離を使用した。

```bash
export ROS_DOMAIN_ID=167
export ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST
```

模擬対象は `NavigateToPose` Action Server、Odometry、固定TFだけである。
`set_paused`、goal ownership、cancel要求、zero Twist、`/stop`停止保持は
SHA256を照合した実Followerコードを使用した。

## 参照元とbuild

参照source：

```text
/home/inoue-yuki/robot_archives/renya_20261025/src
```

`waypoint_follow.py` SHA256：

```text
bdec94385f670b493640397cec2135fbf2bf8e0bb9d46e2f5074f47292ca1a29
```

archiveはREAD-ONLYのまま、開発workspace内の専用overlayへsymlink buildした。
`through_poses`依存の整合確認もできるよう、以下3packageをbuildした。

```bash
cd /home/inoue-yuki/care_patrol_ws
source /opt/ros/jazzy/setup.bash
colcon --log-base log/robot_integration_build build \
  --base-paths /home/inoue-yuki/robot_archives/renya_20261025/src \
  --packages-select \
    robotbase_bt_nodes robotbase_bringup robotbase_waypoint \
  --build-base build/robot_integration \
  --install-base install/robot_integration \
  --symlink-install
```

実Followerの既存testは正しいpackage directoryからcache無効で実行し、
`77 passed`だった。

## 主統合試験

実行コマンド：

```bash
cd /home/inoue-yuki/care_patrol_ws
source /opt/ros/jazzy/setup.bash
source install/robot_integration/setup.bash
source install/setup.bash

CAMERA_SITE=/home/inoue-yuki/camera_project/venv/lib/python3.12/site-packages
export PYTHONPATH="/home/inoue-yuki/care_patrol_ws/src/care_patrol:$CAMERA_SITE:${PYTHONPATH:-}"
export ROS_DOMAIN_ID=167
export ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST
export ROS_LOG_DIR=/home/inoue-yuki/care_patrol_ws/log/waypoint_integration
export CARE_PATROL_REAL_WAYPOINT_INTEGRATION=1
export CARE_PATROL_TEST_MODEL=/home/inoue-yuki/camera_project/yolo11n.pt
export CARE_PATROL_TEST_VIDEO=/home/inoue-yuki/camera_project/videos/2men_change.mp4

python3 -m pytest -s -vv \
  src/care_patrol/test/test_real_waypoint_integration.py
```

Camera projectのsite-packagesはREAD-ONLYで参照した。Camera V7プロセスやC920は
起動していない。正式なNUC環境では卒研専用venvを使用する。

## 検証結果

| 項目 | 結果 |
|---|---|
| 実Followerが模擬Nav2へgoal送信 | PASS、`NavigateToPose` goal 1件 |
| YOLO11n person検出 | PASS、person 2名、最大confidence 0.888 |
| 連続検出 | PASS、3フレーム |
| 実 `set_paused(true)` | PASS、service eventで要求 `[True]` 1件 |
| Nav2 cancel | PASS、要求1件、CANCELED result 1件 |
| Follower safety zero | PASS、観測したTwistはすべてzero |
| Follower `/stop=true` | PASS、0.5秒settle後に継続 |
| 静止Odometry | PASS、模擬Odometryの静止3サンプル |
| 自動再開しない | PASS、停止後1秒とguard終了後を観測。`data=False`なし、追加goalなし、Followerはmanual pause維持 |
| 人物なし | PASS、blank動画20フレームで要求なし |
| service不在 | PASS、`SERVICE_UNAVAILABLE`、送信なし |
| service明示拒否 | PASS、`REQUEST_REJECTED`、要求1件のまま再送・停止確認なし |
| service応答timeout | PASS、`REQUEST_OUTCOME_UNKNOWN`、要求1件のまま再送なし |
| cancel拒否 | PASS、安全判定として `REQUEST_ACCEPTED_UNCONFIRMED` を維持 |
| 古い `/stop`・Odometry | PASS、受付前サンプルは停止確認に不使用 |
| 停止観測が片側だけ | PASS、`/stop`だけ、静止Odometryだけのどちらも停止確認しない |
| カメラ未接続 | PASS、`failed to open camera input`を例外として明示 |

「cancel拒否」のPASSは停止成功を意味しない。実Followerがcancelを要求したものの
模擬Action Serverが拒否し、Odometryを走行中のままにしたとき、guardが
`STOP_CONFIRMED`へ遷移しなかったという安全側判定のPASSである。

## 必須試験監査

既存試験を正常系、検出gate、service transport、停止観測、Follower cancel、lifecycleの
境界で再確認した。監査時に不足していたserviceの`success=false`と、静止Odometryだけで
`/stop=true`がない場合を追加し、どちらも安全側でPASSした。`to_pose`を使う開発PC統合の
必須試験に未実施は残っていない。

ただし実機が`through_poses`を選択している場合、そのAction transportを使ったROS統合は
未実施である。archiveの既存unit testと依存buildはPASSしており、pause/cancel処理は共通
だが、実機READ-ONLY確認で`through_poses`と判明した時点では「完全な同一mode統合」は
PARTIALとして扱う。実機物理停止、C920、DDS、muxは開発PCで代替PASSにしない。

## 保存動画の実測

最終全3件runの値は以下。実行ごとにCPU負荷で変動するため、テストログも保存する。

| 指標 | 実測値 |
|---|---|
| 動画metadata | 29.739 FPS、786 frames、約26.43秒 |
| guard設定再生周期 | 10.0 FPS |
| 人物確定までの処理frame | 3 |
| YOLO平均推論時間 | 93.539 ms/frame |
| 推論単体換算 | 10.691 FPS |
| 人物確定までの実効処理速度 | 7.390 FPS |
| guard起動からperson確定 | 0.406秒 |
| 最初のpositive frameの推論開始から3連続成立 | 0.287秒 |
| 最初のpositive推論結果から3連続成立 | 0.117秒 |
| 最初のpositive frameの推論開始からservice送信 | 0.288秒 |
| person確定からservice送信 | 1.254 ms |
| service送信から模擬停止確認 | 0.582秒 |
| 最初のpositive frameの推論開始から模擬停止確認 | 0.869秒 |
| guard起動から模擬停止確認 | 0.989秒 |
| blank動画20フレーム平均推論 | 45.095 ms/frame、person 0件、service要求0件 |

ここで「最初のpositive frame」は、YOLOが初めてpersonを返したフレームの
推論開始時刻であり、映像上に人物が現れた真の時刻ではない。保存動画はmetadataの
29.739 FPSでは再生せず、guardの`processing_rate_hz=10.0`により1 timer tickごとに
次のframeを読む。timer callback内で同期推論するため10 FPSは上限目標であり、推論や
executor遅延が100 msを超えれば実効FPSは下がる。保存動画はframeを順番に処理するので、
live cameraのbuffer、frame age、dropを含む実時間挙動も再現しない。

従って、この0.869秒を実機停止時間または停止距離として使用してはならない。速度を
`v`、実機で人物が検出可能になってから物理静止までの実測時間を`t`としたとき、単純な
走行距離の一次近似は`v * t`に過ぎない。実際にはcamera露光・USB buffer、YOLO周期、
3連続判定、DDS/service、Nav2 cancel、controllerとmotorの制動距離を実機で別々に測る。
参考として0.15 m/sと今回の0.869秒を単純に掛けると約0.130 mだが、模擬停止確認値で
あり、物理停止距離の予測値・合格値ではない。

## Follower main終了処理

実際のinstalled entry pointをsubprocessとして起動し、manual pause成立後にPOSIX signalを
送った。試行ごとにDomain 181〜183、`LOCALHOST`、模擬NavigateToPoseだけを使用した。

| 条件 | 結果 |
|---|---|
| main起動・初回goal送信 | PASS、3試行とも模擬NavigateToPoseへgoal 1件 |
| 起動時の`/stop=false`外部観測 | PARTIAL、source上の1回送信を確認したが、VOLATILE one-shotは3試行ともDDS discovery前でobserverに未到達 |
| cancel解決済み＋SIGINT | PASS、終了code 130、signalから終了0.904秒、終了時`/stop: true -> false` |
| cancel解決済み＋SIGTERM | PASS、終了code 143、signalから終了0.892秒、終了時`/stop: true -> false` |
| cancel拒否・goal未解決＋SIGTERM | PASS（安全側異常系）、終了code 2、1.896秒、`/stop=true`のみ、`Shutdown unconfirmed` |

manual pause中でもgoal解決済みなら`release_stop()`はzero Twistを送った後に
`/stop=false`をpublishして終了する。このため、Follower processの停止を停止保持手段に
してはならない。manual pause中にFollowerを終了・再起動する必要がある場合は、先に物理
非常停止等へ安全を引き継ぎ、Robot責任者が解除手順を管理する。goal未解決時は
`/stop=true`を維持するが、process終了後のtopic値だけに安全を依存してはならない。

起動時もconstructorは`/stop=false`を1回送るが、publisherはVOLATILEで再送しない。
今回の別process observerでは3試行とも初回sampleを受信できなかった。従って再起動を
「必ずstop lockを解除する操作」としても扱えない。再起動後は`/stop`の実状態、mux出力、
Odometryを確認し、意図しないgoal送信に備えて物理非常停止を維持する。

## 未検証・主張しない項目

- 実Nav2、実costmap、実motor controllerによる物理停止
- C920実映像とUSB帯域
- 実機network上のservice latency
- 実機muxでのFollower起動時one-shot `/stop=false`受信挙動
- `NavigateThroughPoses`のROS通信統合（既存unit testと依存buildはPASS）

したがって「YOLOから実Followerの停止コードまでの開発PC統合」は実装済みだが、
「実機が物理停止した」とは表現しない。

# care_patrol 実機NUC移植手順

## 移植対象

実機へ移すruntime資産は卒研packageとmodelだけとする。

```text
inoueyuki_ws/care_patrol_overlay/repo/src/care_patrol/
inoueyuki_ws/care_patrol_overlay/runtime/models/yolo11n.pt
```

開発用の `build/robot_integration`、`install/robot_integration`、模擬Nav2、保存動画は
移植しない。実機の `robotbase_waypoint`、Nav2、sensor設定を変更・上書きしない。

## 必要条件

- Ubuntu 24.04 / ROS 2 Jazzy / Python 3.12
- 実機の10月25日版 `robotbase_waypoint`
- `rclpy`, `nav_msgs`, `std_msgs`, `std_srvs`
- system OpenCVとNumPy
- 卒研専用venvの `ultralytics==8.4.47`
- ローカル配置した `yolo11n.pt`
- C920の固定 `/dev/v4l/by-id/...-video-index...` capture path

ROS依存は `package.xml`、pip依存は `requirements.txt` を正とする。

移植前の照合値：

| 対象 | SHA256 |
|---|---|
| `care_patrol/person_stop_guard.py` | `222e53104d7ebd1c2bd212ea68f310dcc1f4f012ad950f78e2ced1f6972cc47b` |
| `config/person_stop_guard_nuc.yaml` | `8d2bc4851f42ce1bad8323072313b286cf2ec211619b1c7699e7243d9a8aa05a` |
| `requirements.txt` | `ae7b7108037f6d364ee22f5273bdcd320180add204f6e5b6160f06b5914c63b8` |
| `yolo11n.pt` | `0ebbc80d4a7680d14987a577cd21342b65ecfd94632bd9a8da63ae6417644ee1` |

`build/`、`install/`、`log/`、`.pytest_cache`、`__pycache__`は移植物に含めない。

## NUC上のworkspace構築

配置方針は、既存`/home/koko-chan-nuc/inoueyuki_ws`内へGitHubから取得する方式へ変更した。
既存colcon探索への混入を防ぐため、直下へsource packageを置かず、次の隔離subtreeを
使用する。

```text
~/inoueyuki_ws/care_patrol_overlay/
├── COLCON_IGNORE
├── repo/       # care-patrol-thesis、検証済みcommitをdetached checkout
└── runtime/    # 専用build/install/log/venv/models
```

正確な配置・clone・build手順と2026-10-27の接続結果は
[nuc_inoueyuki_ws_deployment.md](nuc_inoueyuki_ws_deployment.md)を正本とする。
現在の実機構成がREAD-ONLY確認できない場合はcloneしない。

build時は`/opt/ros/jazzy`だけをunderlayにし、`repo/src`を`--base-paths`で明示する。
runtime時は `/opt/ros/jazzy` → 既存Robot workspace →
`~/inoueyuki_ws/care_patrol_overlay/runtime/install`の順にsourceする。Robot workspaceと
既存`inoueyuki_ws`のbuild/installへ卒研packageを混ぜない。

ネット接続を許可しない場合は、Ultralyticsと依存wheelを事前にwheelhouseへ用意し、
`pip install --no-index --find-links /path/to/wheelhouse -r ...`を使用する。

## 設定

安全側templateは `config/person_stop_guard_nuc.yaml`。次を実測値へ置換する。

- `camera_device`: C920のcapture-capable by-id path
- `yolo_model`: NUC上の `yolo11n.pt` 絶対パス
- `ROS_DOMAIN_ID`: 当日の実機 `robot.env`

templateの `allow_direct_camera` は意図的に `false`。Camera V7停止、device所有者不在、
安全監督者配置を確認した試験時だけCLIで `true` を上書きする。

## READ-ONLY起動前確認

```bash
source /opt/ros/jazzy/setup.bash
source ~/robotbase_ws_renya/bash/activate_koko_env.sh
source ~/robotbase_ws_renya/install/setup.bash
source ~/inoueyuki_ws/care_patrol_overlay/runtime/venv/bin/activate
source ~/inoueyuki_ws/care_patrol_overlay/runtime/install/setup.bash

printf 'ROS_DOMAIN_ID=%s\n' "$ROS_DOMAIN_ID"
ros2 service type /koko_waypoint_follower/set_paused
ros2 topic info --verbose /stop
ros2 topic info --verbose /odom/filtered
fuser -v /dev/v4l/by-id/C920_CAPTURE_DEVICE
```

実機service callはこの確認には含めない。

## 実行中Followerのnavigation mode確認（READ-ONLY）

10月25日archiveは起動時に利用者が方式を選択し、未指定時は`to_pose`である。そのため
archiveだけから実機の選択結果を断定しない。起動中nodeのparameterを正本とする。

```bash
ros2 param get /koko_waypoint_follower navigation_mode
ros2 node info /koko_waypoint_follower
ros2 action list -t | grep -E '/navigate_(to_pose|through_poses)'
ros2 action info /navigate_to_pose
ros2 action info /navigate_through_poses
ps -eo pid,args | grep '[w]aypoint_follow'
```

`navigation_mode`が`to_pose`なら監視対象は`/navigate_to_pose/_action/status`、
`through_poses`なら`/navigate_through_poses/_action/status`である。両Action Serverが存在する
だけでは使用中方式を判定できない。parameter値、Follower起動ログの
`navigation_mode=...`、対応Actionのactive goalを一致させて合格とする。parameterを
setする操作やlaunch変更はこの確認に含めない。

## 将来許可後の起動形式

venvのUltralyticsを確実に使うため `ros2 run`ではなく `python -m`を使う。

```bash
cd ~/inoueyuki_ws/care_patrol_overlay/repo
python -m care_patrol.person_stop_guard --ros-args \
  --params-file ../runtime/install/care_patrol/share/care_patrol/config/person_stop_guard_nuc.yaml \
  -p camera_device:=/dev/v4l/by-id/C920_CAPTURE_DEVICE \
  -p yolo_model:=/home/koko-chan-nuc/inoueyuki_ws/care_patrol_overlay/runtime/models/yolo11n.pt \
  -p allow_direct_camera:=true
```

起動前にservice/topic/QoS、Camera V7停止、C920所有権、非常停止担当、低速設定を
再確認する。guardは `data=False`を送らない。終了後の再開判断は既存Robot運用側の
責任とし、このpackageへ自動再開を追加しない。

manual pause中でも、goal cancel解決済みのFollowerへSIGINT/SIGTERMを送ると終了処理が
`/stop=false`をpublishすることを開発PCで確認済みである。Followerを終了・再起動する
前に物理非常停止等へ安全を引き継ぐ。guard終了だけでは`data=False`を送らないが、
Follower終了とは挙動が異なる。また、Follower起動時の`/stop=false`はVOLATILEな
one-shotで、開発PCの別process observerではDDS discovery前となり受信できなかった。
Follower再起動でlock解除済みと仮定せず、`/stop`、mux出力、Odometryを実測する。

## 移植後の受入順序

1. importと保存動画のみ。実機service名を使わない。
2. C920単体＋模擬service。Robot Domainから隔離する。
3. 実機Domainでnode/service/topicをREAD-ONLY確認する。
4. 別途明示許可後に停止機構単体試験を行う。
5. 1〜4合格後にYOLO停止統合を行う。

物理停止、Nav2 cancel、停止距離は開発PCの模擬結果からPASS扱いにしない。

移植直前・移植後の判定項目は
[pre_nuc_final_checklist.md](pre_nuc_final_checklist.md)を使用する。

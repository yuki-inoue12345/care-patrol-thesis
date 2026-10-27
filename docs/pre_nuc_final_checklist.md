# 実機NUC移植前・移植後チェックリスト

判定は `PASS / PARTIAL / BLOCKED` で記録する。未実施をPASSにしない。
実機送信・走行を伴う項目は、別途許可されるまで `BLOCKED（未許可）` とする。

## A. GitHubから取得する前

- [ ] Private repository `yuki-inoue12345/care-patrol-thesis`へ認証できる。
- [ ] `~/inoueyuki_ws/care_patrol_overlay`が存在せず、既存Git・colcon構成と衝突しない。
- [ ] `care_patrol_overlay/COLCON_IGNORE`で既存colcon探索から隔離する。
- [ ] repositoryを`care_patrol_overlay/repo`へcloneする。
- [ ] detached HEADが
  `d9d06a574948ffa2014c196b9b15f15523da86f3`と完全一致する。
- [ ] `.pytest_cache`、`__pycache__`、`build/`、`install/`、`log/`を除外する。
- [ ] `person_stop_guard.py` SHA256が
  `222e53104d7ebd1c2bd212ea68f310dcc1f4f012ad950f78e2ced1f6972cc47b`。
- [ ] `person_stop_guard_nuc.yaml` SHA256が
  `8d2bc4851f42ce1bad8323072313b286cf2ec211619b1c7699e7243d9a8aa05a`。
- [ ] `requirements.txt` SHA256が
  `ae7b7108037f6d364ee22f5273bdcd320180add204f6e5b6160f06b5914c63b8`。
- [ ] `yolo11n.pt` SHA256が
  `0ebbc80d4a7680d14987a577cd21342b65ecfd94632bd9a8da63ae6417644ee1`。
- [ ] configの`camera_device`と`yolo_model`がplaceholderのままであることを把握し、
  NUC上の実測値へ置換する計画がある。
- [ ] Robot/Camera/Nav2本番sourceや設定を移植物へ含めていない。

## B. NUCの隔離準備

- [ ] Ubuntu 24.04、ROS 2 Jazzy、Python 3.12を確認した。
- [ ] build時は既存Robot overlayをsourceせず、`/opt/ros/jazzy`だけをunderlayにした。
- [ ] runtime時の`/opt/ros/jazzy` → Robot overlay →
  `~/inoueyuki_ws/care_patrol_overlay/runtime/install`のsource順を確認した。
- [ ] 卒研専用`care_patrol_overlay/runtime/venv`を`--system-site-packages`で作成した。
- [ ] `requirements.txt`のUltralytics 8.4.47と、NUCに適したPyTorch backendを導入した。
- [ ] `rclpy`、`cv2`、`numpy`、`torch`、`ultralytics`を同じvenvのPythonからimportできた。
- [ ] modelをローカル配置し、推論時にnetwork downloadが発生しない。
- [ ] `colcon build --packages-select care_patrol`が成功した。
- [ ] 保存動画＋模擬SetBoolをRobotと異なるDomainかつ`LOCALHOST`でPASSした。

## C. C920単体

- [ ] `lsusb`と`v4l2-ctl --list-devices`でC920を識別した。
- [ ] capture可能な`/dev/v4l/by-id/...-video-index...`を実測で確定した。
- [ ] Camera V7と他の所有者が停止し、`fuser -v DEVICE`が空である。
- [ ] Robotと異なる`ROS_DOMAIN_ID`、`ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST`を使用した。
- [ ] 模擬serviceだけを指定し、人物3試行を検出、empty scene 30秒で誤要求なし。
- [ ] 実効FPS、confidence、3連続成立時間をNUC上で再計測した。

## D. 実機ROSのREAD-ONLY確認

- [ ] `robot.env`の`ROS_DOMAIN_ID`、RMW、discovery範囲を確認した。
- [ ] 実行中Follower source SHA256が
  `bdec94385f670b493640397cec2135fbf2bf8e0bb9d46e2f5074f47292ca1a29`。
- [ ] package prefixとPython import元が意図した10月25日版workspaceを指す。
- [ ] `/koko_waypoint_follower/set_paused`が`std_srvs/srv/SetBool`。
- [ ] `/stop`と`/odom/filtered`の型、publisher、QoS、更新を確認した。
- [ ] `ros2 param get /koko_waypoint_follower navigation_mode`の値を記録した。
- [ ] parameter、起動ログ、active Actionが`to_pose`または`through_poses`で一致した。
- [ ] 速度上限を確認し、0.15 m/s以下の承認済み試験設定を準備した。

## E. 送信前の安全ゲート

- [ ] 実機service callと走行の明示許可を得た。
- [ ] 操作者、安全監督者、物理非常停止担当者を分けて配置した。
- [ ] 非常停止の作動を事前確認し、平坦な隔離区画と十分な停止余裕を確保した。
- [ ] 最初は車輪浮上架台等で停止機構単体を確認する。
- [ ] `/stop`、Odometry、`/cmd_vel`、`/cmd_vel_direct`、Action statusを同時記録する。
- [ ] 人物は走行線上に立たず、安全退避線外から検出させる。
- [ ] Camera V7を停止し、C920をguardだけが所有する。

## F. 中止条件と終了手順

- [ ] service timeout・拒否・結果不明時に要求を再送しない。
- [ ] `/stop=true`と静止Odometryの両方が揃わなければ`UNCONFIRMED`として中止する。
- [ ] DDS消失、Odometry停止、QoS不一致、camera競合、推論faultで直ちに中止する。
- [ ] guard終了が自動resumeを送らないことを理解している。
- [ ] manual pause中のFollower終了は、cancel解決済みなら`/stop=false`をpublishすることを
  理解している。Followerを終了・再起動する前に物理非常停止等へ安全を引き継ぐ。
- [ ] Follower起動時の`/stop=false`はVOLATILE one-shotで受信保証を確認できていない。
  再起動をlock解除手順にせず、物理非常停止を維持してtopic・mux・Odometryを確認する。
- [ ] `data=false`による再開は今回の試験手順に含めず、Robot責任者が別管理する。

## 最終判定欄

| 区分 | 判定 | 根拠・ログ |
|---|---|---|
| 開発PC統合 | PASS | 実Follower統合3件、main終了3件、package test結果を記入 |
| NUC環境・build |  |  |
| NUC C920単体 |  |  |
| 実機ROS READ-ONLY |  |  |
| 停止機構単体 | BLOCKED（未許可） |  |
| YOLO停止統合 | BLOCKED（未許可） |  |

# 流用元ソースマニフェスト

## 記録条件

- 記録日: 2026-10-26
- 用途: 完了済みREAD-ONLY監査で確認した流用候補の同一性固定
- 方式: 各パスの内容に対するSHA256
- 注意: 参照元はこのワークスペースへコピーしていない。特に `/home/inoue-yuki/camera_project` は既存dirty worktreeであり、今回一切変更していない。将来参照するときはパスだけでなくSHA256も照合する。

## ファイル一覧

| ID | 絶対パス | SHA256 | 主な利用候補 |
|---|---|---|---|
| WP-01 | `/home/inoue-yuki/robot_archives/renya_20261025/src/robotbase_waypoint/robotbase_waypoint/waypoint_follow.py` | `bdec94385f670b493640397cec2135fbf2bf8e0bb9d46e2f5074f47292ca1a29` | `koko_waypoint_follower`、`~/set_paused`、manual pauseと停止維持 |
| WP-02 | `/home/inoue-yuki/robot_archives/renya_20261025/src/robotbase_waypoint/package.xml` | `9372bb5952514a37d3564f6886dc404dda8fb112c286eea0aec236cfb99440d3` | service型と既存ROS依存の確認 |
| CV7-01 | `/home/inoue-yuki/camera_v7_FINAL_20261008_154827/daikoudai_v7.py.new` | `7f78342096a4a0d662f2e059c65d1bc39676a2efdc76e84c41de537706c4ed97` | Camera V7統合、capture server、カメラ共有の呼び出し側 |
| CV7-02 | `/home/inoue-yuki/camera_v7_FINAL_20261008_154827/person_search_vlm_milestone2_v7.py` | `6258c207af24399b590f6829ead31aed78c4349a7ddc359664ea8bef4c370152` | C920候補探索・open/read確認、V7探索処理 |
| CV7-03 | `/home/inoue-yuki/camera_project/person_search_vlm_v3_9_7_final_wait_reduce_v3.py` | `e9c4315ad6fe8c8b9cb8e849018f7310a628d16c9e70b8037a298b3d4d4d06dd` | `ultralytics.YOLO`、person class 0限定の検出・tracking |
| CV7-04 | `/home/inoue-yuki/camera_project/camera_video_recording.py` | `285951d3942664535a52e13d146eb83b0b9760aee382af9b97475965b45f9ec5` | `CameraOwnershipCoordinator`、単一カメラ所有権とrelease確認 |

`CV7-01` と `CV7-02` のSHA256は同梱の `SHA256SUMS.txt` / `MANIFEST.txt` と実ファイルの双方で一致を確認した。`CV7-03` と `CV7-04` は配布物に含まれないローカル依存元なので、記録日時点の実ファイルからSHA256を採取した。

## 依存関係

| 対象 | 直接依存 | 今回の扱い |
|---|---|---|
| WP-01 | `rclpy`、`std_srvs`、`std_msgs`、`geometry_msgs`、`nav2_msgs`、`action_msgs`、`tf2_ros_py`、`visualization_msgs`、`ament_index_python`、`robotbase_bringup`、PyYAML | productionでは既存環境のまま利用。開発PC統合試験ではarchive sourceを変更せず専用overlayへbuild |
| CV7-01 | Python標準ライブラリ、Tk、ROS 2 Python、OpenCV、CV7-02、CV7-04 | UI・撮影・ROS topic処理はコピーしない |
| CV7-02 | OpenCV (`cv2`)、NumPy、CV7-03、CV7-04、LM Studio向け処理 | カメラopen/readの考え方だけ参照。VLM依存は除外 |
| CV7-03 | `ultralytics`、OpenCV、NumPy、Python標準ライブラリ | YOLOのperson class 0検出だけを最小移植。Gemma/HTTP処理は除外 |
| CV7-04 | Python標準ライブラリ、呼び出し側から渡すOpenCV capture | 1 capture ownerとrelease確認の原則を参照。全録画機能は除外 |

## 新規パッケージの依存境界

現在の `care_patrol` は `rclpy`、`std_srvs`、`std_msgs`、`nav_msgs`、OpenCV、NumPyをROS package依存に記録している。UltralyticsはROS配布パッケージではないため `requirements.txt` に分離し、ノードを起動するPython環境へ明示的に導入する。開発PC専用の実Follower統合試験は追加で `geometry_msgs`、`nav2_msgs`、`service_msgs`、`tf2_ros_py` をtest依存に使用する。これらはguard本体のruntime依存ではない。

参照元はruntimeで絶対パスimportしない。実装では上記のSHA256を照合したうえで、person class 0の抽出と単一カメラ所有権という最小原則だけを `care_patrol` 内へ移した。Camera V7のtracking、VLM、録画、UIはコピーしていない。

## 再照合コマンド

```bash
sha256sum \
  /home/inoue-yuki/robot_archives/renya_20261025/src/robotbase_waypoint/robotbase_waypoint/waypoint_follow.py \
  /home/inoue-yuki/robot_archives/renya_20261025/src/robotbase_waypoint/package.xml \
  /home/inoue-yuki/camera_v7_FINAL_20261008_154827/daikoudai_v7.py.new \
  /home/inoue-yuki/camera_v7_FINAL_20261008_154827/person_search_vlm_milestone2_v7.py \
  /home/inoue-yuki/camera_project/person_search_vlm_v3_9_7_final_wait_reduce_v3.py \
  /home/inoue-yuki/camera_project/camera_video_recording.py
```

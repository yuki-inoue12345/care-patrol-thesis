# care-patrol-thesis

介護施設向け卒業研究として、既存Waypoint自律走行中にYOLOで人物を検出し、既存
`/koko_waypoint_follower/set_paused`へ停止要求を1回送るROS 2 Jazzy packageです。

開発PCでは、10月25日版の実Waypoint Followerと模擬Nav2を使い、保存動画から
`person検出 → set_paused(true) → Nav2 cancel → /stop保持`まで統合確認済みです。
物理停止、C920、実機network、停止距離は実機での確認が必要です。

## Repository範囲

```text
src/care_patrol/   卒研専用ROS 2 package、config、tests
docs/              設計、検証結果、実機・NUC移植手順
CHECKPOINT.md       現在の進捗と再開位置
```

既存Robot、Camera V7、Nav2本番sourceは含めません。実機ではこのrepositoryを
`~/inoueyuki_ws/care_patrol_overlay/repo`へcloneし、`COLCON_IGNORE`付きの親directoryと
専用build/install/logで既存環境から隔離します。既存Robot workspaceへ直接
`git pull`しないでください。

## Buildと通常test

```bash
source /opt/ros/jazzy/setup.bash
colcon build --packages-select care_patrol
source install/setup.bash
colcon test --packages-select care_patrol
colcon test-result --verbose
```

UltralyticsはROS entry pointと同じPython環境へ導入します。

```bash
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
python -m pip install -r src/care_patrol/requirements.txt
```

## 安全条件

- serviceの`success=true`は要求受付であり、物理停止完了ではありません。
- `/stop=true`と静止Odometryの両方を新しい観測として確認して初めて停止確認とします。
- service timeout・拒否・結果不明時は再送しません。
- guardは`data=false`を送らず、自動再開しません。
- manual pause中のFollower終了は、cancel解決済みの場合`/stop=false`を送ります。
  Followerを停止・再起動する前に物理非常停止等へ安全を引き継いでください。
- 実機service callと走行には別途明示許可が必要です。

実機移植は[NUC移植手順](docs/nuc_migration.md)と
[最終チェックリスト](docs/pre_nuc_final_checklist.md)を使用してください。
`inoueyuki_ws`へのGitHub cloneは
[専用配置手順](docs/nuc_inoueyuki_ws_deployment.md)を正本とします。

## Model管理

`yolo11n.pt`はrepositoryへ含めません。検証済みartifactのSHA256は
`0ebbc80d4a7680d14987a577cd21342b65ecfd94632bd9a8da63ae6417644ee1`です。
モデルは配布条件を確認した別管理artifactとしてNUCへ配置し、起動前にhashを照合します。

## License・由来

卒研packageは[Apache License 2.0](LICENSE)です。流用元とSHA256は
[source_manifest.md](docs/source_manifest.md)、Git管理方針は
[git_repository_policy.md](docs/git_repository_policy.md)に記録しています。

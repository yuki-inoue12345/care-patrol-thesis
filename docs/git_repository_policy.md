# Git repository管理方針

## Repository境界

- repository: `care-patrol-thesis`
- Private remote: `https://github.com/yuki-inoue12345/care-patrol-thesis`
- 管理対象: `src/care_patrol/`、`docs/`、README、CHECKPOINT
- 管理対象外: Robot/Camera本番source、build/install/log、venv、cache、録画、model
- 実機配置: `~/inoueyuki_ws/care_patrol_overlay/repo`。親に`COLCON_IGNORE`を置き、
  `runtime/build`、`runtime/install`、`runtime/log`へ分離

Robot archiveとCamera資産はREAD-ONLY参照元であり、このrepositoryへコピーしません。
実機NUCではtest済みcommitまたはtagを指定してcheckoutし、既存
`~/robotbase_ws_renya`をGit操作で上書きしません。

## Artifact方針

| Artifact | Git | 理由 |
|---|---|---|
| `yolo11n.pt` | 含めない | 再配布条件の確認が必要。hash固定で別管理 |
| 保存動画・C920録画 | 含めない | 容量と個人情報 |
| ROS bag | 含めない | 容量と施設・走行情報 |
| build/install/log | 含めない | 再生成可能 |
| venv/cache | 含めない | 環境依存・再生成可能 |

Git LFSは開発PCへ未導入です。現在のrepositoryにはLFSが必要なbinaryを入れません。

## Branch・実機運用

1. 開発PCで変更・testを行う。
2. test結果と実機未確認事項をdocs/CHECKPOINTへ記録する。
3. commit SHAまたはannotated tagを固定する。
4. 実機では独立workspaceへ取得する。
5. build、保存動画＋模擬service、C920単体を通してから実機ROSへ接続する。
6. Robot本番workspaceで直接`git pull`しない。

Private設定を維持し、Public化はRobot route、施設情報、第三者コード、モデル配布条件を
再監査するまで行いません。

現在の実行環境ではworkspace直下の空`.git`がREAD-ONLYのため、Git checkoutは
`local_repositories/care-patrol-thesis`へ分離しています。通常環境へ移す際はPrivate
remoteを新しい独立directoryへcloneし、rootとcheckoutの二重編集を避けてください。

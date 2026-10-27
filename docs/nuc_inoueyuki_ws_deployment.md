# `inoueyuki_ws`へのGitHub経由配置

確認日: 2026-10-27

## 今回の判定

**BLOCKED（実機NUCへ到達できないため未配置）**

同日の再試行ではmDNSが`koko-chan-nuc-NUC11PAHi5.local`を`172.22.25.94`へ
解決した。開発PCのWi-Fiは`172.22.23.178/18`で同一subnet routeを持つが、neighbor
解決は`FAILED`、TCP/22とSSHは`No route to host`だった。過去IP`172.22.15.125`も
同じ結果だった。これはGitHub認証前のnetwork到達性blockerである。

確認できた接続候補は次のとおり。

- user: `koko-chan-nuc`
- host: `koko-chan-nuc-NUC11PAHi5` / `koko-chan-nuc-NUC11PAHi5.local`
- 過去IP候補: `172.22.15.125`

初回はhost名を解決できず、再試行では解決した現在IPにも到達できなかった。したがって、
実機のdirectory、Git、colcon、overlay、空き容量、権限、GitHub認証を現在値として
確認できていない。安全条件に従い、clone、directory作成、package install、buildは
行っていない。

2026-10-13の保存logからは、当時のCamera V7が
`/home/koko-chan-nuc/inoueyuki_ws/daikoudai_v7.py`、Pythonが
`/home/koko-chan-nuc/venvs/person_search/bin/python`だったことだけを確認できる。これは
過去の記録であり、現在の実機構成を保証しない。

## 配置前READ-ONLY確認

再接続後、最初に次だけを実行する。ROS node、service、cameraは起動・停止しない。

```bash
ssh -o BatchMode=yes -o ConnectTimeout=5 \
  koko-chan-nuc@koko-chan-nuc-NUC11PAHi5.local

hostname
id
printf 'HOME=%s\n' "$HOME"
test -d "$HOME/inoueyuki_ws" && realpath "$HOME/inoueyuki_ws"
find "$HOME/inoueyuki_ws" -maxdepth 3 -mindepth 1 \
  \( -name package.xml -o -name COLCON_IGNORE -o -name AMENT_IGNORE \
     -o -name .git -o -name setup.bash \) -print
find "$HOME/inoueyuki_ws" -maxdepth 5 -type f -name package.xml -print
find "$HOME/inoueyuki_ws" -maxdepth 5 -iname '*care_patrol*' -print
git -C "$HOME/inoueyuki_ws" status --short --branch 2>/dev/null || true
df -h "$HOME/inoueyuki_ws"
findmnt -T "$HOME/inoueyuki_ws"
ps -eo pid,args | grep -E '[d]aikoudai_v7|[w]aypoint_follow|[p]erson_stop_guard'
```

続いて、環境を変更せずversionと利用可能性を確認する。

```bash
test -r /opt/ros/jazzy/setup.bash
python3 --version
command -v colcon
colcon --help >/dev/null
git --version
gh auth status -h github.com
gh api \
  repos/yuki-inoue12345/care-patrol-thesis/commits/d9d06a574948ffa2014c196b9b15f15523da86f3 \
  --jq .sha
```

`inoueyuki_ws`自身がGit管理され、下記の新規directoryがuntrackedとして既存運用へ影響
する場合、同workspace内への配置を中止する。既存package名`care_patrol`、同名path、
既存overlay規約、書込み権限のいずれかに衝突があっても配置しない。

## 安全な配置案

全READ-ONLY確認が合格した場合だけ、次の新規subtreeを使用する。

```text
/home/koko-chan-nuc/inoueyuki_ws/care_patrol_overlay/
├── COLCON_IGNORE
├── repo/                    care-patrol-thesis Git checkout
└── runtime/
    ├── build/
    ├── install/
    ├── log/
    ├── ros_log/
    ├── venv/
    └── models/
```

`COLCON_IGNORE`により、既存`inoueyuki_ws`をrootとした通常のcolcon探索へ卒研packageを
混入させない。卒研buildは`repo/src`を明示し、build/install/logをすべて新規subtreeへ
限定する。既存のsource、build、install、venvを上書きしない。

## Cloneとcommit固定

以下は配置前確認に合格した後だけ実行する。

```bash
set -eu
overlay="$HOME/inoueyuki_ws/care_patrol_overlay"
repo="$overlay/repo"

test -d "$HOME/inoueyuki_ws"
test ! -e "$overlay"
mkdir -p "$overlay/runtime"
touch "$overlay/COLCON_IGNORE"

gh repo clone yuki-inoue12345/care-patrol-thesis "$repo"
git -C "$repo" checkout --detach \
  d9d06a574948ffa2014c196b9b15f15523da86f3
test "$(git -C "$repo" rev-parse HEAD)" = \
  d9d06a574948ffa2014c196b9b15f15523da86f3
git -C "$repo" status --short --branch
```

期待値はdetached HEAD、追跡差分なし。clone失敗やSHA不一致時はbuildへ進まない。

## 依存関係とmodelのREAD-ONLY確認

既存Camera V7 venvへpip installしない。まず利用可能な環境を読むだけにする。

```bash
for python_bin in \
  "$HOME/venvs/person_search/bin/python" \
  /usr/bin/python3; do
  if test -x "$python_bin"; then
    "$python_bin" -c \
      'import sys; print(sys.executable, sys.version); import rclpy, cv2, numpy; print("ROS imports OK")'
    "$python_bin" -c \
      'import torch, ultralytics; print("torch", torch.__version__, "ultralytics", ultralytics.__version__)' \
      || true
  fi
done

find "$HOME/inoueyuki_ws" -maxdepth 4 -type f -name yolo11n.pt -print
```

既存modelがあれば、使用前に次と一致することを確認する。

```text
0ebbc80d4a7680d14987a577cd21342b65ecfd94632bd9a8da63ae6417644ee1
```

不一致modelを置換しない。専用modelを追加する場合は
`care_patrol_overlay/runtime/models/yolo11n.pt`へ置き、別管理元から取得してhashを照合する。

専用venvが必要な場合だけ、既存venvとは別に作成する。

```bash
runtime="$HOME/inoueyuki_ws/care_patrol_overlay/runtime"
python3 -m venv --system-site-packages "$runtime/venv"
"$runtime/venv/bin/python" -m pip install \
  -r "$HOME/inoueyuki_ws/care_patrol_overlay/repo/src/care_patrol/requirements.txt"
```

package installやdownloadはネットワーク、容量、PyTorch backendを確認後に行う。

## 隔離buildとtest

buildはROS graphへ要求を送らない。既存Robot overlayをsourceせず、ROS 2 Jazzyだけを
underlayにする。

```bash
overlay="$HOME/inoueyuki_ws/care_patrol_overlay"
repo="$overlay/repo"
runtime="$overlay/runtime"

bash --noprofile --norc -c "
  set -eu
  source /opt/ros/jazzy/setup.bash
  colcon --log-base '$runtime/log' build \
    --base-paths '$repo/src' \
    --build-base '$runtime/build' \
    --install-base '$runtime/install' \
    --packages-select care_patrol
"
```

通常testは実機service名を使わず、`/care_patrol_test...`の模擬serviceを使う。DDSを
Robot networkから隔離し、ROS logも新規subtreeへ限定する。

```bash
runtime="$HOME/inoueyuki_ws/care_patrol_overlay/runtime"
export ROS_DOMAIN_ID=232
export ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST
export ROS_LOCALHOST_ONLY=1
export ROS_LOG_DIR="$runtime/ros_log"
mkdir -p "$ROS_LOG_DIR"

source /opt/ros/jazzy/setup.bash
colcon --log-base "$runtime/log-test" test \
  --base-paths "$repo/src" \
  --build-base "$runtime/build" \
  --install-base "$runtime/install" \
  --packages-select care_patrol
colcon test-result --test-result-base "$runtime/build" --verbose
```

明示的な実Follower統合test、guard node起動、実service call、C920 openはこの段階では
行わない。

## 実機検証前に残る作業

- 現在の`inoueyuki_ws`、Git、colcon、overlay構成のREAD-ONLY確認
- NUCからPrivate repositoryへ認証できることの確認
- NUC上のPython、Ultralytics 8.4.47、Torch、OpenCV、ROS Pythonの共存確認
- YOLO model pathとSHA256の確定
- C920 by-id、Camera V7停止、device所有者不在の確認
- 実Follower source、service type、`/stop`、Odometry、QoS、navigation modeのREAD-ONLY確認
- 別途許可後のC920＋模擬service試験
- さらに別途許可後の実停止試験

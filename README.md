# ImmortalWrt x86_64 OVA Builder

这个仓库用于自动构建面向虚拟化环境的 ImmortalWrt x86_64 固件，并把 ImageBuilder 生成的原始镜像转换成可直接导入 ESXi 的 OVA。构建产物通过 GitHub Releases 发布，仓库本身只保存脚本、工作流和轻量级转换记录，不保存大型镜像文件。

当前流程以 ImmortalWrt 官方 ImageBuilder 为基础，不从源码完整编译固件。这样可以把 GitHub Actions 构建时间控制在较短范围内，同时保持包管理和固件版本可追踪。默认每天在同一个 job 内顺序构建并分别发布两个 flavor：标准旁路由镜像，以及以 `dae`/`daed` 为主的独立 daed 镜像。

## 产物说明

每个成功转换的镜像会发布一个独立 GitHub Release。标准镜像和 daed 镜像使用不同 tag 前缀，互不覆盖：

- `.ova`：ESXi 导入使用，包含 streamOptimized VMDK、OVF 描述和校验清单。
- `.ova.sha256`：OVA 文件的 SHA256 校验值。
- `.img.gz`：ImageBuilder 生成的原始压缩镜像，可用于 PVE 或其他支持 raw 镜像导入的环境。

标准镜像 Release tag 使用构建日期、ImmortalWrt commit 和镜像 SHA 标识版本：

```text
openwrt-immortalwrt-x86-64-YYYYMMDD-<immortalwrt_commit>-<image_sha12>
```

例如 `openwrt-immortalwrt-x86-64-20260616-cf234f8de6d5-03b7fe491448`。

daed 镜像 Release tag 使用独立前缀：

```text
openwrt-immortalwrt-x86-64-daed-YYYYMMDD-<immortalwrt_commit>-<image_sha12>
```

例如 `openwrt-immortalwrt-x86-64-daed-20260616-cf234f8de6d5-12ab34cd56ef`。

标准镜像 Release title 使用 `ImmortalWrt x86_64 ESXi OVA - YYYYMMDD <immortalwrt_commit>`，daed 镜像使用 `ImmortalWrt x86_64 daed ESXi OVA - YYYYMMDD <immortalwrt_commit>`。

新发布的资产文件名也使用同一组元数据，例如：

- `immortalwrt-x86-64-20260616-cf234f8de6d5-03b7fe491448.img.gz`
- `immortalwrt-x86-64-esxi-20260616-cf234f8de6d5-03b7fe491448.ova`
- `immortalwrt-x86-64-esxi-20260616-cf234f8de6d5-03b7fe491448.ova.sha256`
- `immortalwrt-x86-64-daed-20260616-cf234f8de6d5-12ab34cd56ef.img.gz`
- `immortalwrt-x86-64-daed-esxi-20260616-cf234f8de6d5-12ab34cd56ef.ova`
- `immortalwrt-x86-64-daed-esxi-20260616-cf234f8de6d5-12ab34cd56ef.ova.sha256`

默认虚拟硬件由转换脚本生成：

- 2 vCPU
- 2048 MB 内存
- 1 个 VmxNet3 网卡
- IDE 磁盘控制器
- `otherLinux64Guest` / `vmx-17`

PVE 使用原始镜像即可，示例：

```bash
gzip -dk immortalwrt-x86-64-20260616-cf234f8de6d5-03b7fe491448.img.gz
qm importdisk <vmid> immortalwrt-x86-64-20260616-cf234f8de6d5-03b7fe491448.img <storage>
```

ESXi 直接下载 Release 中的 `.ova` 并通过 UI 导入。

## 自动构建流程

主流程定义在 `.github/workflows/build-openwrt.yml`，支持手动触发，也会按计划每天运行一次。默认 ImageBuilder 版本为 `24.10.6`。手动触发时可以通过 `ib_version` 临时指定版本，并用 `publish_release=false` 做不发布 Release 的实验构建。

工作流执行顺序：

1. 安装 Ubuntu runner 依赖，包括 ImageBuilder 所需工具和 `qemu-utils`。
2. 读取上游 `sha256sums`，校验 ImageBuilder 文件名、下载地址和 SHA256 后，再下载并解压 `immortalwrt-imagebuilder-${IB_VERSION}-x86-64.Linux-x86_64.tar.zst`。
3. 读取官方 `version.buildinfo`，采集 `r33869-cf234f8de6d5` 这类版本码，并提取 ImmortalWrt commit。
4. 关闭 ISO、qcow2、VDI、VMDK、VHDX 等辅助镜像格式，只保留后续需要的 raw image。
5. 从 `config/third-party-feeds.tsv` 幂等地追加第三方软件源，关闭 `repositories.conf` 中的签名检查项，并对各第三方源的 `Packages.gz` 做预检，必需源不可达时立即失败。
6. 按唯一 asset 匹配规则下载本地 `.ipk` 或 `.tar.gz` 包，包括 PassWall 2 LuCI、`luci-app-tailscale`、MosDNS 离线包，以及 daed 镜像需要的 `luci-app-daede_*_all.ipk`。`daed` 核心包使用 ImmortalWrt 官方源，不从第三方 release 下载。
7. 在同一个 ImageBuilder 环境里顺序构建两个 flavor。标准镜像使用 `config/openwrt-packages.txt`，daed 镜像使用 `config/openwrt-packages-daed.txt`；每个 flavor 都先执行 `make manifest` 预检依赖闭环，再清理 `bin/targets/x86/64` 并执行 `make image`，避免收集到上一轮产物。标准镜像写入 `build-out/immortalwrt-x86-64.img.gz`，daed 镜像写入 `build-out/immortalwrt-x86-64-daed.img.gz`。
8. 调用 `scripts/openwrt_img_to_ova.py scan` 一次扫描 `build-out/` 并转换所有新镜像，传入构建日期、ImmortalWrt 版本码和 commit。
9. 调用 `scripts/openwrt_build_preflight.py copy-raw-images` 按 `build-results.json` 复制每个 raw image，再调用 `scripts/publish_releases.py` 创建 GitHub Release，并校验 `.ova`、`.ova.sha256`、`.img.gz` 三类资产都已上传。
10. 调用 `record` 更新 `manifests/converted-images.json` 和 `docs/converted-images.md`，再校验本次所有 Release tag 和 latest Release 已被记录，最后由 workflow 提交记录。

转换记录使用 `image_sha256:BUILDER_VERSION` 作为去重 key。同一个镜像内容和同一个转换器版本不会重复转换；Release tag 额外包含构建日期、ImmortalWrt commit 和镜像 SHA 前 12 位，便于从 Release 页面追溯来源。每日 workflow 只代表每天检查和构建，镜像 SHA 未变化时不会发布新 Release。Release 清理按 standard/daed family 分开保留最近 30 个，避免 daed Release 把标准 Release 提前清掉。

## 内置组件与运行注意事项

固件面向旁路由场景，内置常用代理、网络和诊断组件：

- LuCI 中文界面和默认 Argon 主题
- PassWall 2
- MosDNS
- OpenClash
- Nikki
- Tailscale
- ZeroTier
- vlmcsd
- UPnP、irqbalance，以及可供运行时测试的 nftables flow offload 内核模块
- `luci-app-statistics` 和常用 collectd 模块
- `curl`、`htop`、`tcpdump`、`mtr`、`iperf3`、`bind-dig` 等诊断工具

注意：Nikki 和 Momo 属于互斥的透明代理栈，它们的 nftables 规则存在冲突。本镜像保留 Nikki（Mihomo）并省略 Momo（sing-box），避免默认固件里两套透明代理规则互相打架。`dae`/`daed` 也是独立透明代理栈，不预装到默认镜像，而是作为单独 daed flavor 发布。

daed 独立镜像使用 `config/openwrt-packages-daed.txt`。它保留 LuCI、Argon、MosDNS、Tailscale、ZeroTier、VMware/网卡驱动、统计组件、诊断工具和旁路由基础 kmod，并加入 `luci-app-daede` 与官方源 `daed`。为避免透明代理规则互相打架，daed 镜像明确不包含 PassWall 2、OpenClash、Nikki/Mihomo：

- `luci-app-passwall2`
- `luci-app-openclash`
- `luci-app-nikki`
- `luci-i18n-nikki-zh-cn`
- `mihomo-meta`

固件内置旁路由调优：

- 启用 IPv4/IPv6 转发。
- 使用 BBR 和 `fq`。
- 提高 conntrack 与 socket buffer 上限。
- 禁止 ICMP redirect。
- 使用 loose `rp_filter`。
- 默认关闭 LAN DHCP、RA 和 DHCPv6，由主路由负责地址分配。
- 默认主机名为 `immortalwrt-bypass`，时区为 `Asia/Shanghai`，NTP 使用中国大陆和通用池混合服务器。
- 默认选择 Argon 作为 LuCI 主题。
- 启用 packet steering；`kmod-nft-offload` 仅预装供运行时测试，不默认开启 flow offload，避免透明代理规则被快路径绕过。
- 默认启用 irqbalance 自启，把软中断和网卡中断分散到多个 CPU 核心。
- 根分区大小为 2048 MB。

导入虚拟机后，请为 LAN 配置一个不与主路由冲突的静态地址。

## daed 独立镜像

`dae` 是基于 eBPF 的高性能透明代理核心，`daed` 是把 dae 后端、API 和 Web Dashboard 打包在一起的一体化形态。它和 Nikki、OpenClash、PassWall 2 一样会接管透明代理相关流量路径，默认同时预装会增加规则冲突和排障复杂度。

截至 2026-06-22 调研，ImmortalWrt 24.10.6 x86_64 官方包源已经提供 `dae` 和 `daed`，目标内核启用了 `CONFIG_KERNEL_DEBUG_INFO_BTF` 和 `CONFIG_KERNEL_XDP_SOCKETS`，并提供 `kmod-sched-core`、`kmod-sched-bpf`、`kmod-veth`、`kmod-xdp-sockets-diag` 等运行依赖。因此 daed 在当前基线具备独立镜像集成条件，但不适合作为默认内置组件。

当前集成按以下原则处理：

- 不替换主构建基线，不切到第三方 snapshot ImageBuilder，优先继续使用官方 ImmortalWrt `IB_VERSION`。
- 只在 daed flavor 中加入 `luci-app-daede`；它依赖 `daed`，由 ImageBuilder 自动拉取官方 `daed` 包。
- `luci-app-daede` 当前不在官方 24.10.6 LuCI 包源中，可从 `kenzok8/openwrt-daede` release 下载 `luci-app-daede_*_all.ipk` 到 ImageBuilder 的 `packages/` 目录。
- 构建前先跑 daed 包清单的 `make manifest PROFILE="generic"` 验证依赖闭环，失败时不要进入正式镜像发布流程。
- daed 镜像不默认注入代理配置，不改防火墙默认规则；它通过移除 Nikki、OpenClash、PassWall 2 等透明代理栈降低冲突面。

## 本地转换

本地转换只依赖 Python 3 标准库和 `qemu-img`。Ubuntu 安装依赖：

```bash
sudo apt-get install -y qemu-utils
```

扫描目录中的 `.img` 或 `.img.gz`，并把未记录过的镜像转换为 OVA：

```bash
python3 scripts/openwrt_img_to_ova.py scan \
  --img-dir <dir-with-img-files> \
  --manifest manifests/converted-images.json \
  --out-dir dist \
  --results dist/build-results.json \
  --nic-count 1
```

如果需要在本地生成与 CI 一致的 release tag，可以显式传入元数据：

```bash
python3 scripts/openwrt_img_to_ova.py scan \
  --img-dir <dir-with-img-files> \
  --manifest manifests/converted-images.json \
  --out-dir dist \
  --results dist/build-results.json \
  --nic-count 1 \
  --release-date 20260616 \
  --immortalwrt-version-code r33869-cf234f8de6d5 \
  --immortalwrt-commit cf234f8de6d5
```

转换完成后，`dist/` 会包含 `.ova`、`.ovf`、`.vmdk`、`.mf` 和 `.ova.sha256`。如果这些产物已经发布，可以更新转换记录：

```bash
python3 scripts/openwrt_img_to_ova.py record \
  --results dist/build-results.json \
  --manifest manifests/converted-images.json \
  --doc docs/converted-images.md
```

发布 GitHub Release 需要已认证的 GitHub CLI。发布脚本会同时上传 OVA、校验文件和原始 `.img.gz`，因此本地发布前需要先按 `build-results.json` 中的资产名复制原始镜像：

```bash
python3 scripts/openwrt_build_preflight.py copy-raw-images \
  --results dist/build-results.json \
  --source-dir build-out \
  --out-dir dist
python3 scripts/publish_releases.py dist/build-results.json --keep-releases 30
```

脚本只创建不存在的 Release tag；如果 tag 已存在，会更新标题、说明和资产。成功发布新 Release 后，脚本会按 standard/daed family 分别保留最近 30 个自动发布的 OpenWrt Release，并删除更旧的自动 Release 及其 tag。手工创建且不匹配本项目自动 tag 格式的 Release 不会被清理。

## 维护指南

核心脚本保持无第三方 Python 依赖，优先使用标准库和显式 subprocess 参数列表。修改转换逻辑、OVF 模板、虚拟硬件默认值、Release tag 或产物命名规则时，必须同步递增 `scripts/openwrt_img_to_ova.py` 中的 `BUILDER_VERSION`，否则旧 manifest 记录可能导致新逻辑不被重新应用。

不要提交构建产物。以下路径和文件类型应保持忽略状态：

- `dist/`
- `build-out/`
- `imagebuilder/`
- `*.ova`
- `*.ovf`
- `*.vmdk`
- `*.mf`
- `*.sha256`

如果调整标准镜像内置包列表，修改 `config/openwrt-packages.txt`；如果调整 daed 镜像内置包列表，修改 `config/openwrt-packages-daed.txt`；如果调整第三方软件源，修改 `config/third-party-feeds.tsv`。工作流会先跑 feed 探活和每个 flavor 的 `make manifest`，ImageBuilder 会在包名不存在时直接失败，这比静默跳过更容易排查。

维护预检脚本时使用标准库，相关轻量测试位于 `tests/`。常用检查命令：

```bash
python3 -m py_compile scripts/*.py
python3 -m unittest discover -s tests
ruby -e 'require "yaml"; YAML.load_file(".github/workflows/build-openwrt.yml"); puts "workflow yaml ok"'
```

## 常见排障

`missing required tools: qemu-img`：安装 `qemu-utils` 后重试。

`make image` 失败并提示找不到包：检查 workflow 中的 `PACKAGES` 列表、第三方 feed URL、release ipk 下载步骤是否仍然可用。

`skip already converted`：当前镜像 SHA256 和 `BUILDER_VERSION` 已存在于 `manifests/converted-images.json`。如果转换逻辑或 Release tag 规则确实变了，先递增 `BUILDER_VERSION`。

GitHub Release 已存在：`publish_releases.py` 会跳过已有 tag，适合重复运行。

Release 太多：发布脚本默认按 standard/daed family 各保留最近 30 个自动 Release；如需调整，修改 workflow 中的 `--keep-releases` 参数。

## 安全与配置

保持仓库私有。不要提交固件镜像、运行时配置密钥、GitHub token、VPN 凭据、代理订阅或其他敏感配置。

第三方 feed 和 `.ipk` 下载源会进入构建链路，修改时需要确认来源可信，并在提交或 PR 描述中说明原因。若未来通过 `files/` 注入 OpenWrt 运行时配置，先确认其中不包含任何秘密信息。

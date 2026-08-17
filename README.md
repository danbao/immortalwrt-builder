# ImmortalWrt x86_64 OVA Builder

这个仓库用于自动构建面向虚拟化环境的 ImmortalWrt x86_64 固件，并把 ImageBuilder 生成的原始镜像转换成可直接导入 ESXi 的 OVA。构建产物通过 GitHub Releases 发布，仓库本身只保存脚本、工作流和轻量级转换记录，不保存大型镜像文件。

当前流程以 ImmortalWrt 25.12.1 官方 ImageBuilder 为基础，不从源码完整编译固件。每天构建一个 flavor：官方基础镜像 + daed（eBPF 透明代理一体包）。除 daed 外不内置任何第三方代理或 DNS 插件。

## 产物说明

每个成功转换的镜像会发布一个独立 GitHub Release：

- `.ova`：ESXi 导入使用，包含 streamOptimized VMDK、OVF 描述和校验清单。
- `.ova.sha256`：OVA 文件的 SHA256 校验值。
- `.img.gz`：ImageBuilder 生成的原始压缩镜像，可用于 PVE 或其他支持 raw 镜像导入的环境。

Release tag 使用构建日期、ImmortalWrt commit 和镜像 SHA 标识版本：

```text
openwrt-immortalwrt-x86-64-daed-YYYYMMDD-<immortalwrt_commit>-<image_sha12>
```

例如 `openwrt-immortalwrt-x86-64-daed-20260616-cf234f8de6d5-12ab34cd56ef`。

Release title 使用 `ImmortalWrt x86_64 daed ESXi OVA - YYYYMMDD <immortalwrt_commit>`。

新发布的资产文件名也使用同一组元数据，例如：

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
gzip -dk immortalwrt-x86-64-daed-20260616-cf234f8de6d5-12ab34cd56ef.img.gz
qm importdisk <vmid> immortalwrt-x86-64-daed-20260616-cf234f8de6d5-12ab34cd56ef.img <storage>
```

ESXi 直接下载 Release 中的 `.ova` 并通过 UI 导入。

## 自动构建流程

主流程定义在 `.github/workflows/build-openwrt.yml`，支持手动触发，也会按计划每天运行一次。默认 ImageBuilder 版本为 `25.12.1`。手动触发时可以通过 `ib_version` 临时指定版本，并用 `publish_release=false` 做不发布 Release 的实验构建。

### Runner 选择

手动触发时可以选择 `runner`：

- `ubuntu-latest`（默认）：GitHub 托管 runner，计划任务固定使用它。
- `self-hosted`：使用你自己的 runner，label 必须包含 `self-hosted`。自托管 runner 需要满足：Linux + 免密 `sudo`（用于 `apt-get` 安装 qemu-utils/zstd 等依赖）、`qemu-img`、以及发布/记录步骤所需的 `gh` CLI（仅 `publish_release=true` 时需要）。自托管 runner 的工作目录会跨 run 保留，工作流开头会先清理上一次的 `imagebuilder/`、`build-out/`、`dist/`。

工作流执行顺序：

1. 安装 Ubuntu runner 依赖，包括 ImageBuilder 所需工具和 `qemu-utils`。
2. 读取上游 `sha256sums`，校验 ImageBuilder 文件名、下载地址和 SHA256 后，再下载并解压 `immortalwrt-imagebuilder-${IB_VERSION}-x86-64.Linux-x86_64.tar.zst`。
3. 读取官方 `version.buildinfo`，采集 `r33869-cf234f8de6d5` 这类版本码，并提取 ImmortalWrt commit。
4. 关闭 ISO、qcow2、VDI、VMDK、VHDX 等辅助镜像格式，只保留后续需要的 raw image。
5. 调用 `scripts/openwrt_build_preflight.py daed-packages`，按 `config/daed-feed.json` 从 kenzok8 daed feed 下载 `daed` 与 `luci-app-daede` 两个 `.apk` 到 ImageBuilder 的 `packages/` 本地包目录；每个包先读 `manifest-daede.txt` 中的 sha256 并逐字节校验，并把实际安装的版本和校验值记录到 `dist/daed-packages.json`。其余依赖（`v2ray-geoip`、`v2ray-geosite`、`kmod-sched-core`、`kmod-sched-bpf`、`kmod-veth`、`ca-bundle` 等）全部来自官方 25.12.1 软件源，签名校验保持开启。
6. 先执行 `make manifest` 断言依赖闭环：清单必须包含 `daed`、`luci-app-daede`、`luci-app-vlmcsd`，且不能出现 PassWall 2、OpenClash、Nikki、MosDNS 等被移除的透明代理/DNS 包；随后清理 `bin/targets/x86/64` 并执行 `make image`，写入 `build-out/immortalwrt-x86-64-daed.img.gz`。
7. 调用 `scripts/openwrt_img_to_ova.py scan` 扫描 `build-out/` 并转换新镜像，传入构建日期、ImmortalWrt 版本码和 commit。
8. 调用 `scripts/openwrt_build_preflight.py copy-raw-images` 按 `build-results.json` 复制 raw image，再调用 `scripts/publish_releases.py` 创建 GitHub Release，并校验 `.ova`、`.ova.sha256`、`.img.gz` 三类资产都已上传。
9. 调用 `record` 更新 `manifests/converted-images.json` 和 `docs/converted-images.md`，再校验本次所有 Release tag 和 latest Release 已被记录，最后由 workflow 提交记录。

转换记录使用 `image_sha256:BUILDER_VERSION` 作为去重 key。同一个镜像内容和同一个转换器版本不会重复转换；Release tag 额外包含构建日期、ImmortalWrt commit 和镜像 SHA 前 12 位，便于从 Release 页面追溯来源。每日 workflow 只代表每天检查和构建，镜像 SHA 未变化时不会发布新 Release。Release 清理按 daed family 保留最近 30 个；历史 standard family 也继续按最近 30 个修剪。

## 内置组件与运行注意事项

固件面向旁路由 + daed 透明代理场景：

- LuCI 中文界面和默认 Argon 主题
- `luci-app-vlmcsd`（KMS 服务，与 ImmortalWrt 官方基线一致）
- daed（kenzok8/openwrt-daede 一体包，含 dae 核心、dae-wing 和内嵌 Web 面板）
- `luci-app-daede`（daed 的 LuCI 管理界面）
- daed 依赖的 `v2ray-geoip`、`v2ray-geosite` 和 eBPF 相关 kmod 由官方源自动解决

镜像明确不包含 PassWall 2、OpenClash、Nikki/Mihomo、MosDNS 等透明代理/DNS 插件，避免多套透明代理规则互相打架。

固件内置旁路由调优（`files/` 覆盖层）：

- 启用 IPv4/IPv6 转发。
- 使用 BBR 和 `fq`。
- 提高 conntrack 与 socket buffer 上限。
- 禁止 ICMP redirect。
- 使用 loose `rp_filter`。
- 默认关闭 LAN DHCP、RA 和 DHCPv6，由主路由负责地址分配。
- 默认主机名为 `immortalwrt-bypass`，时区为 `Asia/Shanghai`，NTP 使用中国大陆和通用池混合服务器。
- 默认选择 Argon 作为 LuCI 主题。
- 启用 packet steering；默认关闭 flow offload，避免透明代理规则被快路径绕过。
- 默认启用 irqbalance 自启，把软中断和网卡中断分散到多个 CPU 核心。
- 根分区大小为 2048 MB。

导入虚拟机后，请为 LAN 配置一个不与主路由冲突的静态地址，然后进入 LuCI「服务 → daede」导入订阅并启动。

## 关于 daed

`dae` 是基于 eBPF 的高性能透明代理核心，`daed` 是把 dae 后端、API 和 Web Dashboard 打包在一起的一体化形态。本镜像只安装 `daed` 一个代理组件（不含独立 `dae` 包），需要热切换后端时再单独安装 `dae`。

daed 包与 `luci-app-daede` 来自 [kenzok8/openwrt-daede](https://github.com/kenzok8/openwrt-daede) 的 25.12 feed（其安装脚本指定的官方分发域名 `down.dllkids.xyz`）。该 feed 无 apk 签名，因此本仓库不把 feed 直接挂进 ImageBuilder 仓库列表，而是下载具体 `.apk` 到本地包目录：sha256 来自 feed 自带的 `manifest-daede.txt`，实际安装的版本与校验值写入 `dist/daed-packages.json` 随构建记录追溯；ImageBuilder 的签名校验保持开启。修改 `config/daed-feed.json` 属于安全敏感变更，需要说明来源可信。

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

脚本只创建不存在的 Release tag；如果 tag 已存在，会更新标题、说明和资产。成功发布新 Release 后，脚本会按 family 分别保留最近 30 个自动发布的 OpenWrt Release，并删除更旧的自动 Release 及其 tag。手工创建且不匹配本项目自动 tag 格式的 Release 不会被清理。

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

调整内置包列表时，修改 workflow 中的 `DAED_PACKAGES` 环境变量；调整 daed 第三方包来源时，修改 `config/daed-feed.json`。工作流会先跑 `make manifest` 断言和 sha256 校验，包名或校验值不匹配时直接失败，这比静默跳过更容易排查。

维护预检脚本时使用标准库，相关轻量测试位于 `tests/`。常用检查命令：

```bash
python3 -m py_compile scripts/*.py
python3 -m unittest discover -s tests
ruby -e 'require "yaml"; YAML.load_file(".github/workflows/build-openwrt.yml"); puts "workflow yaml ok"'
```

## 常见排障

`missing required tools: qemu-img`：安装 `qemu-utils` 后重试。

`make image` 失败并提示找不到包：检查 workflow 中的 `DAED_PACKAGES` 列表、`config/daed-feed.json` 指向的 feed 目录是否仍然可用，以及 `dist/daed-packages.json` 中记录的包版本是否与官方 25.12.1 依赖匹配。kenzok8 滚动新版本后若出现依赖版本不匹配，可在 `config/daed-feed.json` 的 `pin` 字段锁定上一版可用文件名。

`daed-packages` sha256 mismatch：feed 的 `manifest-daede.txt` 与下载文件不一致，中止构建；先确认 feed 域名未被篡改。

`skip already converted`：当前镜像 SHA256 和 `BUILDER_VERSION` 已存在于 `manifests/converted-images.json`。如果转换逻辑或 Release tag 规则确实变了，先递增 `BUILDER_VERSION`。

GitHub Release 已存在：`publish_releases.py` 会跳过已有 tag，适合重复运行。

Release 太多：发布脚本默认按 family 各保留最近 30 个自动 Release；如需调整，修改 workflow 中的 `--keep-releases` 参数。

## 安全与配置

保持仓库私有。不要提交固件镜像、运行时配置密钥、GitHub token、VPN 凭据、代理订阅或其他敏感配置。

daed `.apk` 下载源会进入构建链路，修改 `config/daed-feed.json` 时需要确认来源可信，并在提交或 PR 描述中说明原因。`files/` 覆盖层仅包含通用旁路由调优，不注入任何凭据。

# ImmortalWrt x86_64 OVA Builder

这个仓库用于自动构建面向虚拟化环境的 ImmortalWrt x86_64 固件，并把 ImageBuilder 生成的原始镜像转换成可直接导入 ESXi 的 OVA。构建产物通过 GitHub Releases 发布，仓库本身只保存脚本、工作流和轻量级转换记录，不保存大型镜像文件。

当前流程以 ImmortalWrt 25.12.1 官方 ImageBuilder 为基础，不从源码完整编译固件。每次构建产出单一 flavor：官方基础镜像、daed、Tailscale、vnStat 和 VMware Guest Tools。所有包均由 ImmortalWrt 官方签名源解析，不内置其他透明代理或 DNS 插件。

## 产物说明

每个成功转换的镜像会发布一个独立 GitHub Release：

- `.ova`：ESXi 导入使用，包含 streamOptimized VMDK、OVF 描述和校验清单。
- `.ova.sha256`：OVA 文件的 SHA256 校验值。
- `.img.gz`：ImageBuilder 生成的原始压缩镜像，可用于 PVE 或其他支持 raw 镜像导入的环境。
- `SHA256SUMS`：本次发布主要资产的统一 SHA256 清单。
- `*.manifest`：最终镜像的软件包和版本清单。
- `*.bom.cdx.json`：ImageBuilder 生成的 CycloneDX SBOM。
- `build-metadata.json`：构建来源、profile、全部必需包版本和工作流追溯信息。
- `build-metadata.tar.gz`：确定性打包的 ImageBuilder 配置与 buildinfo 证据。
- `setup-openwrt.sh`：macOS、Linux 和 WSL 可用的主机端初始化向导。

Release tag 使用构建日期、ImmortalWrt commit 和镜像 SHA 标识版本：

```text
openwrt-immortalwrt-x86-64-bypass-YYYYMMDD-<immortalwrt_commit>-<builder_sha12>-<image_sha12>
```

例如 `openwrt-immortalwrt-x86-64-bypass-20260616-cf234f8de6d5-deadbeefcafe-12ab34cd56ef`。

Release title 使用 `ImmortalWrt x86_64 bypass ESXi OVA - YYYYMMDD <immortalwrt_commit> (<builder_sha12>)`。

镜像名来自 `config/build-profile.json` 的 `name` 字段。它标识整个包集，而不是其中某个组件；`20260831` 之前发布的 `-daed-` 标签是旧命名，仍然保留。

新发布的资产文件名也使用同一组元数据，例如：

- `immortalwrt-x86-64-bypass-20260616-cf234f8de6d5-deadbeefcafe-12ab34cd56ef.img.gz`
- `immortalwrt-x86-64-bypass-esxi-20260616-cf234f8de6d5-deadbeefcafe-12ab34cd56ef.ova`
- `immortalwrt-x86-64-bypass-esxi-20260616-cf234f8de6d5-deadbeefcafe-12ab34cd56ef.ova.sha256`

默认虚拟硬件由转换脚本生成：

- 2 vCPU
- 2048 MB 内存
- 1 个 VmxNet3 网卡
- IDE 磁盘控制器
- `otherLinux64Guest` / `vmx-17`

PVE 使用原始镜像即可，示例：

```bash
gzip -dk immortalwrt-x86-64-bypass-20260616-cf234f8de6d5-deadbeefcafe-12ab34cd56ef.img.gz
qm importdisk <vmid> immortalwrt-x86-64-bypass-20260616-cf234f8de6d5-deadbeefcafe-12ab34cd56ef.img <storage>
```

ESXi 直接下载 Release 中的 `.ova` 并通过 UI 导入。

## 自动构建流程

主流程定义在 `.github/workflows/build-openwrt.yml`，支持手动触发，也会按计划每天 02:00（Asia/Shanghai）运行。默认 ImageBuilder 版本为 `25.12.1`。手动触发提供三种 `build_mode`：

- `validate`：只校验 ImageBuilder、安全配置和官方软件包 manifest。
- `dry-run`：完整构建 IMG/OVA，但只保留 14 天 Actions Artifact。
- `publish`（默认）：完整构建并发布 Release、更新转换记录。

定时任务固定使用 `publish`。

### Runner 选择

手动触发时可以选择 `runner`：

- `ubuntu-24.04`（默认）：GitHub 托管 runner，计划任务固定使用它。
- `self-hosted`：仅承担只读构建任务，label 必须包含 `self-hosted`。需要 Linux、免密 `sudo`、GitHub Actions Runner 新版 Action 所要求的 Node.js 运行能力。自托管 runner 不接收 `contents: write` token，也不执行发布。

发布 job 始终在独立的 GitHub 托管 `ubuntu-24.04` runner 上运行，并且是唯一拥有 `contents: write` 权限的 job。

工作流执行顺序：

1. 安装 Ubuntu runner 依赖，包括 ImageBuilder 所需工具和 `qemu-utils`。
2. 读取上游 `sha256sums`，校验 ImageBuilder 文件名、下载地址和 SHA256 后，再下载并解压 `immortalwrt-imagebuilder-${IB_VERSION}-x86-64.Linux-x86_64.tar.zst`。
3. 读取官方 `version.buildinfo`，采集 `r33869-cf234f8de6d5` 这类版本码，并提取 ImmortalWrt commit。
4. 关闭 ISO、qcow2、VDI、VMDK、VHDX 等辅助镜像格式，只保留后续需要的 raw image。
5. 读取 `config/build-profile.json`，执行 `make manifest`，验证 daed、Tailscale、vnStat、open-vm-tools 及其 LuCI 组件齐全，同时拒绝旧 `luci-app-daede` 及冲突代理组件。
6. `validate` 模式到此结束；其他模式执行 `make image`，并强制要求恰好一个 raw image、一个最终 manifest 和一个 CycloneDX SBOM。
7. 调用 `scripts/openwrt_img_to_ova.py scan` 转换 OVA，再由 `prepare-assets` 生成统一校验和、供应链元数据和确定性 buildinfo 归档。
8. `dry-run` 只上传临时 Artifact；`publish` 将一日有效的交接 Artifact 传给 GitHub 托管发布 job。
9. 发布脚本根据 `build-results.json` 中显式声明的 `release_assets` 创建 Release；发布端重新限制 tag 和资产路径，并逐项复验 `.ova.sha256` 与 `SHA256SUMS`，不直接信任构建 runner 的交接声明。已有 Release 视为不可变，只在远端 digest 完全一致时作为幂等成功，禁止覆盖历史资产。
10. 发布成功后由 `scripts/update_release_records.py` 确定性重建 `manifests/converted-images.json` 和 `docs/converted-images.md`；遇到并发 push 冲突会拉取最新目标分支并最多重试三次。Release 不会因记录提交失败而回滚。

转换记录使用 `image_sha256:BUILDER_VERSION:repository_commit` 作为去重 key。`image_sha256` 是整个 `.img.gz` 产物的 SHA256，因此任何进入 rootfs 的包变化都会改变它；`repository_commit` 是本仓库构建这次镜像时的 commit。镜像内容和 builder 仓库都没变时不会重复转换；Release tag 额外包含构建日期、ImmortalWrt commit、builder commit 前 12 位和镜像 SHA 前 12 位。定时 workflow 只代表定期检查和构建，两者都没变时不会发布新 Release。README 改动这类不改变镜像的 commit 仍会发出新 Release，因为 builder commit 变了。Release 清理按 bypass family 保留最近 30 个；历史 daed 与 standard family 也继续各按最近 30 个修剪。

因为去重 key 存放在仓库内的 `manifests/converted-images.json`，把这份清单复制到另一个仓库会让对方把同一镜像视为「已发布」。跨仓库迁移时应清理不属于该仓库的记录。

## 内置组件与运行注意事项

固件面向旁路由 + daed 透明代理场景：

- LuCI 中文界面和默认 Argon 主题
- `luci-app-vlmcsd`（KMS 服务，与 ImmortalWrt 官方基线一致）
- ImmortalWrt 官方 `daed` 服务
- 官方 `daed-geoip`、`daed-geosite`
- 官方 `luci-app-daed` 与 `luci-i18n-daed-zh-cn`
- 官方 `tailscale`、社区 LuCI 管理页及中文翻译
- `vnstat2`、`vnstati2`、LuCI 管理页及中文翻译
- `open-vm-tools`（在 VMware 环境使用，IMG 中保留但不会影响物理机或其他虚拟化平台）
- 防火墙和软件包管理器中文翻译

镜像明确不包含 PassWall 2、OpenClash、Nikki/Mihomo、MosDNS 等透明代理/DNS 插件，避免多套透明代理规则互相打架。

固件内置旁路由调优（`files/` 覆盖层）：

- 启用 IPv4 转发；IPv6 继续由主路由直接提供，不经过本旁路由代理。
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

新镜像不预置 LAN 地址、账号、SSH 密钥、订阅或设备身份。导入后优先运行 Release 中的向导：

```bash
chmod +x setup-openwrt.sh
./setup-openwrt.sh --target root@192.168.1.1
```

向导会先在路由器内创建 `0600` 回滚备份，再配置 LAN、SSH、daed、Tailscale 和 vnStat。它只在主机保存目标地址、网段、网关、DNS、主机名和 SSH 公钥路径；密码、订阅 URI 与认证令牌不会写入文件。重复运行会复用现有 daed 和 Tailscale 状态。只做状态检查可使用 `./setup-openwrt.sh --target root@HOST --check`。详细行为见 [便携式运行基线与升级保护](docs/portable-runtime-baseline.md)。

## 关于 daed

`dae` 是基于 eBPF 的高性能透明代理核心。本镜像强制使用经过 ImageBuilder 官方仓库签名链解析的 daed 软件包，不下载或注入第三方 APK，也不提供第三方源回退。工作流在构建前验证包签名、签名检查、TLS 证书检查、image manifest 和 CycloneDX SBOM 配置；任何安全选项或官方包缺失都会中止构建。

官方包默认把 daed 设为禁用，避免尚未初始化的 Dashboard 自动暴露。先在 LuCI「服务 → daed」设置监听地址并启用，或通过 SSH 执行：

```sh
uci set daed.config.enabled='1'
uci set daed.config.listen_addr='0.0.0.0:2023'
uci commit daed
/etc/init.d/daed enable
/etc/init.d/daed restart
```

随后通过 `http://<旁路由地址>:2023` 完成 Dashboard 初始化和配置。不要把 2023 端口开放到 WAN；需要限制管理来源时，应将监听地址绑定到管理网地址并配置防火墙。配置与订阅保存在官方包声明的 `/etc/daed/` 和 `/etc/config/daed` 中，重启测试应同时确认服务状态、配置持久化和 eBPF 转发效果。参考 [ImmortalWrt 官方 daed 包](https://github.com/immortalwrt/packages/tree/master/net/daed)、[ImmortalWrt 官方 LuCI 应用](https://github.com/immortalwrt/luci/tree/master/applications/luci-app-daed) 和 [dae 官方配置指南](https://github.com/daeuniverse/dae/blob/main/docs/en/README.md)。

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

发布 GitHub Release 需要已认证的 GitHub CLI。应先使用 `prepare-assets` 生成并在 `build-results.json` 中声明完整资产；以下参数按本地 ImageBuilder 输出调整：

```bash
python3 scripts/openwrt_img_to_ova.py prepare-assets \
  --results dist/build-results.json \
  --source-dir build-out \
  --out-dir dist \
  --profile config/build-profile.json \
  --package-manifest build-out/final-image.manifest \
  --sbom build-out/final-image.bom.cdx.json \
  --package-metadata build-out/official-packages.json \
  --setup-wizard scripts/setup-openwrt.sh \
  --build-info-file build-out/imagebuilder.config \
  --imagebuilder-version 25.12.1 \
  --imagebuilder-url <verified-imagebuilder-url> \
  --imagebuilder-sha256 <verified-imagebuilder-sha256> \
  --immortalwrt-version-code <version-code> \
  --immortalwrt-commit <commit> \
  --repository-commit <repository-commit> \
  --workflow-run-url <workflow-run-url> \
  --runner-type local
python3 scripts/publish_releases.py dist/build-results.json \
  --keep-releases 30 \
  --expected-repository-commit <repository-commit> \
  --expected-workflow-run-url <workflow-run-url>
```

脚本只创建不存在的 Release tag；如果 tag 已存在，会验证全部预期资产的 GitHub SHA256 digest，任何缺失或差异都会失败，不会使用 `--clobber` 覆盖历史资产。成功发布新 Release 后，脚本会按 family 分别保留最近 30 个自动发布的 OpenWrt Release，并删除更旧的自动 Release 及其 tag。手工创建且不匹配本项目自动 tag 格式的 Release 不会被清理。

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

调整 profile、内置包、rootfs 或输出匹配规则时，只修改 `config/build-profile.json`。`required_packages` 是构建安全门，`forbidden_packages` 用于阻止旧 daede 和冲突代理栈重新混入镜像。`name` 决定镜像文件名、Release tag 与资产名，改动它属于 Release tag 语义变更，必须同时递增 `scripts/openwrt_img_to_ova.py` 的 `BUILDER_VERSION`，并在 `scripts/publish_releases.py` 中补上对应的 family 规则。

维护预检脚本时使用标准库，相关轻量测试位于 `tests/`。常用检查命令：

```bash
python3 -m py_compile scripts/*.py
python3 -m unittest discover -s tests
ruby -e 'require "yaml"; YAML.load_file(".github/workflows/build-openwrt.yml"); puts "workflow yaml ok"'
```

## 常见排障

`missing required tools: qemu-img`：安装 `qemu-utils` 后重试。

`make manifest` 提示缺少 daed 包：确认所选 ImageBuilder 版本的官方仓库包含 `config/build-profile.json` 声明的完整包集。安全策略不允许临时回退到第三方 APK。

`validate-imagebuilder` 失败：所选 ImageBuilder 缺少签名、TLS、manifest 或 CycloneDX SBOM 安全选项；不要绕过检查，应升级或修复上游构建来源。

缺少 `.manifest` 或 `.bom.cdx.json`：ImageBuilder 没有为最终镜像生成必需的审计资产，工作流会在 OVA 发布前中止。

`skip already converted`：当前镜像 SHA256、`BUILDER_VERSION` 和 builder 仓库 commit 已存在于 `manifests/converted-images.json`。如果转换逻辑或 Release tag 规则确实变了，先递增 `BUILDER_VERSION`。

GitHub Release 已存在：`publish_releases.py` 会复验全部托管资产；完全一致时幂等通过并保留人工添加的非托管资产，不一致时拒绝覆盖。记录修复仍可安全重跑。

Release 太多：发布脚本默认按 family 各保留最近 30 个自动 Release；如需调整，修改 workflow 中的 `--keep-releases` 参数。

## 安全与配置

本仓库按公开源码维护。不要提交固件镜像、运行时配置密钥、GitHub token、VPN 凭据、代理订阅或其他敏感配置。

ImageBuilder 版本和包列表会进入构建信任链，修改下载来源、校验逻辑、`config/build-profile.json` 或 GitHub Actions 固定 SHA 时必须进行安全审查。`files/` 覆盖层仅包含通用旁路由调优，不注入任何凭据。

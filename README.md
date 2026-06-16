# ImmortalWrt x86_64 OVA Builder

这个仓库用于自动构建面向虚拟化环境的 ImmortalWrt x86_64 固件，并把 ImageBuilder 生成的原始镜像转换成可直接导入 ESXi 的 OVA。构建产物通过 GitHub Releases 发布，仓库本身只保存脚本、工作流和轻量级转换记录，不保存大型镜像文件。

当前流程以 ImmortalWrt 官方 ImageBuilder 为基础，不从源码完整编译固件。这样可以把 GitHub Actions 构建时间控制在较短范围内，同时保持包管理和固件版本可追踪。

## 产物说明

每次成功构建会发布以下主要产物：

- `.ova`：ESXi 导入使用，包含 streamOptimized VMDK、OVF 描述和校验清单。
- `.ova.sha256`：OVA 文件的 SHA256 校验值。
- `.img.gz`：ImageBuilder 生成的原始压缩镜像，可用于 PVE 或其他支持 raw 镜像导入的环境。

Release tag 使用构建日期、ImmortalWrt commit 和镜像 SHA 标识版本：

```text
openwrt-immortalwrt-x86-64-YYYYMMDD-<immortalwrt_commit>-<image_sha12>
```

例如 `openwrt-immortalwrt-x86-64-20260616-cf234f8de6d5-03b7fe491448`。

默认虚拟硬件由转换脚本生成：

- 2 vCPU
- 2048 MB 内存
- 1 个 VmxNet3 网卡
- IDE 磁盘控制器
- `otherLinux64Guest` / `vmx-17`

PVE 使用原始镜像即可：

```bash
gzip -dk immortalwrt-x86-64.img.gz
qm importdisk <vmid> immortalwrt-x86-64.img <storage>
```

ESXi 直接下载 Release 中的 `.ova` 并通过 UI 导入。

## 自动构建流程

主流程定义在 `.github/workflows/build-openwrt.yml`，支持手动触发，也会按计划每天运行一次。当前 ImageBuilder 版本由 workflow 顶部的 `IB_VERSION` 控制，值为 `24.10.6`。

工作流执行顺序：

1. 安装 Ubuntu runner 依赖，包括 ImageBuilder 所需工具和 `qemu-utils`。
2. 下载 `immortalwrt-imagebuilder-${IB_VERSION}-x86-64.Linux-x86_64.tar.zst`。
3. 读取官方 `version.buildinfo`，采集 `r33869-cf234f8de6d5` 这类版本码，并提取 ImmortalWrt commit。
4. 关闭 ISO、qcow2、VDI、VMDK、VHDX 等辅助镜像格式，只保留后续需要的 raw image。
5. 追加第三方软件源，并关闭 `repositories.conf` 中的签名检查项。
6. 下载本地 `.ipk` 包，包括 `luci-app-tailscale` 和 MosDNS 离线包。
7. 使用 `make image PROFILE="generic"` 构建 squashfs UEFI 镜像。
8. 将生成的 `*squashfs-combined-efi.img.gz` 复制为 `build-out/immortalwrt-x86-64.img.gz`。
9. 调用 `scripts/openwrt_img_to_ova.py scan` 转换 OVA，并传入构建日期、ImmortalWrt 版本码和 commit。
10. 调用 `scripts/publish_releases.py` 创建 GitHub Release，并上传 `.img.gz`。
11. 调用 `record` 更新 `manifests/converted-images.json` 和 `docs/converted-images.md`，再由 workflow 提交记录。

转换记录使用 `image_sha256:BUILDER_VERSION` 作为去重 key。同一个镜像内容和同一个转换器版本不会重复转换；Release tag 额外包含构建日期、ImmortalWrt commit 和镜像 SHA 前 12 位，便于从 Release 页面追溯来源。

## 内置组件与运行注意事项

固件面向旁路由场景，内置常用代理、网络和诊断组件：

- LuCI 中文界面和 Argon 主题
- PassWall 2
- MosDNS
- OpenClash
- Nikki
- Momo
- Tailscale
- ZeroTier
- vlmcsd
- UPnP、irqbalance、nftables flow offload
- `luci-app-statistics` 和常用 collectd 模块
- `curl`、`htop`、`tcpdump`、`mtr`、`iperf3`、`bind-dig` 等诊断工具

注意：Nikki 和 Momo 都被打入固件，但它们的透明代理 nftables 规则存在冲突。运行时只启用其中一个。

固件内置旁路由调优：

- 启用 IPv4/IPv6 转发。
- 使用 BBR 和 `fq`。
- 提高 conntrack 与 socket buffer 上限。
- 禁止 ICMP redirect。
- 使用 loose `rp_filter`。
- 默认关闭 LAN DHCP、RA 和 DHCPv6，由主路由负责地址分配。

导入虚拟机后，请为 LAN 配置一个不与主路由冲突的静态地址。

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

发布 GitHub Release 需要已认证的 GitHub CLI：

```bash
python3 scripts/publish_releases.py dist/build-results.json
```

脚本只创建不存在的 Release tag；如果 tag 已存在，会跳过该项。

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

如果调整内置包列表，优先在 workflow 中保持显式包名。ImageBuilder 会在包名不存在时直接失败，这比静默跳过更容易排查。

## 常见排障

`missing required tools: qemu-img`：安装 `qemu-utils` 后重试。

`make image` 失败并提示找不到包：检查 workflow 中的 `PACKAGES` 列表、第三方 feed URL、release ipk 下载步骤是否仍然可用。

`skip already converted`：当前镜像 SHA256 和 `BUILDER_VERSION` 已存在于 `manifests/converted-images.json`。如果转换逻辑或 Release tag 规则确实变了，先递增 `BUILDER_VERSION`。

GitHub Release 已存在：`publish_releases.py` 会跳过已有 tag，适合重复运行。

## 安全与配置

保持仓库私有。不要提交固件镜像、运行时配置密钥、GitHub token、VPN 凭据、代理订阅或其他敏感配置。

第三方 feed 和 `.ipk` 下载源会进入构建链路，修改时需要确认来源可信，并在提交或 PR 描述中说明原因。若未来通过 `files/` 注入 OpenWrt 运行时配置，先确认其中不包含任何秘密信息。

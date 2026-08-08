# ImmortalWrt Builder

自动构建适用于 x86_64 虚拟化环境的 ImmortalWrt 固件，并发布 raw `.img.gz` 和可导入 ESXi 的 OVA。

当前旁路由迁移流程是先用临时地址灰度运行，再在维护窗口接通第二条 VMware 链路并接管旧机地址。OVA 默认只包含一张 VMXNET3 网卡；第二张网卡需要在 VMware 中手动添加，并连接到旧 VM 第二张网卡所在的 Port Group。

本项目使用 ImmortalWrt ImageBuilder，不从源码完整编译发行版。默认生成两个 flavor：

- `standard`：PassWall 2、OpenClash、Nikki、MosDNS 等常用旁路由组件。
- `daed`：使用 `dae`/`daed`，不包含与其冲突的透明代理栈。

两个 flavor 都保留 Tailscale 远程访问和 KMS 激活组件，移除未使用的 ZeroTier 与 MiniUPnP，并默认包含 `luci-ssl`。daed 替换场景优先使用 `daed` flavor，别把几套透明代理栈塞进一台机器互相踩 nftables 规则。

## 替换旧旁路由

固件不会包含现场 IP，也不会直接抢占旧机地址。首次启动后先从 VMware 控制台执行一次（把尖括号占位符替换为现场值）：

```sh
/usr/sbin/bypass-router-configure \
  '<STAGING_IP>' '<TARGET_IP>' '<GATEWAY_IP>' '<TRUSTED_MANAGEMENT_CIDR>' --confirm
```

该命令会应用以下无秘密配置：

- LAN 使用临时地址，网关和 DNS 使用主路由地址；目标地址只保存给切换脚本。
- `eth0` 与 `eth1` 加入启用 STP 的 `br-lan`；旧系统里的接口可能叫 `eth3`，迁移依据是 VMware Port Group，不是 Linux 接口编号。
- DHCP、DHCPv6、RA 和流量卸载保持关闭。
- LuCI 在 LAN 地址提供 HTTPS，HTTP 仅重定向；daed 面板只绑定 LAN 地址的 `2023` 端口。SSH、LuCI 和 daed 管理端口仅允许指定可信管理网段访问。
- 系统日志缓冲区为 1 MiB，匿名磁盘挂载关闭。

推荐切换顺序：

1. 关机导入 OVA，将默认网卡连接到旧 VM 第一张网卡所在的 Port Group。暂时不要添加或接通第二张网卡。
2. 在 VMware 控制台运行上述初始化命令，然后访问临时地址的 HTTPS 管理页。首次证书是设备自签名证书。
3. 设置 root 强密码，写入并实际验证 `/etc/dropbear/authorized_keys` 中的 SSH 公钥。
4. 临时将一台客户端的旁路由、DNS 和代理目标改为临时地址，通过第一张网卡验证 MosDNS 和 daed 的真实流量。
5. 进入维护窗口，保留 VMware 快照和控制台，先关闭旧机，再为新 VM 添加第二张 VMXNET3 网卡，连接到旧 VM 第二张网卡所在的 Port Group。不要让旧、新两台二层桥同时连接这对 Port Group。
6. 在新机运行 `/usr/sbin/bypass-router-cutover check`。该检查要求 `br-lan` 至少有两个有载波的端口，而且两个端口在 3 秒采样期内都有流量；无流量时先从两侧各制造一次测试流量再重试。
7. 确认旧地址不再响应后运行 `/usr/sbin/bypass-router-cutover apply --confirm`。SSH 会断开，随后从目标地址的 HTTPS 管理页重连。
8. 再次验证客户端 DNS、透明代理、跨 Port Group 流量和回滚路径，稳定后才移除旧 VM。
9. 公钥登录确认无误后运行 `/usr/sbin/bypass-router-harden`，关闭 root 密码 SSH，并停用 LuCI 明文 HTTP。

`check` 只验证链路、实时流量和进程状态，无法替代第 4 步的真实客户端灰度。`apply` 在发现目标地址仍被占用时会拒绝接管，避免地址冲突。

镜像扩容后出现“backup GPT invalid”时，先创建 VMware 快照并确认控制台可用，再用磁盘工具把备份 GPT 移到磁盘末尾；这是会改分区表的维护操作，不由首次启动脚本自动执行。`sda128` 是 BIOS 引导保留分区，不是数据盘损坏，固件已经关闭 `anon_mount`，不会再把它当 exFAT 自动挂载。

## 当前版本边界

构建默认使用 ImmortalWrt 25.12.1 的 `x86/generic` ImageBuilder。第三方组件统一选用 OpenWrt 25.12 对应的 APK 资产；官方仓库签名仍由 ImageBuilder 自带密钥校验，本地附加 APK 会由 ImageBuilder 建立并签署独立索引。Tailscale 使用 25.12.1 官方包，避免旧第三方 LuCI 包与官方服务脚本冲突。

25.12 已从 opkg 切换到 apk，旧系统不应直接在线执行跨大版本全量包升级。替换旧旁路由时应使用本项目生成的新镜像并重新验证配置，保留 VMware 快照和旧 VM 回退路径。

每次发布仍须在独立构建中同时验证：ImageBuilder target/profile、MosDNS、daed/daede、Nikki/PassWall、APK 包解析、过滤脚本和 OVA 启动。禁止在现用旁路由上在线跨大版本升级。

## 构建计划

GitHub Actions 每天北京时间 02:00 自动执行构建。手动运行默认只生成短期 Actions artifact；只有明确选择 `publish_release=true` 才发布 Release。

定时构建通过全部检查后会自动发布。相同 flavor 和镜像 SHA 已存在于受管 Release 时不会重复发布。

## Release 资产

每个 flavor 使用独立 Release，并包含：

- `.img.gz`：PVE、裸盘或其他 raw image 场景。
- `.ova` 与 `.ova.sha256`：ESXi 导入和完整性校验。
- `build-metadata.json`：ImageBuilder、ImmortalWrt commit 和构建结果。
- `packages.spdx.json`：实际 ImageBuilder manifest 生成的 SPDX 2.3 包清单。
- `upstream-provenance.json`：官方 APK 索引、第三方 Release tag、asset ID、digest 和降级校验状态。
- `third-party-sources.json`：第三方组件的许可证及源码位置。
- GitHub artifact attestation：raw image 和 OVA 的构建来源证明。

## 供应链边界

ImageBuilder 使用 ImmortalWrt 官方 SHA256 校验。GitHub Release 依赖会先解析本次 `latest` 的 tag 和 asset ID，再按 asset ID 下载；API 提供 SHA256 digest 时强制校验，下载期间元数据发生变化时构建失败。

第三方 feed 不会被加入 ImageBuilder，也不会关闭官方仓库的 APK 签名校验。官方包元数据由 ImageBuilder 自带密钥验证；第三方包从已检查的 GitHub Release 资产下载到本地 `packages/`，API 提供 digest 时强制校验，没有 digest 时明确记录为 `unverified-upstream`。ImageBuilder 会为本地 APK 建立并签署独立索引。**这些记录是风险披露，不代表上游内容安全。** 对供应链要求严格的使用者应检查随 Release 发布的 provenance，或自行固定依赖后构建。

## 首次启动安全

项目不会注入密码、SSH key、VPN 配置、代理订阅、daed 凭据或其他运行时秘密，也不会从运行中的路由器导出这些内容。固件沿用 ImmortalWrt/OpenWrt 的首次启动认证行为：

1. 仅在可信 LAN 中首次启动。
2. 立即设置强 root 密码。
3. 配置完成前不要把 LuCI、SSH 或代理管理端口暴露到 WAN。
4. 导入前校验 SHA256，并验证 GitHub artifact attestation。

## 本地检查

```bash
python3 -m py_compile scripts/*.py
python3 -m unittest discover -s tests
go run github.com/rhysd/actionlint/cmd/actionlint@v1.7.7
shellcheck files/etc/uci-defaults/99-bypass-router.sh
shellcheck files/usr/sbin/bypass-router-configure \
  files/usr/sbin/bypass-router-cutover \
  files/usr/sbin/bypass-router-harden \
  files/usr/share/luci-app-daede/daed-filter-sync.sh
```

### daed 订阅名称过滤

`daed` 的群组 API 不能直接为整条订阅设置名称排除规则。固件中包含
`/usr/share/luci-app-daede/daed-filter-sync.sh`，用于更新持久订阅后，把符合条件的节点逐个同步到群组。

例如，将订阅 `codeap` 中名称不含“备用”的节点同步到 `proxy`：

```sh
/usr/share/luci-app-daede/daed-filter-sync.sh 'codeap' 'proxy' '备用'
```

脚本会先更新订阅，再生成过滤计划、校验配置并热加载 dae。建议关闭 daed
内置的订阅定时更新，改由 cron 调用该脚本，避免订阅已更新但群组成员尚未同步的窗口。

本地转换还需要 `qemu-img`。Ubuntu 可安装 `qemu-utils`。

## 许可证与源码

本仓库自有脚本、工作流和配置采用 [MIT License](LICENSE)。固件中的 ImmortalWrt、Linux 内核和第三方包分别遵循各自许可证，MIT 不覆盖这些二进制组件。

第三方来源和许可证见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) 以及每个 Release 的 `third-party-sources.json`。源码位置固定到本次构建解析出的完整 commit 或 Release tag，并附可下载的源码归档 URL；精确包版本见 `packages.spdx.json`。缺少许可证或精确源码记录会阻止发布。

固件按原样提供，不承诺适用于生产网络。使用者应自行评估第三方软件、出口管制、当地法律和网络安全风险。

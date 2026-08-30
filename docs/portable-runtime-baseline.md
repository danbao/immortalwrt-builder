# 便携式 OpenWrt 运行基线与升级保护

## 目标与边界

此基线让同一份 ImmortalWrt 25.12.1 IMG/OVA 同时适用于现机升级和异地新部署。镜像只包含官方签名软件与无隐私默认值，不包含任何密码、SSH 公钥、Dropbear 主机密钥、daed 数据库、订阅 URI、节点链接、Tailscale state 或现场备份。

保持单 profile、单 IMG/OVA、一个网卡。旁路由只承担 IPv4 转发和透明代理；IPv6 地址、网关与 DNS 继续由主路由直接提供。

## 固件内置基线

除 LuCI、Argon 和官方 daed 组件外，`config/build-profile.json` 还强制包含：

- `tailscale`、`luci-app-tailscale-community`、`luci-i18n-tailscale-community-zh-cn`
- `vnstat2`、`vnstati2`、`luci-app-vnstat2`、`luci-i18n-vnstat2-zh-cn`
- `open-vm-tools`

构建 manifest 必须解析出全部 `required_packages`；发布元数据逐项记录版本。Tailscale 使用 ImmortalWrt 25.12 官方签名版本，不混入 snapshot 软件源，也不覆盖静态二进制。

`files/etc/uci-defaults/99-bypass-router.sh` 是带版本标记的幂等迁移。首次运行会在 `/etc/openwrt-setup/backups/` 备份可能修改的 UCI 文件，只补充缺失的通用值；现场网络、DNS、账号、订阅和手工值保持不变。唯一强制兼容迁移是移除 Tailscale zone 的 `network` 引用，直接匹配 `device=tailscale0`，并删除会干扰 tailscaled 路由的已知错误 netifd 接口形态。

通用默认值包括：

- 关闭 LAN DHCP、RA 和 DHCPv6，关闭 flow offload。
- IPv4 forwarding、BBR/`fq`、packet steering、irqbalance。
- `Asia/Shanghai`、混合 NTP 池、Argon。
- Tailscale zone 默认拒绝输入和转发，只开放 IPv4 ICMP 与 TCP 22/80/443/2023，并允许 `tailscale → lan`。
- daed 未初始化时保持禁用；已有设置不被 uci-defaults 覆盖。
- vnStat 数据目录为 `/etc/vnstat/`，新部署默认五分钟保存。

`/etc/sysupgrade.conf` 明确保留：

```text
/etc/daed/wing.db
/etc/tailscale/
/etc/vnstat/
/etc/openwrt-setup/
```

OpenWrt 自身的配置保留机制仍负责 `/etc/config/`、Dropbear 配置与主机密钥。升级前必须用 `sysupgrade -l` 再次确认实际清单。

## 新环境向导

Release 中的 `setup-openwrt.sh` 与 IMG/OVA 一起进入 `SHA256SUMS`。主机需要 Bash、OpenSSH、curl、jq 和 tar，支持 macOS、Linux 和 WSL：

```bash
sha256sum -c SHA256SUMS --ignore-missing
chmod +x setup-openwrt.sh
./setup-openwrt.sh --target root@192.168.1.1
```

可选参数：

```text
setup-openwrt.sh [--target root@HOST] [--check] [--env-file PATH]
```

向导依次完成回滚备份、LAN 与自动重连、SSH 公钥、固件和持久化检查、daed、Tailscale 交互登录以及端到端验证。daed 阶段会复用已有管理员和配置，建立通用 `proxy` 组与只含日本/美国节点的 `ai` 组；韩国等其他节点仍留在 `proxy` 中。策略为国内直连、其他流量选最低移动平均代理，AI 域名使用 `ai`，Tailscale 进程、域名、STUN 3478 和 UDP 源端口 41641 强制直连。配置先执行 dry-run，失败时恢复阶段开始前的数据库副本。

向导状态文件默认位于 `${XDG_CONFIG_HOME:-$HOME/.config}/danbao-openwrt/setup.env`，权限为 `0600`，仅保存非敏感环境值。daed 密码、订阅和令牌仅驻留当前进程；退出后变量会清空。

Tailscale 登录后仍需在浏览器中批准该设备发布的 LAN CIDR，并在 Access Controls 中只授权需要访问该网段的用户或设备。不要恢复全网 `*:*` 放行规则，也不要把路由器配置成 exit node，除非另有明确需求。

## 现机升级前迁移

现场迁移必须遵循以下顺序，且不在同一次操作中刷机或重启：

1. 在路由器 `/root/` 创建 `0600` 的带时间戳压缩备份，并验证非空。
2. 停止 vnStat，把数据库复制到 `/etc/vnstat/`，只修改 `DatabaseDir`，保留现场 `SaveInterval`，再启动并验证统计连续。
3. 幂等补充 sysupgrade 保留清单。
4. 删除 `firewall.tailscale.network`，保留 `firewall.tailscale.device=tailscale0`，重载防火墙。
5. 验证 daed、Tailscale、vnStat、DNS 分流、P2P/公网出口和 `sysupgrade -l`。

不得修改现场 daed 用户、订阅、节点、Tailscale 登录状态、LAN 地址、DNS 或 SSH 凭据。恢复时先停止相关服务，再从 `/root/openwrt-portable-migration-*.tar.gz` 选择性还原；不要整包覆盖不同版本固件。

## 验证命令

仓库侧轻量验证：

```bash
python3 -m py_compile scripts/*.py
python3 -m unittest discover -s tests
bash -n scripts/setup-openwrt.sh
sh -n files/etc/uci-defaults/99-bypass-router.sh
shellcheck scripts/setup-openwrt.sh files/etc/uci-defaults/*.sh
```

真实 IMG/OVA 仍需通过 `dry-run` workflow 验证官方 manifest、SBOM、overlay、向导 Release 资产和全部校验和；新部署需在临时虚拟机完成一次人工向导验收。

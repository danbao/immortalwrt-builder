# ImmortalWrt Builder

自动构建适用于 x86_64 虚拟化环境的 ImmortalWrt 固件，并发布 raw `.img.gz` 和可导入 ESXi 的 OVA。

本项目使用 ImmortalWrt ImageBuilder，不从源码完整编译发行版。默认生成两个 flavor：

- `standard`：PassWall 2、OpenClash、Nikki、MosDNS 等常用旁路由组件。
- `daed`：使用 `dae`/`daed`，不包含与其冲突的透明代理栈。

## 构建计划

GitHub Actions 每周一北京时间 02:00 检查一次，以 `2026-01-05` 为固定纪元按 14 天间隔执行构建，跨年也不会受 ISO 第 53 周影响。手动运行默认只生成短期 Actions artifact；只有明确选择 `publish_release=true` 才发布 Release。

定时构建通过全部检查后会自动发布。相同 flavor 和镜像 SHA 已存在于受管 Release 时不会重复发布。

## Release 资产

每个 flavor 使用独立 Release，并包含：

- `.img.gz`：PVE、裸盘或其他 raw image 场景。
- `.ova` 与 `.ova.sha256`：ESXi 导入和完整性校验。
- `build-metadata.json`：ImageBuilder、ImmortalWrt commit 和构建结果。
- `packages.spdx.json`：实际 ImageBuilder manifest 生成的 SPDX 2.3 包清单。
- `upstream-provenance.json`：第三方 feed、Release tag、asset ID、digest 和降级校验状态。
- `third-party-sources.json`：第三方组件的许可证及源码位置。
- GitHub artifact attestation：raw image 和 OVA 的构建来源证明。

## 供应链边界

ImageBuilder 使用 ImmortalWrt 官方 SHA256 校验。GitHub Release 依赖会先解析本次 `latest` 的 tag 和 asset ID，再按 asset ID 下载；API 提供 SHA256 digest 时强制校验，下载期间元数据发生变化时构建失败。

第三方 feed 不会被加入 ImageBuilder，也不会关闭官方 feed 的签名校验。工作流会快照第三方索引、按索引 SHA256 镜像所有包到本地 `packages/`，并在下载后重新确认索引未变化。由于第三方签名公钥尚未全部建立独立信任链，这些镜像记录为 `hash-verified-packages-untrusted-signing-key`。没有 API digest 的 GitHub 资产记录为 `unverified-upstream`。**这些记录是风险披露，不代表上游内容安全。** 对供应链要求严格的使用者应检查随 Release 发布的 provenance，或自行固定依赖后构建。

## 首次启动安全

项目不会注入密码、SSH key、VPN 配置、代理订阅或其他运行时秘密。固件沿用 ImmortalWrt/OpenWrt 的首次启动认证行为：

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
```

本地转换还需要 `qemu-img`。Ubuntu 可安装 `qemu-utils`。

## 许可证与源码

本仓库自有脚本、工作流和配置采用 [MIT License](LICENSE)。固件中的 ImmortalWrt、Linux 内核和第三方包分别遵循各自许可证，MIT 不覆盖这些二进制组件。

第三方来源和许可证见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) 以及每个 Release 的 `third-party-sources.json`。源码位置固定到本次构建解析出的完整 commit 或 Release tag，并附可下载的源码归档 URL；精确包版本见 `packages.spdx.json`。缺少许可证或精确源码记录会阻止发布。

固件按原样提供，不承诺适用于生产网络。使用者应自行评估第三方软件、出口管制、当地法律和网络安全风险。

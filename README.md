# ImmortalWrt Builder

使用官方 ImageBuilder 自动构建 ImmortalWrt 25.12.1 `x86/generic` 固件，并生成 raw `.img.gz`、ESXi OVA、构建元数据和 SPDX 2.3 软件包清单。

## 构建原则

- 固件使用官方 `generic` profile 的默认软件包集合。
- 不传入自定义 `PACKAGES`，不注入 `FILES` 覆盖。
- 不包含第三方代理、DNS 插件或本仓库的旁路由运行时配置。
- 不下载或安装第三方代理、DNS、LuCI 插件。
- ImageBuilder 下载必须通过官方 `sha256sums` 校验。

官方发布目录：<https://downloads.immortalwrt.org/releases/25.12.1/targets/x86/generic/>

## 自动构建

GitHub Actions 每天北京时间 02:00 执行构建，也可以手动运行。手动运行默认只上传短期 Actions artifact；只有明确设置 `publish_release=true` 才发布 GitHub Release。

每次构建执行以下流程：

1. 获取并校验 ImmortalWrt 25.12.1 `x86/generic` ImageBuilder。
2. 使用 `make manifest PROFILE="generic"` 记录官方默认软件包。
3. 使用 `make image PROFILE="generic"` 构建固件。
4. 复制 raw 镜像并转换为单网卡 VMXNET3 ESXi OVA。
5. 生成构建元数据、上游来源记录和 SPDX 软件包清单。
6. 对发布产物生成 GitHub artifact attestation。

## 本地验证

```sh
python3 -m py_compile scripts/*.py
python3 -m unittest discover -s tests
go run github.com/rhysd/actionlint/cmd/actionlint@v1.7.7
```

本仓库不保存密码、订阅、VPN 配置、SSH 密钥或现场网络信息。

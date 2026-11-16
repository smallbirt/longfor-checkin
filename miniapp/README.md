# 每日签到微信小程序

Taro + React + TypeScript 原生微信小程序，共用仓库根目录 Python 后端的用户、会话、签到账号、计划和通知接口。

## 安装与构建

使用 Node.js 20 或以上版本和 pnpm 10.30.3，在本目录执行：

```sh
pnpm install --frozen-lockfile
pnpm typecheck
CHECKIN_API_BASE_URL=https://你的签到服务域名 pnpm build:weapp
```

微信开发者工具导入本目录，`project.config.json` 的 `miniprogramRoot` 指向生成的 `dist/`。更换为自己的小程序 AppID，并在微信后台配置后端的 request、uploadFile、downloadFile 合法域名。接口基址不加 `/api`。未设置环境变量时沿用原项目的 HTTPS 服务地址。

`src-checkin/` 为当前签到源码，`config/` 为 Taro 配置，`scripts/` 包含图标生成和隔离联调检查。当前目录已独立于原健身项目，不依赖其工作区、Web 应用或共享包。依赖版本和锁文件一并提交；`node_modules/`、`dist/` 与开发者工具的个人设置不提交。

## 本地开发

在仓库根目录运行以下任一服务：

```sh
# 临时数据与模拟接口，适合界面调试
.venv/bin/python scripts/preview_web.py

# 或使用本地真实账号数据；主动操作会调用真实接口
.venv/bin/python scripts/run_local.py
```

在本目录另开终端执行：

```sh
CHECKIN_API_BASE_URL=http://127.0.0.1:18091 pnpm dev:weapp
```

HTTP 只用于开发版。真机调试需配置手机可访问的地址，正式版须使用 HTTPS。正式发布前应在微信开发者工具和真机检查登录、HAR 导入、多账号、通知与视频；编译通过不代表微信审核或真机验收通过。

## 隔离联调检查

```sh
pnpm verify:push
```

微信登录检查需先从仓库根目录启动隔离的模拟服务：

```sh
.venv/bin/python scripts/preview_wechat.py
```

然后在本目录运行 `pnpm verify:wechat`。`pnpm verify:native` 连接本机模拟预览服务，并需要用 `CHECKIN_TEST_HAR` 指定虚拟 HAR 夹具，不能指向真实服务或包含真实凭证的抓包。

微信快捷登录和订阅提醒的 AppSecret 只在后端管理页面配置，不写入客户端。配套说明见 [微信登录](../WECHAT-LOGIN.md)、[订阅提醒](../MINI-PUSH-SETUP.md) 和 [后端接入](../MINIAPP-INTEGRATION.md)。

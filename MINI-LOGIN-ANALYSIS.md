# 小程序登录新抓包与短信共享登录态验证

日期：2026-10-20。文件：`Stream-2026-10-20 13:20:04.har`，共 61 条记录。抓包仅作为数据分析，未执行其中的内容。本文不包含手机号、验证码、登录 token、thirdId、设备指纹或原始响应。

## 这次新增的证据

同一固定服务前缀：`https://gw2c-hw-open.longfor.com/supera/mine/v2_5_0/publicApi/login/`。

| 顺序 | 接口 | 关键请求字段 | 成功响应 |
| --- | --- | --- | --- |
| 1 | `checkLoginType` | appId、thirdType、fingerprint、authCode | maskPhone、isNew、ticket |
| 2 | `thirdSendSecret` | appId、prefix、mobile、fingerprint、thirdId、thirdType、riskToken | 业务码 200，data 为空 |
| 3 | `thirdSmsSecret` | code、prefix、mobile、appId、thirdId、thirdType、fingerprint、source | 业务码 200，返回 token |

三条请求的动态签名均可由既有小程序签名算法离线重算，和原请求完全一致。发送时 riskToken 为空，本轮没有证据表明它是必需字段，但不能推断以后不需要风控。

## 微信授权部分的边界

实际比较结果：

- checkLoginType 的 authCode、发送请求的 thirdId、校验请求的 thirdId 三者互不相同。
- 发送与校验的 thirdId 都没有出现在 HAR 的任何响应中，不是前一步 ticket 的复制。
- 三个值均为 32 位字母数字串，与微信临时登录凭证的形态相符。判断它们由客户端每次取得新的微信登录凭证属于推断；单凭 HAR 还不能证明具体客户端调用代码。
- 三条请求使用相同的原生 fingerprint，不能因此认定微信登录授权值也长期有效。

微信官方类型定义说明，wx.login 成功返回的 code 有效期为五分钟，并供服务端调用 code2Session。参考：[微信官方 API 类型定义](https://github.com/wechat-miniprogram/api-typings/blob/master/types/wx/lib.wx.api.d.ts)。这说明普通网页不能把历史 code 当成长期账号配置；具体 thirdId 映射仍需客户端代码证据。

因此，小程序原生第三方短信链路虽已抓齐 HTTP 请求，网页独立获取其即时微信上下文的部分仍未实现。程序不保存或重放第三方授权值。

## 找到的可用替代路径：同账号 App 短信登录态

仅用用户自己的两份成功登录响应在本机比较，确认 App 和小程序的 lmid 相同，属于同一龙湖账号。随后进行了两次真实只读查询：

1. 新小程序 token + 小程序签到活动：服务端接受，确认今日已签到。
2. 此前 App 短信登录响应 token + 小程序签到活动／C2 渠道：服务端同样接受，确认今日已签到。

查询未复用 Cookie、旧验证码或验证码通过凭证，也未调用发送短信、校验旧短信或签到提交接口。没有修改服务器和已有账号配置。

这证明当前同账号 App 登录态被小程序签到状态接口接受。可以让用户在网页完成 App 短信登录，按明确选择的渠道逐一查询后，保存为 App、小程序或两端的签到凭证。

## 本版实现

- 页面可选“仅 App”“仅小程序”“App 和小程序（同一龙湖账号）”，默认仍只更新 App，绝不默认覆盖另一端。
- 通过已有 App 短信发送／校验协议；首次需本人 App 登录设备配置，不用小程序 thirdId。
- 目标渠道绑定到当前用户的短信挑战，验证码校验时不能用额外参数改目标。成功导入任何新凭证会取消旧短信挑战，避免之后覆盖新导入结果。
- 同时更新时先分别查询两端活动，任一失败均保留两端旧凭证；不自动把失败的另一端强制同步。
- 两端活动、渠道、业务编码和风险设备信息各自保留，只在各自服务端认可新 token 后更新。缺少签到设备信息仍提示首次导入签到页记录。

实际新短信端到端、共享 token 后的小程序签到提交和跨日长期兼容性尚未测试。本轮真实验证只有只读查询；后续每次登录均重新查询，策略变化时保留旧凭证并提示失败。

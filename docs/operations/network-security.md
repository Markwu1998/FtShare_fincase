# 网络与主机配置

> 状态：现行 · 适用版本：v0.1.x · 最后核对：2026-11-19

FastAPI 默认监听 `127.0.0.1:8080`。对外访问必须经过 HTTPS 反向代理，并限制访问范围。下文的主机检查命令来自 macOS 遗留路径，仅作为示例。

## 基本原则

- **不要把 8080 直接暴露到公网**：FastAPI 没有认证与限流。
- 对外使用 nginx/Caddy + HTTPS + 访问控制。
- SSE（`/api/etf-burst/stream`）需关闭代理响应缓冲。
- RQData 凭据只在采集侧；网页进程不需要 `RQDATAC2_CONF`。
- 数据目录与 `.env` 均不入库。

## 验证 HTTPS 入口

配置域名、证书和路由后，从外部网络验证目标主机：

```sh
curl --noproxy '*' --connect-timeout 5 --max-time 15 \
  --resolve etf.example.com:443:YOUR_PUBLIC_IPV4 \
  https://etf.example.com/api/health
```

核对证书与返回的构建信息，再通过手机移动网络访问完整页面，检查图表、雷达及数据时间。IPv4 和 IPv6 分别验证；出口地址、NAT 及入站路由一并检查。验证通过后配置公共 DNS。

## 服务配置

| 项目 | 配置 |
|---|---|
| 网页进程 | 普通专用用户、独立目录、回环监听 |
| 公网入口 | HTTPS 代理接收 443；80 按跳转与证书验证需要配置 |
| 读取接口 | GET／HEAD，按固定路由读取已发布数据 |
| 请求管理 | 反向代理层按实际访问量限流 |
| 行情来源 | 后台统一采集；网页读取文件缓存 |
| 代理信任 | 显式信任同机代理的客户端地址转发 |
| 凭据与证书 | 后台 Secret 或权限 600 的受限文件 |
| 历史恢复 | 保存快照与代码版本，更新前验证，失败恢复前一版本 |

## 主机检查（macOS 遗留）

```sh
npm run mac:check
```

报告保存到 `.local/macmini-check.json`，包含网卡与出口地址、代理启用状态、端口、防火墙、凭据文件权限及服务状态。仅在遗留 macOS 回退环境使用，详见 [macOS](../archive/legacy-node/macos.md)。

## 相关文档

- [部署](deployment.md)
- [运维手册](runbook.md)
- [安全说明](../../SECURITY.md)

# Cloudflare Tunnel 部署手册（clu7.xyz → 本地 ashare）

把本地 `ashare serve`（`127.0.0.1:8000`）通过 Cloudflare Tunnel 暴露到自有域名 `clu7.xyz`，
全程 HTTPS，且**不需要公网 IP、不用开路由器端口**。

## 架构

```
浏览器 → https://clu7.xyz → Cloudflare 边缘(自动 HTTPS)
        → 加密隧道(出站，无需开端口) → 本机 cloudflared → http://localhost:8000
```
本机只发**出站**连接到 Cloudflare，外部无法直连你的机器，比开端口安全得多。

---

## 一次性准备

### 1. 域名托管到 Cloudflare（改 NS）
1. dash.cloudflare.com → **Add a site** → 输入 `clu7.xyz` → 选 Free 套餐
2. Cloudflare 给两个名称服务器（本项目实际分到的是）：
   ```
   drake.ns.cloudflare.com
   joan.ns.cloudflare.com
   ```
3. 到**阿里云域名控制台**（注册商）→ 域名管理 → **DNS 修改 / 修改DNS服务器**（注意不是"解析设置"）
   → 把 `DNS25/26.HICHINA.COM` 换成上面两个 → 保存
4. 等生效：Cloudflare 面板显示「**您的域现在受 Cloudflare 保护**」= Active（几分钟~几小时）
   - 判断命令（注意本机若开代理 fake-ip 会污染 DNS，结果不准；**以 Cloudflare 面板 Active 为准**）：
     ```bash
     dig NS clu7.xyz @a.nic.xyz +short   # 出现 *.ns.cloudflare.com 即通
     ```

### 2. 安装并登录 cloudflared
```bash
brew install cloudflared
cloudflared tunnel login      # 开浏览器授权你的 CF 账号并选中 clu7.xyz，生成 ~/.cloudflared/cert.pem
```

### 3. 建隧道 + 路由 DNS
```bash
cloudflared tunnel create ashare-web           # 生成隧道与凭证 ~/.cloudflared/<ID>.json
cloudflared tunnel route dns ashare-web clu7.xyz   # 给 clu7.xyz 加 CNAME 指向隧道
```
> 本项目当前已建的隧道：`hello-test`，ID `eebd1a25-6723-4cf4-8d28-7129ae28265b`（用于测试）。

### 4. 写配置 `~/.cloudflared/config.yml`
```yaml
tunnel: <你的隧道ID>
credentials-file: /Users/ltc/.cloudflared/<你的隧道ID>.json
ingress:
  - hostname: clu7.xyz
    service: http://localhost:8000     # 指向本地 ashare（测试时可先用 8080 hello）
  - service: http_status:404
```
校验：`cloudflared tunnel ingress validate`

---

## 日常运行

前提：本地 `ashare serve` 一直开着（`127.0.0.1:8000`）。

```bash
# 前台跑（调试用，Ctrl+C 停）
cloudflared tunnel run ashare-web

# 或装成开机自启的系统服务（常驻，读 ~/.cloudflared/config.yml）
sudo cloudflared service install
# 之后：sudo launchctl start com.cloudflare.cloudflared  （macOS）
```
访问 `https://clu7.xyz`。

---

## ⚠️ 上线真服务前，必须加鉴权（Cloudflare Access）

ashare 的 Web 后台**无鉴权且有破坏性接口**（改 cookie/Telegram token、触发抓取、清空数据）。
公开暴露前**必须**在前面加一层登录，否则任何人扫到 `clu7.xyz` 就能删数据/改配置。

dash.cloudflare.com → **Zero Trust → Access → Applications → Add an application → Self-hosted**：
- Application domain：`clu7.xyz`
- Policy：Action=Allow，规则 = **Emails 包含你的邮箱**
- 保存。之后访问 `clu7.xyz` 会先要求邮箱验证码登录，挡住所有陌生人（免费）。

---

## 运维速查

```bash
cloudflared tunnel list                    # 看隧道
cloudflared tunnel info ashare-web         # 看某隧道连接
cloudflared tunnel run ashare-web          # 手动跑
pkill -f "cloudflared tunnel"              # 停掉本地隧道进程（对外即 502，安全）
cloudflared tunnel route dns ashare-web clu7.xyz   # 重新绑/换域名
cloudflared tunnel delete ashare-web       # 删隧道（先停）
```
切换后端端口：改 `config.yml` 的 `service:`（8080↔8000）→ 重启 `cloudflared tunnel run`。

---

## 排错

| 现象 | 原因 / 处理 |
|------|------|
| 面板 Active 但本机 `dig` 仍显示旧 NS | 本机代理(Clash fake-ip，返回 `198.18.x`)污染 DNS，**以 Cloudflare 面板为准** |
| 访问 clu7.xyz 报 **502/1033** | 隧道没在本地跑，或 `service` 指向的端口没服务 → 起 `cloudflared tunnel run` + 确认 8000/8080 在监听 |
| `cloudflared tunnel list` 报 no cert.pem | 没登录 → `cloudflared tunnel login` |
| DNS 迟迟不生效 | 新域名 + 换 NS，注册局推送可能要数小时，耐心等；以 Cloudflare Active 为准 |

---

## 安全说明（重要）

- **出站隧道模型**本身较安全：外部连不到你机器，只有 cloudflared 主动出站。
- 但**真正的风险在应用层**：ashare 后台无鉴权 + 有删除/改配置接口。
  → 暴露真服务**必须**配 Cloudflare Access（见上）。只测 hello 时无所谓。
- **凭证保管**：`~/.cloudflared/cert.pem` 和 `<ID>.json` 是密钥级文件，别外泄、别进 git（默认在 home 目录，不在项目里）。
- **最小暴露**：`ashare serve` 保持绑 `127.0.0.1`（默认），只让 cloudflared 在本机连它，不要绑 `0.0.0.0`。
- 不用时 `pkill -f "cloudflared tunnel"` 停掉隧道，对外立即不可达。

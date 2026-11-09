# Cursor 远程窗口的模型地区限制

Remote SSH 连到国内或香港服务器时，本机目录里能用的 Gemini、Claude、GPT，在远程窗口里可能整组消失。Cursor 按**发出 AI 请求的那台机器的出口 IP** 决定模型列表，不按账号注册地，也不按人坐在哪里。

## 怎么判断

同一账号、同一台电脑：

- 打开本机目录，模型列表里有 Gemini。
- 用 Remote SSH 打开远程项目后，Gemini（往往还有 Claude、GPT）不见了，只剩 Composer、Grok、Kimi、GLM 这类不受该地区限制的模型。

这就是远程主机出口被供应商拒绝，不是套餐把模型关掉了。

再在远程主机上查出口，不要用 IDE 内置浏览器的结果代替主机出口。两者可以不是同一个 IP。星页这台开发机上曾出现：内置浏览器走香港 `45.146.232.53`（Nearoute），主机自己的请求走广州 `8.138.118.232`（阿里云）。Agent 在远程窗口里走的是主机自己的网络，所以以主机上的 `curl` 为准：

```bash
curl -fsS https://ipinfo.io/json
```

## 供应商名单

Cursor 只是把供应商的地区限制反映到模型列表里。Google 的 Gemini **网页版**和 **API** 不是同一份名单。Cursor 走 API。

截至 2026-10，[Gemini API 可用地区](https://ai.google.dev/gemini-api/docs/available-regions)包含台湾、新加坡、日本、美国等，**不包含中国大陆、香港、澳门**。2026 年 3 月对香港开放的是网页版，API 没有一起放开。自己在 Cursor 里填写 Google API Key 也一样，请求仍从被拒绝的地区发出时，Google 会拒绝。

官方说明：[Cursor Regions](https://cursor.com/docs/account/regions)。

## 为什么以前远程窗口也能用

2026 年 9 月前后，Cursor 上线 `cursor_agent_host`。Remote SSH 窗口里的 Agent 改到远程主机上跑，AI 请求从远程主机的出口发出。在那之前，远程窗口的 AI 请求仍从本机客户端发出，所以本机在支持地区时，连上国内服务器也能用 Gemini。

另一类「突然不能用」来自桌面端自己拉模型列表的方式。2026 年 6 月底（约 3.8 / 3.9）起，拉列表的进程走独立 HTTP/2，经常不经过系统代理。浏览器里看到的出口和 Cursor 用来判断地区的出口可以不同。

## 改回从本机发出

远程窗口可以把 Agent 请求固定改回本机客户端，恢复「文件和终端在远程、模型请求在本机」的旧行为。

要求 Cursor **3.21.16 及以上**。设置写在**远程主机**上，本地窗口会忽略它。Agent 宿主启动时读一次，改完必须重新加载窗口。

远程机器设置文件：

`~/.cursor-server/data/Machine/settings.json`

```json
{
  "cursorAgentHost.remoteInferenceRoute": "always"
}
```

| 值 | 行为 |
| --- | --- |
| `auto` | 默认。探测一次远程出口，再决定走远程网络还是本机 |
| `always` | 远程窗口一律经本机客户端转发，不探测 |
| `never` | 远程窗口一律用远程主机自己的网络 |

也可以在 Cursor 设置里切到 **Remote [SSH: 主机名]** 那一页，搜索 `remoteInferenceRoute`，把 **Cursor Agent Host: Remote Inference Route** 设为 `always`，然后执行 **Developer: Reload Window**。

这项配置在 cursor-server 的数据目录里，不在项目仓库中。重装或清空 `~/.cursor-server/data` 后需要再写一次。`always` 在 private-inference 远程上会被忽略，因为本机转发会跳过那条链路。

## 星页这台机器

- 主机出口：广州，`8.138.118.232`，阿里云。
- 远程 Cursor 版本：3.24.12（2026-10-09）。
- 已在该机器的 `Machine/settings.json` 写成 `always`。重新加载连着 star-page 的窗口后生效。

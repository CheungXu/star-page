# code

代码目录，存放主框架模块。

## Dockerfile

`code/Dockerfile` 是后续 Next.js/Node.js 应用的生产镜像模板。

当前仓库尚未初始化应用代码，因此该 Dockerfile 主要用于沉淀部署约定。应用初始化后，需要确保：

- 仓库根目录存在 `package.json`。
- `package.json` 中存在 `build` 脚本。
- Next.js 项目启用 standalone 输出，或按实际框架调整 Dockerfile 的产物复制路径。

构建命令：

```bash
docker build -f code/Dockerfile -t star-page:latest .
```

## LLM

`code/llm/` 是大模型请求适配层。

当前提供统一 `LlmClient` 接口，并先支持：

- OpenAI Chat Completions 兼容格式。
- Anthropic Messages 兼容格式。

业务代码不应直接调用具体厂商 SDK 或 HTTP API，应优先通过该模块接入。

# SSH 远程连接配置（阿里云轻量服务器）

## 连接别名

本机 `~/.ssh/config` 已配置：

```text
Host star-page-aliyun
HostName 8.138.118.232
User root
Port 22
IdentityFile ~/.ssh/stars-page-demo.pem
```

## 当前密钥对

已在阿里云控制台为服务器创建密钥对，实例关联信息：

```text
实例 ID：d21728930b834bcfb59e2d548ca84db5
私钥文件：~/.ssh/stars-page-demo.pem
```

私钥原始下载位置为 `~/Downloads/stars-page-demo.pem`。由于 Cursor 进程可能没有读取 `Downloads` 的权限，需要在本机终端执行：

```bash
mkdir -p ~/.ssh
cp ~/Downloads/stars-page-demo.pem ~/.ssh/stars-page-demo.pem
chmod 600 ~/.ssh/stars-page-demo.pem
```

完成后测试：

```bash
ssh star-page-aliyun "uname -a"
```

## 在 Cursor 中连接

1. 安装扩展 **Remote - SSH**（Cursor 通常已内置）。
2. `Cmd+Shift+P` → 输入 `Remote-SSH: Connect to Host...`
3. 选择 **`star-page-aliyun`**
4. 首次连接会在新窗口打开远程 Ubuntu 环境。

也可在左下角远程图标中选择同一主机。

## 安全建议

- 上传公钥成功后，在阿里云防火墙中考虑将 SSH `22` 端口限制为个人固定 IP。
- 建议在服务器上关闭 root 密码登录，仅保留密钥登录（确认密钥可用后再改）。

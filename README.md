# PDF Automated Distributor
这是一个基于 Streamlit 的 PDF 自动化分发工具。它能自动完成 PDF 的栅格化（去矢量化）、多渠道水印添加（飞书/企微/红书）、加密，并自动上传至百度网盘指定目录。

## ✨ 特性

- **安全压制**: 将 PDF 转换为图片再重组，防止源文件内容被复制。
- **动态水印**: 支持平铺水印算法，自动调整角度和密度。
- **多渠道分发**: 一键生成飞书、企业微信、小红书三个版本的专用文件。
- **云端同步**: 对接百度网盘 API，自动归档。
- **持久化授权**: Docker 部署重启不丢失网盘登录状态。

## 🛠️ 部署指南 (Ubuntu VPS)

### 1. 环境准备
确保服务器已安装 Docker 和 Git。

### 2. 获取代码
```bash
git clone https://github.com/frosn0w/pdf-distributor.git
cd pdf-distributor
```
### 3. 配置环境
```bash
cp .env.example .env
vi .env
```
### 4. 准备水印素材 (可选)
如果使用默认水印功能，请将 WM.Feishu.png, WM.WeCOM.png 等文件上传至项目根目录。

### 5. 一键启动
运行部署脚本，它会自动处理构建和运行：
```bash
chmod +x deploy.sh
./deploy.sh
```
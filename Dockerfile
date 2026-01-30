# 使用轻量级 Python 镜像
FROM python:3.11-slim

# 设置工作目录
WORKDIR /app

# 安装必要的系统库（PyMuPDF 渲染 PDF 需底层库支持）
RUN apt-get update && apt-get install -y \
    build-essential \
    libgl1-mesa-glx \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖文件并安装
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制项目代码和默认水印图
COPY . .

# 暴露 Streamlit 默认端口
EXPOSE 8501

# 启动命令
CMD ["streamlit", "run", "PDF-Distributor.py", "--server.port=10031", "--server.address=0.0.0.0"]

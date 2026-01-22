# 导入必要的库
import streamlit as st  # 用于构建Web界面
import fitz  # PyMuPDF，用于处理PDF文件
import os, json, requests, hashlib, urllib.parse, time, tempfile, math  # 系统操作、网络请求等辅助库
from datetime import datetime  # 用于日期时间处理
from pathlib import Path  # 用于文件路径处理

# --- [1. 页面设置] ---
# 配置Streamlit页面标题和布局
st.set_page_config(page_title="PDF 安全分发助手", layout="centered")

# 定义各渠道默认水印文件名映射
DEFAULT_WM = {
    'feishu': 'WM.Feishu.png',  # 飞书渠道默认水印
    'wecom': 'WM.WeCOM.png',  # 企微渠道默认水印
    'red': 'WM.Red.png'  # 小红书渠道默认水印
}

# --- [2. 百度网盘管理类] ---
class BaiduManager:
    """百度网盘管理类，负责授权验证、文件上传等操作"""
    
    def __init__(self, ak, sk, t_file):
        """
        初始化百度网盘管理器
        参数:
            ak: 百度开放平台App Key
            sk: 百度开放平台Secret Key
            t_file: 存储Token的文件名
        """
        self.ak, self.sk, self.t_file = ak, sk, t_file  # 保存关键参数
        self.api_base = "https://pan.baidu.com/rest/2.0/xpan"  # API基础地址
        self.headers = {'User-Agent': 'pan.baidu.com'}  # 请求头设置
        self.token_data = self._load_token()  # 加载已有的Token数据

    def _load_token(self):
        """
        从文件加载Token数据
        返回:
            Token字典或None（如果文件不存在或加载失败）
        """
        if os.path.exists(self.t_file):
            try:
                with open(self.t_file, 'r') as f:
                    return json.load(f)
            except:
                return None  # 加载失败时返回None
        return None  # 文件不存在时返回None

    def save_token(self, data):
        """
        保存Token数据到文件
        参数:
            data: 包含Token的字典
        """
        with open(self.t_file, 'w') as f:
            json.dump(data, f)
        self.token_data = data  # 更新内存中的Token数据

    def refresh_token_safe(self, max_retries=3):
        """
        带有重试限制的Token刷新逻辑
        参数:
            max_retries: 最大重试次数
        返回:
            刷新成功返回True，失败返回False
        """
        # 检查是否有可用的refresh_token
        if not self.token_data or 'refresh_token' not in self.token_data:
            return False

        rf_tk = self.token_data['refresh_token']
        url = "https://openapi.baidu.com/oauth/2.0/token"
        params = {
            "grant_type": "refresh_token",
            "refresh_token": rf_tk,
            "client_id": self.ak,
            "client_secret": self.sk
        }

        # 重试机制，使用指数退避策略
        for i in range(max_retries):
            try:
                res = requests.get(url, params=params, timeout=10).json()
                if 'access_token' in res:
                    self.save_token(res)
                    return True
                else:
                    # 百度返回明确错误（如refresh_token失效），不再重试
                    break 
            except Exception as e:
                # 仅在网络异常时重试
                if i < max_retries - 1:
                    time.sleep(2 * (i + 1))  # 指数退避：2s, 4s, 6s
                continue
        return False

    def check_auth(self):
        """
        核心鉴权逻辑：验证Token有效性，失效时自动尝试刷新
        返回:
            授权有效返回True，否则返回False
        """
        if not self.token_data:
            return False
        
        try:
            # 尝试执行简单的列表操作验证Token
            res = requests.get(
                f"{self.api_base}/file?method=list&access_token={self.token_data['access_token']}&dir=/apps",
                headers=self.headers,
                timeout=5
            ).json()
            
            if res.get('errno') == 0:
                return True  # Token有效
            elif res.get('errno') in [110, 111]:  # 110: Token过期, 111: Token无效
                # 触发静默刷新
                return self.refresh_token_safe()
            else:
                return False  # 其他错误
        except:
            # 网络异常，暂不判定授权失效
            return False

    def upload(self, local_path, app_folder, remote_sub):
        """
        上传文件到百度网盘
        参数:
            local_path: 本地文件路径
            app_folder: 应用文件夹名
            remote_sub: 远程子目录名
        返回:
            (状态字符串, 消息字符串)
        """
        fn = os.path.basename(local_path)  # 获取文件名
        td = f"/apps/{app_folder}/{remote_sub}"  # 目标目录路径
        tk = self.token_data['access_token']  # 获取访问令牌
        
        # 检查文件是否已存在
        if self.check_exists(td, fn):
            return "EXISTS", "同名文件已存在"
            
        # 计算文件MD5值
        md5 = hashlib.md5(open(local_path, 'rb').read()).hexdigest()
        
        # 预创建文件
        pre = requests.post(
            f"{self.api_base}/file?method=precreate&access_token={tk}",
            data={
                'path': f"{td}/{fn}",
                'size': str(os.path.getsize(local_path)),
                'isdir': '0',
                'autoinit': '1',
                'block_list': json.dumps([md5]),
                'rtype': '3'
            },
            headers=self.headers
        ).json()
        
        if 'uploadid' not in pre:
            return "FAILED", f"预处理失败: {pre.get('errno')}"
            
        # 上传文件块
        up_url = f"https://d.pcs.baidu.com/rest/2.0/pcs/superfile2?method=upload&access_token={tk}&type=tmpfile&path={urllib.parse.quote(f'{td}/{fn}')}&uploadid={pre['uploadid']}&partseq=0"
        requests.post(up_url, files={'file': open(local_path, 'rb')}, headers=self.headers)
        
        # 完成文件创建
        final = requests.post(
            f"{self.api_base}/file?method=create&access_token={tk}",
            data={
                'path': f"{td}/{fn}",
                'size': str(os.path.getsize(local_path)),
                'isdir': '0',
                'uploadid': pre['uploadid'],
                'block_list': json.dumps([md5]),
                'rtype': '3'
            },
            headers=self.headers
        ).json()
        
        return "SUCCESS", f"{td}/{fn}" if 'fs_id' in final else "落盘失败"

    def check_exists(self, dir_path, filename):
        """
        检查文件是否已存在（注意：原代码中未实现此方法）
        参数:
            dir_path: 目录路径
            filename: 文件名
        返回:
            存在返回True，否则返回False
        """
        # 注意：该方法在原代码中被调用但未实现，需要补充实现
        # 这里仅提供一个空实现，实际使用时需要完成
        return False

# --- [3. 水印引擎] ---
def add_watermark(doc, wm_bytes, rot, w_pct, h_multiplier):
    """
    为PDF文档添加水印
    参数:
        doc: fitz.Document对象，待加水印的PDF文档
        wm_bytes: 水印图片的字节数据
        rot: 水印旋转角度（度）
        w_pct: 水印宽度占页面宽度的比例
        h_multiplier: 纵向间距（水印高度的倍数）
    """
    # 创建临时文件保存水印图片
    with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as f:
        f.write(wm_bytes)
        wm_p = f.name
    
    # 计算水印图片的原始尺寸
    wm_img = fitz.open(wm_p)
    iw, ih = wm_img[0].rect.width, wm_img[0].rect.height
    wm_img.close()
    
    # 创建临时PDF存放水印（利用show_pdf_page的旋转优势）
    src_wm_pdf = fitz.open()
    wm_page = src_wm_pdf.new_page(width=iw, height=ih)
    wm_page.insert_image(wm_page.rect, filename=wm_p)  # 插入水印图片，保留透明度
    
    # 为每一页添加水印
    for page in doc:
        # 计算水印在当前页面上的显示尺寸
        vw = page.rect.width * w_pct  # 水印宽度
        vh = vw * (ih / iw)  # 水印高度（保持比例）
        
        # 计算旋转后水印的边界框尺寸
        rad = abs(rot) * (math.pi / 180.0)
        bw = vw * math.cos(rad) + vh * math.sin(rad)
        bh = vw * math.sin(rad) + vh * math.cos(rad)
        
        # 计算纵向间距
        step_y = bh * h_multiplier
        
        # 在页面上垂直排列水印
        y = 150 + bh/2  # 起始Y坐标（考虑页面边距）
        while y <= page.rect.height - 150 - bh/2:
            # 计算水印位置（水平居中）
            r = fitz.Rect(
                (page.rect.width - bw) / 2,  # 左边界
                y - bh/2,  # 上边界
                (page.rect.width + bw) / 2,  # 右边界
                y + bh/2   # 下边界
            )
            # 添加水印（使用show_pdf_page实现旋转）
            page.show_pdf_page(r, src_wm_pdf, 0, rotate=rot)
            y += step_y  # 下移到下一个水印位置
            
    # 清理资源
    src_wm_pdf.close()
    if os.path.exists(wm_p):
        os.unlink(wm_p)  # 删除临时水印图片文件

# --- [4. 主界面] ---
st.title("📄 PDF 自动化助手")  # 应用标题

# A. 全局基础参数设置
with st.expander("🛠️ 全局配置 (网盘/API)", expanded=True):
    c_cfg1, c_cfg2 = st.columns(2)  # 创建两列布局
    with c_cfg1:
        # 优先从环境变量获取，无则使用默认值
        app_folder_name = st.text_input("网盘沙盒文件夹", value=os.getenv("APP_FOLDER", "转存分享助手"))
        file_prefix_base = st.text_input("文件前缀基准", value=os.getenv("FILE_PREFIX", "BLS"))
    with c_cfg2:
        # 敏感Key从环境变量获取，无则留空等待用户手动输入
        app_key = st.text_input("App Key", value=os.getenv("BAIDU_AK", ""))
        secret_key = st.text_input("Secret Key", value=os.getenv("BAIDU_SK", ""), type="password")
    t_file = st.text_input("Token 文件名", value="baidu_token.json")

# B. 画质参数设置
with st.expander("🖼️ 压制清晰度控制", expanded=False):
    st.info("💡 DPI 越高文字越清晰；Quality 决定压缩率。")
    c_q1, c_q2 = st.columns(2)  # 创建两列布局
    with c_q1:
        raster_zoom = st.slider("DPI 缩放倍率 (Zoom)", 1.0, 4.0, 2.5, step=0.5)
    with c_q2:
        jpg_quality = st.slider("图片压缩质量 (Quality)", 30, 100, 80)

# 初始化百度网盘管理器
mgr = BaiduManager(app_key, secret_key, t_file)

# C. 鉴权校验
if not mgr.check_auth():
    # 到达这里意味着：1. 没授权；2. 自动刷新 3 次都失败了
    st.error("🚨 百度网盘授权已失效，且自动续约失败。")
    st.info("原因可能是：长时间未登录、API 密钥变更或百度安全策略要求重新扫码。")

    # 生成授权URL
    auth_url = f"https://openapi.baidu.com/oauth/2.0/authorize?response_type=code&client_id={app_key}&redirect_uri=oob&scope=basic,netdisk"
    st.markdown(f"[🔗 点击此处获取授权码]({auth_url})")
    code = st.text_input("Code:")  # 输入授权码
    if st.button("确认授权"):
        # 交换授权码获取Token
        url = f"https://openapi.baidu.com/oauth/2.0/token?grant_type=authorization_code&code={code}&client_id={app_key}&client_secret={secret_key}&redirect_uri=oob"
        res = requests.get(url).json()
        if 'access_token' in res:
            mgr.save_token(res)
            st.success("授权成功!")
            st.rerun()  # 重新运行应用
    st.stop()  # 未授权时停止执行

# D. 渠道独立配置
st.subheader("🚀 渠道任务设置")
channels_to_process = []  # 存储需要处理的渠道配置
# 定义支持的渠道列表
channel_defs = [
    {"id": "feishu", "name": "飞书", "suffix": "f", "sub": "Feishu", "def_owner": "zwg5427", "def_user": "888888"},
    {"id": "wecom", "name": "企微", "suffix": "w", "sub": "WeCom", "def_owner": "zwg5427", "def_user": "888888"},
    {"id": "red", "name": "小红书", "suffix": "r", "sub": "Red", "def_owner": "zwg5427", "def_user": "888888"}
]

# 为每个渠道创建配置界面
for ch in channel_defs:
    with st.container(border=True):  # 创建带边框的容器
        # 选择是否分发到该渠道
        active = st.checkbox(f"分发至 [{ch['name']}]", value=True, key=f"act_{ch['id']}")
        # 选择是否使用默认水印
        use_def = st.checkbox("默认水印", value=True, key=f"def_{ch['id']}", disabled=not active)
        # 上传自定义水印（当不使用默认水印时可用）
        up_file = st.file_uploader(
            f"自定义水印 ({ch['name']})", type="png", 
            key=f"up_{ch['id']}", disabled=use_def or not active
        )
        # 密码设置（两列布局）
        col_pw1, col_pw2 = st.columns(2)
        with col_pw1:
            ch_owner_pw = st.text_input(
                f"{ch['name']} 管理员密码", value=ch['def_owner'], 
                key=f"opw_{ch['id']}", disabled=not active
            )
        with col_pw2:
            ch_user_pw = st.text_input(
                f"{ch['name']} 打开密码", value=ch['def_user'], 
                key=f"upw_{ch['id']}", disabled=not active
            )
        # 如果渠道被激活，将配置添加到处理列表
        if active:
            channels_to_process.append({
                **ch,  # 扩展渠道基本信息
                "use_def": use_def,  # 是否使用默认水印
                "up_file": up_file,  # 自定义水印文件
                "owner_pw": ch_owner_pw,  # 管理员密码
                "user_pw": ch_user_pw,  # 用户打开密码
                "full_prefix": f"{file_prefix_base}{ch['suffix']}"  # 文件前缀
            })

# 水印样式微调
with st.expander("🎨 水印样式微调"):
    # 旋转角度
    rot = st.slider("旋转", -90, 90, -60)
    # 宽度占比
    w_pct = st.slider("宽度占比", 0.1, 1.0, 0.6)
    # 纵向间距
    h_multiplier = st.slider("纵向间距 (水印高度的倍数)", 1.0, 5.0, 2.5, step=0.1)
    st.caption("ℹ️ 提示：请在原始 PNG 图片中配置好适宜的水印透明度。")

# E. 文件处理区
st.subheader("📤 文件上传")
# 原文档密码（选填）
src_pdf_pw = st.text_input("原文档密码 (选填)", value="", help="如果原文件已加密，请在此填写解密密码")
# 上传待处理的PDF文件
main_pdf = st.file_uploader("选择待处理 PDF", type="pdf")

# 组装&发射按钮（全宽显示）
if main_pdf and st.button("🔥 组装 & 发射", type="primary", use_container_width=True):
    wm_data = {}  # 存储各渠道的水印数据
    valid = True  # 验证标志
    
    # 准备各渠道的水印数据
    for ch in channels_to_process:
        if ch['use_def']:
            # 使用默认水印
            if os.path.exists(DEFAULT_WM[ch['id']]):
                wm_data[ch['id']] = open(DEFAULT_WM[ch['id']], 'rb').read()
            else:
                st.error(f"本地缺失: {DEFAULT_WM[ch['id']]}信号")
                valid = False
        else:
            # 使用自定义水印
            if ch['up_file']:
                wm_data[ch['id']] = ch['up_file'].getvalue()
            else:
                # 未上传自定义水印，设为None（不加水印）
                wm_data[ch['id']] = None
    
    # 验证通过且有渠道需要处理时执行
    if valid and channels_to_process:
        # 显示处理状态
        with st.status("🛠️ 正在执行自动化流水线...", expanded=True) as status:
            dt = datetime.now().strftime('%y%m%d')  # 当前日期，用于文件名
            real_folder = mgr.get_real_folder(app_folder_name)  # 获取真实文件夹名（注意：原代码中未实现此方法）
            
            # 创建临时目录存放处理过程中的文件
            with tempfile.TemporaryDirectory() as td:
                st.write("🔍 **Step 1: 基础层提取与栅格化...**")
                
                # 保存上传的文件到临时目录
                in_p = Path(td) / "input.pdf"
                in_p.write_bytes(main_pdf.read())
                
                # 打开原始PDF文件
                src = fitz.open(str(in_p))
                # 处理加密的PDF文件
                if src.is_encrypted:
                    if not src.authenticate(src_pdf_pw):
                        st.error("❌ 原文档密码错误！")
                        st.stop()
                
                # 创建新的PDF文档用于存储栅格化内容
                raster_doc = fitz.open()
                mat = fitz.Matrix(raster_zoom, raster_zoom)  # 设置缩放矩阵
                
                # 将每一页转换为图片后插入新文档
                for page in src:
                    pix = page.get_pixmap(matrix=mat)  # 生成图片
                    np = raster_doc.new_page(width=page.rect.width, height=page.rect.height)  # 创建新页
                    np.insert_image(np.rect, stream=pix.tobytes("jpg", jpg_quality=jpg_quality))  # 插入图片
                
                # 保存栅格化后的PDF
                raster_p = Path(td) / "raster.pdf"
                raster_doc.save(str(raster_p))
                src.close()  # 关闭原始文档
                st.toast("✅ 基础层栅格化完成", icon="🌈")  # 显示提示信息
                
                # 为每个渠道处理PDF
                for ch in channels_to_process:
                    st.write(f"🎨 **Step 2: 加工【{ch['name']}】渠道专版...**")
                    
                    # 生成输出文件名
                    out_fn = f"{ch['full_prefix']}{dt}(先存后看).pdf"
                    out_p = Path(td) / out_fn  # 输出文件路径
                    
                    # 打开栅格化后的PDF
                    doc = fitz.open(str(raster_p))
                    
                    # 添加水印（如果有）
                    if wm_data.get(ch['id']) is not None:
                        add_watermark(doc, wm_data[ch['id']], rot, w_pct, h_multiplier)
                    else:
                        st.write(f"ℹ️ {ch['name']} 渠道：未设置水印，跳过加注步骤。")
                        
                    # 保存带水印且加密的PDF
                    doc.save(
                        str(out_p),
                        encryption=fitz.PDF_ENCRYPT_AES_256,  # 使用AES-256加密
                        owner_pw=ch['owner_pw'],  # 管理员密码（可编辑文档）
                        user_pw=ch['user_pw']  # 用户密码（仅可阅读文档）
                    )
                    doc.close()  # 关闭文档
                    
                    st.write(f"☁️ **Step 3: 同步至网盘 /{ch['sub']} 目录...**")
                    # 上传文件到百度网盘
                    state, msg = mgr.upload(str(out_p), real_folder, ch['sub'])
                    if state == "EXISTS":
                        st.warning(f"⏭️ {ch['name']} 跳过：云端已存在同名文件")
                    elif state == "SUCCESS":
                        st.success(f"✅ {ch['name']} 同步完成")
                    else:
                        st.error(f"❌ {ch['name']} 失败: {msg}")
            
            # 更新状态为完成
            status.update(label="🎊 任务流全部处理完毕!", state="complete")
            st.balloons()  # 显示庆祝气球
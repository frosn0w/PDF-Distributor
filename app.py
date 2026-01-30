import streamlit as st
import fitz  # PyMuPDF
import os
import json
import requests
import hashlib
import urllib.parse
import tempfile
import math
import gc
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple, Dict, Any

# --- [0. 核心配置与工具] ---

class Config:
    """集中管理配置，避免全局变量污染"""
    SECRETS = {
        "SYS_PASSWORD": os.getenv("SYS_PASSWORD", "admin888"),
        "BAIDU_AK": os.getenv("BAIDU_AK", ""),
        "BAIDU_SK": os.getenv("BAIDU_SK", ""),
    }
    
    APP = {
        "APP_FOLDER": os.getenv("APP_FOLDER", "PDF_Distributor"),
        "FILE_PREFIX": os.getenv("FILE_PREFIX", "Dist"),
        "TOKEN_FILE": "baidu_token.json",
        "RASTER_DPI": 2.5,
        "JPG_QUALITY": 80
    }

    CHANNEL_DEFAULTS = {
        "feishu": {"opw": "zwg5427", "upw": "888888", "suffix": "f", "sub": "Feishu", "name": "飞书"},
        "wecom":  {"opw": "zwg5427", "upw": "888888", "suffix": "w", "sub": "WeCom",  "name": "企微"},
        "red":    {"opw": "zwg5427", "upw": "888888", "suffix": "r", "sub": "Red",    "name": "小红书"},
    }

    DEFAULT_WM_PATHS = {
        'feishu': 'WM.Feishu.png',
        'wecom': 'WM.WeCOM.png',
        'red': 'WM.Red.png'
    }

# --- [1. 业务逻辑层] ---

class BaiduManager:
    def __init__(self, ak: str, sk: str, t_file: str):
        self.ak = ak
        self.sk = sk
        self.t_file = t_file
        self.api_base = "https://pan.baidu.com/rest/2.0/xpan"
        self.headers = {'User-Agent': 'pan.baidu.com'}
        self.token_data = self._load_token()

    def _load_token(self) -> Optional[Dict]:
        if os.path.exists(self.t_file):
            try:
                with open(self.t_file, 'r') as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                return None
        return None

    def save_token(self, data: Dict):
        with open(self.t_file, 'w') as f:
            json.dump(data, f)
        self.token_data = data

    def check_auth(self) -> bool:
        if not self.token_data:
            return False
        try:
            # 简单验证 token 有效性
            url = f"{self.api_base}/file?method=list&access_token={self.token_data.get('access_token')}&dir=/apps&limit=1"
            res = requests.get(url, headers=self.headers, timeout=5).json()
            return res.get('errno') == 0
        except Exception:
            return False

    def upload(self, local_path: str, app_folder: str, remote_sub: str) -> Tuple[str, str]:
        """执行分片上传逻辑"""
        try:
            p = Path(local_path)
            fn = p.name
            file_bytes = p.read_bytes()
            md5 = hashlib.md5(file_bytes).hexdigest()
            fsize = len(file_bytes)
            
            target_dir = f"/apps/{app_folder}/{remote_sub}"
            tk = self.token_data['access_token']
            
            # 1. Precreate
            pre_url = f"{self.api_base}/file?method=precreate&access_token={tk}"
            pre_data = {
                'path': f"{target_dir}/{fn}", 'size': str(fsize), 'isdir': '0',
                'autoinit': '1', 'block_list': json.dumps([md5]), 'rtype': '3'
            }
            pre = requests.post(pre_url, data=pre_data, headers=self.headers).json()
            
            if 'uploadid' not in pre:
                return "FAILED", f"预处理失败: {pre.get('errno')} - {pre.get('errmsg', 'Unknown')}"

            # 2. Upload
            up_url = (f"https://d.pcs.baidu.com/rest/2.0/pcs/superfile2?method=upload&access_token={tk}"
                      f"&type=tmpfile&path={urllib.parse.quote(f'{target_dir}/{fn}')}"
                      f"&uploadid={pre['uploadid']}&partseq=0")
            
            requests.post(up_url, files={'file': file_bytes}, headers=self.headers)

            # 3. Create
            create_url = f"{self.api_base}/file?method=create&access_token={tk}"
            create_data = {
                'path': f"{target_dir}/{fn}", 'size': str(fsize), 'isdir': '0',
                'uploadid': pre['uploadid'], 'block_list': json.dumps([md5]), 'rtype': '3'
            }
            final = requests.post(create_url, data=create_data, headers=self.headers).json()
            
            if 'fs_id' in final:
                return "SUCCESS", f"{target_dir}/{fn}"
            return "FAILED", f"落盘失败: {final}"
            
        except Exception as e:
            return "FAILED", str(e)

class PDFProcessor:
    @staticmethod
    def rasterize_pdf(input_path: Path, output_path: Path, password: str = None) -> bool:
        """将 PDF 每一页转为图片再重组（去矢量化/压制）"""
        try:
            with fitz.open(input_path) as src:
                if src.is_encrypted and password:
                    if not src.authenticate(password):
                        return False
                elif src.is_encrypted:
                    return False # 加密但未提供密码

                with fitz.open() as r_doc:
                    mat = fitz.Matrix(Config.APP["RASTER_DPI"], Config.APP["RASTER_DPI"])
                    for page in src:
                        # 使用 jpg 压缩以减小体积
                        pix = page.get_pixmap(matrix=mat)
                        img_bytes = pix.tobytes("jpg", Config.APP["JPG_QUALITY"])
                        
                        np = r_doc.new_page(width=page.rect.width, height=page.rect.height)
                        np.insert_image(np.rect, stream=img_bytes)
                    r_doc.save(output_path)
            return True
        except Exception as e:
            st.error(f"栅格化错误: {e}")
            return False

    @staticmethod
    def add_watermark(target_pdf_path: Path, output_path: Path, wm_bytes: bytes, 
                      owner_pw: str, user_pw: str):
        """添加水印并加密保存"""
        with fitz.open(target_pdf_path) as doc:
            # 临时创建一个只包含水印的 PDF 页面，用于重复盖章
            with fitz.open() as wm_pdf_doc:
                if wm_bytes:
                    img = fitz.open("png", wm_bytes)
                    rect = img[0].rect
                    w_page = wm_pdf_doc.new_page(width=rect.width, height=rect.height)
                    w_page.insert_image(rect, stream=wm_bytes)
                    
                    # 核心水印逻辑
                    PDFProcessor._apply_tiled_watermark(doc, wm_pdf_doc)
            
            # 保存加密
            doc.save(output_path, encryption=fitz.PDF_ENCRYPT_AES_256, 
                     owner_pw=owner_pw, user_pw=user_pw)

    @staticmethod
    def _apply_tiled_watermark(target_doc, wm_source_doc):
        """平铺水印算法"""
        rot, w_pct, h_mult = -60, 0.6, 2.5
        iw, ih = wm_source_doc[0].rect.width, wm_source_doc[0].rect.height
        
        for page in target_doc:
            vw = page.rect.width * w_pct
            vh = vw * (ih / iw)
            rad = abs(rot) * (math.pi / 180.0)
            
            # 计算旋转后的包围盒大小
            bw = vw * math.cos(rad) + vh * math.sin(rad)
            bh = vw * math.sin(rad) + vh * math.cos(rad)
            
            step_y = bh * h_mult
            y = 150 + bh/2
            
            while y <= page.rect.height - 150 - bh/2:
                # 居中计算
                r = fitz.Rect((page.rect.width - bw) / 2, y - bh/2, 
                              (page.rect.width + bw) / 2, y + bh/2)
                page.show_pdf_page(r, wm_source_doc, 0, rotate=rot)
                y += step_y

# --- [2. UI 展现层] ---

def main():
    st.set_page_config(page_title="PDF Distributor", layout="centered")

    # 1. 鉴权
    if "authenticated" not in st.session_state:
        st.title("🔐 系统访问受限")
        pwd = st.text_input("请输入访问密钥", type="password")
        if st.button("解锁"):
            if pwd == Config.SECRETS["SYS_PASSWORD"]:
                st.session_state.authenticated = True
                st.rerun()
            else:
                st.error("密钥错误")
        st.stop()

    st.title("🚀 PDF Distributor")

    # 2. 百度网盘配置
    with st.expander("⚙️ 核心配置 (Secrets)", expanded=False):
        c1, c2 = st.columns(2)
        app_key = c1.text_input("Baidu AK", value=Config.SECRETS["BAIDU_AK"])
        secret_key = c2.text_input("Baidu SK", value=Config.SECRETS["BAIDU_SK"], type="password")
        target_folder = c1.text_input("网盘文件夹", value=Config.APP["APP_FOLDER"])
        file_prefix = c2.text_input("输出文件前缀", value=Config.APP["FILE_PREFIX"])

    mgr = BaiduManager(app_key, secret_key, Config.APP["TOKEN_FILE"])

    # 3. 网盘OAuth流程
    if not mgr.check_auth():
        st.warning("⚠️ 百度云未授权")
        auth_url = f"https://openapi.baidu.com/oauth/2.0/authorize?response_type=code&client_id={app_key}&redirect_uri=oob&scope=basic,netdisk"
        st.markdown(f"1. [点击获取授权码]({auth_url})")
        code = st.text_input("2. 输入授权码:")
        if st.button("激活授权"):
            url = f"https://openapi.baidu.com/oauth/2.0/token?grant_type=authorization_code&code={code}&client_id={app_key}&client_secret={secret_key}&redirect_uri=oob"
            try:
                res = requests.get(url).json()
                if 'access_token' in res:
                    mgr.save_token(res)
                    st.success("授权成功")
                    st.rerun()
                else:
                    st.error(f"授权失败: {res}")
            except Exception as e:
                st.error(str(e))
        st.stop()

    # 4. 任务配置
    st.subheader("📦 分发策略")

    configured_channels = []
    for ch_id, defaults in Config.CHANNEL_DEFAULTS.items():
        with st.container(border=True):
            is_active = st.checkbox(f"分发到 {defaults['name']}", value=True, key=f"active_{ch_id}")
            
            if is_active:
                col_a, col_b = st.columns(2)
                opw = col_a.text_input("管理密码", value=defaults["opw"], key=f"opw_{ch_id}")
                upw = col_b.text_input("阅读密码", value=defaults["upw"], key=f"upw_{ch_id}")
                
                use_def_wm = col_a.checkbox("使用默认水印", value=True, key=f"wm_def_{ch_id}")
                custom_wm_file = None
                if not use_def_wm:
                    custom_wm_file = col_b.file_uploader("自定义水印PNG", type="png", key=f"wm_up_{ch_id}")
                
                configured_channels.append({
                    "id": ch_id, "meta": defaults, 
                    "opw": opw, "upw": upw,
                    "use_def_wm": use_def_wm, "custom_wm_file": custom_wm_file
                })

    # 5. 执行逻辑
    # 源 PDF 解密
    src_pdf_password = st.text_input(
        "🔓 源 PDF 密码", 
        type="password", 
        help="如果上传的 PDF 已加密，请在此输入密码；否则留空",
        placeholder="无密码则留空"
    )

    main_pdf = st.file_uploader("📄 上传待处理 PDF", type="pdf")
    
    if main_pdf and st.button("🔥 开始自动化分发", type="primary", use_container_width=True):
        if not configured_channels:
            st.warning("请至少选择一个分发渠道")
            st.stop()

        status_container = st.status("正在启动处理引擎...", expanded=True)
        
        try:
            with tempfile.TemporaryDirectory() as td:
                work_dir = Path(td)
                input_path = work_dir / "input.pdf"
                input_path.write_bytes(main_pdf.read())
                
                # A. 栅格化 (只做一次)
                status_container.write("🔨 正在压制 PDF (去矢量化)...")
                raster_path = work_dir / "raster_base.pdf"
                
                success = PDFProcessor.rasterize_pdf(input_path, raster_path, src_pdf_password)
                if not success:
                    status_container.update(label="❌ 处理失败", state="error")
                    st.error("无法读取源 PDF，可能是密码错误或文件损坏。")
                    st.stop()

                # B. 分渠道处理
                dt_str = datetime.now().strftime('%y%m%d')
                
                for idx, ch in enumerate(configured_channels):
                    ch_name = ch['meta']['name']
                    status_container.write(f"🎨 [{idx+1}/{len(configured_channels)}] 处理渠道: {ch_name}...")
                    
                    # 准备水印数据
                    wm_bytes = None
                    if ch['use_def_wm']:
                        def_path = Config.DEFAULT_WM_PATHS.get(ch['id'])
                        if def_path and os.path.exists(def_path):
                            with open(def_path, 'rb') as f: wm_bytes = f.read()
                    elif ch['custom_wm_file']:
                        wm_bytes = ch['custom_wm_file'].getvalue()
                    
                    # 生成最终文件
                    out_filename = f"{file_prefix}{ch['meta']['suffix']}_{dt_str}.pdf"
                    out_path = work_dir / out_filename
                    
                    PDFProcessor.add_watermark(
                        target_pdf_path=raster_path,
                        output_path=out_path,
                        wm_bytes=wm_bytes,
                        owner_pw=ch['opw'],
                        user_pw=ch['upw']
                    )
                    
                    # 上传
                    status_container.write(f"☁️ 上传 {ch_name} 至百度网盘...")
                    state, msg = mgr.upload(str(out_path), target_folder, ch['meta']['sub'])
                    
                    if state == "SUCCESS":
                        st.toast(f"✅ {ch_name} 分发完成")
                    else:
                        st.error(f"❌ {ch_name} 上传失败: {msg}")
                        
                status_container.update(label="🎉 所有任务执行完毕", state="complete")
                st.balloons()
                
        except Exception as e:
            st.error(f"系统运行时错误: {str(e)}")
            raise e
        finally:
            gc.collect()

if __name__ == "__main__":
    main()
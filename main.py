import os
import sys
import time
import random
import re
import requests
import tempfile
from xvfbwrapper import Xvfb
from DrissionPage import ChromiumPage, ChromiumOptions

try:
    import speech_recognition as sr
    from pydub import AudioSegment
except ImportError:
    pass


# ==============================================================================
# Telegram 通知模块
# ==============================================================================
def send_tg_message(token, chat_id, message, photo_path=None):
    if not token or not chat_id:
        print("未配置 TG_TOKEN 或 TG_CHAT_ID，跳过通知。")
        return

    safe_message = message.replace("<b>", "").replace("</b>", "")

    if photo_path and os.path.exists(photo_path):
        photo_url = f"https://api.telegram.org/bot{token}/sendPhoto"
        data = {"chat_id": chat_id, "caption": safe_message}
        try:
            with open(photo_path, "rb") as photo_file:
                response = requests.post(
                    photo_url,
                    data=data,
                    files={"photo": photo_file},
                    timeout=20,
                )
            response.raise_for_status()
            print(f"✅ Telegram 截图通知发送成功：{photo_path}")
            return
        except Exception as e:
            print(f"⚠️ Telegram 截图发送失败，回退到文本消息: {e}")

    text_url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": safe_message}
    try:
        response = requests.post(text_url, json=payload, timeout=10)
        response.raise_for_status()
        print("✅ Telegram 文本通知发送成功！")
    except Exception as e:
        print(f"❌ Telegram 通知请求异常: {e}")


def capture_failure_artifacts(page, prefix):
    # 加时间戳，避免多台机器跑下来截图互相覆盖
    ts = int(time.time())
    screenshot_path = f"{prefix}_{ts}.png"
    html_path = f"{prefix}_{ts}.html"

    if not page:
        return None, None

    try:
        page.handle_alert(accept=True)
    except Exception:
        pass

    try:
        page.get_screenshot(path=screenshot_path, full_page=True)
        print(f"✅ 已保存错误截图: {screenshot_path}")
    except Exception as screenshot_err:
        screenshot_path = None
        print(f"⚠️ 保存截图失败: {screenshot_err}")

    try:
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(page.html)
        print(f"✅ 已保存现场 HTML: {html_path}")
    except Exception as html_err:
        html_path = None
        print(f"⚠️ 保存源码失败: {html_err}")

    return screenshot_path, html_path


# ==============================================================================
# 语音验证码破解模块 (DrissionPage 适配版)
# ==============================================================================
class RecaptchaAudioSolver:
    def __init__(self, page):
        self.page = page
        self.log_func = print

    def log(self, msg):
        self.log_func(f"[Solver] {msg}")

    def human_type(self, ele, text):
        """完全模拟人类的不规则打字节奏"""
        ele.click()
        time.sleep(random.uniform(0.1, 0.3))
        ele.clear()

        for char in text:
            ele.input(char, clear=False)
            time.sleep(random.uniform(0.08, 0.25))
        time.sleep(random.uniform(0.3, 0.8))

    def solve(self, bframe):
        self.log("🎧 启动过盾流程...")
        try:
            audio_btn = bframe.ele("#recaptcha-audio-button", timeout=3)
            if audio_btn:
                self.page.actions.move_to(audio_btn, duration=random.uniform(0.5, 1.2))
                time.sleep(random.uniform(0.2, 0.5))
                audio_btn.click()
                self.log("🖱️ 点击了音频破解按钮")
            else:
                self.log("❌ 未找到验证按钮，可能被 Google 屏蔽")
                return False

            time.sleep(random.uniform(3, 5))

            src = None
            for attempt in range(3):
                src = self.get_audio_source(bframe)
                if src:
                    break

                err_msg = bframe.ele(".rc-audiochallenge-error-message", timeout=1)
                if err_msg and err_msg.states.is_displayed:
                    error_txt = err_msg.text
                    if error_txt and "try again" not in error_txt.lower():
                        self.log(f"⛔ Google 拒绝提供音频: {error_txt}")

                self.log(f"⚠️ 第 {attempt + 1} 次获取TOKEN失败，尝试点击刷新...")
                reload_btn = bframe.ele("#recaptcha-reload-button", timeout=2)
                if reload_btn:
                    self.page.actions.move_to(
                        reload_btn, duration=random.uniform(0.3, 0.8)
                    )
                    time.sleep(random.uniform(0.2, 0.5))
                    reload_btn.click()
                    time.sleep(random.uniform(4, 7))

            if not src:
                self.log("❌ 最终无法获取链接 (IP 可能被暂时风控)")
                return False

            self.log("📥 正在下载并处理音频数据...")
            r = requests.get(src, timeout=15)
            with open("audio.mp3", "wb") as f:
                f.write(r.content)

            try:
                sound = AudioSegment.from_mp3("audio.mp3")
                sound.export("audio.wav", format="wav")
            except Exception as e:
                self.log(f"❌ ffmpeg 转码失败: {e}")
                return False

            key_text = ""
            recognizer = sr.Recognizer()
            with sr.AudioFile("audio.wav") as source:
                audio_data = recognizer.record(source)
                try:
                    key_text = recognizer.recognize_google(audio_data)
                    self.log(f"🗣️ 识别结果: [{key_text}]")
                except Exception as e:
                    self.log("❌ 语音识别失败 (可能音频含糊或引擎无响应)")
                    return False

            input_box = bframe.ele("#audio-response", timeout=2)
            if input_box:
                self.human_type(input_box, key_text)

                verify_btn = bframe.ele("#recaptcha-verify-button", timeout=2)
                if verify_btn:
                    self.page.actions.move_to(
                        verify_btn, duration=random.uniform(0.5, 1.0)
                    )
                    time.sleep(random.uniform(0.2, 0.5))
                    verify_btn.click()
                    self.log("🚀 提交验证...")
                    time.sleep(4)

                    err_check = bframe.ele(
                        ".rc-audiochallenge-error-message", timeout=1
                    )
                    if err_check and err_check.states.is_displayed:
                        self.log(f"❌ 验证未通过: {err_check.text}")
                        return False

                    return True
            return False

        except Exception as e:
            self.log(f"💥 异常: {e}")
            return False
        finally:
            for f in ["audio.mp3", "audio.wav"]:
                if os.path.exists(f):
                    os.remove(f)

    def get_audio_source(self, bframe):
        try:
            link1 = bframe.ele(".rc-audiochallenge-ndownload-link", timeout=0.5)
            if link1:
                return link1.attr("href")

            link2 = bframe.ele('xpath://a[contains(@href, ".mp3")]', timeout=0.5)
            if link2:
                return link2.attr("href")

            audio_src = bframe.ele("#audio-source", timeout=0.5)
            if audio_src:
                return audio_src.attr("src")

            return None
        except:
            return None


# ==============================================================================
# 核心续期业务逻辑
# ==============================================================================
def renew_host2play(url, proxy_url=None):
    """
    [v5] 返回值扩展：
        (success, msg, screenshot, before_text, after_text, server_name)
    用于上层组装"前后对比汇总"消息。
    """
    print("启动 Xvfb 虚拟桌面...")
    vdisplay = Xvfb(width=1280, height=720, colordepth=24)
    vdisplay.start()

    success = False
    msg = ""
    screenshot_path = None
    before_text = ""
    after_text = ""
    server_name = ""
    page = None

    try:
        co = ChromiumOptions()
        co.set_browser_path("/usr/bin/google-chrome")
        co.set_argument("--no-sandbox")
        co.set_argument("--disable-dev-shm-usage")
        co.set_argument("--disable-gpu")
        co.set_argument("--disable-setuid-sandbox")
        co.set_argument("--disable-software-rasterizer")
        co.set_argument("--disable-extensions")
        co.set_argument("--no-first-run")
        co.set_argument("--no-default-browser-check")
        co.set_argument("--disable-popup-blocking")
        co.set_argument("--window-size=1280,720")

        # 每次都用全新的 user_data_dir，避免 Google 通过 cookie/指纹串联识别
        user_data_dir = tempfile.mkdtemp()
        co.set_user_data_path(user_data_dir)
        co.auto_port()
        co.headless(False)

        if proxy_url:
            if "://" not in proxy_url:
                proxy_url = f"http://{proxy_url}"
            co.set_proxy(proxy_url)

        page = ChromiumPage(co)

        print("🛡️ 注入 WebGL 硬件欺骗与反侦察指纹...")
        page.add_init_js("""
            const getParameter = WebGLRenderingContext.prototype.getParameter;
            WebGLRenderingContext.prototype.getParameter = function(parameter) {
                if (parameter === 37445) return 'Intel Inc.';
                if (parameter === 37446) return 'Intel(R) UHD Graphics 630';
                return getParameter.apply(this, [parameter]);
            };
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
            Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3]});
        """)

        print(f"🌐 访问续期目标网址: {url}")
        page.get(url, retry=3)
        time.sleep(random.uniform(5, 8))

        # [v5] 尝试提取服务器名（用于汇总消息可读性）
        try:
            sn_ele = page.ele(
                'xpath://*[contains(text(), "Renew server")'
                ' or contains(text(), "Server name")'
                ' or contains(text(), "Hostname")]',
                timeout=2,
            )
            if sn_ele:
                txt = (sn_ele.text or "").strip()
                # 形如 "Renew server: mcf4008"
                if ":" in txt:
                    server_name = txt.split(":", 1)[1].strip()
                else:
                    server_name = txt
        except Exception:
            pass
        # 兜底：用 URL 末段作为标识
        if not server_name:
            try:
                server_name = url.rstrip("/").split("/")[-1][:24]
            except Exception:
                server_name = "unknown"
        print(f"🏷️ 服务器标识: {server_name!r}")

        print("🧹 清理遮挡元素...")
        page.run_js("""
            const cssSelectors = ['ins.adsbygoogle', 'iframe[src*="ads"]', '.modal-backdrop'];
            cssSelectors.forEach(sel => {
                document.querySelectorAll(sel).forEach(el => el.remove());
            });
        """)
        time.sleep(2)

        consent_btn = page.ele("tag:button@@text():Consent", timeout=2)
        if consent_btn:
            consent_btn.click()
            time.sleep(3)

        print("🤸 积累真实的鼠标轨迹和滚动数据...")
        for _ in range(3):
            scroll_y = random.randint(200, 600)
            page.scroll.down(scroll_y)
            time.sleep(random.uniform(0.5, 1.5))
            page.actions.move(random.randint(100, 800), random.randint(100, 500))
            time.sleep(random.uniform(0.5, 1.0))
        time.sleep(random.uniform(1.0, 2.0))

        print("🖱️ 打开续期弹窗...")
        renew_btn1 = page.ele(
            'xpath://button[contains(text(), "Renew server")]', timeout=3
        )
        if renew_btn1:
            try:
                renew_btn1.click()
            except:
                renew_btn1.click(by_js=True)
        else:
            page.run_js(
                "document.querySelectorAll('button').forEach(b => {if(b.textContent.includes('Renew server')) b.click();});"
            )
        time.sleep(3)

        for _ in range(8):
            if page.ele("text:Expires in:", timeout=0.5) or page.ele(
                "text:Deletes on:", timeout=0.5
            ):
                break
            time.sleep(1)

        renew_btn2 = page.ele(
            'xpath://button[contains(text(), "Renew server")]', timeout=2
        )
        if renew_btn2:
            try:
                renew_btn2.click()
            except:
                renew_btn2.click(by_js=True)

        time.sleep(random.uniform(7, 10))

        solved_captcha = False
        anchor_frame = page.get_frame(
            'xpath://iframe[contains(@src, "recaptcha/api2/anchor")]', timeout=5
        )

        if anchor_frame:
            print("✅ 锁定 reCAPTCHA 框架")
            anchor_box = None

            for _ in range(20):
                anchor_box = anchor_frame.ele("#recaptcha-anchor", timeout=1)
                if anchor_box:
                    break
                time.sleep(1)

            if not anchor_box:
                msg = "❌ host2 reCAPTCHA checkbox 超时"
                screenshot_path, _ = capture_failure_artifacts(
                    page, "error_anchor_timeout"
                )
            else:
                print("🖱️ 物理模拟点击 reCAPTCHA checkbox...")
                page.actions.move_to(anchor_box, duration=random.uniform(0.5, 1.5))
                time.sleep(random.uniform(0.2, 0.6))
                anchor_box.click()
                time.sleep(random.uniform(4, 7))

                checked = anchor_box.attr("aria-checked")

                if checked == "true":
                    print("✅ reCAPTCHA 已自动验证通过！")
                    solved_captcha = True
                else:
                    print("🎲 需要手动破解音频验证码...")
                    bframe = page.get_frame(
                        'xpath://iframe[contains(@src, "recaptcha/api2/bframe")]',
                        timeout=5,
                    )
                    if bframe:
                        solver = RecaptchaAudioSolver(page)
                        if solver.solve(bframe):
                            solved_captcha = True
        else:
            print("⚠️ 未发现 reCAPTCHA iframe")
            screenshot_path, _ = capture_failure_artifacts(page, "error_no_iframe")
            msg = "❌ host2 未找到 reCAPTCHA 验证码区域，请检查源码。"

        if solved_captcha:
            # ==================================================================
            # [v4] 真正的续期验证流程（强化 disable 检测和到期文本读取）
            # ==================================================================
            print("🚀 reCAPTCHA 已通过，等待最终 Renew 按钮可用...")

            def _btn_disabled_reasons(btn):
                """返回按钮 disabled 的原因列表（空 = 可点）。
                覆盖：disabled 属性 / aria-disabled / class 含 disabled /
                CSS pointer-events:none / opacity 过低
                """
                reasons = []
                try:
                    if btn.attr("disabled") is not None:
                        reasons.append("disabled-attr")
                    aria = (btn.attr("aria-disabled") or "").lower()
                    if aria in ("true", "disabled"):
                        reasons.append(f"aria={aria}")
                    cls = (btn.attr("class") or "").lower()
                    if "disabled" in cls or "btn-disabled" in cls:
                        reasons.append(f"class~disabled")
                except Exception:
                    pass
                # CSS 层面检测
                try:
                    pe = page.run_js(
                        "return window.getComputedStyle(arguments[0]).pointerEvents;",
                        btn,
                    )
                    if pe == "none":
                        reasons.append("pointer-events:none")
                except Exception:
                    pass
                try:
                    op = page.run_js(
                        "return parseFloat(window.getComputedStyle(arguments[0])"
                        ".opacity || '1');",
                        btn,
                    )
                    if isinstance(op, (int, float)) and op < 0.6:
                        reasons.append(f"opacity={op:.2f}")
                except Exception:
                    pass
                return reasons

            def _read_expire_text():
                """读取并归一化到期文本，优先抓取 Expire/Deletes 关键值。"""
                def _get_expire_and_delete():
                    expire_raw = ""
                    delete_raw = ""
                    try:
                        expire_raw = page.run_js(
                            "var e=document.querySelector('#expireDate'); return e ? (e.innerText||e.textContent||'') : '';"
                        ) or ""
                    except Exception:
                        expire_raw = ""
                    try:
                        delete_raw = page.run_js(
                            "var e=document.querySelector('#deleteDate'); return e ? (e.innerText||e.textContent||'') : '';"
                        ) or ""
                    except Exception:
                        delete_raw = ""
                    expire_raw = re.sub(r"\s+", " ", str(expire_raw)).strip(" :\t\r\n")
                    delete_raw = re.sub(r"\s+", " ", str(delete_raw)).strip(" :\t\r\n")
                    return expire_raw, delete_raw

                def _parse_delete_epoch(v):
                    if not v:
                        return None
                    s = str(v).strip()
                    for fmt in ("%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
                        try:
                            return time.mktime(time.strptime(s, fmt))
                        except Exception:
                            continue
                    return None

                def _parse_expire_seconds(v):
                    if not v:
                        return None
                    s = str(v).strip().lower()
                    m = re.match(r"^(\d{1,2}):(\d{2}):(\d{2})$", s)
                    if m:
                        return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
                    m = re.search(r"(\d+)\s*day[s]?\s*(\d+)\s*h", s)
                    if m:
                        return int(m.group(1)) * 86400 + int(m.group(2)) * 3600
                    m = re.search(r"(\d+)\s*day[s]?", s)
                    if m:
                        return int(m.group(1)) * 86400
                    m = re.search(r"(\d+)\s*h\b", s)
                    if m:
                        return int(m.group(1)) * 3600
                    return None

                def _normalize_expire_text(raw_text):
                    text = re.sub(r"\s+", " ", str(raw_text or "")).strip()
                    if not text:
                        return ""

                    m = re.search(r"Expires in[:：]?\s*([^,\n\r|]+)", text, re.IGNORECASE)
                    if m:
                        v = m.group(1).strip(" :\t\r\n")
                        # 排除 "Expires in: :" 这类只有标签无值
                        if not v or v in (":", "-"):
                            return ""
                        return f"Expires in: {v}"

                    m = re.search(r"Deletes on[:：]?\s*([^,\n\r|]+)", text, re.IGNORECASE)
                    if m:
                        v = m.group(1).strip(" :\t\r\n")
                        if not v or v in (":", "-"):
                            return ""
                        return f"Deletes on: {v}"

                    m = re.search(r"\b\d+\s*day[s]?\s*\d+\s*h\b", text, re.IGNORECASE)
                    if m:
                        return re.sub(r"\s+", " ", m.group(0)).strip()

                    m = re.search(r"\b\d+\s*day[s]?\b", text, re.IGNORECASE)
                    if m:
                        return re.sub(r"\s+", " ", m.group(0)).strip()

                    m = re.search(r"\b\d+\s*h\b", text, re.IGNORECASE)
                    if m:
                        return re.sub(r"\s+", " ", m.group(0)).strip()

                    m = re.search(r"\b\d{4}[/-]\d{1,2}[/-]\d{1,2}\b", text)
                    if m:
                        return m.group(0)

                    return ""

                try:
                    # 最高优先：直接读取已知稳定节点
                    expire_raw, delete_raw = _get_expire_and_delete()
                    if expire_raw:
                        return f"Expires in: {expire_raw}"
                    if delete_raw:
                        return f"Deletes on: {delete_raw}"

                    ele = page.ele(
                        'xpath://*[contains(text(), "Expires in") '
                        'or contains(text(), "Deletes on")]',
                        timeout=2,
                    )
                    if ele:
                        own = _normalize_expire_text(ele.text or "")
                        if own:
                            return own
                        # 同级/父级精确提取，避免只拿到 "Expires in:" 标签
                        try:
                            precise = page.run_js(
                                r"""
                                return (function(el){
                                    function clean(t){ return String(t||'').replace(/\s+/g,' ').trim(); }
                                    function pick(t){
                                        t = clean(t);
                                        if (!t) return '';
                                        var m = t.match(/\b\d+\s*day[s]?\s*\d+\s*h\b/i);
                                        if (m) return clean(m[0]);
                                        m = t.match(/\b\d+\s*day[s]?\b/i);
                                        if (m) return clean(m[0]);
                                        m = t.match(/\b\d+\s*h\b/i);
                                        if (m) return clean(m[0]);
                                        m = t.match(/\b\d{4}[/-]\d{1,2}[/-]\d{1,2}\b/);
                                        if (m) return clean(m[0]);
                                        return '';
                                    }
                                    var line = el;
                                    for (var i=0;i<3 && line;i++){
                                        var p = pick(line.innerText || line.textContent || '');
                                        if (p) return p;
                                        var sib = line.nextElementSibling;
                                        if (sib){
                                            p = pick(sib.innerText || sib.textContent || '');
                                            if (p) return p;
                                        }
                                        line = line.parentElement;
                                    }
                                    return '';
                                })(arguments[0]);
                                """,
                                ele,
                            ) or ""
                            precise = str(precise).strip()
                            if precise:
                                return precise
                        except Exception:
                            pass
                        try:
                            parent = ele.parent()
                            if parent:
                                pt = _normalize_expire_text(parent.text or "")
                                if pt:
                                    return pt
                                gp = parent.parent()
                                if gp:
                                    gpt = _normalize_expire_text(gp.text or "")
                                    if gpt:
                                        return gpt
                        except Exception:
                            pass

                    # 兜底：全文抽取
                    try:
                        body_text = page.run_js(
                            "return (document.body && (document.body.innerText || document.body.textContent)) || '';"
                        ) or ""
                    except Exception:
                        body_text = page.html or ""
                    return _normalize_expire_text(body_text)
                except Exception:
                    return ""

            final_btn = None
            last_reasons = []
            for wait_i in range(30):
                btn = page.ele(
                    'xpath://button[normalize-space(text())="Renew"]', timeout=1
                )
                if btn:
                    reasons = _btn_disabled_reasons(btn)
                    if not reasons:
                        final_btn = btn
                        print(f"✅ 最终 Renew 按钮已可点击 (等了 {wait_i}s)")
                        break
                    last_reasons = reasons
                    if wait_i == 0 or wait_i % 5 == 0:
                        print(f"⏳ Renew 仍 disabled [{','.join(reasons)}] ({wait_i}s)")
                else:
                    if wait_i == 0:
                        print("⏳ 暂未找到 Renew 按钮，等待...")
                time.sleep(1)

            if not final_btn:
                reasons_str = ",".join(last_reasons) if last_reasons else "未找到按钮"
                msg = (
                    f"❌ host2play Renew 按钮 30s 内一直不可点 "
                    f"[{reasons_str}]，疑似 reCAPTCHA token 未回填或站点节流"
                )
                screenshot_path, _ = capture_failure_artifacts(
                    page, "error_btn_disabled"
                )
            else:
                # 记录点击前快照
                old_expire_text = _read_expire_text()
                before_text = old_expire_text  # [v5] 暴露给上层
                print(f"📌 点击前到期文本: {old_expire_text!r}")
                old_expire_raw, old_delete_raw = _get_expire_and_delete()
                old_delete_epoch = _parse_delete_epoch(old_delete_raw)
                old_expire_secs = _parse_expire_seconds(old_expire_raw)

                try:
                    final_btn.click()
                except Exception:
                    final_btn.click(by_js=True)
                print("👇 已点击最终 Renew，开始验证页面变化...")

                # 多重验证信号，任一满足即视为成功
                verified = False
                new_expire_text = ""
                success_reason = ""

                for poll_i in range(15):
                    time.sleep(1)

                    # 信号 A1：绝对到期时间必须增长
                    cur_expire_raw, cur_delete_raw = _get_expire_and_delete()
                    cur_delete_epoch = _parse_delete_epoch(cur_delete_raw)
                    if (
                        old_delete_epoch is not None
                        and cur_delete_epoch is not None
                        and cur_delete_epoch > old_delete_epoch
                    ):
                        new_expire_text = f"Deletes on: {cur_delete_raw}"
                        verified = True
                        success_reason = "deleteDate 增长"
                        print(f"✅ deleteDate 增长: {old_delete_raw!r} → {cur_delete_raw!r}")
                        break

                    # 信号 A2：倒计时明显回跳增长（避免 07:53:11→07:53:10 误判）
                    cur_expire_secs = _parse_expire_seconds(cur_expire_raw)
                    if (
                        old_expire_secs is not None
                        and cur_expire_secs is not None
                        and cur_expire_secs > old_expire_secs + 30
                    ):
                        new_expire_text = f"Expires in: {cur_expire_raw}"
                        verified = True
                        success_reason = "expireDate 回跳增长"
                        print(f"✅ expireDate 回跳增长: {old_expire_raw!r} → {cur_expire_raw!r}")
                        break

                    # 信号 B：续期弹窗里的 Renew 按钮消失了 (modal 关闭)
                    try:
                        still_btn = page.ele(
                            'xpath://button[normalize-space(text())="Renew"]',
                            timeout=0.3,
                        )
                        if not still_btn:
                            verified = True
                            success_reason = "Renew 按钮消失（弹窗已关闭）"
                            print(f"✅ Renew 按钮消失，推测续期已完成")
                            # 尝试读最新 expire 作为 new_expire_text
                            new_expire_text = _read_expire_text() or ""
                            break
                    except Exception:
                        pass

                    # 信号 C：页面文字含成功关键字
                    try:
                        page_text = (page.html or "").lower()
                        for kw in (
                            "successfully renewed",
                            "server renewed",
                            "renewed successfully",
                            "renewal successful",
                            "successfully extended",
                        ):
                            if kw in page_text:
                                verified = True
                                success_reason = f"成功关键字: {kw}"
                                new_expire_text = _read_expire_text() or ""
                                print(f"✅ 检测到成功文案: {kw}")
                                break
                        if verified:
                            break
                    except Exception:
                        pass

                if verified:
                    # [v5] 记录 after_text 用于汇总；做短重试，尽量拿到更新后的值
                    after_text = new_expire_text or _read_expire_text() or ""
                    if not after_text:
                        for _ in range(8):
                            time.sleep(1)
                            probe = _read_expire_text()
                            if probe:
                                after_text = probe
                                break
                    if (not after_text) or (old_expire_text and after_text == old_expire_text):
                        # 站点有时点击后不立刻刷字段，强制刷新一次再读
                        try:
                            print("🔄 续期后时间未更新，执行一次刷新后重读...")
                            page.refresh()
                            time.sleep(4)
                            for _ in range(10):
                                cur_expire_raw, cur_delete_raw = _get_expire_and_delete()
                                cur_delete_epoch = _parse_delete_epoch(cur_delete_raw)
                                cur_expire_secs = _parse_expire_seconds(cur_expire_raw)
                                if (
                                    old_delete_epoch is not None
                                    and cur_delete_epoch is not None
                                    and cur_delete_epoch > old_delete_epoch
                                ):
                                    after_text = f"Deletes on: {cur_delete_raw}"
                                    break
                                if (
                                    old_expire_secs is not None
                                    and cur_expire_secs is not None
                                    and cur_expire_secs > old_expire_secs + 30
                                ):
                                    after_text = f"Expires in: {cur_expire_raw}"
                                    break
                                probe = _read_expire_text()
                                if probe:
                                    after_text = probe
                                time.sleep(1)
                        except Exception as refresh_err:
                            print(f"⚠️ 刷新后重读失败: {refresh_err}")

                    if new_expire_text and new_expire_text != old_expire_text:
                        msg = (
                            f"🎉 host2play 续期成功！[{success_reason}]\n"
                            f"   {old_expire_text} → {new_expire_text}"
                        )
                    else:
                        msg = (
                            f"🎉 host2play 续期成功！[{success_reason}]"
                        )
                    success = True
                    # 成功截图
                    try:
                        ts = int(time.time())
                        success_shot = f"success_{ts}.png"
                        page.get_screenshot(path=success_shot, full_page=True)
                        screenshot_path = success_shot
                        print(f"📸 已保存成功截图: {success_shot}")
                    except Exception as shot_err:
                        print(f"⚠️ 成功截图保存失败（不影响续期）: {shot_err}")
                else:
                    # 真失败前再做一次 disable 检测，给排查线索
                    cur_reasons = []
                    try:
                        cur_btn = page.ele(
                            'xpath://button[normalize-space(text())="Renew"]',
                            timeout=0.5,
                        )
                        if cur_btn:
                            cur_reasons = _btn_disabled_reasons(cur_btn)
                    except Exception:
                        pass
                    diag = ""
                    if cur_reasons:
                        diag = f"，点击后按钮状态: [{','.join(cur_reasons)}]"
                    # [v5] 即便失败也记一次 after_text
                    after_text = _read_expire_text() or old_expire_text
                    msg = (
                        f"❌ host2play 点了 Renew 但 15s 内页面无变化"
                        f"{diag}\n"
                        f"   到期文本: {old_expire_text!r} (始终未变)"
                    )
                    screenshot_path, _ = capture_failure_artifacts(
                        page, "error_no_change_after_click"
                    )
        else:
            if "操作成功" not in msg and "续期成功" not in msg:
                msg = "❌ host2play 无法通过 reCAPTCHA"
                screenshot_path, _ = capture_failure_artifacts(
                    page, "error_captcha_failed"
                )

    except Exception as e:
        msg = f"💥 host2play 运行异常: {str(e)[:200]}"
        print(msg)
        screenshot_path, _ = capture_failure_artifacts(page, "error_runtime_exception")
    finally:
        if page:
            try:
                page.quit()
            except:
                pass
        vdisplay.stop()
        return success, msg, screenshot_path, before_text, after_text, server_name


# ==============================================================================
# 入口：支持多 URL（换行 / 逗号 / 单条 都兼容）
# ==============================================================================
def parse_urls(raw: str):
    raw = (raw or "").strip()
    if not raw:
        return []
    if "\n" in raw:
        return [l.strip() for l in raw.splitlines() if l.strip()]
    if "," in raw:
        return [l.strip() for l in raw.split(",") if l.strip()]
    return [raw]


if __name__ == "__main__":
    raw_urls = os.getenv("RENEW_URL", "")
    tg_token = os.getenv("TG_TOKEN")
    tg_chat_id = os.getenv("TG_CHAT_ID")
    proxy_url = os.getenv("PROXY", "127.0.0.1:1080")

    urls = parse_urls(raw_urls)
    if not urls:
        print("❌ 缺少 RENEW_URL")
        sys.exit(1)

    print(f"📋 共需续期 {len(urls)} 台服务器")

    # [v5] 收集每台完整记录用于汇总
    results = []
    any_failed = False

    for i, url in enumerate(urls, 1):
        print(f"\n{'='*60}")
        print(f"🔄 [{i}/{len(urls)}] 处理: {url}")
        print(f"{'='*60}")

        try:
            ok, msg, shot, before, after, server = renew_host2play(url, proxy_url)
        except Exception as e:
            ok = False
            msg = f"💥 顶层异常: {str(e)[:200]}"
            shot = None
            before = ""
            after = ""
            server = url.rstrip("/").split("/")[-1][:24]

        # [v5] 失败才单独发 TG（带截图，便于即时排查）
        # 成功/失败都发单条卡片；成功会附上成功截图
        status = "✅ 成功" if ok else "❌ 失败"
        reason_line = (msg or "").split("\n", 1)[0].strip()
        per_msg = (
            "🎮 host2play 续期通知\n"
            f"🔢 进度: {i}/{len(urls)}\n"
            f"🖥️ 服务器: {server}\n"
            f"📊 续期结果: {status}\n"
            f"🕒 续期前: {before or '(未读取)'}\n"
            f"🕒 续期后: {after or '(未读取)'}\n"
            f"🧪 判定依据: {reason_line or ('续期成功' if ok else '续期失败')}\n"
            f"🔗 {url}"
        )
        send_tg_message(tg_token, tg_chat_id, per_msg, shot)

        results.append({
            "url": url,
            "server": server or "?",
            "ok": ok,
            "msg": msg,
            "before": before or "(未读取)",
            "after": after or "(未读取)",
        })
        if not ok:
            any_failed = True

        # 节点间冷却
        if i < len(urls):
            cooldown = random.randint(45, 90)
            print(f"😴 冷却 {cooldown}s 再处理下一台...")
            time.sleep(cooldown)

    # ==========================================================================
    # [v5] 汇总消息：前后对比表，所有机器一封
    # ==========================================================================
    success_count = sum(1 for r in results if r["ok"])
    total = len(results)

    summary_lines = [
        f"🎯 host2play 续期汇总  {success_count}/{total} 成功",
        "━━━━━━━━━━━━━━━━━━━━",
    ]

    for r in results:
        flag = "✅" if r["ok"] else "❌"
        # 形如：✅ mcf4006
        summary_lines.append(f"{flag} {r['server']}")
        summary_lines.append(f"  续期前 {r['before']}")
        summary_lines.append(f"  续期后 {r['after']}")
        # before == after 的成功是可疑的（误报兜底提示）
        if r["ok"] and r["before"] == r["after"] and r["before"] != "(未读取)":
            summary_lines.append("  ⚠️ 时间未变化但被判成功，请人工核查")
        if not r["ok"]:
            # 失败原因截短一行展示
            reason = r["msg"].split("\n", 1)[0][:120]
            summary_lines.append(f"  原因: {reason}")
        summary_lines.append("")

    send_tg_message(tg_token, tg_chat_id, "\n".join(summary_lines).rstrip())

    if any_failed:
        sys.exit(1)

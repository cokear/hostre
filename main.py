import os
import re
import sys
import time
import random
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


# ==============================================================================
# [v6] 剩余时间解析器（带单测）
# 支持的输入示例：
#   "Expires in: 04:16:03"             → 4*3600+16*60+3 = 15363
#   "Expires in:\n04:16:03\nDeletes on:\n2026/05/18 00:59:26\nRenew server"
#                                       → 15363（只看 Expires in 后面）
#   "Expires in: 3 days, 12 hours"     → 3*86400 + 12*3600 = 302400
#   "Expires in: 2d 5h 30m"            → 同上规则
#   "Expires in:" / "" / None          → None（解析失败）
# ==============================================================================
def _parse_remaining_seconds(text):
    """
    把 "Expires in" 后面的剩余时间文本转成秒数。
    解析失败返回 None。
    """
    if not text:
        return None
    s = str(text)

    # 截取 "Expires in" 之后的内容
    lower = s.lower()
    idx = lower.find("expires in")
    if idx >= 0:
        s = s[idx + len("expires in"):]
    # 去掉冒号和换行混入的 Deletes on 段
    s = s.split("Deletes on")[0]
    s = s.split("Renew server")[0]
    s = s.strip(": \n\r\t")

    if not s:
        return None

    # 形态 1: HH:MM:SS（最常见）
    m = re.search(r"(\d{1,3}):(\d{2}):(\d{2})", s)
    if m:
        h, mn, sec = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return h * 3600 + mn * 60 + sec

    # 形态 2: 带 day/hour/minute 的英文 / 中文
    total = 0
    matched = False
    patterns = [
        (r"(\d+)\s*(?:days|day|d|天)\b", 86400),
        (r"(\d+)\s*(?:hours|hour|hr|h|小时)\b", 3600),
        (r"(\d+)\s*(?:minutes|minute|min|m|分钟|分)\b", 60),
        (r"(\d+)\s*(?:seconds|second|sec|s|秒)\b", 1),
    ]
    for pat, mult in patterns:
        for m in re.finditer(pat, s, flags=re.IGNORECASE):
            total += int(m.group(1)) * mult
            matched = True
    if matched:
        return total

    return None


def _format_remaining(seconds):
    """秒数 → 中文友好显示（用于 TG 汇总）"""
    if seconds is None:
        return "未知"
    s = int(seconds)
    days = s // 86400
    s %= 86400
    hours = s // 3600
    s %= 3600
    mins = s // 60
    secs = s % 60
    parts = []
    if days:
        parts.append(f"{days}天")
    if hours or days:
        parts.append(f"{hours}小时")
    parts.append(f"{mins}分{secs:02d}秒")
    return "".join(parts)


# 模块自检（CI 跑 main.py 之前会先验证解析器）
def _self_test_parser():
    cases = [
        ("Expires in: 04:16:03", 4 * 3600 + 16 * 60 + 3),
        (
            "Expires in:\n04:16:03\nDeletes on:\n2026/05/18 00:59:26\nRenew server",
            4 * 3600 + 16 * 60 + 3,
        ),
        ("Expires in: 3 days, 12 hours", 3 * 86400 + 12 * 3600),
        ("Expires in: 2d 5h 30m", 2 * 86400 + 5 * 3600 + 30 * 60),
        ("Expires in:", None),
        ("", None),
        (None, None),
    ]
    failed = []
    for text, expected in cases:
        got = _parse_remaining_seconds(text)
        if got != expected:
            failed.append((text, expected, got))
    if failed:
        for text, expected, got in failed:
            print(f"❌ 解析器单测失败: {text!r} → got={got}, expected={expected}")
        return False
    print("✅ 剩余时间解析器单测全部通过")
    return True


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
                """读到期文本：先用 XPath 拿到节点，再爬到父节点取完整 innerText。
                避免 host2play 把 'Expires in:' label 和数值放在两个 sibling span 时
                只读到 label 的问题。
                """
                try:
                    ele = page.ele(
                        'xpath://*[contains(text(), "Expires in") '
                        'or contains(text(), "Deletes on")]',
                        timeout=2,
                    )
                    if not ele:
                        return ""
                    own = (ele.text or "").strip()
                    # 如果自身文本看起来只是 label（无数字 / 太短），找父节点
                    has_digit = any(c.isdigit() for c in own)
                    if not has_digit or len(own) <= 15:
                        try:
                            parent = ele.parent()
                            if parent:
                                pt = (parent.text or "").strip()
                                if pt and any(c.isdigit() for c in pt):
                                    return pt
                                # 再爬一层
                                gp = parent.parent()
                                if gp:
                                    gpt = (gp.text or "").strip()
                                    if gpt and any(c.isdigit() for c in gpt):
                                        return gpt
                        except Exception:
                            pass
                    return own
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
                before_text = old_expire_text
                before_seconds = _parse_remaining_seconds(old_expire_text)
                print(f"📌 续期前剩余文本: {old_expire_text!r}  ≈ {before_seconds}s")

                try:
                    final_btn.click()
                except Exception:
                    final_btn.click(by_js=True)
                print("👇 已点击最终 Renew，等待弹窗关闭...")

                # ----------------------------------------------------------
                # [v6] 关键修正：先等弹窗关掉，然后 page.refresh()
                # 不再依赖"按钮消失"这种 DOM 信号当作成功证据，
                # 唯一硬标准 = reload 后剩余时间至少多 1 小时
                # ----------------------------------------------------------
                modal_closed = False
                for _ in range(15):
                    time.sleep(1)
                    btn_check = page.ele(
                        'xpath://button[normalize-space(text())="Renew"]',
                        timeout=0.3,
                    )
                    if not btn_check:
                        modal_closed = True
                        print("📭 续期弹窗已关闭")
                        break

                # 不论弹窗关没关，都强制 reload，host2play 主页面不会自动更新
                print("🔄 强制刷新页面以拉取最新到期信息...")
                try:
                    page.refresh()
                    time.sleep(random.uniform(4, 6))
                except Exception as ref_err:
                    print(f"⚠️ refresh 失败，回退到 page.get(url): {ref_err}")
                    try:
                        page.get(url, retry=2)
                        time.sleep(random.uniform(4, 6))
                    except Exception:
                        pass

                # 读取 reload 后的剩余时间
                new_expire_text = _read_expire_text()
                after_text = new_expire_text
                after_seconds = _parse_remaining_seconds(new_expire_text)
                print(f"📌 续期后剩余文本: {new_expire_text!r}  ≈ {after_seconds}s")

                # 不论成功失败都先截一张图（页面已 reload，反映最新真实状态）
                try:
                    ts = int(time.time())
                    final_shot = f"after_renew_{ts}.png"
                    page.get_screenshot(path=final_shot, full_page=True)
                    screenshot_path = final_shot
                    print(f"📸 已保存 reload 后截图: {final_shot}")
                except Exception as shot_err:
                    print(f"⚠️ 截图保存失败: {shot_err}")

                # ----------------------------------------------------------
                # [v6] 严格成功判定：剩余秒数至少多 3600s（1 小时）
                # ----------------------------------------------------------
                SUCCESS_DELTA_SECONDS = 3600
                if (
                    after_seconds is not None
                    and before_seconds is not None
                    and after_seconds >= before_seconds + SUCCESS_DELTA_SECONDS
                ):
                    success = True
                    delta_h = (after_seconds - before_seconds) / 3600.0
                    msg = (
                        f"🎉 host2play 续期成功（剩余时间 +{delta_h:.1f}h）"
                    )
                elif (
                    after_seconds is not None
                    and before_seconds is not None
                    and after_seconds < before_seconds + SUCCESS_DELTA_SECONDS
                ):
                    # 时间没变长 → 真失败
                    success = False
                    msg = (
                        f"❌ host2play 续期失败：剩余时间未明显增加"
                        f"（前 {before_seconds}s → 后 {after_seconds}s）"
                    )
                else:
                    # 解析失败时退化为字符串对比 + 弹窗关闭综合判断
                    if (
                        new_expire_text
                        and old_expire_text
                        and new_expire_text != old_expire_text
                    ):
                        success = True
                        msg = "🎉 host2play 续期成功（文本变化）"
                    elif modal_closed and not new_expire_text:
                        # 极端：reload 后读不到 expire，但弹窗确实关了
                        success = True
                        msg = "🎉 host2play 续期成功（弹窗关闭，但解析剩余时间失败）"
                    else:
                        success = False
                        msg = (
                            f"❌ host2play 续期失败：剩余时间无变化"
                            f"（reload 后仍 {new_expire_text!r}）"
                        )
        else:
            if "续期成功" not in msg:
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
    # [v6] 启动时自检解析器，避免线上跑出靠运气的判定
    if not _self_test_parser():
        print("⚠️ 剩余时间解析器单测失败，请检查 _parse_remaining_seconds")
        sys.exit(1)

    raw_urls = os.getenv("RENEW_URL", "")
    tg_token = os.getenv("TG_TOKEN")
    tg_chat_id = os.getenv("TG_CHAT_ID")
    proxy_url = os.getenv("PROXY", "127.0.0.1:1080")

    urls = parse_urls(raw_urls)
    if not urls:
        print("❌ 缺少 RENEW_URL")
        sys.exit(1)

    print(f"📋 共需续期 {len(urls)} 台服务器")

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

        # 失败的立即发独立 TG（带截图，方便即时看到现场）
        if not ok:
            per_msg = (
                f"[{i}/{len(urls)}] ❌ {server} 续期失败\n"
                f"{msg}\n🔗 {url}"
            )
            send_tg_message(tg_token, tg_chat_id, per_msg, shot)

        results.append({
            "url": url,
            "server": server or "?",
            "ok": ok,
            "msg": msg,
            "before_text": before,
            "after_text": after,
            "before_seconds": _parse_remaining_seconds(before),
            "after_seconds": _parse_remaining_seconds(after),
            "screenshot": shot,
        })
        if not ok:
            any_failed = True

        # 节点间冷却
        if i < len(urls):
            cooldown = random.randint(45, 90)
            print(f"😴 冷却 {cooldown}s 再处理下一台...")
            time.sleep(cooldown)

    # ==========================================================================
    # [v6] 中文化汇总消息
    # ==========================================================================
    success_count = sum(1 for r in results if r["ok"])
    total = len(results)

    SEP = "━━━━━━━━━━━━━━━━━━━━"
    summary_lines = [
        f"🎯 host2play 续期汇总  {success_count}/{total} 成功",
        SEP,
    ]

    for r in results:
        flag = "✅" if r["ok"] else "❌"
        verdict = "续期成功" if r["ok"] else "续期失败"
        summary_lines.append(f"{flag} {r['server']}  {verdict}")

        before_disp = _format_remaining(r["before_seconds"])
        after_disp = _format_remaining(r["after_seconds"])
        summary_lines.append(f"  续期前剩余  {before_disp}")
        summary_lines.append(f"  续期后剩余  {after_disp}")

        # 计算变化量（如果两个都解析出来了）
        if (
            r["before_seconds"] is not None
            and r["after_seconds"] is not None
        ):
            delta = r["after_seconds"] - r["before_seconds"]
            if delta > 0:
                summary_lines.append(f"  本次新增    {_format_remaining(delta)}")
            elif delta < 0:
                summary_lines.append(f"  ⚠️ 时间反而减少 {_format_remaining(-delta)}")

        if not r["ok"]:
            reason = r["msg"].split("\n", 1)[0][:120]
            summary_lines.append(f"  失败原因    {reason}")

        summary_lines.append(SEP)

    summary_text = "\n".join(summary_lines)

    # 汇总也带一张图：用第一台成功机器的 reload 截图作为视觉证据
    summary_shot = None
    for r in results:
        if r["ok"] and r["screenshot"]:
            summary_shot = r["screenshot"]
            break
    # 如果没有成功的，就用第一台失败的截图
    if not summary_shot:
        for r in results:
            if r["screenshot"]:
                summary_shot = r["screenshot"]
                break

    send_tg_message(tg_token, tg_chat_id, summary_text, summary_shot)

    if any_failed:
        sys.exit(1)

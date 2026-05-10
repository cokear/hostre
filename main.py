import os
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
    payload = {"chat_id": chat_id, "text": safe_message, "parse_mode": "None"}
    try:
        response = requests.post(text_url, json=payload, timeout=10)
        response.raise_for_status()
        print("✅ Telegram 文本通知发送成功！")
    except Exception as e:
        print(f"❌ Telegram 通知请求异常: {e}")


def capture_failure_artifacts(page, prefix):
    screenshot_path = f"{prefix}.png"
    html_path = f"{prefix}.html"

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
    print("启动 Xvfb 虚拟桌面...")
    vdisplay = Xvfb(width=1280, height=720, colordepth=24)
    vdisplay.start()

    success = False
    msg = ""
    screenshot_path = None
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
            print("🚀 验证完成，点击最终 Renew...")
            final_btn = page.ele(
                'xpath://button[normalize-space(text())="Renew"]', timeout=3
            )
            if final_btn:
                try:
                    final_btn.click()
                except:
                    final_btn.click(by_js=True)
                time.sleep(10)
                msg = "🎉 host2play 续期操作成功！"
                success = True
            else:
                msg = "❌ host2play 找不到最终 Renew 按钮"
                screenshot_path, _ = capture_failure_artifacts(
                    page, "error_no_final_btn"
                )
        else:
            if "操作成功" not in msg:
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
        return success, msg, screenshot_path


if __name__ == "__main__":
    renew_url = os.getenv("RENEW_URL")
    tg_token = os.getenv("TG_TOKEN")
    tg_chat_id = os.getenv("TG_CHAT_ID")
    proxy_url = os.getenv("PROXY", "127.0.0.1:1080")

    if not renew_url:
        print("❌ 缺少 RENEW_URL")
        sys.exit(1)

    is_success, result_message, screenshot_path = renew_host2play(renew_url, proxy_url)
    send_tg_message(tg_token, tg_chat_id, result_message, screenshot_path)
    if not is_success:
        sys.exit(1)

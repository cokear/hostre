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
# Telegram 通知
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
                response = requests.post(photo_url, data=data, files={"photo": photo_file}, timeout=20)
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
# 语音验证码破解
# ==============================================================================
class RecaptchaAudioSolver:
    def __init__(self, page):
        self.page = page
        self.log_func = print

    def log(self, msg):
        self.log_func(f"[Solver] {msg}")

    def human_type(self, ele, text):
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
                    self.page.actions.move_to(reload_btn, duration=random.uniform(0.3, 0.8))
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
                    self.page.actions.move_to(verify_btn, duration=random.uniform(0.5, 1.0))
                    time.sleep(random.uniform(0.2, 0.5))
                    verify_btn.click()
                    self.log("🚀 提交验证...")
                    time.sleep(4)
                    err_check = bframe.ele(".rc-audiochallenge-error-message", timeout=1)
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
# [v7] 核心续期逻辑
# ==============================================================================
def renew_host2play(url, proxy_url=None):
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

        # [v7] 等前端 load() 的 fetch 完成
        time.sleep(3)

        # [v7] 用专属 ID 读服务器名和删除日期
        try:
            server_name = (page.ele("#serverName", timeout=2).text or "").strip()
        except Exception:
            server_name = url.rstrip("/").split("/")[-1][:24]
        print(f"🏷️ 服务器标识: {server_name!r}")

        try:
            before_text = (page.ele("#deleteDate", timeout=2).text or "").strip()
            print(f"📌 续期前删除日期: {before_text!r}")
        except Exception:
            before_text = ""

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
        renew_btn1 = page.ele('xpath://button[contains(text(), "Renew server")]', timeout=3)
        if renew_btn1:
            try:
                renew_btn1.click()
            except:
                renew_btn1.click(by_js=True)
        else:
            page.run_js("document.querySelectorAll('button').forEach(b => {if(b.textContent.includes('Renew server')) b.click();});")
        time.sleep(3)

        # [v7] 等 SweetAlert2 弹窗出现
        for _ in range(10):
            if page.ele(".swal2-popup", timeout=0.5):
                print("✅ SweetAlert2 弹窗已出现")
                break
            time.sleep(1)

        solved_captcha = False
        # [v7] iframe 等待: 30s 轮询
        anchor_frame = None
        for iframe_wait in range(30):
            anchor_frame = page.get_frame('xpath://iframe[contains(@src, "recaptcha/api2/anchor")]', timeout=1)
            if anchor_frame:
                print(f"✅ 锁定 reCAPTCHA 框架 (等了 {iframe_wait}s)")
                break
            if iframe_wait == 0 or iframe_wait % 5 == 0:
                print(f"⏳ 等待 reCAPTCHA iframe ({iframe_wait}s)...")
            time.sleep(1)

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
                screenshot_path, _ = capture_failure_artifacts(page, "error_anchor_timeout")
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
                    bframe = page.get_frame('xpath://iframe[contains(@src, "recaptcha/api2/bframe")]', timeout=5)
                    if bframe:
                        solver = RecaptchaAudioSolver(page)
                        if solver.solve(bframe):
                            solved_captcha = True
        else:
            print("⚠️ 未发现 reCAPTCHA iframe")
            screenshot_path, _ = capture_failure_artifacts(page, "error_no_iframe")
            msg = "❌ host2 未找到 reCAPTCHA 验证码区域"

        if solved_captcha:
            print("🚀 reCAPTCHA 已通过，点击最终 Renew...")
            # [v7] SweetAlert2 的确认按钮
            final_btn = page.ele(".swal2-confirm", timeout=5)
            if not final_btn:
                msg = "❌ host2play 找不到 SweetAlert 确认按钮"
                screenshot_path, _ = capture_failure_artifacts(page, "error_no_swal_confirm")
            else:
                try:
                    final_btn.click()
                except Exception:
                    final_btn.click(by_js=True)
                print("👇 已点击最终 Renew，等待弹窗关闭和页面 reload...")

                # 等 SweetAlert 消失
                for _ in range(20):
                    time.sleep(1)
                    if not page.ele(".swal2-popup", timeout=0.5):
                        print("📭 SweetAlert 弹窗已关闭")
                        break

                # 站点续期成功后会 location.reload()，我们主动刷新确保读到新值
                print("🔄 刷新页面获取最新状态...")
                try:
                    page.get(url, retry=2)
                    time.sleep(random.uniform(4, 6))
                except Exception as ref_err:
                    print(f"⚠️ 刷新失败: {ref_err}")

                try:
                    after_text = (page.ele("#deleteDate", timeout=3).text or "").strip()
                    print(f"📌 续期后删除日期: {after_text!r}")
                except Exception:
                    after_text = ""

                # 截图
                try:
                    ts = int(time.time())
                    final_shot = f"after_renew_{ts}.png"
                    page.get_screenshot(path=final_shot, full_page=True)
                    screenshot_path = final_shot
                    print(f"📸 已保存截图: {final_shot}")
                except Exception as shot_err:
                    print(f"⚠️ 截图保存失败: {shot_err}")

                # [v7] 判定: deleteDate 变了 = 真成功
                if after_text and after_text != before_text:
                    success = True
                    msg = f"🎉 host2play 续期成功！\n   {before_text} → {after_text}"
                elif not after_text:
                    success = False
                    msg = "❌ host2play 续期后无法读取删除日期"
                else:
                    success = False
                    msg = f"❌ host2play 续期失败：删除日期未变化（始终为 {after_text!r}）"
        else:
            if "续期成功" not in msg:
                msg = "❌ host2play 无法通过 reCAPTCHA"
                screenshot_path, _ = capture_failure_artifacts(page, "error_captcha_failed")

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
# 入口
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

        if not ok:
            per_msg = f"[{i}/{len(urls)}] ❌ {server} 续期失败\n{msg}\n🔗 {url}"
            send_tg_message(tg_token, tg_chat_id, per_msg, shot)

        results.append({
            "url": url,
            "server": server or "?",
            "ok": ok,
            "msg": msg,
            "before": before or "未知",
            "after": after or "未知",
            "screenshot": shot,
        })
        if not ok:
            any_failed = True

        if i < len(urls):
            cooldown = random.randint(45, 90)
            print(f"😴 冷却 {cooldown}s 再处理下一台...")
            time.sleep(cooldown)

    # 中文化汇总
    success_count = sum(1 for r in results if r["ok"])
    total = len(results)
    SEP = "━━━━━━━━━━━━━━━━━━━━"
    summary_lines = [f"🎯 host2play 续期汇总  {success_count}/{total} 成功", SEP]

    for r in results:
        flag = "✅" if r["ok"] else "❌"
        verdict = "续期成功" if r["ok"] else "续期失败"
        summary_lines.append(f"{flag} {r['server']}  {verdict}")
        summary_lines.append(f"  续期前删除日期  {r['before']}")
        summary_lines.append(f"  续期后删除日期  {r['after']}")
        if not r["ok"]:
            reason = r["msg"].split("\n", 1)[0][:120]
            summary_lines.append(f"  失败原因        {reason}")
        summary_lines.append(SEP)

    summary_text = "\n".join(summary_lines)

    summary_shot = None
    for r in results:
        if r["ok"] and r["screenshot"]:
            summary_shot = r["screenshot"]
            break
    if not summary_shot:
        for r in results:
            if r["screenshot"]:
                summary_shot = r["screenshot"]
                break

    send_tg_message(tg_token, tg_chat_id, summary_text, summary_shot)

    if any_failed:
        sys.exit(1)

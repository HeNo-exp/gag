import argparse
import random
import time
import sys
import os
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from dotenv import load_dotenv

# Safe print wrapper to avoid UnicodeEncodeError on Windows terminals (e.g. CP949 encoding)
_original_print = print
def print(*args, **kwargs):
    sep = kwargs.get('sep', ' ')
    end = kwargs.get('end', '\n')
    flush = kwargs.get('flush', False)
    msg = sep.join(str(arg) for arg in args)
    try:
        _original_print(msg, sep=sep, end=end, flush=flush)
    except UnicodeEncodeError:
        encoding = sys.stdout.encoding or 'utf-8'
        cleaned = msg.encode(encoding, errors='replace').decode(encoding)
        try:
            _original_print(cleaned, sep=sep, end=end, flush=flush)
        except Exception:
            cleaned_ascii = msg.encode('ascii', errors='replace').decode('ascii')
            _original_print(cleaned_ascii, sep=sep, end=end, flush=flush)

# Load environment variables from .env relative to script directory
# Detect if running as packaged executable
if getattr(sys, 'frozen', False):
    script_dir = os.path.dirname(sys.executable)
    # Point Playwright to the bundled browsers directory inside _MEIPASS
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = os.path.join(sys._MEIPASS, "ms-playwright")
else:
    script_dir = os.path.dirname(os.path.abspath(__file__))

env_path = os.path.join(script_dir, ".env")
if os.path.exists(env_path):
    load_dotenv(dotenv_path=env_path)
else:
    load_dotenv()

# ANSI Escape Colors for premium terminal output
class Colors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'

state_callback = None
should_cancel = None

class CancelledError(Exception):
    pass

def check_cancel():
    if should_cancel and should_cancel():
        raise CancelledError("사용자에 의해 작업이 중단되었습니다.")

def log_info(msg):
    print(f"{Colors.OKCYAN}[INFO] {msg}{Colors.ENDC}")
    if state_callback:
        try:
            state_callback("log", "info", msg)
        except Exception:
            pass

def log_success(msg):
    print(f"{Colors.OKGREEN}[SUCCESS] {msg}{Colors.ENDC}")
    if state_callback:
        try:
            state_callback("log", "success", msg)
        except Exception:
            pass

def log_warning(msg):
    print(f"{Colors.WARNING}[WARNING] {msg}{Colors.ENDC}")
    if state_callback:
        try:
            state_callback("log", "warning", msg)
        except Exception:
            pass

def log_error(msg):
    print(f"{Colors.FAIL}[ERROR] {msg}{Colors.ENDC}")
    if state_callback:
        try:
            state_callback("log", "error", msg)
        except Exception:
            pass

def log_step(step, msg):
    print(f"{Colors.BOLD}{Colors.HEADER}\n=== Step {step}: {msg} ==={Colors.ENDC}")
    if state_callback:
        try:
            state_callback("step", step, msg)
        except Exception:
            pass

def random_delay(base_delay):
    # Add minor randomness to humanize interactions (faster range)
    delay = base_delay + random.uniform(-0.05, 0.05)
    delay = max(0.05, delay)  # Ensure delay is at least 50ms
    time.sleep(delay)

def handle_dialog(dialog):
    log_info(f"브라우저 대화상자 감지: {dialog.message}")
    dialog.accept()
    log_success("대화상자를 수락했습니다.")

def find_login_btn(page):
    """
    Find the Roblox login button using multiple fallback selectors.
    """
    for selector in ["a.brick-btn.brick-btn--roblox", "text='Sign in with Roblox'", "text='Roblox 계정으로 로그인'"]:
        try:
            btn = page.locator(selector)
            if btn.count() > 0 and btn.first.is_visible():
                return btn.first
        except Exception:
            pass
    try:
        btn = page.get_by_text("Sign in with Roblox")
        if btn.count() > 0 and btn.first.is_visible():
            return btn.first
    except Exception:
        pass
    return None

def find_visible_agree_button(page):
    """
    Find the first visible and enabled Roblox OAuth authorization/consent button.
    """
    selector_str = (
        "button:has-text('Authorize'), button:has-text('Agree'), button:has-text('Continue'), "
        "button:has-text('Confirm'), button:has-text('Give Access'), "
        "a:has-text('Authorize'), a:has-text('Agree'), a:has-text('Continue'), "
        "a:has-text('Confirm'), a:has-text('Give Access'), "
        "[role='button']:has-text('Authorize'), [role='button']:has-text('Agree'), "
        "[role='button']:has-text('Continue'), [role='button']:has-text('Confirm'), "
        "[role='button']:has-text('Give Access'), "
        "button:has-text('동의'), button:has-text('접근 허용'), button:has-text('계속'), "
        "button:has-text('허용'), button:has-text('승인'), button:has-text('확인 및 접근 허용'), "
        "a:has-text('동의'), a:has-text('접근 허용'), a:has-text('계속'), "
        "a:has-text('허용'), a:has-text('승인'), a:has-text('확인 및 접근 허용')"
    )
    try:
        locator = page.locator(selector_str)
        count = locator.count()
        for i in range(count):
            btn = locator.nth(i)
            if btn.is_visible() and btn.is_enabled():
                return btn
    except Exception as e:
        log_warning(f"동의 버튼 검색 중 오류: {e}")
    return None

def check_logged_in(page, goto_if_needed=False):
    """
    Check if the user is currently logged in on gag.gg.
    If goto_if_needed is True, it will navigate to the profile page first.
    Otherwise, it checks the current page state without navigating.
    """
    try:
        if goto_if_needed:
            page.goto("https://gag.gg/profile", timeout=15000)
            page.wait_for_timeout(500)
            
        current_url = page.url
        
        # If we are on roblox.com, we are not logged in on gag.gg.
        if "roblox.com" in current_url:
            return False
            
        # If we are not even on gag.gg, we are not logged in yet.
        if "gag.gg" not in current_url:
            return False
            
        # If "Sign in with Roblox" button is visible, we are NOT logged in.
        login_btn = find_login_btn(page)
        if login_btn is not None and login_btn.is_visible():
            return False
            
        # Indicators that only exist in the DOM when logged in (even if hidden inside menus/dropdowns)
        logout_btn = page.locator("button:has-text('Log out'), button:has-text('Sign out'), a:has-text('Log out'), a:has-text('Sign out'), button:has-text('로그아웃')")
        delete_btn = page.locator("text='Delete account & data', text='Delete account', text='Delete Data', text='Delete Profile', text='데이터 삭제'")
        swipe_like = page.locator("button.swipe__btn.swipe__btn--like")
        carrot_input = page.locator("input.ctst__entry-input, button.ctst__entry-submit")
        
        # If any of these authenticated DOM elements are present, we are logged in!
        if logout_btn.count() > 0:
            return True
        if delete_btn.count() > 0:
            return True
        if swipe_like.count() > 0:
            return True
        if carrot_input.count() > 0:
            return True
            
        # Fallback check for profile page buttons
        if "profile" in current_url:
            all_buttons = page.locator("button, a.brick-btn")
            for i in range(all_buttons.count()):
                try:
                    btn = all_buttons.nth(i)
                    if btn.is_visible():
                        btn_text = btn.inner_text().strip().lower()
                        if "sign in" not in btn_text and "roblox" not in btn_text:
                            return True
                except Exception:
                    pass
                
        return False
    except Exception as e:
        log_warning(f"로그인 상태 확인 중 에러 발생: {e}")
        return False

def wait_for_user_login(page, roblox_user=None, roblox_pass=None):
    """
    Wait until the user completes login manually, or auto-fill if credentials are provided.
    """
    log_warning("Roblox 계정이 gag.gg에 연결되어 있지 않습니다.")
    log_info("브라우저 창에서 Roblox 로그인 및 gag.gg 연결을 완료해 주세요.")
    
    # Try clicking the Roblox login button if we are on the profile page
    try:
        login_btn = find_login_btn(page)
        if login_btn is not None:
            log_info("자동으로 'Sign in with Roblox' 버튼을 클릭합니다.")
            login_btn.click()
            page.wait_for_timeout(1000)
    except Exception:
        pass
        
    has_attempted_login = False
    
    while True:
        check_cancel()
        try:
            if not has_attempted_login and roblox_user and roblox_pass:
                if "roblox.com" in page.url and "login" in page.url:
                    attempt_success = handle_roblox_login_page(page, roblox_user, roblox_pass)
                    has_attempted_login = True
                    
            # Check for Roblox OAuth/consent page (like Continue or Authorize) and click it automatically
            if "roblox.com" in page.url:
                agree_btn = find_visible_agree_button(page)
                if agree_btn is not None:
                    try:
                        btn_text = agree_btn.evaluate("el => el.innerText").strip()
                        log_info(f"Roblox 동의/계속 버튼 자동 클릭 시도: '{btn_text}'")
                        agree_btn.click(timeout=4000)
                        log_success(f"Roblox 동의/계속 버튼 클릭 완료: '{btn_text}'")
                        page.wait_for_timeout(1000)
                    except Exception as e:
                        log_warning(f"Roblox 버튼 클릭 실패 (재시도 예정): {e}")
                    
            if check_logged_in(page, goto_if_needed=False):
                log_success("로그인이 확인되었습니다!")
                break
            time.sleep(0.5)
        except KeyboardInterrupt:
            log_error("사용자에 의해 중단되었습니다.")
            sys.exit(0)

def swipe_loop(page, base_delay):
    """
    Perform swiping on gag.gg/vote until swipes run out.
    """
    log_info("투표(스와이프) 페이지로 이동합니다: https://gag.gg/vote")
    page.goto("https://gag.gg/vote", timeout=15000)
    page.wait_for_timeout(500)
    
    swipe_count = 0
    consecutive_errors = 0
    
    # Like button selector
    like_btn_selector = "button.swipe__btn.swipe__btn--like"
    
    while True:
        check_cancel()
        try:
            # Dismiss "Lucky Swipe" popups if they appear
            awesome_btn = page.locator("button:has-text('Awesome'), [role='button']:has-text('Awesome'), button:has-text('Awesome!')")
            if awesome_btn.count() > 0 and awesome_btn.first.is_visible():
                log_info("Lucky Swipe 팝업 감지! 팝업을 닫습니다.")
                awesome_btn.first.click()
                page.wait_for_timeout(500)
                
            like_btn = page.locator(like_btn_selector)
            
            # If the button is not immediately visible/present, wait up to 3 seconds for it to load
            if like_btn.count() == 0 or not like_btn.first.is_visible():
                log_info("스와이프 버튼 대기 중 (3초)...")
                page.wait_for_timeout(3000)
                like_btn = page.locator(like_btn_selector)
                
            if like_btn.count() == 0 or not like_btn.first.is_visible():
                log_success("스와이프 버튼이 더 이상 보이지 않습니다. 스와이프를 완료했습니다.")
                break
                
            if like_btn.first.is_disabled():
                # It might be temporarily disabled during card transition. Wait 1.5 seconds and check again.
                page.wait_for_timeout(1500)
                like_btn = page.locator(like_btn_selector)
                if like_btn.count() > 0 and like_btn.first.is_disabled():
                    log_success("스와이프 버튼이 실제로 비활성화되었습니다. 스와이프를 완료했습니다.")
                    break
                
            # Perform click
            like_btn.first.click()
            swipe_count += 1
            print(f"\r{Colors.OKBLUE}[SWIPE] 스와이프 {swipe_count}회 완료...{Colors.ENDC}", end="", flush=True)
            if state_callback:
                try:
                    state_callback("swipe", swipe_count)
                except Exception:
                    pass
            
            if swipe_count >= 20:
                log_success("\n목표 스와이프 횟수(20회)에 도달했습니다. 스와이프를 완료합니다.")
                break
                
            consecutive_errors = 0
            random_delay(base_delay)
            
        except PlaywrightTimeoutError:
            log_warning("요소 로딩 대기 시간 초과. 다음 단계로 진행합니다.")
            break
        except Exception as e:
            consecutive_errors += 1
            log_warning(f"\n스와이프 진행 중 오류 발생 ({consecutive_errors}/3): {e}")
            if consecutive_errors >= 3:
                log_error("연속 오류 발생으로 스와이프 루프를 종료합니다.")
                break
            time.sleep(2)
            
    print() # New line after carriage return
    log_success(f"총 {swipe_count}회의 스와이프를 완료했습니다.")
    return swipe_count

def delete_profile_data(page):
    """
    Navigate to profile and trigger Delete Data flow.
    """
    log_info("프로필 페이지로 이동합니다: https://gag.gg/profile")
    page.goto("https://gag.gg/profile", timeout=15000)
    page.wait_for_timeout(500)
    
    # Define possible locators for the Delete Data button
    delete_locators = [
        page.locator("text='Delete account & data'"),
        page.locator("text='Delete account'"),
        page.locator("text='Delete Data'"),
        page.locator("text='Delete Profile'"),
        page.locator("text='데이터 삭제'"),
        page.locator("button:has-text('Delete')"),
        page.locator("button:has-text('Delete Data')"),
        page.locator("button:has-text('Remove Account')")
    ]
    
    target_btn = None
    for loc in delete_locators:
        if loc.count() > 0 and loc.first.is_visible():
            target_btn = loc.first
            break
            
    if target_btn:
        log_info(f"데이터 삭제 버튼 발견: '{target_btn.inner_text()}'")
        target_btn.click()
        page.wait_for_timeout(300)
        
        # Check if there is an additional HTML modal confirmation button
        # We must filter out target_btn itself to avoid clicking the main button again.
        confirm_btn = None
        for text in ['Confirm', 'Yes', 'Delete', '확인']:
            loc = page.locator(f"button:has-text('{text}'), [role='button']:has-text('{text}')")
            for i in range(loc.count()):
                btn = loc.nth(i)
                try:
                    if btn.is_visible() and btn.evaluate("el => el.innerText").strip() != target_btn.evaluate("el => el.innerText").strip():
                        confirm_btn = btn
                        break
                except Exception:
                    pass
            if confirm_btn:
                break
                
        if confirm_btn:
            log_info(f"확인 모달 버튼 클릭: '{confirm_btn.evaluate('el => el.innerText').strip()}'")
            confirm_btn.click()
            page.wait_for_timeout(500)
            
        log_success("데이터 삭제 요청을 전송했습니다.")
        return True
    else:
        log_warning("Delete Data 버튼을 자동으로 찾을 수 없습니다. 1초 대기 후 강제 진행합니다.")
        page.wait_for_timeout(1000)
        return True

def logout(page):
    """
    Log out from gag.gg by clicking logout button and clearing gag.gg cookies.
    """
    log_info("gag.gg 로그아웃을 진행합니다...")
    try:
        page.goto("https://gag.gg/profile", timeout=15000)
        page.wait_for_timeout(500)
        
        # Try clicking logout button
        logout_btn = page.locator("button:has-text('Log out'), button:has-text('Sign out'), a:has-text('Log out'), a:has-text('Sign out'), button:has-text('로그아웃')")
        if logout_btn.count() > 0 and logout_btn.first.is_visible():
            log_info(f"로그아웃 버튼 클릭: '{logout_btn.first.inner_text().strip()}'")
            logout_btn.first.click()
            page.wait_for_timeout(800)
            
        # Fallback/Safety: Clear gag.gg cookies
        page.context.clear_cookies(domain="gag.gg")
        page.context.clear_cookies(domain=".gag.gg")
        page.wait_for_timeout(200)
        log_success("로그아웃 완료.")
        return True
    except Exception as e:
        log_warning(f"로그아웃 중 예외 발생 (쿠키 강제 삭제 시도): {e}")
        try:
            page.context.clear_cookies(domain="gag.gg")
            page.context.clear_cookies(domain=".gag.gg")
            return True
        except Exception as ex:
            log_error(f"쿠키 강제 삭제 실패: {ex}")
            return False

def relogin(page, roblox_user=None, roblox_pass=None):
    """
    Perform Roblox authorization login reset, retrying until successful.
    """
    # Safeguard: if already logged in, log out first
    if check_logged_in(page, goto_if_needed=True):
        log_warning("재로그인 시작 시점에 로그인 상태가 감지되어 로그아웃을 실행합니다.")
        logout(page)
        
    attempt = 0
    while True:
        check_cancel()
        attempt += 1
        log_info(f"재로그인 시도 중... (시도 횟수: {attempt})")
        
        try:
            log_info("로그인 페이지/프로필 페이지로 이동합니다.")
            page.goto("https://gag.gg/profile", timeout=15000)
            page.wait_for_timeout(500)
            
            # Check if we somehow got logged in
            if check_logged_in(page, goto_if_needed=False):
                log_success(f"재로그인 완료! (시도 횟수: {attempt})")
                return True
                
            login_btn = find_login_btn(page)
            if login_btn is not None:
                log_info("Roblox 로그인 버튼 클릭 시도...")
                login_btn.click()
                page.wait_for_timeout(1000) # Wait for redirects
                
                # 1. Handle Roblox credentials input if page redirects to login
                handle_roblox_login_page(page, roblox_user, roblox_pass)
                
                # 2. Check if redirected to roblox.com oauth page
                # If Roblox already has active session, it will either auto-authorize or show an "Authorize" or "Continue" button
                roblox_attempts = 0
                while "roblox.com" in page.url and roblox_attempts < 15:
                    roblox_attempts += 1
                    log_info(f"Roblox 인증 페이지 감지됨 (단계 {roblox_attempts}). 로딩 대기 및 버튼 자동 클릭 시도...")
                    page.wait_for_timeout(800)
                    agree_btn = find_visible_agree_button(page)
                    if agree_btn is not None:
                        try:
                            btn_text = agree_btn.evaluate("el => el.innerText").strip()
                            log_info(f"Roblox 동의/계속 버튼 자동 클릭 시도: '{btn_text}'")
                            agree_btn.click(timeout=4000)
                            log_success(f"Roblox 동의/계속 버튼 클릭 완료: '{btn_text}'")
                            page.wait_for_timeout(1000) # Wait for page redirect/load
                        except Exception as e:
                            log_warning(f"Roblox 버튼 클릭 실패 (재시도 예정): {e}")
                            page.wait_for_timeout(300)
                    else:
                        log_info("표시된 Roblox 동의/계속 버튼을 찾을 수 없습니다. 대기 중...")
                        page.wait_for_timeout(300)
                            
                # 3. Wait until redirected back to gag.gg and logged in
                log_info("gag.gg로의 리다이렉션 및 로그인을 대기 중...")
                for sec in range(1, 11):
                    if "gag.gg" in page.url and check_logged_in(page, goto_if_needed=False):
                        log_success(f"재로그인 완료! (시도 횟수: {attempt})")
                        return True
                    print(f"\r대기 중... ({sec*0.5:.1f}초 경과)", end="", flush=True)
                    time.sleep(0.5)
                print()
                    
            else:
                log_warning("로그인 버튼을 찾을 수 없습니다.")
                
            # If we are here, the login attempt failed. Check if we are logged in anyway just in case
            if check_logged_in(page, goto_if_needed=False):
                log_success(f"재로그인 완료! (시도 횟수: {attempt})")
                return True
                
            log_warning(f"재로그인 {attempt}회차 실패. 1초 후 재시도합니다...")
            page.wait_for_timeout(1000)
            
        except Exception as e:
            log_warning(f"재로그인 {attempt}회차 중 예외 발생: {e}. 1초 후 재시도합니다...")
            page.wait_for_timeout(1000)

def handle_roblox_login_page(page, username, password):
    """
    Detect Roblox login page and fill credentials if provided.
    """
    current_url = page.url
    if "roblox.com" in current_url and ("login" in current_url or "login" in page.content().lower()):
        log_info("Roblox 로그인 페이지 감지됨.")
        if not username or not password:
            log_warning("Roblox 계정 정보(username/password)가 설정되지 않았습니다.")
            log_info("브라우저 창에서 수동으로 로그인을 진행해 주세요.")
            return False
            
        try:
            log_info(f"Roblox 계정({username}) 로그인 정보 자동 입력 중...")
            
            # Locate username input
            username_input = page.locator("input#login-username, input[name='username'], input#username")
            password_input = page.locator("input#login-password, input[name='password'], input#password")
            submit_btn = page.locator("button#login-button, button:has-text('Log In'), button:has-text('로그인')")
            
            if username_input.count() > 0:
                username_input.first.fill(username)
                page.wait_for_timeout(500)
                password_input.first.fill(password)
                page.wait_for_timeout(500)
                
                # Check for clickability
                if submit_btn.count() > 0:
                    submit_btn.first.click()
                    log_info("로그인 버튼을 클릭했습니다. 로딩 대기 중...")
                    page.wait_for_timeout(5000)
                    
                    # Detect CAPTCHA
                    if "captcha" in page.content().lower() or page.locator("iframe[src*='arkoselabs']").count() > 0:
                        log_warning("Roblox 로그인 보안 캡차가 감지되었습니다. 브라우저 창에서 캡차를 직접 풀어주세요.")
                        return False
                    return True
            else:
                log_warning("로그인 입력 필드를 찾을 수 없습니다.")
        except Exception as e:
            log_error(f"Roblox 자동 로그인 중 오류 발생: {e}")
    return False

def claim_contest_rewards(page, skip_if_not_found=False):
    """
    Go to contest page and submit carrots (or convert/claim rewards).
    """
    log_info("보상 수령 및 당근 소진 페이지로 이동합니다: https://gag.gg/contest")
    try:
        page.goto("https://gag.gg/contest", timeout=15000)
        page.wait_for_timeout(800)
        
        # 1. Look for Carrot Contest "Max" and "Join" buttons to spend/submit carrots
        max_btn = page.locator("button.ctst__entry-max, button:has-text('Max'), button:has-text('max')")
        join_btn = page.locator("button.ctst__entry-submit, button:has-text('Join'), button:has-text('join'), button:has-text('참가')")
        
        submitted_carrots = False
        
        if max_btn.count() > 0 and max_btn.first.is_visible() and join_btn.count() > 0 and join_btn.first.is_visible():
            # Check if the join button is disabled (e.g. 0 carrots)
            if not join_btn.first.is_disabled():
                log_info("당근 소진을 위해 'Max' 버튼 클릭 시도...")
                max_btn.first.click()
                page.wait_for_timeout(300)
                
                # Check if input has any value (if it's 0 or empty, we don't need to join)
                input_field = page.locator("input.ctst__entry-input, input[aria-label='Carrots to join with']")
                val = ""
                if input_field.count() > 0:
                    val = input_field.first.evaluate("el => el.value")
                
                if val and val != "0":
                    log_info(f"당근 {val}개 제출을 위해 'Join' 버튼 클릭 시도...")
                    join_btn.first.click()
                    page.wait_for_timeout(1000)
                    log_success(f"성공적으로 당근 {val}개를 소진하여 콘테스트에 참가했습니다.")
                    if state_callback:
                        try:
                            state_callback("carrots", val)
                        except Exception:
                            pass
                    submitted_carrots = True
                else:
                    log_info("보유한 당근이 0개이므로 참가(소진) 단계를 건너뜁니다.")
                    submitted_carrots = True # Treat as success/done
            else:
                log_info("콘테스트 참가 버튼이 이미 비활성화되어 있습니다.")
                submitted_carrots = True
                
        # 2. Fallback: Look for other claim/convert buttons if "Max" & "Join" were not processed
        if not submitted_carrots:
            keywords = ["claim", "collect", "convert", "exchange", "submit", "당근", "수령", "보상", "전환", "받기"]
            buttons = page.locator("button, a.brick-btn, div[role='button']").all()
            target_btn = None
            
            for btn in buttons:
                try:
                    if btn.is_visible() and not btn.is_disabled():
                        text = btn.inner_text().strip().lower()
                        if not text:
                            continue
                        if any(kw in text for kw in keywords):
                            if any(prefer in text for prefer in ["claim", "collect", "당근", "수령", "받기"]):
                                target_btn = btn
                                break
                            if target_btn is None:
                                target_btn = btn
                except Exception:
                    pass
                    
            if target_btn:
                btn_text = target_btn.inner_text().strip()
                log_info(f"일반 보상 버튼 클릭 시도: '{btn_text}'")
                target_btn.click()
                page.wait_for_timeout(1000)
                log_success(f"보상 버튼('{btn_text}')을 정상적으로 클릭했습니다.")
                return True
            else:
                if skip_if_not_found:
                    log_info("보상/소진 버튼을 찾지 못했고 이번 루프에서 스와이프를 진행하지 않았으므로 건너뜁니다.")
                    return True
                    
                log_warning("자동으로 보상 수령/소진 버튼을 특정할 수 없습니다. 1초 대기 후 강제 진행합니다.")
                page.wait_for_timeout(1000)
                return True
                
        return True
    except Exception as e:
        log_error(f"보상 수령/소진 진행 중 오류 발생: {e}")
        return False

def main():
    parser = argparse.ArgumentParser(description="gag.gg Grow a Garden 2 무료 시드 팩 획득 자동화 스크립트")
    parser.add_argument("--loops", type=int, default=5, help="반복 실행할 루프 횟수 (기본값: 5)")
    parser.add_argument("--delay", type=float, default=0.3, help="동작 간 기본 대기 시간 (초) (기본값: 0.3)")
    parser.add_argument("--profile-dir", type=str, default=None, help="파이어폭스 프로필 경로 (기본값: c:\\gag\\.firefox_profile)")
    parser.add_argument("--no-headless", action="store_true", help="헤드리스 모드 비활성화 (브라우저 UI 표시)")
    parser.add_argument("--roblox-user", type=str, default=os.environ.get("ROBLOX_USER") or os.environ.get("ROBLOX_USERNAME"), help="Roblox 사용자명 (자동 로그인용)")
    parser.add_argument("--roblox-pass", type=str, default=os.environ.get("ROBLOX_PASS") or os.environ.get("ROBLOX_PASSWORD"), help="Roblox 비밀번호 (자동 로그인용)")
    
    args = parser.parse_args()
    
    # Default to headless (background) unless --no-headless is passed
    headless_val = not args.no_headless
    
    # Setup profile directory
    if args.profile_dir is None:
        args.profile_dir = os.path.join(os.getcwd(), ".firefox_profile")
        
    if not os.path.exists(args.profile_dir):
        os.makedirs(args.profile_dir)
        log_info(f"파이어폭스 프로필 디렉토리를 생성했습니다: {args.profile_dir}")
        
    log_info("=== gag.gg 자동화 프로그램 시작 ===")
    log_info(f"설정된 루프 수: {args.loops}회")
    log_info(f"동작 간 지연 시간: {args.delay}초")
    log_info(f"프로필 경로: {args.profile_dir}")
    log_info(f"헤드리스 모드: {headless_val}")
    if args.roblox_user:
        log_info(f"Roblox 자동 로그인 설정됨 (사용자: {args.roblox_user})")
    else:
        log_warning("Roblox 사용자명이 지정되지 않았습니다. 필요 시 브라우저에서 직접 로그인해야 합니다.")
    
    with sync_playwright() as p:
        log_info("파이어폭스 브라우저를 시작합니다...")
        try:
            context = p.firefox.launch_persistent_context(
                user_data_dir=args.profile_dir,
                headless=headless_val,
                slow_mo=50  # 50ms action delay for fast and stable operation
            )
        except Exception as e:
            log_error(f"브라우저 시작 오류. 다른 파이어폭스 프로세스가 프로필 디렉토리를 점유하고 있을 수 있습니다: {e}")
            sys.exit(1)
            
        page = context.new_page()
        
        # Handle JS confirm/alert dialogs automatically
        page.on("dialog", handle_dialog)
        
        # Ensure user is logged in first
        log_step("0", "로그인 상태 확인 및 대기")
        if not check_logged_in(page, goto_if_needed=True):
            wait_for_user_login(page, args.roblox_user, args.roblox_pass)
            
        loop = 0
        while True:
            if args.loops > 0 and loop >= args.loops:
                break
            loop += 1
            log_step(str(loop), f"총 {args.loops if args.loops > 0 else '무제한'}회 중 {loop}번째 루프 시작")
            
            # Step 1: Swipe until finished
            log_info(f"[{loop}루프] 스와이프를 시작합니다.")
            swipe_count = swipe_loop(page, args.delay)
            
            # Step 1.5: Claim Contest Rewards (Convert votes to carrots)
            log_info(f"[{loop}루프] 보상을 당근으로 전환/수령합니다.")
            claim_contest_rewards(page, skip_if_not_found=(swipe_count == 0))
            
            # Step 2: Delete Data
            log_info(f"[{loop}루프] 데이터를 삭제하여 스와이프 횟수 초기화를 진행합니다.")
            delete_success = delete_profile_data(page)
            if not delete_success:
                log_error("데이터 삭제 단계 실패. 스크립트를 중지합니다.")
                break
                
            # Step 2.5: Log out to ensure session is cleared
            log_info(f"[{loop}루프] 이전 세션을 정리하기 위해 로그아웃합니다.")
            logout(page)
            
            # Step 3: Re-login
            log_info(f"[{loop}루프] 다시 로그인하여 스와이프 제한을 리셋합니다.")
            login_success = relogin(page, args.roblox_user, args.roblox_pass)
            if not login_success:
                log_error("재로그인 단계 실패. 스크립트를 중지합니다.")
                break
                
            log_success(f"[{loop}루프] 성공적으로 완료되었습니다!")
            
        # Keep open at the end if not headless, so user can check
        if not headless_val:
            log_info("모든 루프가 성공적으로 끝났습니다. 브라우저를 수동으로 종료하시거나 터미널을 종료해주세요.")
            input("프로그램을 종료하려면 Enter를 누르세요...")
            
        context.close()
        log_success("프로그램이 정상적으로 종료되었습니다.")

if __name__ == "__main__":
    main()

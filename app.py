import json
import threading
import sys
import os
import time
import traceback
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

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

# Detect if running as packaged executable
if getattr(sys, 'frozen', False):
    base_dir = os.path.dirname(sys.executable)
    # Point Playwright to the bundled browsers directory inside _MEIPASS
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = os.path.join(sys._MEIPASS, "ms-playwright")
else:
    base_dir = os.path.dirname(os.path.abspath(__file__))

sys.path.append(base_dir)
import autoplay
from playwright.sync_api import sync_playwright

# Global state
farming_state = {
    "status": "idle",            # "idle", "running", "completed", "failed", "stopped"
    "current_loop": 0,
    "total_loops": 5,
    "current_step": "대기 중",
    "progress_percent": 0.0,
    "swipe_count": 0,
    "total_swipes": 0,
    "carrots_submitted": 0,
    "logs": [],
    "error_message": None
}

# Shared variables for synchronization
active_context = None
active_thread = None

# Load initial config from .env if available
def load_env_credentials():
    user = os.environ.get("ROBLOX_USER") or os.environ.get("ROBLOX_USERNAME") or ""
    pwd = os.environ.get("ROBLOX_PASS") or os.environ.get("ROBLOX_PASSWORD") or ""
    return user, pwd

initial_user, initial_pass = load_env_credentials()

def state_callback(event_type, *args):
    global farming_state
    if event_type == "log":
        level, msg = args
        farming_state["logs"].append({"level": level, "msg": msg, "time": time.strftime("%H:%M:%S")})
        # Keep logs under 300 entries
        if len(farming_state["logs"]) > 300:
            farming_state["logs"].pop(0)
    elif event_type == "step":
        step, msg = args
        farming_state["current_step"] = msg
        try:
            loop_val = int(step)
            farming_state["current_loop"] = loop_val
            total = farming_state["total_loops"]
            if total > 0:
                if loop_val == 0:
                    farming_state["progress_percent"] = 5.0
                else:
                    # Increment progress based on loops completed
                    farming_state["progress_percent"] = min(100.0, round(((loop_val - 1) / total) * 100.0 + 10.0, 1))
        except ValueError:
            pass
    elif event_type == "swipe":
        count, = args
        farming_state["swipe_count"] = count
        farming_state["total_swipes"] += 1
    elif event_type == "carrots":
        val, = args
        try:
            farming_state["carrots_submitted"] += int(val)
        except ValueError:
            pass

def run_automation_thread(config):
    global farming_state, active_context, active_thread
    
    # Initialize State
    farming_state["status"] = "running"
    farming_state["current_loop"] = 0
    farming_state["total_loops"] = config["loops"]
    farming_state["current_step"] = "브라우저 초기화 중..."
    farming_state["progress_percent"] = 0.0
    farming_state["swipe_count"] = 0
    farming_state["total_swipes"] = 0
    farming_state["carrots_submitted"] = 0
    farming_state["logs"] = []
    farming_state["error_message"] = None
    
    autoplay.state_callback = state_callback
    autoplay.should_cancel = lambda: farming_state["status"] == "stopped"
    
    class MockArgs:
        loops = config["loops"]
        delay = config["delay"]
        profile_dir = os.path.join(base_dir, ".firefox_profile")
        roblox_user = config["roblox_user"]
        roblox_pass = config["roblox_pass"]
        headless = config["headless"]
        
    args = MockArgs()
    
    if not os.path.exists(args.profile_dir):
        os.makedirs(args.profile_dir)
        
    autoplay.log_info("=== Web UI를 통한 자동화 파밍 시작 ===")
    autoplay.log_info(f"설정: loops={args.loops}, delay={args.delay}s, headless={args.headless}")
    
    try:
        with sync_playwright() as p:
            autoplay.log_info("파이어폭스 브라우저를 시작합니다...")
            try:
                context = p.firefox.launch_persistent_context(
                    user_data_dir=args.profile_dir,
                    headless=args.headless,
                    slow_mo=50
                )
                active_context = context
            except Exception as e:
                autoplay.log_error(f"브라우저 시작 오류: {e}")
                raise e
                
            page = context.new_page()
            page.on("dialog", autoplay.handle_dialog)
            
            # Ensure user is logged in
            autoplay.log_step("0", "로그인 상태 확인 및 대기")
            if not autoplay.check_logged_in(page, goto_if_needed=True):
                autoplay.wait_for_user_login(page, args.roblox_user, args.roblox_pass)
                
            loop = 0
            while True:
                # Check for cancellation
                if farming_state["status"] == "stopped":
                    break
                
                # Check if loop count reached
                if args.loops > 0 and loop >= args.loops:
                    break
                
                loop += 1
                autoplay.log_step(str(loop), f"총 {args.loops if args.loops > 0 else '무제한'}회 중 {loop}번째 루프 시작")
                
                # Step 1: Swipe
                autoplay.log_info(f"[{loop}루프] 스와이프를 시작합니다.")
                swipe_count = autoplay.swipe_loop(page, args.delay)
                
                # Step 1.5: Claim Contest Rewards
                autoplay.log_info(f"[{loop}루프] 보상을 당근으로 전환/수령합니다.")
                autoplay.claim_contest_rewards(page, skip_if_not_found=(swipe_count == 0))
                
                # Step 2: Delete Data
                autoplay.log_info(f"[{loop}루프] 데이터를 삭제하여 스와이프 횟수 초기화를 진행합니다.")
                delete_success = autoplay.delete_profile_data(page)
                if not delete_success:
                    autoplay.log_error("데이터 삭제 단계 실패. 스크립트를 중지합니다.")
                    raise RuntimeError("데이터 삭제 단계 실패")
                    
                # Step 2.5: Logout
                autoplay.log_info(f"[{loop}루프] 이전 세션을 정리하기 위해 로그아웃합니다.")
                autoplay.logout(page)
                
                # Step 3: Re-login
                autoplay.log_info(f"[{loop}루프] 다시 로그인하여 스와이프 제한을 리셋합니다.")
                login_success = autoplay.relogin(page, args.roblox_user, args.roblox_pass)
                if not login_success:
                    autoplay.log_error("재로그인 단계 실패. 스크립트를 중지합니다.")
                    raise RuntimeError("재로그인 단계 실패")
                    
                autoplay.log_success(f"[{loop}루프] 성공적으로 완료되었습니다!")
                if args.loops > 0:
                    farming_state["progress_percent"] = round((loop / args.loops) * 100.0, 1)
                else:
                    farming_state["progress_percent"] = 100.0
                
            # Close browser context
            if active_context:
                context.close()
                active_context = None
                
            if farming_state["status"] == "running":
                farming_state["status"] = "completed"
                farming_state["progress_percent"] = 100.0
                farming_state["current_step"] = "모든 루프 완료"
                autoplay.log_success("모든 자동화 루프가 끝났습니다!")
                
    except Exception as e:
        active_context = None
        tb = traceback.format_exc()
        if farming_state["status"] == "stopped" or isinstance(e, autoplay.CancelledError):
            autoplay.log_warning("농사가 사용자에 의해 중단되었습니다.")
            farming_state["current_step"] = "사용자 중단됨"
        else:
            autoplay.log_error(f"실행 오류 발생:\n{tb}")
            farming_state["status"] = "failed"
            farming_state["error_message"] = str(e)
            farming_state["current_step"] = f"에러: {e}"

def stop_farming():
    global farming_state, active_context
    if farming_state["status"] == "running":
        farming_state["status"] = "stopped"
        autoplay.log_warning("중단 요청을 처리 중입니다...")
        if active_context:
            try:
                active_context.close()
            except Exception:
                pass

HTML_DASHBOARD = f"""
<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>GAG2 자동 파밍 제어 센터</title>
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;800&family=JetBrains+Mono:wght@400;700&display=swap" rel="stylesheet">
    <style>
        :root {{
            --bg-color: #080911;
            --panel-bg: rgba(18, 20, 38, 0.65);
            --border-color: rgba(255, 255, 255, 0.08);
            --primary-accent: #00f2fe;
            --secondary-accent: #4facfe;
            --success-color: #00f5a0;
            --warning-color: #ff9f43;
            --danger-color: #ff4d6e;
            --text-primary: #ffffff;
            --text-secondary: #a0aec0;
            --card-glow: rgba(0, 242, 254, 0.15);
        }}

        * {{
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }}

        body {{
            background-color: var(--bg-color);
            color: var(--text-primary);
            font-family: 'Outfit', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            overflow-x: hidden;
            background-image: radial-gradient(circle at 10% 20%, rgba(0, 242, 254, 0.06) 0%, transparent 45%),
                              radial-gradient(circle at 90% 80%, rgba(79, 172, 254, 0.06) 0%, transparent 45%);
            min-height: 100vh;
        }}

        .wrapper {{
            max-width: 1200px;
            margin: 0 auto;
            padding: 40px 20px;
        }}

        header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 35px;
            border-bottom: 1px solid var(--border-color);
            padding-bottom: 20px;
        }}

        header .logo-area h1 {{
            font-weight: 800;
            font-size: 2.2rem;
            background: linear-gradient(135deg, var(--primary-accent), var(--secondary-accent));
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            letter-spacing: -1px;
        }}

        header .logo-area p {{
            font-size: 0.9rem;
            color: var(--text-secondary);
            margin-top: 4px;
        }}

        .status-badge {{
            padding: 8px 18px;
            border-radius: 30px;
            font-weight: 700;
            font-size: 0.85rem;
            display: flex;
            align-items: center;
            gap: 8px;
            background: rgba(255, 255, 255, 0.04);
            border: 1px solid var(--border-color);
            letter-spacing: 0.5px;
            transition: all 0.3s ease;
        }}

        .status-badge::before {{
            content: '';
            display: inline-block;
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background-color: var(--text-secondary);
        }}

        .status-idle {{ color: var(--text-secondary); }}
        .status-idle::before {{ background-color: var(--text-secondary); box-shadow: 0 0 10px var(--text-secondary); }}

        .status-running {{ 
            color: var(--primary-accent); 
            border-color: rgba(0, 242, 254, 0.3);
            background: rgba(0, 242, 254, 0.05);
        }}
        .status-running::before {{
            background-color: var(--primary-accent);
            box-shadow: 0 0 12px var(--primary-accent);
            animation: pulse 1.5s infinite;
        }}

        .status-completed {{ 
            color: var(--success-color); 
            border-color: rgba(0, 245, 160, 0.3);
            background: rgba(0, 245, 160, 0.05);
        }}
        .status-completed::before {{ background-color: var(--success-color); box-shadow: 0 0 10px var(--success-color); }}

        .status-failed {{ 
            color: var(--danger-color); 
            border-color: rgba(255, 77, 110, 0.3);
            background: rgba(255, 77, 110, 0.05);
        }}
        .status-failed::before {{ background-color: var(--danger-color); box-shadow: 0 0 10px var(--danger-color); }}

        .status-stopped {{ 
            color: var(--warning-color); 
            border-color: rgba(255, 159, 67, 0.3);
            background: rgba(255, 159, 67, 0.05);
        }}
        .status-stopped::before {{ background-color: var(--warning-color); box-shadow: 0 0 10px var(--warning-color); }}

        @keyframes pulse {{
            0% {{ opacity: 0.4; transform: scale(1); }}
            50% {{ opacity: 1; transform: scale(1.25); }}
            100% {{ opacity: 0.4; transform: scale(1); }}
        }}

        .grid-container {{
            display: grid;
            grid-template-columns: 380px 1fr;
            gap: 30px;
            align-items: start;
        }}

        .panel {{
            background: var(--panel-bg);
            border-radius: 20px;
            border: 1px solid var(--border-color);
            padding: 30px;
            backdrop-filter: blur(20px);
            box-shadow: 0 15px 35px rgba(0,0,0,0.3);
            transition: border-color 0.3s ease, box-shadow 0.3s ease;
        }}

        .panel:hover {{
            border-color: rgba(255, 255, 255, 0.12);
        }}

        .panel-title {{
            font-size: 1.25rem;
            font-weight: 700;
            margin-bottom: 25px;
            display: flex;
            align-items: center;
            gap: 10px;
            border-bottom: 1px solid var(--border-color);
            padding-bottom: 12px;
            color: var(--text-primary);
        }}

        .panel-title svg {{
            width: 20px;
            height: 20px;
            fill: var(--primary-accent);
        }}

        /* Configuration Panel */
        .form-group {{
            margin-bottom: 20px;
        }}

        .form-group label {{
            display: block;
            margin-bottom: 8px;
            font-size: 0.85rem;
            color: var(--text-secondary);
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}

        .form-group input[type="text"],
        .form-group input[type="password"],
        .form-group input[type="number"] {{
            width: 100%;
            padding: 12px 16px;
            border-radius: 12px;
            background: rgba(255, 255, 255, 0.03);
            border: 1px solid var(--border-color);
            color: var(--text-primary);
            font-family: inherit;
            font-size: 0.95rem;
            transition: all 0.3s ease;
        }}

        .form-group input:focus {{
            outline: none;
            border-color: var(--secondary-accent);
            box-shadow: 0 0 15px rgba(79, 172, 254, 0.2);
            background: rgba(255, 255, 255, 0.06);
        }}

        .slider-container {{
            display: flex;
            align-items: center;
            gap: 15px;
        }}

        .slider-container input[type="range"] {{
            flex: 1;
            accent-color: var(--primary-accent);
            cursor: pointer;
        }}

        .slider-value {{
            font-family: 'JetBrains Mono', monospace;
            font-weight: bold;
            font-size: 1.1rem;
            width: 55px;
            text-align: right;
            color: var(--primary-accent);
        }}

        .checkbox-container {{
            display: flex;
            align-items: center;
            gap: 10px;
            cursor: pointer;
            font-weight: 600;
            font-size: 0.9rem;
            user-select: none;
            color: var(--text-secondary);
            transition: color 0.2s ease;
        }}

        .checkbox-container:hover {{
            color: var(--text-primary);
        }}

        .checkbox-container input {{
            width: 18px;
            height: 18px;
            accent-color: var(--primary-accent);
            cursor: pointer;
        }}

        .btn-btn {{
            width: 100%;
            padding: 15px;
            border-radius: 14px;
            font-family: inherit;
            font-size: 1.1rem;
            font-weight: 700;
            border: none;
            cursor: pointer;
            transition: all 0.3s cubic-bezier(0.175, 0.885, 0.32, 1.275);
            display: flex;
            justify-content: center;
            align-items: center;
            gap: 8px;
        }}

        .btn-start {{
            background: linear-gradient(135deg, var(--primary-accent), var(--secondary-accent));
            color: #05060b;
            box-shadow: 0 5px 20px rgba(0, 242, 254, 0.2);
        }}

        .btn-start:hover {{
            transform: translateY(-2px);
            box-shadow: 0 8px 25px rgba(0, 242, 254, 0.4);
        }}

        .btn-stop {{
            background: rgba(255, 77, 110, 0.12);
            color: var(--danger-color);
            border: 1px solid rgba(255, 77, 110, 0.25);
            margin-top: 15px;
            box-shadow: 0 5px 15px rgba(255, 77, 110, 0.05);
        }}

        .btn-stop:hover {{
            background: var(--danger-color);
            color: #ffffff;
            box-shadow: 0 8px 25px rgba(255, 77, 110, 0.35);
            transform: translateY(-2px);
            border-color: transparent;
        }}

        .btn-btn:active {{
            transform: translateY(1px);
        }}

        /* Dashboard Details Panel */
        .progress-section {{
            margin-bottom: 30px;
        }}

        .current-step-label {{
            font-size: 0.85rem;
            color: var(--text-secondary);
            margin-bottom: 6px;
            font-weight: 600;
            text-transform: uppercase;
        }}

        .current-step-value {{
            font-size: 1.25rem;
            font-weight: 700;
            margin-bottom: 20px;
            color: var(--primary-accent);
            display: flex;
            align-items: center;
            gap: 8px;
        }}

        .progress-bar-container {{
            background: rgba(255, 255, 255, 0.02);
            border: 1px solid var(--border-color);
            border-radius: 30px;
            height: 28px;
            width: 100%;
            overflow: hidden;
            position: relative;
            box-shadow: inset 0 2px 4px rgba(0,0,0,0.5);
        }}

        .progress-bar-fill {{
            height: 100%;
            width: 0%;
            background: linear-gradient(90deg, var(--secondary-accent), var(--primary-accent));
            transition: width 0.5s cubic-bezier(0.4, 0, 0.2, 1);
            box-shadow: 0 0 15px rgba(0, 242, 254, 0.6);
            border-radius: 30px;
        }}

        .progress-bar-text {{
            position: absolute;
            top: 50%;
            left: 50%;
            transform: translate(-50%, -50%);
            font-size: 0.9rem;
            font-weight: 800;
            color: #ffffff;
            text-shadow: 0 1px 3px rgba(0,0,0,0.8);
            font-family: 'JetBrains Mono', monospace;
        }}

        .stats-grid {{
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 20px;
            margin-bottom: 30px;
        }}

        .stat-card {{
            background: rgba(255, 255, 255, 0.02);
            border: 1px solid var(--border-color);
            border-radius: 16px;
            padding: 22px 15px;
            text-align: center;
            position: relative;
            overflow: hidden;
            transition: transform 0.3s ease, border-color 0.3s ease;
        }}

        .stat-card:hover {{
            transform: translateY(-3px);
            border-color: rgba(255,255,255,0.15);
        }}

        .stat-card-label {{
            font-size: 0.8rem;
            color: var(--text-secondary);
            margin-bottom: 10px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}

        .stat-card-value {{
            font-size: 1.6rem;
            font-weight: 800;
            color: var(--text-primary);
        }}

        .stat-card-sub {{
            font-size: 0.75rem;
            color: var(--text-secondary);
            margin-top: 4px;
        }}

        .stat-accent-swipe {{ color: var(--primary-accent); text-shadow: 0 0 10px rgba(0, 242, 254, 0.2); }}
        .stat-accent-carrot {{ color: var(--success-color); text-shadow: 0 0 10px rgba(0, 245, 160, 0.2); }}
        .stat-accent-loop {{ color: var(--secondary-accent); text-shadow: 0 0 10px rgba(79, 172, 254, 0.2); }}

        /* Live Terminal Logs */
        .terminal-panel {{
            grid-column: span 2;
        }}

        .terminal-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 15px;
        }}

        .terminal-actions {{
            display: flex;
            gap: 10px;
        }}

        .terminal-btn {{
            background: rgba(255,255,255,0.05);
            border: 1px solid var(--border-color);
            color: var(--text-secondary);
            padding: 5px 12px;
            border-radius: 8px;
            font-size: 0.75rem;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s;
        }}

        .terminal-btn:hover {{
            background: rgba(255,255,255,0.1);
            color: var(--text-primary);
        }}

        .terminal {{
            background: #04050a;
            border: 1px solid var(--border-color);
            border-radius: 14px;
            height: 280px;
            overflow-y: auto;
            padding: 20px;
            font-family: 'JetBrains Mono', Consolas, monospace;
            font-size: 0.85rem;
            line-height: 1.6;
            color: #cbd5e1;
            box-shadow: inset 0 4px 20px rgba(0,0,0,0.8);
            border-top: 4px solid #1a1c2e;
        }}

        .log-row {{
            margin-bottom: 6px;
            word-break: break-all;
            display: flex;
            gap: 12px;
            align-items: flex-start;
            border-bottom: 1px solid rgba(255,255,255,0.01);
            padding-bottom: 4px;
        }}

        .log-time {{
            color: #475569;
            flex-shrink: 0;
            user-select: none;
            font-weight: 600;
        }}

        .log-body {{
            flex-grow: 1;
        }}

        .log-info {{ color: #94a3b8; }}
        .log-success {{ color: var(--success-color); font-weight: 700; }}
        .log-warning {{ color: var(--warning-color); }}
        .log-error {{ color: var(--danger-color); font-weight: 700; }}

        @media (max-width: 900px) {{
            .grid-container {{
                grid-template-columns: 1fr;
            }}
            .terminal-panel {{
                grid-column: span 1;
            }}
        }}
    </style>
</head>
<body>
    <div class="wrapper">
        <header>
            <div class="logo-area">
                <h1>GAG2 Auto-Farming Center</h1>
                <p>자동 파밍 매니저 대시보드</p>
            </div>
            <div id="statusBadge" class="status-badge status-idle">대기 중 (Idle)</div>
        </header>

        <div class="grid-container">
            <!-- Left: Settings Panel -->
            <div class="panel">
                <h2 class="panel-title">
                    <svg viewBox="0 0 24 24"><path d="M19.14,12.94c0.04-0.3,0.06-0.61,0.06-0.94c0-0.32-0.02-0.64-0.07-0.94l2.03-1.58c0.18-0.14,0.23-0.41,0.12-0.61 l-1.92-3.32c-0.12-0.22-0.37-0.29-0.59-0.22l-2.39,0.96c-0.5-0.38-1.03-0.7-1.62-0.94L14.4,2.81c-0.04-0.24-0.24-0.41-0.48-0.41 h-3.84c-0.24,0-0.43,0.17-0.47,0.41L9.25,5.35C8.66,5.59,8.12,5.92,7.63,6.29L5.24,5.33c-0.22-0.08-0.47,0-0.59,0.22L2.74,8.87 C2.62,9.08,2.66,9.34,2.86,9.48l2.03,1.58C4.84,11.36,4.8,11.69,4.8,12s0.04,0.64,0.07,0.94l-2.03,1.58 c-0.18,0.14-0.23,0.41-0.12,0.61l1.92,3.32c0.12,0.22,0.37,0.29,0.59,0.22l2.39-0.96c0.5,0.38,1.03,0.7,1.62,0.94l0.36,2.54 c0.05,0.24,0.24,0.41,0.48,0.41h3.84c0.24,0,0.44-0.17,0.47-0.41l0.36-2.54c0.59-0.24,1.13-0.56,1.62-0.94l2.39,0.96 c0.22,0.08,0.47,0,0.59-0.22l1.92-3.32c0.12-0.22,0.07-0.47-0.12-0.61L19.14,12.94z M12,15.6c-1.98,0-3.6-1.62-3.6-3.6 s1.62-3.6,3.6-3.6s3.6,1.62,3.6,3.6S13.98,15.6,12,15.6z"/></svg>
                    파밍 구성 설정
                </h2>
                
                <div class="form-group">
                    <label for="robloxUser">Roblox 사용자 ID</label>
                    <input type="text" id="robloxUser" placeholder="Roblox ID 또는 이메일" value="{initial_user}">
                </div>

                <div class="form-group">
                    <label for="robloxPass">Roblox 비밀번호</label>
                    <input type="password" id="robloxPass" placeholder="Roblox 비밀번호" value="{initial_pass}">
                </div>

                <div class="form-group">
                    <label for="loops">반복할 총 루프 횟수</label>
                    <div style="display:flex; gap:10px; align-items:center;">
                        <input type="number" id="loops" min="1" max="500" value="5" style="flex:1;">
                        <label class="checkbox-container" style="margin:0; white-space:nowrap;">
                            <input type="checkbox" id="infiniteLoops" onchange="toggleInfinite(this.checked)">
                            <span>무제한</span>
                        </label>
                    </div>
                </div>

                <div class="form-group">
                    <label for="delay">스와이프 지연 속도</label>
                    <div class="slider-container">
                        <input type="range" id="delay" min="0.1" max="3.0" step="0.1" value="0.3" oninput="updateSliderVal(this.value)">
                        <span id="delayVal" class="slider-value">0.3s</span>
                    </div>
                </div>

                <div class="form-group" style="margin-top: 25px; margin-bottom: 25px;">
                    <label class="checkbox-container">
                        <input type="checkbox" id="showBrowser">
                        <span>브라우저 창 화면 켜기 (no-headless)</span>
                    </label>
                </div>

                <button id="btnStart" class="btn-btn btn-start" onclick="startFarming()">
                    <svg style="width:20px;height:20px;fill:currentColor" viewBox="0 0 24 24"><path d="M8,5.14V19.14L19,12.14L8,5.14Z"/></svg>
                    자동 파밍 시작
                </button>
                <button id="btnStop" class="btn-btn btn-stop" onclick="stopFarming()" style="display:none;">
                    <svg style="width:20px;height:20px;fill:currentColor" viewBox="0 0 24 24"><path d="M18,18H6V6H18V18Z"/></svg>
                    자동 파밍 중단
                </button>
            </div>

            <!-- Right: Progress Dashboard -->
            <div class="panel">
                <h2 class="panel-title">
                    <svg viewBox="0 0 24 24"><path d="M19,3H5C3.9,3,3,3.9,3,5V19C3,20.1,3.9,21,5,21H19C20.1,21,21,20.1,21,19V5C21,3.9,20.1,3,19,3M9,17H7V10H9V17M13,17H11V7H13V17M17,17H15V12H17V17Z"/></svg>
                    진행 상황 모니터
                </h2>

                <div class="progress-section">
                    <div class="current-step-label">현재 진행 단계</div>
                    <div id="currentStep" class="current-step-value">대기 중...</div>

                    <div class="progress-bar-container">
                        <div id="progressBarFill" class="progress-bar-fill"></div>
                        <span id="progressBarText" class="progress-bar-text">0.0%</span>
                    </div>
                </div>

                <div class="stats-grid">
                    <div class="stat-card">
                        <div class="stat-card-label">루프 진행 상태</div>
                        <div id="statLoop" class="stat-card-value stat-accent-loop">0 / 5</div>
                        <div class="stat-card-sub">반복 횟수</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-card-label">완료된 스와이프</div>
                        <div id="statSwipe" class="stat-card-value stat-accent-swipe">0</div>
                        <div id="statSwipeCurrent" class="stat-card-sub">현재 루프: 0 / 20</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-card-label">소진된 당근 수</div>
                        <div id="statCarrots" class="stat-card-value stat-accent-carrot">0</div>
                        <div class="stat-card-sub">누적 제출량</div>
                    </div>
                </div>
            </div>

            <!-- Bottom: Live Logs -->
            <div class="panel terminal-panel">
                <div class="terminal-header">
                    <h2 class="panel-title" style="margin-bottom:0; border-bottom:0; padding-bottom:0">
                        <svg viewBox="0 0 24 24"><path d="M20,19V7H4V19H20M20,3A2,2 0 0,1 22,5V19A2,2 0 0,1 20,21H4A2,2 0 0,1 2,19V5A2,2 0 0,1 4,3H20M5.41,8.17L6.83,9.58L4.25,12.17L6.83,14.75L5.41,16.17L1.41,12.17L5.41,8.17M12,17H6V15H12V17Z"/></svg>
                        실시간 터미널 출력
                    </h2>
                    <div class="terminal-actions">
                        <button class="terminal-btn" onclick="clearLogs()">로그 비우기</button>
                    </div>
                </div>
                <div id="terminal" class="terminal">
                    <div class="log-row">
                        <span class="log-time">[System]</span>
                        <span class="log-body" style="color: #64748b;">자동 파밍 제어 센터 대시보드가 초기화되었습니다. 설정을 입력한 후 시작을 누르세요.</span>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <script>
        let pollTimer = null;

        function updateSliderVal(val) {{
            document.getElementById('delayVal').innerText = val + 's';
        }}

        function updateStatusBadge(status) {{
            const badge = document.getElementById('statusBadge');
            
            let statusText = '대기 중 (Idle)';
            if (status === 'running') statusText = '파밍 진행 중 (Running)';
            else if (status === 'completed') statusText = '완료됨 (Completed)';
            else if (status === 'failed') statusText = '에러 발생 (Failed)';
            else if (status === 'stopped') statusText = '중단됨 (Stopped)';
            
            badge.className = 'status-badge status-' + status;
            badge.innerText = statusText;
            
            const btnStart = document.getElementById('btnStart');
            const btnStop = document.getElementById('btnStop');
            
            if (status === 'running') {{
                btnStart.disabled = true;
                btnStart.style.opacity = '0.5';
                btnStart.style.pointerEvents = 'none';
                btnStop.style.display = 'flex';
            }} else {{
                btnStart.disabled = false;
                btnStart.style.opacity = '1';
                btnStart.style.pointerEvents = 'auto';
                btnStop.style.display = 'none';
            }}
        }}

        function appendLog(time, level, msg) {{
            const term = document.getElementById('terminal');
            const row = document.createElement('div');
            row.className = 'log-row';
            
            const timeSpan = document.createElement('span');
            timeSpan.className = 'log-time';
            timeSpan.innerText = '[' + time + ']';
            
            const bodySpan = document.createElement('span');
            bodySpan.className = 'log-body log-' + level;
            bodySpan.innerText = msg;
            
            row.appendChild(timeSpan);
            row.appendChild(bodySpan);
            term.appendChild(row);
            
            // Auto scroll
            term.scrollTop = term.scrollHeight;
        }}

        function clearLogs() {{
            document.getElementById('terminal').innerHTML = `
                <div class="log-row">
                    <span class="log-time">[System]</span>
                    <span class="log-body" style="color: #64748b;">로그가 초기화되었습니다.</span>
                </div>
            `;
        }}

        function pollStatus() {{
            fetch('/api/status')
                .then(res => res.json())
                .then(data => {{
                    updateStatusBadge(data.status);
                    
                    document.getElementById('currentStep').innerText = data.current_step;
                    if (data.total_loops === 0) {{
                        document.getElementById('progressBarFill').style.width = '100%';
                        document.getElementById('progressBarText').innerText = '무제한 파밍 중 (루프 ' + data.current_loop + '회 진행 중)';
                    }} else {{
                        document.getElementById('progressBarFill').style.width = data.progress_percent + '%';
                        document.getElementById('progressBarText').innerText = data.progress_percent + '%';
                    }}
                    
                    document.getElementById('statLoop').innerText = data.current_loop + ' / ' + (data.total_loops === 0 ? '무제한' : data.total_loops);
                    document.getElementById('statSwipe').innerText = data.total_swipes;
                    document.getElementById('statSwipeCurrent').innerText = '현재 루프: ' + data.swipe_count + ' / 20';
                    document.getElementById('statCarrots').innerText = data.carrots_submitted;
                    
                    // Reprint logs to sync
                    const term = document.getElementById('terminal');
                    term.innerHTML = '';
                    if (data.logs.length === 0) {{
                        term.innerHTML = `
                            <div class="log-row">
                                <span class="log-time">[System]</span>
                                <span class="log-body" style="color: #64748b;">로그가 비어 있습니다.</span>
                            </div>
                        `;
                    }} else {{
                        data.logs.forEach(log => {{
                            appendLog(log.time, log.level, log.msg);
                        }});
                    }}
                    
                    if (data.status !== 'running') {{
                        clearInterval(pollTimer);
                        pollTimer = null;
                    }}
                }})
                .catch(err => console.error('Status polling error:', err));
        }}

        function toggleInfinite(checked) {{
            const loopsInput = document.getElementById('loops');
            if (checked) {{
                loopsInput.disabled = true;
                loopsInput.style.opacity = '0.5';
            }} else {{
                loopsInput.disabled = false;
                loopsInput.style.opacity = '1';
            }}
        }}

        function startFarming() {{
            const roblox_user = document.getElementById('robloxUser').value;
            const roblox_pass = document.getElementById('robloxPass').value;
            const isInfinite = document.getElementById('infiniteLoops').checked;
            const loops = isInfinite ? 0 : parseInt(document.getElementById('loops').value);
            const delay = parseFloat(document.getElementById('delay').value);
            const headless = !document.getElementById('showBrowser').checked;
            
            if (!isInfinite && (isNaN(loops) || loops < 1)) {{
                alert('반복 루프 횟수를 올바르게 입력해주세요.');
                return;
            }}
            
            const payload = {{ roblox_user, roblox_pass, loops, delay, headless }};
            
            fetch('/api/start', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify(payload)
            }})
            .then(res => res.json())
            .then(data => {{
                if (data.success) {{
                    updateStatusBadge('running');
                    if (!pollTimer) {{
                        pollTimer = setInterval(pollStatus, 800);
                    }}
                }} else {{
                    alert('파밍 시작 실패: ' + data.message);
                }}
            }})
            .catch(err => alert('서버 연결 실패: ' + err));
        }}

        function stopFarming() {{
            fetch('/api/stop', {{ method: 'POST' }})
                .then(res => res.json())
                .then(data => {{
                    if (data.success) {{
                        updateStatusBadge('stopped');
                    }}
                }});
        }}
        
        // Initial state sync
        pollStatus();
        // If it was already running in the background, keep polling
        pollTimer = setInterval(pollStatus, 1000);
    </script>
</body>
</html>
"""

class GAGDashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Silence default terminal logs of HTTP requests
        return

    def do_GET(self):
        global farming_state
        parsed_url = urlparse(self.path)
        
        if parsed_url.path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_DASHBOARD.encode("utf-8"))
        elif parsed_url.path == "/api/status":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(farming_state).encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        global farming_state, active_thread
        parsed_url = urlparse(self.path)
        
        if parsed_url.path == "/api/start":
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length).decode('utf-8')
            config = json.loads(post_data)
            
            if farming_state["status"] == "running":
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "message": "이미 실행 중입니다."}).encode("utf-8"))
                return
                
            # Save credentials to .env for persistence
            try:
                env_path = os.path.join(base_dir, ".env")
                with open(env_path, "w", encoding="utf-8") as f:
                    f.write(f"ROBLOX_USER={config['roblox_user']}\nROBLOX_PASS={config['roblox_pass']}\n")
            except Exception:
                pass
                
            # Launch automation thread
            active_thread = threading.Thread(target=run_automation_thread, args=(config,), daemon=True)
            active_thread.start()
            
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"success": True}).encode("utf-8"))
            
        elif parsed_url.path == "/api/stop":
            stop_farming()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"success": True}).encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

def main():
    port = 5000
    httpd = None
    
    # Dynamically find an available port
    while port < 5100:
        try:
            server_address = ('', port)
            httpd = HTTPServer(server_address, GAGDashboardHandler)
            break
        except OSError:
            port += 1
            
    if not httpd:
        print("❌ 사용 가능한 포트를 찾을 수 없습니다. (5000-5100)")
        sys.exit(1)
        
    print(f"\n=======================================================")
    print(f"🚀 GAG2 자동화 제어 센터 웹 서버를 구동합니다.")
    print(f"🔗 웹 브라우저를 열고 다음 주소에 접속하세요:")
    print(f"   👉  http://localhost:{port}/")
    print(f"=======================================================\n")
    
    # Auto open browser
    try:
        webbrowser.open(f"http://localhost:{port}/")
    except Exception:
        pass
        
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n서버를 종료합니다.")
        stop_farming()
        httpd.server_close()

if __name__ == "__main__":
    main()

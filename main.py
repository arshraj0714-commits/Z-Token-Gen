# =============================================================================
# Standard Library    | IMPORTS 
# =============================================================================
import asyncio
import concurrent.futures
import io
import json
import os
import random
import re
import shutil
import stat
import string
import subprocess
import sys
import tempfile
import time
import traceback
import warnings
import zipfile
import termios
import tty
from datetime import datetime

# =============================================================================
# Third-Party Libraries 
# =============================================================================
import requests
from requests.adapters import HTTPAdapter, Retry

# =============================================================================
# Typing & Data Structures
# =============================================================================
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import truedriver as uc
from truedriver import Tab, Element
import tls_client

warnings.filterwarnings("ignore", category=DeprecationWarning, module="truedriver")

class Theme:
    PURPLE = "\033[35m"
    GREY = "\033[90m"
    LIGHT_GREY = "\033[37m"
    END = "\033[0m"
    
    GRADIENT_PURPLE = [
        "\033[38;2;128;0;128m", "\033[38;2;147;112;219m", "\033[38;2;138;43;226m",
        "\033[38;2;148;0;211m", "\033[38;2;153;50;204m",
    ]
    GRADIENT_DARK = [
        "\033[38;2;64;64;64m", "\033[38;2;48;48;48m", "\033[38;2;32;32;32m",
    ]

# Global Stats Trackers
class Stats:
    generated = 0
    attempts = 0
    captchas_solved = 0

    @classmethod
    def success_rate(cls) -> str:
        if cls.attempts == 0:
            return "0.0%"
        rate = (cls.generated / cls.attempts) * 100
        return f"{rate:.1f}%"

def ts(): return f"[{datetime.now().strftime('%H:%M:%S')}]"

def log(msg, tag="INFO"):
    padded_tag = f"{tag:<20}"
    print(f"{Theme.PURPLE}{padded_tag}{Theme.GREY} | {Theme.LIGHT_GREY}{msg}{Theme.END}")

def log_exception(e: Exception, context: str = ""):
    log(f"{context}: {e}", "ERROR")
    traceback.print_exc()

def rl_log(remaining):
    mins, secs = int(remaining // 60), int(remaining % 60)
    padded_tag = f"{'RATELIMIT':<20}"
    print(f"{Theme.PURPLE}{padded_tag}{Theme.GREY} | {Theme.LIGHT_GREY}Waiting {mins}m{secs}s remaining...{Theme.END}", end="\r")

# -------------------------------------------------------------------
#  Proxy manager
# -------------------------------------------------------------------
class ProxyManager:
    PROXY_FILE = Path(__file__).parent / "proxies.txt"
    _proxies = []
    _loaded = False

    @classmethod
    def load(cls):
        if cls._loaded:
            return
        cls._loaded = True
        if not cls.PROXY_FILE.exists():
            return
        try:
            with open(cls.PROXY_FILE, "r") as f:
                cls._proxies = [line.strip() for line in f if line.strip()]
            if cls._proxies:
                log(f"Loaded {len(cls._proxies)} proxies from proxies.txt", "PROXY")
        except Exception as e:
            log_exception(e, "Error reading proxies.txt")

    @classmethod
    def get_random_proxy(cls) -> Optional[str]:
        cls.load()
        if not cls._proxies:
            return None
        return random.choice(cls._proxies)

    @classmethod
    def has_proxies(cls) -> bool:
        cls.load()
        return len(cls._proxies) > 0

# -------------------------------------------------------------------
#  NopeCha Manager (uses gen.py's exact local folder + zip download method)
# -------------------------------------------------------------------
class NopechaManager:
    ZIP_URL = "https://github.com/NopeCHALLC/nopecha-extension/releases/latest/download/chromium_automation.zip"
    EXTENSION_DIR = Path(__file__).parent / "nopecha_ext"

    @classmethod
    def ensure_extension_ready(cls) -> bool:
        # If already extracted locally, just inject the key
        if cls.EXTENSION_DIR.exists() and (cls.EXTENSION_DIR / "manifest.json").exists():
            cls.inject_nopecha_key(Config.NOPECHA_API_KEY)
            return True
            
        cls.EXTENSION_DIR.mkdir(exist_ok=True)
        try:
            log("Downloading NopeCHA extension...", "NOPECHA")
            resp = requests.get(cls.ZIP_URL, timeout=60, headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code != 200:
                log(f"NopeCHA download failed: HTTP {resp.status_code}", "ERROR")
                return False
            with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
                z.extractall(cls.EXTENSION_DIR)
            if not (cls.EXTENSION_DIR / "manifest.json").exists():
                log("Extraction failed: manifest.json not found.", "ERROR")
                return False
                
            try:
                subprocess.run(['xattr', '-cr', str(cls.EXTENSION_DIR)], capture_output=True)
                for root, dirs, files in os.walk(cls.EXTENSION_DIR):
                    for d in dirs: os.chmod(os.path.join(root, d), stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)
                    for f in files: os.chmod(os.path.join(root, f), stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)
            except Exception:
                pass

            log("NopeCHA extension downloaded successfully.", "NOPECHA")
            cls.inject_nopecha_key(Config.NOPECHA_API_KEY)
            return True
        except Exception as e:
            log_exception(e, "NopeCHA setup failed")
            return False

    @classmethod
    def inject_nopecha_key(cls, apikey: str) -> bool:
        """gen.py's method: push NopeCHA API key to extension via manifest/storage_init/config"""
        if not apikey or not cls.EXTENSION_DIR.exists():
            return False
        try:
            # 1) Inject key into manifest.json
            manifest_path = cls.EXTENSION_DIR / "manifest.json"
            if manifest_path.exists():
                with open(manifest_path, 'r') as f:
                    manifest = json.load(f)
                if 'nopecha' not in manifest:
                    manifest['nopecha'] = {}
                manifest['nopecha']['key'] = apikey
                with open(manifest_path, 'w') as f:
                    json.dump(manifest, f, indent=2)
            
            # 2) Create storage_init.js
            storage_init_path = cls.EXTENSION_DIR / "storage_init.js"
            storage_init_code = f"""
// Auto-generated storage initialization for NopeCHA extension
(function() {{
  const nopecha_api_key = '{apikey}';
  chrome.storage.local.set({{'nopecha_key': nopecha_api_key}}, function() {{
    console.log('[NopeCHA Storage] API Key initialized');
  }});
}})();
"""
            with open(storage_init_path, 'w') as f:
                f.write(storage_init_code)
            
            # 3) Create nopecha_config.json
            config_path = cls.EXTENSION_DIR / "nopecha_config.json"
            config_data = {
                'api_key': apikey,
                'enabled': True,
                'timestamp': datetime.now().isoformat()
            }
            with open(config_path, 'w') as f:
                json.dump(config_data, f, indent=2)
                
            log("NopeCHA API key injected into extension files.", "NOPECHA")
            return True
        except Exception as e:
            log(f"Inject failed: {e}", "WARN")
            return False

    @classmethod
    def get_load_arg(cls) -> Optional[str]:
        if cls.EXTENSION_DIR.exists() and (cls.EXTENSION_DIR / "manifest.json").exists():
            return f"--load-extension={os.path.abspath(cls.EXTENSION_DIR)}"
        return None

# -------------------------------------------------------------------
#  WARP VPN manager (Optional - only used if Config.USE_VPN is True)
# -------------------------------------------------------------------
class WarpManager:
    WARP_CLI = shutil.which("warp-cli") or "/Applications/Cloudflare WARP.app/Contents/Resources/warp-cli"

    @classmethod
    def _cli(cls, *args, timeout=15):
        return subprocess.run([cls.WARP_CLI, *args], capture_output=True, text=True, timeout=timeout)

    @classmethod
    def _wait_for_status(cls, desired="Connected", timeout=10):
        for _ in range(timeout):
            try:
                if desired.lower() in cls._cli("status", timeout=5).stdout.lower():
                    return True
            except: pass
            time.sleep(1)
        return False

    @classmethod
    def connect(cls):
        if not os.path.exists(cls.WARP_CLI):
            log("Cloudflare WARP not found. Install from https://1.1.1.1/", "ERROR")
            return False
        try: cls._cli("disconnect"); time.sleep(1)
        except: pass
        try: cls._cli("delete"); time.sleep(1)
        except: pass
        try: cls._cli("registration", "new")
        except: pass
        try: cls._cli("mode", "warp")
        except: pass
        try:
            r = cls._cli("connect", timeout=20)
            if r.returncode != 0:
                log(f"WARP connect error: {r.stderr.strip()}", "ERROR")
                return False
        except Exception as e:
            log_exception(e, "WARP connect failed")
            return False
        if not cls._wait_for_status("Connected", 10):
            log("WARP did not reach Connected state – check privileges", "ERROR")
            return False
        for _ in range(5):
            try:
                ip = requests.get("https://api.ipify.org", timeout=5).text.strip()
                if ip:
                    log(f"Connected! IP: {ip}", "VPN")
                    return True
            except: pass
            time.sleep(2)
        log("WARP connected but IP check timed out – continuing anyway", "WARN")
        return True

    @classmethod
    def disconnect(cls):
        try: cls._cli("disconnect", timeout=10)
        except: pass

# -------------------------------------------------------------------
#  Banner, helpers, Config
# -------------------------------------------------------------------
def gradient_text(text, colors=None):
    if colors is None: colors = Theme.GRADIENT_PURPLE
    return "".join(colors[i % len(colors)] + c for i, c in enumerate(text)) + Theme.END

def show_banner():
    os.system("clear")
    banner = r"""
███████╗    ████████╗ ██████╗  ██████╗ ██╗     ███████╗
╚══███╔╝    ╚══██╔══╝██╔═══██╗██╔═══██╗██║     ██╔════╝
  ███╔╝        ██║   ██║   ██║██║   ██║██║     ███████╗
 ███╔╝         ██║   ██║   ██║██║   ██║██║     ╚════██║
███████╗       ██║   ╚██████╔╝╚██████╔╝███████╗███████║
╚══════╝       ╚═╝    ╚═════╝  ╚═════╝ ╚══════╝╚══════╝
"""
    print("\n".join(gradient_text(line) for line in banner.split("\n")))
    
    stats_border = gradient_text("=" * 79, Theme.GRADIENT_DARK)
    print(stats_border)
    stats_line = (
        f"{Theme.PURPLE}| {Theme.LIGHT_GREY}Accounts Generated : {Theme.PURPLE}{Stats.generated:<5}{Theme.END}"
        f"{Theme.PURPLE} | {Theme.LIGHT_GREY}Success Rate : {Theme.PURPLE}{Stats.success_rate():<7}{Theme.END}"
        f"{Theme.PURPLE} | {Theme.LIGHT_GREY}Captchas Solved : {Theme.PURPLE}{Stats.captchas_solved:<5}{Theme.END}"
        f"{Theme.PURPLE} |{Theme.END}"
    )
    print(stats_line)
    print(stats_border)
    print()

def random_string(length=12):
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))

class Config:
    SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
    OUTPUT_DIR = os.path.join(SCRIPT_DIR, "output")
    TOKENS_FILE = os.path.join(OUTPUT_DIR, "tokens.txt")
    EVS_FILE = os.path.join(OUTPUT_DIR, "evs.txt")
    CHECKER_OUTPUT = os.path.join(OUTPUT_DIR, "checked_tokens.json")
    PROXY_FILE = os.path.join(SCRIPT_DIR, "proxies.txt")
    
    DUCKMAIL_BASE_URL = "https://api.duckmail.sbs"
    VENUMZ_BASE_URL = "https://api.venumzmail.xyz"
    
    # SET VENUMZ API KEY (Auto-switches provider to VenumzMail)
    VENUMZ_API_KEY = "VENUMZ_API_KEY"
    
    FIXED_PASSWORD = ""
    TOKEN_FETCH_DELAY = 1
    RATE_LIMIT_RETRY_DELAY = 120
    HEADLESS = False
    USE_VPN = False
    USE_AUTO_SOLVER = False
    
    # CORRECT NOPECHA API KEY
    NOPECHA_API_KEY = "NOPECHA_API_KEY" 
    
    BRAVE_PATH = "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"
    DISPLAY_NAMES = []

    @classmethod
    def load_names(cls):
        path = os.path.join(cls.SCRIPT_DIR, "names.txt")
        try:
            with open(path, "r", encoding="utf-8") as f:
                names = [line.strip() for line in f if line.strip()]
                if names: cls.DISPLAY_NAMES = names
        except FileNotFoundError: pass
        except Exception as e: log_exception(e, "Error reading names.txt")

    @classmethod
    def get_display_name(cls):
        return random.choice(cls.DISPLAY_NAMES) if cls.DISPLAY_NAMES else random_string(12)

    @classmethod
    def setup(cls):
        os.makedirs(cls.OUTPUT_DIR, exist_ok=True)
        cls.load_names()

Config.setup()

@dataclass
class AccountData:
    email: str; username: str; password: str
    token: Optional[str] = None; verified: bool = False; disabled: bool = False

@dataclass
class TokenInfo:
    token: str; valid: bool = False; username: Optional[str] = None
    email: Optional[str] = None; phone: Optional[str] = None
    verified: bool = False; nitro: bool = False; flags: int = 0
    locale: str = ""; mfa_enabled: bool = False

DESKTOP_UAS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
]
SCREEN_RES = [(1920, 1080), (1366, 768), (1536, 864)]
TIMEZONES = ["America/New_York", "America/Chicago", "America/Los_Angeles", "Europe/London", "Australia/Sydney"]
LANGUAGES = ["en-US,en;q=0.9", "en-GB,en;q=0.8", "en-CA,en;q=0.7"]

def random_fp(): return {"screen": random.choice(SCREEN_RES), "user_agent": random.choice(DESKTOP_UAS)}

def headers():
    return {
        "Accept":"application/json, text/plain, */*", "Accept-Language":random.choice(LANGUAGES),
        "Content-Type":"application/json", "Origin":"https://discord.com",
        "Referer":"https://discord.com/register",
        "Sec-Ch-Ua":'"Chromium";v="146", "Not-A.Brand";v="24", "Google Chrome";v="146"',
        "Sec-Ch-Ua-Mobile":"?0", "Sec-Ch-Ua-Platform":'"macOS"',
        "Sec-Fetch-Dest":"empty", "Sec-Fetch-Mode":"cors", "Sec-Fetch-Site":"same-origin",
        "User-Agent":random_fp()["user_agent"], "X-Debug-Options":"bugReporterEnabled",
        "X-Discord-Locale":"en-US", "X-Discord-Timezone":random.choice(TIMEZONES),
    }

async def delay(min_s=0.1, max_s=0.3): await asyncio.sleep(random.uniform(min_s, max_s))

async def find_el(tab: Tab, sel: str, timeout=5) -> Optional[Element]:
    start = time.time()
    while time.time() - start < timeout:
        try:
            el = await tab.find(sel)
            if el: return el
        except: pass
        await asyncio.sleep(0.1)
    return None

async def find_any(tab: Tab, sels: List[str], timeout=6) -> Optional[Element]:
    for sel in sels:
        el = await find_el(tab, sel, timeout=max(1, timeout // len(sels)))
        if el: return el
    return None

async def robust(method, *args, **kwargs):
    res = method(*args, **kwargs)
    return await res if asyncio.iscoroutine(res) else res

async def clear(el: Element):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        await robust(el.clear)
async def click(el: Element): await robust(el.click)
async def send_keys(el: Element, text: str): await robust(el.send_keys, text)
async def scroll_into(el: Element): await robust(el.scroll_into_view)

async def safe_click(tab: Tab, el: Optional[Element]) -> bool:
    if not el: return False
    try:
        await scroll_into(el); await delay(0.05, 0.1); await click(el)
        return True
    except: return False

async def human_type(el: Element, text: str, wpm=320):
    if not el: return
    cps = wpm * 5 / 60
    for ch in text:
        await send_keys(el, ch)
        await asyncio.sleep(1.0 / cps * random.uniform(0.7, 1.0))

async def mouse_moves(tab: Tab, dur=2.0):
    start = time.time()
    while time.time() - start < dur:
        x, y = random.randint(100, 700), random.randint(100, 700)
        await tab.evaluate(f"document.elementFromPoint({x},{y})?.dispatchEvent(new MouseEvent('mousemove',{{clientX:{x},clientY:{y},bubbles:true}}))")
        await asyncio.sleep(random.uniform(0.15, 0.35))

def extract_tokens(text: str) -> List[str]:
    pattern = r'[A-Za-z0-9_-]{24,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{27,}'
    tokens = re.findall(pattern, text)
    if not tokens:
        tokens = re.findall(r'([A-Za-z0-9+/=_-]+\.[A-Za-z0-9+/=_-]+\.[A-Za-z0-9+/=_-]+)', text)
    return [t.strip().strip('"') for t in tokens if t.count('.') == 2 and len(t) > 50]

# -------------------------------------------------------------------
#  Email Provider Manager (gen.py's VenumzMail usage + DuckMail fallback)
# -------------------------------------------------------------------
class EmailProvider:
    # VenumzMail domains supported (gen.py uses pussy.lickingonline)
    VENUMZ_DOMAINS = [
 	 'analgex.com',
 	 'arewecookedd.com',
	  'arewecooked.gg',
	  'bheekarirndi.org',
	  'gareebiontop.life',
	  'goontome.com',
	  'goontome.gg',
 	 'venumzmail.in',
	  'venumzmail.life',
	  'venumzmail.net',
 	 'venumzmail.org'
	]

    def __init__(self):
        self.s = requests.Session()
        self.s.headers.update({"Content-Type": "application/json"})
        retry_strategy = Retry(total=3, backoff_factor=0.5, status_forcelist=[429, 500, 502, 503, 504])
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.s.mount("https://", adapter); self.s.mount("http://", adapter)
        
        self.provider = "venumz" if Config.VENUMZ_API_KEY else "duckmail"
        self.current_email = None

    def _domain(self) -> str:
        try:
            r = self.s.get(f"{Config.DUCKMAIL_BASE_URL}/domains")
            if r.status_code == 200:
                for m in r.json().get("hydra:member", []):
                    if m.get("ownerId") is None: return m["domain"]
        except: pass
        return "duckmail.sbs"

    def create_inbox(self) -> tuple[str, str, str]:
        pw = Config.FIXED_PASSWORD or random_string(12)
        
        if self.provider == "venumz":
            # ---- gen.py's VenumzMail usage ----
            # Generate random username (12-18 chars) like gen.py
            for attempt in range(3):
                try:
                    username = ''.join(random.choices(string.ascii_lowercase + string.digits, k=random.randint(12, 18)))
                    domain = random.choice(self.VENUMZ_DOMAINS)
                    
                    headers = {"x-api-key": Config.VENUMZ_API_KEY, "Content-Type": "application/json"}
                    payload = {
                        "count": 1,
                        "username": username,
                        "domain": domain,
                        "type": "public"
                    }
                    r = self.s.post(f"{Config.VENUMZ_BASE_URL}/create", json=payload, headers=headers, timeout=60)
                    
                    if r.status_code in [200, 201]:
                        data = r.json()
                        inboxes = data.get("inboxes", [])
                        if inboxes:
                            self.current_email = inboxes[0].get("email")
                            log(f"Mail (Venumz): {self.current_email}", "MAIL")
                            return self.current_email, pw, Config.VENUMZ_API_KEY
                        # Fallback: build email manually like gen.py
                        self.current_email = f"{username}@{domain}"
                        log(f"Mail (Venumz): {self.current_email}", "MAIL")
                        return self.current_email, pw, Config.VENUMZ_API_KEY
                    else:
                        log(f"Venumz attempt {attempt + 1} failed: HTTP {r.status_code}", "WARN")
                except Exception as e:
                    log(f"Venumz attempt {attempt + 1} failed: {e}", "WARN")
                    continue
            
            log("VenumzMail all attempts failed, reverting to Duckmail", "WARN")
            self.provider = "duckmail"

        domain = self._domain()
        addr = f"{random_string(10).lower()}@{domain}"
        r = self.s.post(f"{Config.DUCKMAIL_BASE_URL}/accounts", json={"address": addr, "password": pw, "expiresIn": 0})
        if r.status_code != 201: raise Exception("DuckMail creation failed")
        token_r = self.s.post(f"{Config.DUCKMAIL_BASE_URL}/token", json={"address": addr, "password": pw})
        if token_r.status_code != 200: raise Exception("DuckMail token failed")
        bearer = token_r.json().get("token")
        if not bearer: raise Exception("No token")
        log(f"Mail (Duckmail): {addr}", "MAIL")
        self.current_email = addr
        return addr, pw, bearer

    async def get_verify_link(self, token: str, timeout=120) -> Optional[str]:
        if not token: return None
        start = time.time()
        log("Waiting for verification email...", "MAIL")
        
        while time.time() - start < timeout:
            try:
                if self.provider == "venumz":
                    # ---- gen.py's VenumzMail inbox polling ----
                    headers = {"x-api-key": Config.VENUMZ_API_KEY, "Content-Type": "application/json"}
                    r = self.s.get(f"{Config.VENUMZ_BASE_URL}/inbox/{self.current_email}", headers=headers, timeout=60)
                    if r.status_code == 200:
                        data = r.json()
                        messages = data.get("messages", [])
                        for msg in messages:
                            sender = (msg.get("sender", "") or "").lower()
                            subject = (msg.get("subject", "") or "").lower()
                            # gen.py filters Discord emails
                            if "discord" not in sender and "discord" not in subject:
                                continue
                            body = msg.get("body", "") or msg.get("body_html", "")
                            combined = body + " " + msg.get("body_html", "")
                            link = self._extract_discord_link(combined)
                            if link:
                                log("Verification link found!", "MAIL")
                                return link
                else:
                    headers = {"Authorization": f"Bearer {token}"}
                    r = self.s.get(f"{Config.DUCKMAIL_BASE_URL}/messages", headers=headers)
                    if r.status_code != 200:
                        await asyncio.sleep(5); continue
                    for msg in r.json().get("hydra:member", []):
                        subj = (msg.get("subject", "") or "").lower()
                        sender = (msg.get("from", {}) or {}).get("address", "").lower()
                        if "verify" in subj or "discord" in sender or "noreply@discord" in sender:
                            detail = self.s.get(f"{Config.DUCKMAIL_BASE_URL}/messages/{msg['id']}", headers=headers).json()
                            body = detail.get("text", "") or " ".join(detail.get("html", []))
                            link = self._extract_discord_link(body)
                            if link:
                                self.s.delete(f"{Config.DUCKMAIL_BASE_URL}/messages/{msg['id']}", headers=headers)
                                log("Verification link found!", "MAIL")
                                return link
                await asyncio.sleep(5)
            except Exception as e:
                log(f"Email check error: {e}", "WARN")
                await asyncio.sleep(5)
        log("No verification link found", "WARN")
        return None

    def _extract_discord_link(self, body: str) -> Optional[str]:
        for pat in [r'https://discord\.com/verify/[^\s"\'<>]+',
                    r'https://discord\.com/verify\?token=[^\s"\'<>]+',
                    r'https://click\.discord\.com/[^\s"\'<>]+']:
            m = re.search(pat, body, re.IGNORECASE)
            if m:
                return m.group(0).replace("&amp;", "&")
        return None

class DiscordRegistrator:
    def __init__(self):
        self.email = EmailProvider()

    async def _browser(self, tid: int):
        fp = random_fp()
        w, h = fp["screen"]
        
        ext_arg = NopechaManager.get_load_arg()

        args = [
            '--disable-blink-features=AutomationControlled', 
            '--no-sandbox',
            '--disable-dev-shm-usage', 
            '--disable-gpu', 
            '--disable-software-rasterizer',
            '--disable-background-timer-throttling', 
            '--no-default-browser-check',
            '--no-first-run', 
            '--password-store=basic', 
            '--use-mock-keychain',
            f'--user-agent={fp["user_agent"]}', 
            f'--window-size={w},{h}',
            '--disable-web-security', 
            '--disable-features=IsolateOrigins,site-per-process',
        ]

        if ext_arg:
            # Exactly like gen.py: just use --load-extension
            clean_ext = ext_arg.replace('"', '')
            args.append(clean_ext)

        use_proxy = ProxyManager.has_proxies()
        if use_proxy:
            proxy = ProxyManager.get_random_proxy()
            args.append(f'--proxy-server={proxy}')
            log(f"Using proxy: {proxy}", "PROXY")
        else:
            log("Using direct connection (no proxy).", "NETWORK")

        tmp = tempfile.mkdtemp(prefix=f"zd_{tid}_")
        args.append(f'--user-data-dir={tmp}')
        
        browser_executable = Config.BRAVE_PATH if os.path.exists(Config.BRAVE_PATH) else None

        is_headless = False if ext_arg else Config.HEADLESS

        browser = await uc.start(
            headless=is_headless, 
            browser_args=args, 
            browser_executable_path=browser_executable
        )
        browser._tmp = tmp
        
        return browser

    async def _close_browser(self, browser):
        if browser:
            try: await robust(browser.stop)
            except: pass
            if hasattr(browser, '_tmp'): shutil.rmtree(browser._tmp, ignore_errors=True)

    async def _inject_rate_limit_capture(self, tab: Tab):
        await tab.evaluate("""
            window.__rate_limit_retry_after = null;
            window.__rate_limit_captured = false;
            const origFetch = window.fetch;
            window.fetch = async function(...args) {
                const response = await origFetch.apply(this, args);
                const url = args[0]?.url || args[0];
                if (typeof url === 'string' && url.includes('/auth/register')) {
                    try {
                        const clone = response.clone();
                        const data = await clone.json();
                        if (data && data.retry_after !== undefined) {
                            window.__rate_limit_retry_after = data.retry_after;
                            window.__rate_limit_captured = true;
                        }
                    } catch(e) {}
                }
                return response;
            };
        """)

    async def _get_rate_limit_seconds(self, tab: Tab) -> Optional[float]:
        try:
            seconds = await tab.evaluate("window.__rate_limit_retry_after")
            if seconds is not None and float(seconds) > 0: return float(seconds)
        except: pass
        return None

    async def _wait_for_rate_limit_capture(self, tab: Tab, max_wait: float = 3.0) -> bool:
        start = time.time()
        while time.time() - start < max_wait:
            try:
                captured = await tab.evaluate("window.__rate_limit_captured")
                if captured: return True
            except: pass
            await asyncio.sleep(0.3)
        return False

    async def _repeatedly_try_submit(self, tab: Tab, max_attempts=10):
        for _ in range(max_attempts):
            btn = await find_any(tab, ["button[type='submit']"], timeout=2)
            if btn:
                await safe_click(tab, btn)
                return
            try:
                await tab.evaluate("document.querySelector('input[name=\"password\"]')?.focus()")
                await self._press(tab, "Enter")
            except: pass
            await asyncio.sleep(1)

    async def _handle_rate_limit(self, tab: Tab) -> bool:
        wait = await self._get_rate_limit_seconds(tab)
        if wait is None:
            wait = Config.RATE_LIMIT_RETRY_DELAY
            log(f"Could not read exact wait – using default {int(wait)}s", "WARN")
        else:
            log(f"Rate limited – retry after {wait:.1f}s", "RATELIMIT")

        start = time.time()
        while True:
            remaining = wait - (time.time() - start)
            if remaining <= 0: break
            rl_log(remaining)
            await asyncio.sleep(0.5)
        print(" " * 60, end="\r")
        log("Rate limit wait finished – resubmitting...", "RATELIMIT")
        await self._repeatedly_try_submit(tab)
        return True

    async def _find_dob(self, tab: Tab, label: str):
        for sel in [f"//div[text()='{label}']", f"//div[contains(text(),'{label}')]", f"//span[text()='{label}']"]:
            el = await find_el(tab, sel, timeout=2)
            if el: return el
        js = f"(function(){{ const all = document.querySelectorAll('div, span'); for (let el of all) if (el.textContent.trim() === '{label}') return el; return null; }})();"
        res = await tab.evaluate(js)
        if res: return res
        raise Exception(f"DOB trigger not found: {label}")

    async def _press(self, tab: Tab, key: str, count=1):
        await tab.evaluate(f"(function(){{ const active = document.activeElement || document.body; for (let i=0; i<{count}; i++) {{ active.dispatchEvent(new KeyboardEvent('keydown', {{ key: '{key}', bubbles: true }})); active.dispatchEvent(new KeyboardEvent('keyup', {{ key: '{key}', bubbles: true }})); }} }})();")

    async def _type_js(self, tab: Tab, chars: str):
        await tab.evaluate(f"(function(){{ const active = document.activeElement; if (!active) return; const chars = '{chars}'.split(''); for (let c of chars) {{ active.value += c; active.dispatchEvent(new KeyboardEvent('keydown', {{ key: c, bubbles: true }})); active.dispatchEvent(new KeyboardEvent('keypress', {{ key: c, bubbles: true }})); active.dispatchEvent(new Event('input', {{ bubbles: true }})); }} active.dispatchEvent(new Event('change', {{ bubbles: true }})); }})();")

    async def _select_dob(self, tab: Tab, month="January", day="2", year="2000"):
        trigger = await self._find_dob(tab, "Month")
        await safe_click(tab, trigger)
        await asyncio.sleep(0.2)
        js = f"(()=>{{ const opts = document.querySelectorAll('[role=\"listbox\"] div, [role=\"option\"], li, div'); for (let o of opts) if (o.textContent.trim() === '{month}') {{ o.click(); return true; }} return false; }})()"
        if not await tab.evaluate(js): raise Exception("Month selection failed")
        await asyncio.sleep(0.1)
        trigger = await self._find_dob(tab, "Day")
        await safe_click(tab, trigger)
        await asyncio.sleep(0.2)
        await self._press(tab, "ArrowDown"); await asyncio.sleep(0.05)
        await self._press(tab, "Enter"); await asyncio.sleep(0.1)
        trigger = await self._find_dob(tab, "Year")
        await safe_click(tab, trigger)
        await asyncio.sleep(0.1)
        await self._type_js(tab, year); await asyncio.sleep(0.05)
        await self._press(tab, "Enter"); await asyncio.sleep(0.1)

    async def _fill_form(self, tab: Tab, email: str, username: str, password: str):
        await self._inject_rate_limit_capture(tab)
        try: 
            await tab.get("https://discord.com/register", timeout=20)
        except: 
            log("Registration page load timeout (20s) – continuing…", "WARN")

        await asyncio.sleep(2.0)
        await mouse_moves(tab, 0.3)
        display = Config.get_display_name()

        log("Filling form…", "FORM")
        field = await find_any(tab, [
            "input[name='email']", 
            "input[type='email']", 
            "input#email", 
            "input[autocomplete='email']"
        ], timeout=15)
        
        if not field:
            log("Standard email field not found, trying generic input...", "WARN")
            field = await find_any(tab, [
                "input[type='text']",
                "input:not([type='password']):not([type='hidden']):not([type='checkbox'])"
            ], timeout=5)
        
        if not field:
            page_text = await tab.evaluate("document.body.innerText")
            if "Access denied" in page_text or "Enable JavaScript and cookies" in page_text:
                raise Exception("Cloudflare blocked the registration page. Try using a proxy or VPN.")
            raise Exception("Email field not found. Discord layout might have changed or page didn't load.")
            
        await safe_click(tab, field); await clear(field)
        await human_type(field, email, wpm=random.randint(350, 450))
        await asyncio.sleep(random.uniform(0.1, 0.2))

        field = await find_el(tab, "input[name='global_name']", timeout=2)
        if field:
            await safe_click(tab, field); await clear(field)
            await human_type(field, display, wpm=random.randint(350, 450))
            await asyncio.sleep(random.uniform(0.05, 0.1))

        field = await find_el(tab, "input[name='username']", timeout=2)
        if field:
            await safe_click(tab, field); await clear(field)
            await human_type(field, username, wpm=random.randint(350, 450))
            await asyncio.sleep(random.uniform(0.05, 0.1))

        field = await find_any(tab, ["input[name='password']", "input[type='password']"], timeout=2)
        if field:
            await safe_click(tab, field); await clear(field)
            await human_type(field, password, wpm=random.randint(350, 450))
            await asyncio.sleep(random.uniform(0.1, 0.2))

        await self._select_dob(tab)
        await asyncio.sleep(random.uniform(0.1, 0.2))
        await tab.evaluate("(function(){ const cb = document.querySelector('input[type=\"checkbox\"]'); if (cb && !cb.checked) cb.click(); })();")
        await asyncio.sleep(random.uniform(0.05, 0.1))
        btn = await find_any(tab, ["button[type='submit']"], timeout=3)
        if not btn: raise Exception("Submit button not found")
        await safe_click(tab, btn)

        await self._wait_for_rate_limit_capture(tab, max_wait=3.0)
        rl_seconds = await self._get_rate_limit_seconds(tab)
        if rl_seconds is not None and rl_seconds > 0:
            await self._handle_rate_limit(tab)

        log("Form submitted!", "FORM")

    async def _wait_for_captcha_solved(self, tab: Tab, timeout=120):
        log("Waiting for captcha…", "CAPTCHA")
        start = time.time()
        submit_clicked = False
        while time.time() - start < timeout:
            try:
                url = await tab.evaluate("window.location.href")
                if "discord.com/channels/@me" in str(url):
                    log("Captcha solved! Redirected to Discord.", "CAPTCHA")
                    Stats.captchas_solved += 1
                    return True
                
                if "/register" in str(url) and time.time() - start > 20 and not submit_clicked:
                    log("Captcha might be solved but not submitted. Clicking submit...", "CAPTCHA")
                    btn = await find_any(tab, ["button[type='submit']"], timeout=0.5)
                    if btn:
                        await safe_click(tab, btn)
                        submit_clicked = True
                        await asyncio.sleep(5)

                if time.time() - start > 60:
                    log("Captcha seems to be looping. Discord might be blocking this IP.", "WARN")
                    return False

            except: pass
            await asyncio.sleep(1.5)
        log("Captcha timeout", "WARN"); return False

    async def _verify_email(self, tab: Tab, link: str) -> bool:
        if not link: return False
        log("Verifying email…", "MAIL")
        await tab.get(link)
        await asyncio.sleep(1.0)
        try:
            btn = await find_el(tab, "button", timeout=3)
            if btn and "verify" in (btn.text or "").lower():
                await safe_click(tab, btn)
                log("Clicked verify button", "MAIL")
                await asyncio.sleep(1.0)
        except: pass
        for _ in range(20):
            if await find_el(tab, "//h2[contains(text(),'Email Verified!')]", timeout=0.5):
                log("Email verified!", "MAIL")
                try: await tab.get("https://discord.com/channels/@me", timeout=5)
                except: log("Load timeout @me – continuing…", "ERROR")
                return True
            try:
                url = await tab.evaluate("window.location.href")
                if "@me" in str(url) or "app" in str(url): return True
            except: pass
            cont = await find_el(tab, "//button[contains(text(),'Continue')]", timeout=0.5)
            if cont: await safe_click(tab, cont); await asyncio.sleep(1.0)
            await asyncio.sleep(0.5)
        log("Email verification incomplete", "WARN"); return False

    async def _extract_token(self, tab: Tab) -> Optional[str]:
        log("Extracting token…", "INFO")
        for _ in range(10):
            try:
                token = await tab.evaluate("""
                    (() => { try { const iframe = document.createElement('iframe'); document.body.appendChild(iframe); const t = iframe.contentWindow.localStorage.token; iframe.remove(); return t; } catch(e) { return null; } })();
                """)
                if token and isinstance(token, str) and len(token) > 20 and '.' in token:
                    log("Token extracted!", "SUCCESS"); return token.strip('"')
            except: pass
            try:
                token = await tab.evaluate("localStorage.getItem('token')")
                if token and len(token) > 20 and '.' in token:
                    log("Token from localStorage", "SUCCESS"); return token.strip('"')
            except: pass
            await asyncio.sleep(0.5)
        try:
            await tab.evaluate("""window.__captured_token = null; const origFetch = window.fetch; window.fetch = async function(...args){ const req=args[0]; const opts=args[1]||{}; try{ let auth=null; if(opts.headers){ if(opts.headers instanceof Headers) auth=opts.headers.get('authorization')||opts.headers.get('Authorization'); else if(typeof opts.headers==='object') auth=opts.headers['authorization']||opts.headers['Authorization']; } if(!auth && req instanceof Request) auth=req.headers.get('authorization')||req.headers.get('Authorization'); if(auth) window.__captured_token=auth; } catch(e){} return origFetch.apply(this,args); };""")
        except: pass
        for _ in range(5):
            try:
                token = await tab.evaluate("window.__captured_token")
                if token and len(token) > 20 and '.' in token:
                    log("Token captured from fetch", "SUCCESS"); return token.strip('"')
            except: pass
            await asyncio.sleep(0.3)
        log("Token extraction failed", "ERROR"); return None

    async def _create_account(self, tid: int) -> Optional[AccountData]:
        Stats.attempts += 1
        log(f"Thread {tid}: Starting…", "INFO")

        if not ProxyManager.has_proxies() and Config.USE_VPN:
            WarpManager.disconnect()

        try:
            email, pw, mail_token = self.email.create_inbox()
        except Exception as e:
            log_exception(e, "Email creation failed"); return None

        username = random_string()
        browser = None
        try:
            browser = await self._browser(tid)
            tab = browser.main_tab
            await self._fill_form(tab, email, username, pw)

            if not await self._wait_for_captcha_solved(tab):
                log("Captcha not solved – skipping", "ERROR"); return None

            if not ProxyManager.has_proxies() and Config.USE_VPN:
                log("Captcha solved – connecting VPN now", "VPN")
                if not WarpManager.connect():
                    log("VPN connection failed – continuing anyway", "WARN")

            if mail_token:
                verify_link = await self.email.get_verify_link(mail_token)
                if verify_link:
                    if not await self._verify_email(tab, verify_link): return None
                else: return None
            else:
                try: await tab.get("https://discord.com/channels/@me", timeout=5)
                except: log("Load timeout @me – continuing…", "ERROR")
                await asyncio.sleep(2)

            await asyncio.sleep(Config.TOKEN_FETCH_DELAY)
            token = await self._extract_token(tab)
            if not token:
                log("Token extraction failed – skipping account", "ERROR"); return None

            try:
                with open(Config.TOKENS_FILE, "a") as f: f.write(f"{token}\n")
                with open(Config.EVS_FILE, "a") as f: f.write(f"{email}:{pw}:{token}\n")
            except PermissionError:
                log("Permission denied writing token. Attempting to fix permissions...", "WARN")
                subprocess.run(['sudo', 'chown', '-R', os.getlogin(), Config.OUTPUT_DIR])
                with open(Config.TOKENS_FILE, "a") as f: f.write(f"{token}\n")
                with open(Config.EVS_FILE, "a") as f: f.write(f"{email}:{pw}:{token}\n")
                
            log("Account Created!", "SUCCESS")
            Stats.generated += 1
            return AccountData(email, username, pw, token, verified=True, disabled=False)
        except Exception as e:
            log_exception(e, f"Thread {tid} error"); return None
        finally:
            if browser: await self._close_browser(browser)
            if not ProxyManager.has_proxies() and Config.USE_VPN:
                WarpManager.disconnect()

    def generate(self, tid: int) -> Optional[AccountData]:
        return asyncio.run(self._create_account(tid))


class TokenChecker:
    def __init__(self):
        self.s = tls_client.Session(client_identifier='chrome_110')
        self.s.headers.update(headers())

    def check(self, token: str) -> TokenInfo:
        info = TokenInfo(token=token)
        try:
            r = self.s.get("https://discord.com/api/v9/users/@me", headers={"Authorization": token})
            if r.status_code == 200:
                d = r.json(); info.valid = True
                info.username = f"{d.get('username')}#{d.get('discriminator', '0')}"
                info.email = d.get("email"); info.phone = d.get("phone")
                info.verified = d.get("verified", False); info.flags = d.get("flags", 0)
                info.locale = d.get("locale", ""); info.mfa_enabled = d.get("mfa_enabled", False)
                info.nitro = d.get("premium_type", 0) > 0
        except: pass
        return info

    def batch(self, tokens: List[str]) -> List[TokenInfo]:
        results = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as pool:
            futs = [pool.submit(self.check, t) for t in tokens]
            for f in concurrent.futures.as_completed(futs): results.append(f.result())
        return results

    def show(self, results: List[TokenInfo]):
        valid = [r for r in results if r.valid]
        if not valid:
            print(f"\n{Theme.PURPLE}No valid tokens.{Theme.END}")
            return
        from rich.table import Table; from rich.console import Console
        c = Console()
        tbl = Table(title="", header_style="bold magenta", border_style="purple")
        tbl.add_column("Token", style="dim", width=35)
        tbl.add_column("Username", style="magenta"); tbl.add_column("Email"); tbl.add_column("Phone")
        tbl.add_column("Verified", justify="center"); tbl.add_column("Nitro", justify="center", style="white")
        tbl.add_column("MFA", justify="center"); tbl.add_column("Locale")
        for r in valid:
            tbl.add_row(
                r.token[:30] + "…" if len(r.token) > 30 else r.token,
                r.username or "-", r.email or "-", r.phone or "-",
                "[magenta]YES[/]" if r.verified else "[grey]NO[/]",
                "[white]YES[/]" if r.nitro else "[grey]NO[/]",
                "[magenta]YES[/]" if r.mfa_enabled else "[grey]NO[/]",
                r.locale or "-"
            )
        c.print(tbl)

    def export(self, results: List[TokenInfo], path: str):
        valid = [r for r in results if r.valid]
        with open(path, "w") as f:
            json.dump([{"token": r.token, "username": r.username, "email": r.email,
                        "phone": r.phone, "verified": r.verified, "nitro": r.nitro,
                        "mfa": r.mfa_enabled, "locale": r.locale} for r in valid], f, indent=2)
        with open(Config.TOKENS_FILE, "a") as f:
            for r in valid:
                f.write(f"{r.token}\n")
        print(f"{Theme.PURPLE}Saved {len(valid)} tokens to {os.path.abspath(path)}{Theme.END}")

class App:
    def __init__(self):
        self.reg = DiscordRegistrator()
        self.chk = TokenChecker()

    def gen(self):
        show_banner()
        try: n = int(input(f"{Theme.PURPLE}Accounts? (1): {Theme.END}") or "1")
        except: n = 1
        try: t = int(input(f"{Theme.PURPLE}Threads? (1): {Theme.END}") or "1")
        except: t = 1
        print()

        solver_choice = input(f"{Theme.PURPLE}Use Auto Solver? (Y/N): {Theme.END}").strip().upper()
        Config.USE_AUTO_SOLVER = solver_choice == "Y"
        if Config.USE_AUTO_SOLVER:
            log("Setting up NopeCHA extension...", "NOPECHA")
            if not NopechaManager.ensure_extension_ready():
                log("Failed to load NopeCHA extension. Captcha will not be solved automatically.", "ERROR")
                Config.USE_AUTO_SOLVER = False
            else:
                log("NopeCHA extension loaded with API key injected.", "NOPECHA")
                if not Config.NOPECHA_API_KEY:
                    log("No NopeCHA API key set in Config. Extension might not solve automatically.", "WARN")
        else:
            log("Solve Captcha manually...", "NOPECHA")

        log(f"Creating {n} account(s), {t} thread(s).", "INFO")
        per = [n // t] * t
        for i in range(n % t): per[i] += 1
        success = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=t) as pool:
            futs = []; tid = 1
            for c in per:
                for _ in range(c): futs.append(pool.submit(self.reg.generate, tid))
                tid += 1
            for f in concurrent.futures.as_completed(futs):
                r = f.result()
                if r and r.token: success.append(r.token)
        if success:
            print(f"\n{Theme.PURPLE}Accounts Created:{Theme.END}")
            for i, tok in enumerate(success, 1): print(f"  {Theme.PURPLE}{i}. {tok}{Theme.END}")
        else:
            print(f"\n{Theme.PURPLE}No accounts were created.{Theme.END}")
        input("\nPress Enter to return…")

    def check_tokens(self):
        print(f"\n{Theme.PURPLE}Paste tokens... (Press Enter twice):{Theme.END}")
        lines = []
        while True:
            line = input()
            if line.strip() == "":
                if lines: break
            else: lines.append(line)
        tokens = extract_tokens("\n".join(lines))
        if not tokens:
            log("No tokens found.", "ERROR"); return
        log(f"Checking {len(tokens)} token(s)…", "INFO")
        res = self.chk.batch(tokens); self.chk.show(res); self.chk.export(res, Config.CHECKER_OUTPUT)

    def menu(self):
        while True:
            show_banner()
            print(f"{Theme.PURPLE}1> {Theme.LIGHT_GREY}Generate Accounts{Theme.END}")
            print(f"{Theme.PURPLE}2> {Theme.LIGHT_GREY}Check Tokens{Theme.END}")
            print(f"{Theme.PURPLE}3> {Theme.LIGHT_GREY}Exit{Theme.END}")
            print()
            key = get_key()
            if key == "1":
                self.gen()
            elif key == "2":
                self.check_tokens(); input("\nPress Enter to return…")
            elif key == "3":
                print(f"\n{Theme.PURPLE}Exiting…{Theme.END}"); break
            else:
                print(f"\n{Theme.PURPLE}Invalid option.{Theme.END}"); time.sleep(1)

def get_key() -> str:
    try:
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            ch = sys.stdin.read(1)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        return ch
    except (termios.error, ValueError, OSError):
        try:
            return input().strip()[:1]
        except:
            return ''

if __name__ == "__main__":
    ProxyManager.load()
    App().menu()
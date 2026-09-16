# -*- coding: utf-8 -*-
"""치이카와 극장판 상영회차/취소표 감시 -> Windows 토스트 알림"""
import base64, json, os, subprocess, sys, time, datetime
from playwright.sync_api import sync_playwright

BASE = os.path.dirname(os.path.abspath(__file__))
CFG = json.load(open(os.path.join(BASE, "config.json"), encoding="utf-8"))
STATE_F = os.path.join(BASE, "state.json")
CGV_PROFILE = os.environ.get("CGV_PROFILE", os.path.join(BASE, ".cgv-profile"))
CHROME_CHANNEL = os.environ.get("CHROME_CHANNEL", "chrome")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")
KEY = CFG["movie_name_contains"]
DATES = CFG["watch_dates"]

APP_ID = "Chiikawa.Watch"


def _register_app_id():
    """알림에 'PowerShell' 대신 전용 이름이 뜨도록 HKCU에 AppUserModelId 등록(관리자 불필요)."""
    ps = (f"$p='HKCU:\\SOFTWARE\\Classes\\AppUserModelId\\{APP_ID}';"
          "if(-not (Test-Path $p)){New-Item $p -Force|Out-Null};"
          "$n=[Convert]::FromBase64String('"
          + base64.b64encode("치이카와 알리미".encode("utf-8")).decode("ascii")
          + "');"
          "Set-ItemProperty $p -Name DisplayName -Value ([Text.Encoding]::UTF8.GetString($n));")
    try:
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                       capture_output=True, timeout=30)
    except Exception:
        pass


def _xesc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def toast(title, lines, url=None, actions=None):
    """Base64로 XML을 전달해 콘솔 코드페이지(CP949)에 의한 한글 깨짐을 원천 차단.

    url     : 토스트 본문을 클릭했을 때 열 예매 페이지
    actions : [(버튼라벨, url), ...] 최대 3개까지 버튼으로 노출
    """
    body = "\n".join(lines)[:600]
    launch = (f' activationType="protocol" launch="{_xesc(url)}"') if url else ""
    btns = ""
    if actions:
        items = "".join(
            f'<action content="{_xesc(lbl)}" activationType="protocol" '
            f'arguments="{_xesc(u)}"/>'
            for lbl, u in actions[:3])
        btns = f"<actions>{items}</actions>"
    xml = (f'<toast scenario="reminder"{launch}><visual><binding template="ToastGeneric">'
           f'<text><![CDATA[{title}]]></text><text><![CDATA[{body}]]></text>'
           '</binding></visual>'
           '<audio src="ms-winsoundevent:Notification.Looping.Alarm2"/>'
           f'{btns}</toast>')
    b64 = base64.b64encode(xml.encode("utf-8")).decode("ascii")
    ps = (
        "[Windows.UI.Notifications.ToastNotificationManager,Windows.UI.Notifications,"
        "ContentType=WindowsRuntime]|Out-Null;"
        "[Windows.Data.Xml.Dom.XmlDocument,Windows.Data.Xml.Dom,"
        "ContentType=WindowsRuntime]|Out-Null;"
        f"$x=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{b64}'));"
        "$d=New-Object Windows.Data.Xml.Dom.XmlDocument;$d.LoadXml($x);"
        "$n=New-Object Windows.UI.Notifications.ToastNotification $d;"
        f"[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{APP_ID}').Show($n);"
    )
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                           capture_output=True, timeout=30)
        if r.returncode != 0:
            print("  [toast 오류]", r.stderr.decode("cp949", "replace")[:200])
            return False
    except Exception as e:
        print("  [toast 실패]", e)
        return False
    print(f"  *** {title} | {body[:120]}")
    return True


def cgv(pg, out):
    # 다른 영화관 페이지에서 호출하면 CORS 오류와 인증 실패를 구분할 수 없다.
    pg.goto("https://cgv.co.kr/cnm/movieBook/movie",
            wait_until="domcontentloaded", timeout=45000)
    pg.wait_for_timeout(2000)
    body = pg.locator("body").inner_text()
    if "이용이 제한" in body or "RAY_ID" in body:
        raise RuntimeError("CGV 접근 제한 화면: 회차 조회 실패 (로그인 필요 여부 미확인)")
    js = """async ({site, ymd, mov}) => {
      const q = new URLSearchParams({coCd:'A420',siteNo:site,scnYmd:ymd,scnsNo:'',scnSseq:'',
        movNo:mov,prodNo:'',rtctlScopCd:'08',salsTznCd:'',tcscnsGradCd:'',sascnsGradCd:'',custNo:''});
      const r = await fetch('/api/v1/booking/searchSchByMov?'+q,
        {cache:'no-store', credentials:'include',
         signal: AbortSignal.timeout(20000),
         headers:{'accept':'application/json','accept-language':'ko-KR'}});
      return {s:r.status, b: await r.text()};
    }"""
    mov = CFG["cgv"]["movNo"]
    pending = {}
    for site, nm in CFG["cgv"]["sites"].items():
        for ymd in DATES:
            try:
                r = pg.evaluate(js, {"site": site, "ymd": ymd, "mov": mov})
            except Exception as e:
                raise RuntimeError(f"{nm}/{ymd} 요청 실패: {e}") from e
            if r["s"] != 200:
                raise RuntimeError(f"{nm}/{ymd} HTTP {r['s']}: 회차 조회 실패")
            try:
                payload = json.loads(r["b"])
                data = payload["data"]
                if payload.get("statusCode") != 0 or not isinstance(data, list):
                    raise ValueError("data가 회차 목록이 아님")
            except (ValueError, KeyError, TypeError) as e:
                raise RuntimeError(f"{nm}/{ymd} CGV 응답 형식 확인 필요") from e
            pending.update(parse_cgv(data, site, nm, ymd, mov))
            pg.wait_for_timeout(300)
    # 일부 지점만 성공한 결과로 이전 기준선을 덮어쓰지 않는다.
    out.update(pending)
    print(f"  [CGV] 조회 성공: {len(pending)}회차")


def parse_cgv(data, site, name, date, movie):
    """CGV 웹 예매 응답: data는 평면 목록, 좌석 수는 문자열이다."""
    out = {}
    for it in data:
        if not isinstance(it, dict):
            raise ValueError("CGV 회차 형식 오류")
        if str(it.get("movNo")) != movie or str(it.get("siteNo")) != site or str(it.get("scnYmd")) != date:
            raise ValueError("CGV 요청과 다른 영화/지점/날짜 응답")
        start = it.get("scnsrtTm", "")
        if len(start) != 4 or not start.isdigit() or not it.get("scnsNo") or not it.get("scnSseq"):
            raise ValueError("CGV 회차 식별자/시간 오류")
        left, total = int(it["frSeatCnt"]), int(it["stcnt"])
        if not 0 <= left <= total or it.get("cntlYn") not in ("Y", "N"):
            raise ValueError("CGV 좌석/예매 상태 오류")
        # CGV와 동일하게 시작 시간이 지난 회차는 알림 대상에서 제외한다.
        start_at = datetime.datetime.strptime(date, "%Y%m%d") + datetime.timedelta(
            hours=int(start[:2]), minutes=int(start[2:]))
        opened = it["cntlYn"] == "N" and start_at > datetime.datetime.now()
        key = f"CGV|{name}|{date}|{start[:2]}:{start[2:]}|{it['scnsNm']}"
        out[key] = {"left": left, "total": total, "opened": opened,
                    "url": "https://cgv.co.kr/cnm/movieBook/movie",
                    "format": it.get("sbtdivNm", "")}
    return out


def changes(previous, current):
    new_sh, seats = [], []
    for key, value in current.items():
        if value.get("opened") is False:
            continue
        old = previous.get(key)
        left = value.get("left")
        text = key.replace("|", " ")
        if old is None or old.get("opened") is False:
            text += f" (잔여 {left})" if left is not None else ""
            new_sh.append((text, key.split("|")[0], value.get("url")))
        elif isinstance(old.get("left"), int) and isinstance(left, int) and old["left"] == 0 and left > 0:
            if key.startswith("롯데|") and old.get("seat_semantics") != value.get("seat_semantics"):
                continue  # Correcting legacy inverted counts is not a cancellation.
            seats.append((text + f" -> 취소표 {left}석!", key.split("|")[0], value.get("url")))
    return new_sh, seats


def cgv_context(playwright):
    context = playwright.chromium.launch_persistent_context(
        CGV_PROFILE, channel=CHROME_CHANNEL, headless=False, locale="ko-KR")
    page = context.pages[0] if context.pages else context.new_page()
    try:
        session = context.new_cdp_session(page)
        window = session.send("Browser.getWindowForTarget")
        session.send("Browser.setWindowBounds", {
            "windowId": window["windowId"], "bounds": {"windowState": "minimized"}})
        session.detach()
    except Exception:
        pass  # 최소화 실패는 회차 조회 실패가 아니다.
    return context


def scan_iter(previous=None):
    from cinema_scan import scan_iter as parallel_scan
    return parallel_scan(previous or {})


def scan(previous=None, strict_cgv=False):
    current = {}
    for result in scan_iter(previous):
        current.update(result.schedules)
        if strict_cgv and result.brand == "CGV" and result.errors:
            raise RuntimeError("CGV scan incomplete: " + "; ".join(result.errors))
    return current


def main():
    if sys.stdout is None:
        sys.stdout = sys.stderr = open(os.path.join(BASE, "watch.log"), "a", encoding="utf-8", buffering=1)
    elif hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if "--check-cgv" in sys.argv:
        with sync_playwright() as p:
            context = cgv_context(p)
            try:
                out = {}
                cgv(context.pages[0], out)
                print(json.dumps(out, ensure_ascii=False, indent=2))
            finally:
                context.close()
        return
    _register_app_id()
    # One cloud detector sends phone pushes and publishes the same desktop events.
    from desktop_alerts import follow
    follow(once="--once" in sys.argv)


if __name__ == "__main__":
    main()

"""Independent cinema scans with bounded requests and per-scope recovery."""
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

from playwright.sync_api import sync_playwright

import watch

BRANDS = ("CGV", "롯데", "메가박스", "씨네큐")
URLS = {"CGV": "https://cgv.co.kr/cnm/movieBook/movie",
        "롯데": "https://www.lottecinema.co.kr/NLCHS/Ticketing",
        "메가박스": "https://www.megabox.co.kr/booking",
        "씨네큐": "https://www.cineq.co.kr/Movie/Info?MovieCode=" + watch.CFG["cineq"]["MovieCode"]}

# Each origin gets at most four simultaneous requests, one retry on transient
# failure, and a two-minute batch deadline. Every queued scope gets a result.
BATCH_JS = """async ({jobs, concurrency, timeout, budget}) => {
  let next = 0;
  const results = new Array(jobs.length);
  const deadline = Date.now() + budget;
  async function worker() {
    while (next < jobs.length) {
      const index = next++, job = jobs[index];
      for (let attempt = 0; attempt < 2; attempt++) {
        const remaining = deadline - Date.now();
        if (remaining <= 0) { results[index] = {error:'batch deadline'}; break; }
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), Math.min(timeout, remaining));
        let retry = false;
        try {
          const options = {cache:'no-store', credentials:'include', signal:controller.signal,
                           method:job.method || 'GET', headers:job.headers || {}};
          if (job.form) {
            options.body = new FormData();
            for (const [key,value] of Object.entries(job.form)) options.body.append(key,value);
          } else if (job.body) options.body = job.body;
          const response = await fetch(job.url, options);
          const body = await response.text();
          results[index] = {s:response.status, b:body};
          retry = response.status >= 500 || response.status === 408 || response.status === 429;
        } catch (error) { results[index] = {error:error.name}; retry = true; }
        finally { clearTimeout(timer); }
        if (!retry || attempt === 1) break;
        await new Promise(resolve => setTimeout(resolve, 400));
      }
    }
  }
  await Promise.all(Array.from({length:Math.min(concurrency,jobs.length)}, worker));
  return results;
}"""


@dataclass
class Result:
    brand: str
    schedules: dict
    errors: list
    seconds: float = 0
    requests: int = 0


def jobs_for(brand):
    jobs = []
    if brand == "CGV":
        from urllib.parse import urlencode
        for site, name in watch.CFG["cgv"]["sites"].items():
            for date in watch.DATES:
                query = dict(coCd="A420", siteNo=site, scnYmd=date, scnsNo="", scnSseq="",
                             movNo=watch.CFG["cgv"]["movNo"], prodNo="", rtctlScopCd="08",
                             salsTznCd="", tcscnsGradCd="", sascnsGradCd="", custNo="")
                jobs.append(dict(scope=f"CGV|{name}|{date}|", site=site, name=name, date=date,
                                 url="/api/v1/booking/searchSchByMov?" + urlencode(query),
                                 headers={"accept": "application/json", "accept-language": "ko-KR"}))
    elif brand == "롯데":
        for cinema, name in watch.CFG["lotte"]["cinemas"].items():
            for date in watch.DATES:
                params = dict(MethodName="GetPlaySequence", channelType="HO", osType="W",
                              osVersion="Chrome", playDate=f"{date[:4]}-{date[4:6]}-{date[6:]}",
                              cinemaID=cinema, representationMovieCode="")
                jobs.append(dict(scope=f"롯데|{name}|{date}|", name=name, date=date,
                                 url="/LCWS/Ticketing/TicketingData.aspx", method="POST",
                                 form={"paramList": json.dumps(params)}))
    elif brand == "메가박스":
        for date in watch.DATES:
            params = dict(playDe=date, incomeMovieNo=watch.CFG["megabox"]["movieNo"], onLoad="N",
                          sellChnlCd="", incomeTheabKindCd="", incomeBrchNo1="", incomePlayDe=date)
            jobs.append(dict(scope=f"메가박스|(전지점)|{date}|", date=date,
                             url="/on/oh/ohb/SimpleBooking/selectBokdList.do", method="POST",
                             headers={"Content-Type": "application/json;charset=UTF-8",
                                      "X-Requested-With": "XMLHttpRequest"}, body=json.dumps(params)))
    return jobs


def parse(brand, job, response):
    if response.get("error"):
        raise ValueError(response["error"])
    if response.get("s") != 200:
        raise ValueError(f"HTTP {response.get('s')}")
    data = json.loads(response["b"])
    if not isinstance(data, dict):
        raise ValueError("response is not an object")
    if brand == "CGV":
        if data.get("statusCode") != 0 or not isinstance(data.get("data"), list):
            raise ValueError("invalid CGV schedule list")
        return watch.parse_cgv(data["data"], job["site"], job["name"], job["date"], watch.CFG["cgv"]["movNo"])
    result = {}
    if brand == "롯데":
        items = data.get("PlaySeqs")
        if not isinstance(items, dict) or not isinstance(items.get("Items"), list):
            raise ValueError("invalid Lotte schedule list")
        for item in items["Items"]:
            if not isinstance(item, dict) or "MovieNameKR" not in item:
                raise ValueError("invalid Lotte schedule")
            if watch.KEY not in str(item["MovieNameKR"]):
                continue
            total, booked = int(item["TotalSeatCount"]), int(item["BookingSeatCount"])
            if not 0 <= booked <= total or not item.get("StartTime") or not item.get("ScreenNameKR"):
                raise ValueError("invalid Lotte seats/time")
            result[job["scope"] + f"{item['StartTime']}|{item['ScreenNameKR']}"] = {
                "left": total - booked, "total": total, "url": URLS[brand]}
    elif brand == "메가박스":
        if data.get("statCd") != 0 or data.get("paramMap", {}).get("playDe") != job["date"]:
            raise ValueError("Megabox failed or returned a different date")
        items = data.get("movieList")
        if not isinstance(items, list) or any(not isinstance(item, dict) or "movieNm" not in item for item in items):
            raise ValueError("invalid Megabox movie list")
        selected = [item for item in items if str(item.get("movieNo")) == watch.CFG["megabox"]["movieNo"]]
        if any(item.get("formAt") not in ("Y", "N") for item in selected):
            raise ValueError("unknown Megabox booking availability")
        # movieList also contains unreleased/unavailable films. Only formAt=Y
        # marks this movie selectable on the requested date (not a seat count).
        if any(item["formAt"] == "Y" for item in selected):
            result[job["scope"] + "편성됨|-"] = {"left": None, "total": None, "url": URLS[brand]}
    return result


def merge_responses(brand, jobs, responses, previous):
    current, errors = {}, []
    for index, job in enumerate(jobs):
        try:
            response = responses[index] if index < len(responses) else {"error": "missing response"}
            current.update(parse(brand, job, response))
        except (ValueError, KeyError, TypeError) as error:
            current.update({key: value for key, value in previous.items() if key.startswith(job["scope"])})
            errors.append(job["scope"] + " " + str(error)[:100])
    return Result(brand, current, errors, requests=len(jobs))


def scan_brand(brand, previous):
    started = time.monotonic()
    baseline = {key: value for key, value in previous.items() if key.startswith(brand + "|")}
    jobs = jobs_for(brand)
    try:
        # Playwright objects stay on their own thread. Each origin has its own page.
        with sync_playwright() as playwright:
            if brand == "CGV":
                context = watch.cgv_context(playwright)
                browser = None
            else:
                browser = playwright.chromium.launch(channel=watch.CHROME_CHANNEL, headless=True)
                context = browser.new_context(locale="ko-KR", user_agent=watch.UA)
            try:
                page = context.pages[0] if context.pages else context.new_page()
                page.set_default_timeout(8000)
                response = page.goto(URLS[brand], wait_until="domcontentloaded", timeout=20000)
                if response and response.status >= 400:
                    raise ValueError(f"page HTTP {response.status}")
                body = page.locator("body").inner_text(timeout=8000)
                if "이용이 제한" in body or "RAY_ID" in body:
                    raise ValueError("access restricted")
                if brand == "씨네큐":
                    page.wait_for_function("key => document.body.innerText.includes(key)", arg=watch.KEY, timeout=8000)
                    html = page.content()
                    current = {f"씨네큐|{name}|-|상영예정|-": {"left": None, "total": None, "url": URLS[brand]}
                               for code, name in watch.CFG["cineq"]["theaters"].items()
                               if name in html or f"TheaterCode={code}" in html}
                    result = Result(brand, current, [], requests=1)
                else:
                    responses = page.evaluate(BATCH_JS, dict(jobs=jobs, concurrency=4, timeout=8000, budget=120000))
                    result = merge_responses(brand, jobs, responses, baseline)
            finally:
                context.close()
                if browser:
                    browser.close()
    except Exception as error:
        result = Result(brand, baseline, [f"{brand}: {type(error).__name__}: {str(error)[:120]}"], requests=len(jobs))
    result.seconds = round(time.monotonic() - started, 2)
    print(f"[{brand}] {result.seconds}s, requests={result.requests}, schedules={len(result.schedules)}, failures={len(result.errors)}", flush=True)
    for error in result.errors:
        print("  " + error, flush=True)
    return result


def scan_iter(previous):
    with ThreadPoolExecutor(max_workers=4) as pool:
        tasks = [pool.submit(scan_brand, brand, previous) for brand in BRANDS]
        for task in as_completed(tasks):
            yield task.result()

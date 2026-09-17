# -*- coding: utf-8 -*-
"""권익위 민원빅데이터 1회성 수집기 — GitHub Actions 시간 단위 실행용.

매 실행:
  0) API 생존 확인 1회. 504/오류면 기록만 남기고 정상 종료(다음 시간에 재시도)
  1) 남은 일 호출 예산(KST 기준 90회) 계산 — state/daily_calls.json
  2) View7 잔여 조합(불법매립·폐기물방치 × 2024-01~2026-08) 수집 → 결과/무단투기_시군구월별.csv
  3) View8 시도별 처리기관 건수: 최초 1회 실측(경기 2026-06) 통과 시에만 수집
     → 결과/민원_시도별처리기관월별.csv
  4) 체크포인트(state/*.jsonl)와 결과를 갱신하면 워크플로우가 커밋

서비스키는 환경변수 DATA_GO_KR_SERVICE_KEY로만 받는다. 키·응답 원문은 로그에 남기지 않는다.
"""
import csv
import datetime as dt
import json
import os
import sys
import time
from zoneinfo import ZoneInfo

from 민원_client import MinwonBigdataClient, PortalApiError, load_service_key

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "결과")
STATE = os.path.join(BASE, "state")
KST = ZoneInfo("Asia/Seoul")
DAILY_BUDGET = int(os.environ.get("DAILY_BUDGET", "90"))
TARGET = "pttn,dfpt,saeol"

DATE_FROM, DATE_TO = dt.date(2024, 1, 1), dt.date(2026, 8, 31)
V7_KEYWORDS = [("무단투기", "무단투기"), ("불법투기", "불법투기"), ("불법매립", "불법매립"), ("폐기물방치", "(폐기물+방치)")]
V8_KEYWORDS = [("무단투기", "무단투기"), ("불법투기", "불법투기")]
SIDO = [
    ("서울특별시", ["6110000"]), ("부산광역시", ["6260000"]), ("대구광역시", ["6270000"]),
    ("인천광역시", ["6280000"]), ("대전광역시", ["6300000"]), ("울산광역시", ["6310000"]),
    ("세종특별자치시", ["5690000"]), ("경기도", ["6410000"]), ("강원특별자치도", ["6420000"]),
    ("충청북도", ["6430000"]), ("충청남도", ["6440000"]), ("전북특별자치도", ["6450000"]),
    ("전남광주통합특별시", ["6460000", "6290000"]), ("경상북도", ["6470000"]),
    ("경상남도", ["6480000"]), ("제주특별자치도", ["6500000"]),
]


def log(msg):
    print(f"[{dt.datetime.now(KST):%m-%d %H:%M:%S}] {msg}", flush=True)


def months():
    d = DATE_FROM
    while d <= DATE_TO:
        last = (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(days=1)
        yield d.strftime("%Y-%m"), d.strftime("%Y%m%d"), min(last, DATE_TO).strftime("%Y%m%d")
        d = last + dt.timedelta(days=1)


def jload(path, default):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            try:
                return json.load(f)
            except ValueError:
                return default
    return default


def jsave(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)


def ckpt_load(path, keyfn):
    done = set()
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            try:
                done.add(keyfn(json.loads(line)))
            except (ValueError, KeyError):
                pass
    return done


class Budget:
    def __init__(self):
        self.path = os.path.join(STATE, "daily_calls.json")
        self.today = dt.datetime.now(KST).strftime("%Y-%m-%d")
        d = jload(self.path, {})
        self.used = int(d.get(self.today, 0))

    @property
    def left(self):
        return max(0, DAILY_BUDGET - self.used)

    def spend(self, n=1):
        self.used += n
        d = jload(self.path, {})
        d = {k: v for k, v in d.items() if k >= (dt.datetime.now(KST) - dt.timedelta(days=7)).strftime("%Y-%m-%d")}
        d[self.today] = self.used
        jsave(self.path, d)


def alive(cli8, budget):
    r = cli8.call("minPrcsInstInfo", {"searchword": "무단투기", "dateFrom": "20260601", "dateTo": "20260630",
                                      "target": TARGET, "mainSubCode": "6410000", "omitDuplicate": "true"}, raw=True)
    budget.spend()
    ok = r.status_code == 200 and "SERVICETIMEOUT" not in (r.text or "")
    return ok, r


def items_of(body):
    arr = (body or {}).get("items") or (body or {}).get("item") or []
    if isinstance(arr, dict):
        arr = [arr]
    return [it.get("item", it) for it in arr if isinstance(it, dict)], (body or {}).get("totalHits")


def append_rows(path, fields, rows):
    exists = os.path.exists(path)
    with open(path, "a", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if not exists:
            w.writeheader()
        w.writerows(rows)


def run_v7(cli7, budget):
    """View7 잔여 조합 수집."""
    ck = os.path.join(STATE, "v7_체크포인트.jsonl")
    out = os.path.join(OUT, "무단투기_시군구월별.csv")
    done = ckpt_load(ck, lambda j: (j["kw"], j["ym"]))
    added = 0
    with open(ck, "a", encoding="utf-8") as ckf:
        for kw_label, kw_q in V7_KEYWORDS:
            for ym, d1, d2 in months():
                if (kw_label, ym) in done:
                    continue
                if budget.left <= 0:
                    return added, False
                try:
                    body = cli7.call("minPttnStstAddrInfo", {"searchword": kw_q, "dateFrom": d1, "dateTo": d2,
                                                             "target": TARGET, "omitDuplicate": "true"})
                    budget.spend()
                except PortalApiError as e:
                    budget.spend()
                    log(f"  ! V7 {kw_label} {ym}: {e.code if hasattr(e,'code') else e}")
                    return added, False  # 장애로 간주, 다음 실행에 재시도
                items, _ = items_of(body)
                now = dt.datetime.now(KST).strftime("%Y-%m-%d %H:%M")
                rows = []
                for it in items:
                    parts = str(it.get("label") or "").split()
                    rows.append({"키워드": kw_label, "연월": ym, "시도": parts[0] if parts else "",
                                 "시군구": " ".join(parts[1:]), "건수": it.get("hits", ""), "수집일시": now})
                append_rows(out, ["키워드", "연월", "시도", "시군구", "건수", "수집일시"], rows)
                ckf.write(json.dumps({"kw": kw_label, "ym": ym, "n": len(rows)}, ensure_ascii=False) + "\n")
                added += 1
                time.sleep(0.6)
    return added, True


def self_check(cli8, budget):
    """View8 시도 필터 실측(1회). 시군구 라벨 3개 이상이면 통과. 결과를 state에 저장."""
    p = os.path.join(STATE, "v8_실측.json")
    st = jload(p, None)
    if st is not None:
        return st.get("ok", False)
    r = cli8.call("minPrcsInstInfo", {"searchword": "무단투기", "dateFrom": "20260601", "dateTo": "20260630",
                                      "target": TARGET, "mainSubCode": "6410000", "omitDuplicate": "true"}, raw=True)
    budget.spend()
    labels, total = [], None
    try:
        j = r.json()
        resp = j.get("response", j)
        body = resp.get("body", resp)
        items, total = items_of(body)
        labels = [str(it.get("label")) for it in items]
    except ValueError:
        pass
    sgg = [l for l in labels if l.endswith(("시", "군", "구", "시청", "군청", "구청"))]
    ok = len(sgg) >= 3
    jsave(p, {"checked_at": dt.datetime.now(KST).isoformat(), "status": r.status_code, "ok": ok,
              "labels": labels[:40], "totalHits": total})
    with open(os.path.join(OUT, "민원_시도별_실측원문.txt"), "w", encoding="utf-8") as f:
        f.write((r.text or "")[:6000])
    log(f"V8 실측: 항목 {len(labels)} / 시군구형 {len(sgg)} → {'OK' if ok else '실패'}")
    return ok


def run_v8(cli8, budget):
    ck = os.path.join(STATE, "v8_체크포인트.jsonl")
    out = os.path.join(OUT, "민원_시도별처리기관월별.csv")
    done = ckpt_load(ck, lambda j: (j["kw"], j["sido"], j["ym"]))
    added = 0
    fields = ["키워드", "연월", "시도", "시도코드", "순위", "처리기관", "건수", "총건수", "수집일시"]
    with open(ck, "a", encoding="utf-8") as ckf:
        for kw_label, kw_q in V8_KEYWORDS:
            for sido, codes in SIDO:
                for ym, d1, d2 in months():
                    if (kw_label, sido, ym) in done:
                        continue
                    if budget.left < len(codes):
                        return added, False
                    rows, used = [], None
                    for code in codes:
                        try:
                            body = cli8.call("minPrcsInstInfo", {"searchword": kw_q, "dateFrom": d1, "dateTo": d2,
                                                                 "target": TARGET, "mainSubCode": code, "omitDuplicate": "true"})
                            budget.spend()
                        except PortalApiError as e:
                            budget.spend()
                            log(f"  ! V8 {kw_label} {sido} {ym} {code}: {e.code if hasattr(e,'code') else e}")
                            return added, False
                        items, total = items_of(body)
                        now = dt.datetime.now(KST).strftime("%Y-%m-%d %H:%M")
                        for it in items:
                            rows.append({"키워드": kw_label, "연월": ym, "시도": sido, "시도코드": code,
                                         "순위": it.get("rank", ""), "처리기관": it.get("label", ""),
                                         "건수": it.get("hits", ""), "총건수": total if total is not None else "",
                                         "수집일시": now})
                        used = code
                        if items:
                            break
                        time.sleep(0.5)
                    append_rows(out, fields, rows)
                    ckf.write(json.dumps({"kw": kw_label, "sido": sido, "ym": ym, "code": used, "n": len(rows)},
                                         ensure_ascii=False) + "\n")
                    added += 1
                    time.sleep(0.6)
    return added, True


def main():
    os.makedirs(OUT, exist_ok=True)
    os.makedirs(STATE, exist_ok=True)
    key = load_service_key()
    if key:
        key = key.strip().strip('"').strip("'").lstrip("﻿").strip()
    if not key:
        log("서비스키 없음(DATA_GO_KR_SERVICE_KEY) — 종료")
        return 2
    budget = Budget()
    log(f"오늘 사용 {budget.used}/{DAILY_BUDGET} · 키 길이 {len(key)} · %포함 {chr(37) in key}")
    if budget.left <= 0:
        log("일 한도 소진 — 종료")
        return 0
    cli8 = MinwonBigdataClient(key, view="minAnalsInfoView8", timeout=60)
    cli7 = MinwonBigdataClient(key, view="minAnalsInfoView7", timeout=60)
    try:
        ok, r = alive(cli8, budget)
        status_code, why = r.status_code, ""
    except PortalApiError as e:
        # 포털 자체 연결 실패(시간초과 등)도 미복구로 취급하고 다음 실행에 재시도
        ok, r, status_code, why = False, None, 0, f"network:{str(e)[:50]}"
    status_path = os.path.join(STATE, "api_status.json")
    hist = jload(status_path, {"history": []})
    hist["history"] = (hist["history"] + [{"at": dt.datetime.now(KST).isoformat(timespec="minutes"),
                                           "status": status_code, "alive": ok}])[-200:]
    hist["last_alive_at"] = dt.datetime.now(KST).isoformat(timespec="minutes") if ok else hist.get("last_alive_at")
    jsave(status_path, hist)
    if not ok:
        msg = why
        if r is not None:
            try:
                msg = str(r.json().get("OpenAPI_ServiceResponse", {}).get("cmmMsgHeader", {}).get("errMsg", ""))[:60]
            except ValueError:
                msg = (r.text or "")[:60].replace(chr(10), " ")
        log(f"API 미복구 status={status_code} {msg} — 다음 실행에 재시도")
        return 0
    log("API 정상 — 수집 시작")
    n7, full7 = run_v7(cli7, budget)
    log(f"V7 +{n7}조합 ({'완료' if full7 else '예산·오류로 중단'}) · 남은 예산 {budget.left}")
    if budget.left > 0 and self_check(cli8, budget):
        n8, full8 = run_v8(cli8, budget)
        log(f"V8 +{n8}조합 ({'완료' if full8 else '예산·오류로 중단'}) · 남은 예산 {budget.left}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

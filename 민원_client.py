# -*- coding: utf-8 -*-
"""무단투기 민원 데이터 — 공공데이터 자립형 최소 클라이언트.

한국환경공단 부적정처리 의심업체 발굴 과제 전용(무단투기 민원 × 차량 GPS 시공간 매칭).
**외부 모듈 의존 없음**(requests만). 같은 폴더 nara_client.py 와 대칭 구조.

두 계열을 다룬다.

1) 소스탐색(odcloud) — 공공데이터활용지원센터 목록조회 v1 (데이터셋 pk 15077093)
   · https://api.odcloud.kr/api/15077093/v1/{dataset|file-data-list|open-data-list|standard-data-list}
   · 조건검색 문법: cond[필드::연산자]=값 (연산자 EQ/LIKE/LT/LTE/GT/GTE)
   · dataset 검색가능 필드: title, desc, keywords, category_nm, new_category_nm,
     list_type, org_cd, org_nm, ext, created_at, updated_at
   → '무단투기·불법투기·폐기물 민원' 데이터셋을 **기관 전수로 자동 탐색**하는 용도.

2) 민원통계(권익위) — 민원빅데이터 분석정보 API
   · http://apis.data.go.kr/1140100/minAnalsInfoView{N}/{오퍼레이션}
   · 연도판별 View 번호(2026-08-31 공식 기술문서 docx로 전량 확정):
     View5=2022년판(16종, 오퍼레이션명 끝에 '5' 접미사) / View6=2023년판(3종)
     View7=2024년판(2종) / View8=2025년판(2종)
   · 연도판마다 data.go.kr 활용신청이 **별도**다. 신청 안 한 판은 403
     SERVICE_KEY_IS_NOT_REGISTERED_ERROR(코드30)가 난다.
   · 2025년판(View8) 승인 실측 완료(2026-08-31). 과제 핵심인 시군구 단위
     지표는 View7 minPttnStstAddrInfo → 2024년판(15128648) 추가 신청 필요.

인증: data.go.kr serviceKey. 인코딩/디코딩 키 모두 허용(내부 unquote 1회).
      환경변수 DATA_GO_KR_SERVICE_KEY 우선, 없으면 같은 폴더 .env.

⚠ 수집 범위: **공공API·공개 파일데이터만** 사용한다. 로그인이 필요한 화면이나
   국민신문고 개별 민원 본문은 수집 대상이 아니다(요구사항정의서 keco-006 정합).
"""
import os
import time
import urllib.parse

import requests

BASE = os.path.dirname(os.path.abspath(__file__))

ODCLOUD = "https://api.odcloud.kr/api/15077093/v1"
ACRC_HOST = "http://apis.data.go.kr/1140100"
ACRC_VIEW = "minAnalsInfoView8"          # 기본=2025년판(활용신청 승인 실측 2026-08-31)

# 연도판(View)별 오퍼레이션 전량 — 공식 기술문서(2025년판 페이지 첨부 docx,
# FILE_000000003649360)에서 2026-08-31 추출. {오퍼레이션: 설명}
ACRC_OPS = {
    "minAnalsInfoView5": {   # 2022년판(15101903) — 명칭 끝 '5' 접미사 주의
        "minRisingKeyword5": "급증 키워드",
        "minTopNKeyword5": "핵심 키워드(TF-IDF)",
        "minClfcInfo5": "민원분석 분류체계",
        "minStaticsInfo5": "맞춤형통계(기관·분야·성별·연령)",
        "minTimeSeriseView5": "키워드 트렌드(추이)",
        "minSimilarInfo5": "유사사례(공개민원 목록)",
        "minWdcloudInfo5": "연관어 분석",
        "minTodayTopicInfo5": "오늘의 민원이슈",
        "minMofacetInfo5": "민원발생 기관 순위",
        "minMrfacetInfo5": "민원발생 지역 순위",
        "minSearchDocCnt5": "키워드 기반 민원 건수",
        "minMrPopltnRtInfo5": "지역 인구수 대비 민원 현황",
        "minDFTopNKeyword5": "최다 민원 키워드(DF)",
        "minAnalsRptstInfo5": "분석보고서(주간·월간·이슈)",
        "minPttnStstGndrInfo5": "키워드 기반 성별",
        "minPttnStstAgeInfo5": "키워드 기반 연령대",
    },
    "minAnalsInfoView6": {   # 2023년판(15114773)
        "minGndrClsfDocCnt": "성별 민원분야별 건수",
        "minAgeClsfDocCnt": "연령대별 민원분야별 건수",
        "minAddrClsfDocCnt": "민원발생지별 민원분야별 건수",
    },
    "minAnalsInfoView7": {   # 2024년판(15128648)
        "minPttnStstAddrInfo": "키워드 기반 민원발생지별 건수(시군구 단위) — 과제 핵심",
        "minPotholeLatLonInfo": "포트홀 민원 위경도",
    },
    "minAnalsInfoView8": {   # 2025년판(15143948) — 승인 실측 완료
        "minPrcsInstInfo": "키워드 기반 처리기관별 민원건수",
        "minActsSubordinateStatutesInfo": "키워드 기반 법령정보",
    },
}

# 공통 요청 파라미터(기술문서 실측): target=pttn,dfpt,saeol(국민신문고·국민제안·새올),
# searchword, dateFrom/dateTo=yyyyMMdd, mainSubCode=기관코드(View8 minPrcsInstInfo 필수),
# searchOption=B0060005(제목+내용, 기본), omitDuplicate=true/false
ACRC_DEFAULT_PARAMS = {"target": "pttn,dfpt,saeol", "searchOption": "B0060005",
                       "omitDuplicate": "true"}

# probe() 기본 후보 — 현재 뷰의 전 오퍼레이션
VERIFIED_OPS = list(ACRC_OPS[ACRC_VIEW])
CANDIDATE_OPS = VERIFIED_OPS


class PortalApiError(Exception):
    def __init__(self, code, msg):
        self.code, self.msg = code, msg
        super().__init__(f"[{code}] {msg}")


def load_service_key():
    """환경변수 우선 → 같은 폴더 .env 폴백. 폐기물낙찰_수집기와 동일 규약."""
    k = os.environ.get("DATA_GO_KR_SERVICE_KEY")
    if k:
        return k.strip()
    env = os.path.join(BASE, ".env")
    if os.path.exists(env):
        for line in open(env, encoding="utf-8"):
            line = line.strip()
            if line.startswith("DATA_GO_KR_SERVICE_KEY") and "=" in line:
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


class _Base:
    def __init__(self, service_key, *, timeout=30, retries=4):
        if not service_key:
            raise PortalApiError("no-key", "service_key 미설정")
        self.key = urllib.parse.unquote(str(service_key))   # 인코딩/디코딩 키 모두 허용
        self.timeout, self.retries = timeout, retries
        self.calls = 0                                       # 개발계정 호출한도 추적

    def _get(self, url, params, headers=None):
        last = None
        for a in range(self.retries):
            self.calls += 1
            try:
                r = requests.get(url, params=params, headers=headers, timeout=self.timeout)
            except requests.RequestException as e:
                last = e
                time.sleep(1.5 * (a + 1))
                continue
            return r
        raise PortalApiError("network", str(last)[:120])


class OdcloudCatalogClient(_Base):
    """공공데이터포털 카탈로그 검색(odcloud). 민원 원천 데이터셋을 자동 발굴한다."""

    def _call(self, resource, params):
        """serviceKey 쿼리 → 실패 시 Authorization: Infuser 헤더로 1회 재시도.

        odcloud는 두 인증 방식을 모두 받지만 게이트웨이 설정에 따라 한쪽만 통과하는
        경우가 있어 양쪽을 시도한다. 그래도 -4면 해당 API 활용신청 미승인이다.
        """
        url = f"{ODCLOUD}/{resource}"
        attempts = (({"serviceKey": self.key, "returnType": "JSON", **params}, None),
                    ({"returnType": "JSON", **params},
                     {"Authorization": f"Infuser {self.key}"}))
        err = None
        for p, hdr in attempts:
            r = self._get(url, p, hdr)
            try:
                j = r.json()
            except ValueError:
                err = PortalApiError(f"http-{r.status_code}", (r.text or "")[:160].strip())
                continue
            if r.status_code == 200 and "data" in j:
                return j
            code = j.get("code") or j.get("resultCode") or f"http-{r.status_code}"
            msg = j.get("message") or j.get("msg") or j.get("resultMsg") or str(j)[:160]
            err = PortalApiError(code, msg)
        raise err

    def search(self, resource, conds, *, per_page=100, max_pages=20):
        """conds = {"title::LIKE": "무단투기", ...} → 전 페이지 순회 제너레이터."""
        params = {f"cond[{k}]": v for k, v in conds.items()}
        pg = 1
        while pg <= max_pages:
            j = self._call(resource, {**params, "page": pg, "perPage": per_page})
            rows = j.get("data") or []
            for it in rows:
                yield it
            got = pg * per_page
            total = int(j.get("matchCount") or j.get("totalCount") or 0)
            if not rows or got >= total:
                break
            pg += 1

    def search_datasets(self, keyword, **kw):
        """제목 LIKE 검색(파일·API 통합 카탈로그)."""
        return list(self.search("dataset", {"title::LIKE": keyword}, **kw))

    def search_datasets_by_keyword_field(self, keyword, **kw):
        """포털 키워드(태그) LIKE 검색 — 제목에 안 걸리는 건을 보완."""
        return list(self.search("dataset", {"keywords::LIKE": keyword}, **kw))

    def search_open_apis(self, keyword, **kw):
        """오픈API 목록에서 제목 LIKE 검색(연계 자동화 가능 후보 선별용)."""
        return list(self.search("open-data-list", {"title::LIKE": keyword}, **kw))


class MinwonBigdataClient(_Base):
    """국민권익위 민원빅데이터 분석정보 API.

    개별 민원 본문·좌표는 제공되지 않는다(통계·키워드 단위). 지역 단위
    '무단투기 민원 밀도' 보조지표 산출에만 쓴다.
    """

    OK_CODES = ("00", "200", 200, None)      # 연도판마다 정상코드 표기가 다르다(실측)

    def __init__(self, service_key, *, view=ACRC_VIEW, **kw):
        super().__init__(service_key, **kw)
        self.view = view

    def call(self, op, params=None, *, raw=False):
        p = {"serviceKey": self.key, "type": "json", "numOfRows": 100, "pageNo": 1,
             **(params or {})}
        r = self._get(f"{ACRC_HOST}/{self.view}/{op}", p)
        if raw:
            return r
        try:
            j = r.json()
        except ValueError:
            raise PortalApiError(f"http-{r.status_code}", (r.text or "")[:160].strip())
        resp = j.get("response", j)
        hdr = resp.get("header", {}) if isinstance(resp, dict) else {}
        code = hdr.get("resultCode")
        if code not in self.OK_CODES:
            raise PortalApiError(code, hdr.get("resultMsg", ""))
        return resp.get("body", resp) if isinstance(resp, dict) else resp

    def keyword_stats(self, op, searchword, date_from, date_to, **extra):
        """키워드 통계 계열 오퍼레이션 공통 호출 → items 평탄화 list.

        예) View8 minPrcsInstInfo (mainSubCode 필수),
            View7 minPttnStstAddrInfo (시군구 단위 — 2024년판 승인 필요)
        """
        p = {**ACRC_DEFAULT_PARAMS, "searchword": searchword,
             "dateFrom": date_from, "dateTo": date_to, **extra}
        body = self.call(op, p)
        items = (body or {}).get("items") or []
        return [it.get("item", it) for it in items]

    def probe(self, ops=None, params=None):
        """오퍼레이션 후보를 실제 호출해 유효/무효를 실측한다.

        공개 명세(docx)를 열지 않고도 쓸 수 있는 오퍼레이션을 가려내기 위한 진단 기능.
        상태코드가 곧 판정이다 — 200 정상 / 403 오퍼레이션은 실재하나 활용신청 미승인 /
        404 그런 이름의 오퍼레이션 없음.
        반환: [{"op":…, "ok":bool, "exists":bool, "status":…, "note":…, "sample_keys":[…]}]
        """
        out = []
        for op in (ops or CANDIDATE_OPS):
            rec = {"op": op, "ok": False, "exists": False, "status": "",
                   "note": "", "sample_keys": []}
            try:
                r = self.call(op, params, raw=True)
                rec["status"] = r.status_code
                head = (r.text or "")[:200].replace("\n", " ").strip()
                if r.status_code == 403:
                    rec["exists"] = True
                    rec["note"] = "실재 — 활용신청 미승인(승인되면 즉시 사용 가능)"
                elif r.status_code == 404:
                    rec["note"] = "해당 명칭의 오퍼레이션 없음"
                elif r.status_code != 200:
                    rec["note"] = head
                else:
                    try:
                        j = r.json()
                    except ValueError:
                        rec["note"] = "JSON 아님: " + head
                    else:
                        body = j.get("response", j)
                        hdr = body.get("header", {}) if isinstance(body, dict) else {}
                        code = hdr.get("resultCode")
                        if code not in self.OK_CODES:
                            rec["exists"] = True
                            msg = hdr.get("resultMsg", "")
                            rec["note"] = f"resultCode={code} {msg}"
                            if "INVALID_REQUEST_PARAMETER" in str(msg):
                                # 파라미터 검증까지 갔다 = 키 승인은 통과한 상태
                                rec["note"] += " — 승인됨(필수 파라미터 지정 필요)"
                        else:
                            rec["ok"] = rec["exists"] = True
                            b = body.get("body", body) if isinstance(body, dict) else body
                            rec["sample_keys"] = sorted(b.keys())[:15] if isinstance(b, dict) else []
                            rec["note"] = "정상"
            except PortalApiError as e:
                rec["note"] = str(e)[:160]
            out.append(rec)
        return out

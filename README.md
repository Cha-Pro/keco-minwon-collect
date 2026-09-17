# 권익위 민원빅데이터 자동 수집 (GitHub Actions)

PC가 꺼져 있어도 1시간마다 권익위 민원분석정보 API를 확인하고, 살아 있으면 하루 호출 한도(90회) 안에서
수집한 뒤 결과를 이 저장소에 커밋한다.

- 수집 대상
  1. View7 `minPttnStstAddrInfo` 남은 조합(불법매립·폐기물방치 × 2024-01~2026-08) → `결과/무단투기_시군구월별.csv`
  2. View8 `minPrcsInstInfo` + 시도 기관코드 → `결과/민원_시도별처리기관월별.csv` (시도별 실측 통과 후)
- 상태: `state/` (체크포인트·일별 호출 수·실측 결과). 재실행 시 이어서 수집.
- 서비스키: 저장소 Secret `DATA_GO_KR_SERVICE_KEY` (코드·로그에 출력하지 않음)
- 수동 실행: Actions → collect → Run workflow

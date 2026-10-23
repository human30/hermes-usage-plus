# Hermes Usage+

Hermes용 사용량·계정 한도 플러그인. 코어 수정 없이 웹 Dashboard의 Usage/Analytics 상단과 별도 **Usage+** 페이지에 표시합니다. Desktop에는 별도 Usage+ 페이지를 제공합니다.

## 기능

- 모델/provider/billing mode별 main·auxiliary 호출, 입력·출력·reasoning·cache read/write tokens
- 관측된 캐시 적중률: `cache_read / (uncached_input + cache_read + cache_write)`
- 설치 후 측정되는 처리속도: `sum(output_tokens) / sum(API request seconds)`
- 실제 청구액, Hermes 기록의 추정 비용, 최신 공식 단가의 **API 환산 참고액**을 분리
- 서비스가 반환한 계정 quota windows, reset time 및 credit 정보
- 공식 가격 자동 수집과 source별 정상 확인 시각, 갱신 오류, 가격 적용 범위

## 설치

Hermes의 Python 환경에서 실행합니다. 소스를 활성 프로필의 `plugins/usage-plus` 폴더에 둡니다.

```powershell
hermes plugins doctor usage-plus
hermes plugins enable usage-plus
```

웹 Dashboard를 새로고침하면 기존 Usage/Analytics 페이지의 상단과 Usage+ 탭에 표시됩니다. Desktop은 **Capabilities → Plugins → Usage+**를 켜세요. 통합 패키지의 Desktop UI는 별도 opt-in입니다. 전체 앱이나 gateway 재시작은 필요하지 않습니다.

## 공식 가격 자동 갱신 (Windows)

플러그인 디렉터리에서:

```powershell
.\scripts\install-schedule.ps1
Start-ScheduledTask -TaskName Hermes_UsagePlus_Prices
Get-ScheduledTaskInfo -TaskName Hermes_UsagePlus_Prices
```

기본 실행: 매일 **06:15, PC의 로컬 시간**, 사용자 로그인 시. 놓친 실행은 StartWhenAvailable로 처리합니다. 로그인된 사용자 권한으로만 실행하며 PC를 깨우지 않습니다. LLM 호출·추론 비용은 없습니다. 다른 프로필은 별도 `-TaskName`을 지정하세요. Hermes Python 설치 경로가 바뀌면 설치 스크립트를 다시 실행하세요.

가격 소스:
- [Nous 공식 models API](https://inference-api.nousresearch.com/v1/models)
- [OpenRouter 공식 models API](https://openrouter.ai/api/v1/models)
- [OpenAI 공식 pricing](https://developers.openai.com/api/docs/pricing)
- [Anthropic 공식 pricing](https://platform.claude.com/docs/en/about-claude/pricing)

정해진 parser로만 읽습니다. 문서 형식이 바뀌거나 실패하면 source별 마지막 정상 가격과 원래 확인 시각을 유지합니다. 가격이 없으면 **미확인**입니다. 정확한 지원 범위와 단가 조건은 [README-pricing.md](README-pricing.md)를 참고하세요.

## 범위와 한계

- 7/30/90일은 **그 기간에 시작한 세션의 생애 누적값**입니다. 그 기간 중의 모든 소비량 또는 일별 청구 명세가 아닙니다. Hermes 외부 호출은 사용량 집계에 포함되지 않습니다.
- 공식 가격은 현재의 기본 구간 참고 단가입니다. 과거 가격, long-context·Fast·Batch·지역 요율·도구 비용을 재구성하지 않습니다. cache TTL도 source의 명시된 기본 조건만 적용합니다.
- 미확인 모델은 환산 합계에서 제외하며 부분 합계로 표시합니다. 구독의 API 환산액은 실제 청구액이나 환급 가능한 금액이 아닙니다.
- 속도는 첫 토큰 대기까지 포함하며 순수 decode 속도가 아닙니다. 속도는 provider/model 전체 표본 기준이며 billing mode별 측정이 아닙니다. 동일 provider/model의 여러 billing mode 행에서 같은 통계가 보일 수 있습니다.
- 과거 캐시 0은 미보고일 수 있어 0%로 단정하지 않습니다. 과거 속도를 소급 생성하지 않습니다.
- 계정 adapter: Nous, OpenAI Codex, Anthropic, OpenRouter, OpenCode Go. 실제 가용성은 인증과 서비스 응답에 따라 다릅니다. 현재 선택된 credential 기준이며 모든 계정 pool을 합산하지 않습니다.
- Infron, Hive 등 검증된 balance adapter가 없는 서비스는 **미지원**으로 표시합니다. Google 등 가격 parser가 없는 provider도 가격 미확인입니다. 다른 vendor의 가격으로 몰래 대체하지 않습니다.

## 안전 구조

API handler는 async이며 SQL·계정 HTTP 요청은 별도 일회성 Python 프로세스에서 실행합니다. 30초 제한 후 kill/reap, 조회 동시성 2개, 사용량 60초/계정 300초 cache, 실패 시 마지막 정상값을 유지합니다. UI에는 React error boundary가 있습니다.

원본 `state.db`는 읽기 전용이며 메시지 본문을 검색하지 않습니다. 별도 `cache/usage-plus/metrics.db`에는 session/request 식별자, 모델/provider, 시간·token 숫자만 기록합니다. prompt/response/URL/key는 저장하지 않습니다. 쓰기 큐와 표본 수는 제한됩니다.

이것은 OS 보안 sandbox가 아닙니다. 메모리·CPU 고갈에 대한 하드 제한은 없으므로 장애 가능성 0을 보장하지 않습니다. 프로필의 전역 `plugins.isolation` 설정은 변경하지 않습니다.

## 검증

```powershell
python -m unittest discover -s ./tests -p 'test_*.py' -v
node --test ./tests/ui.test.cjs
hermes plugins doctor usage-plus
```

Python 테스트는 Hermes Python 환경이 필요합니다. UI 테스트는 기존 Hermes source의 React/ReactDOM/React Query/jsdom을 사용합니다. 소스 경로가 기본 sibling `../../hermes-agent`와 다르면 `$env:HERMES_CORE_DIR`를 지정하세요. 실제 서비스 fixture와 테스트용 합성 fixture를 구분합니다.

## 비활성화 / 롤백

```powershell
hermes plugins disable usage-plus
Disable-ScheduledTask -TaskName Hermes_UsagePlus_Prices
```

Desktop UI도 Plugins에서 끄세요. 이후 plugin 폴더를 옮겨 보관할 수 있습니다. 원본 대화 DB와 기존 플러그인 설정을 제거하지 않습니다. `cache/usage-plus`는 별도 선택적 로컬 데이터이며 저장소에 포함하지 않습니다.

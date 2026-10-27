# Hermes Usage+

Hermes용 사용량·계정 한도 플러그인. 코어 수정 없이 웹 Dashboard의 Usage/Analytics 상단과 별도 **Usage+** 페이지에 표시합니다. Desktop에는 별도 Usage+ 페이지를 제공합니다.

## 기능

- 모델/provider/billing mode별 main·auxiliary 호출, 입력·출력·reasoning·cache read/write tokens
- 관측된 캐시 적중률: `cache_read / (uncached_input + cache_read + cache_write)`
- 설치 후 측정되는 처리속도: `sum(output_tokens) / sum(API request seconds)`
- 실제 청구액, Hermes 기록의 추정 비용, 최신 공식 단가의 **API 환산 참고액**을 분리
- 서비스가 반환한 계정 quota windows, reset time 및 credit 정보
- 조회 불가·미지원·실패 계정 카드는 숨기고, 표시할 계정이 없으면 해당 영역도 숨김
- 경유 제공자/결제 방식 미확인 또는 단가 누락 시, 식별된 모델 원제공자의 공식 API 단가로 참고 환산
- 가격 출처 열·링크·버전 문구를 화면에서 제거하고, 검증용 출처·단가 metadata는 backend에 보존
- 공식 가격 자동 수집과 source별 정상 확인 시각, 갱신 오류, 가격 적용 범위
- 측정 기준·주의 문구는 표와 카드 사이에 끼우지 않고 화면 최하단 **안내 및 참고사항** 섹션에 모아 표시. 데이터 조회 상태(불러오는 중·오류·오래된 데이터)만 상단에 남김

## Install from Git — 권장 설치 방법

**Hermes Desktop → Capabilities → Plugins → Install from Git**에 아래 주소를 붙여 넣으세요.

```text
https://github.com/human30/hermes-usage-plus.git
```

이 저장소는 **Agent + Desktop 통합 패키지**입니다. 별도 빌드나 파일 복사가 필요하지 않습니다. Windows Hermes Desktop v0.22.2에서 화면과 실제 backend 조회를 확인했습니다.

1. 설치 대화상자가 저장소를 검사할 때까지 기다립니다.
2. **Agent와 Desktop을 모두 선택**하고, Agent를 설치할 연결·프로필이 맞는지 확인합니다.
3. 설치 후 활성화 옵션을 선택하고 설치를 승인합니다.
4. 설치 후 **Capabilities → Plugins → Filters → Installed**에서 `usage`를 검색합니다. 공식 온라인 카탈로그에는 등록되지 않았으므로 **Installed** 목록에서 찾으세요.
5. **Usage+ 카드의 제목을 눌러 상세 화면**을 엽니다. **`Agent: Usage+`와 `Desktop: Usage+`를 각각 ON**으로 확인합니다.
6. 왼쪽 사이드바의 **Usage+**를 엽니다. 7일·30일·90일 선택과 새로고침을 사용할 수 있습니다.

> **중요:** 바깥 Usage+ 카드의 스위치가 ON이어도 상세 화면의 Desktop 스위치는 OFF일 수 있습니다. 메뉴가 안 보일 때는 재설치보다 **`Desktop: Usage+`**부터 확인하세요. 두 스위치는 서로 다른 실행 부분을 제어합니다.

### 공개 저장소 접근

이 저장소는 **공개**이며, 소스 다운로드와 Install from Git에 GitHub 로그인이나 개인 접근 토큰이 필요하지 않습니다. 터미널에서 접근 여부를 확인하려면:

```powershell
git ls-remote https://github.com/human30/hermes-usage-plus.git HEAD
```

명령이 commit SHA를 반환하면 저장소에 접근할 수 있습니다. 원격 backend를 사용한다면 Desktop 기기와 Agent 서버가 각각 GitHub에 연결될 수 있어야 합니다. 서비스별 계정 한도 조회에 필요한 Hermes의 기존 서비스 인증은 저장소 다운로드 인증과 별개이며, 토큰을 저장소 URL이나 채팅에 붙여 넣지 마세요.

### 다른 PC / Mac에서 원격 Windows backend에 연결하는 경우

- **Agent**는 데이터가 있는 gateway 서버·프로필에 설치합니다.
- **Desktop**은 지금 화면을 여는 각 기기에 설치합니다.
- Windows 서버에 Agent가 이미 설치되어 있다면 다른 기기의 Install from Git에서는 **Desktop만 선택**할 수 있습니다. 기존 Agent를 강제로 덮어쓸 필요가 없습니다.
- 다른 기기에서도 상세 화면의 Desktop 스위치를 켜야 합니다. 화면 파일만 설치하고 Agent backend가 없거나 꺼져 있으면 조회할 수 없습니다.
- 가격 갱신 예약 작업은 **Agent 서버에서 별도로 설정**합니다. Git 설치만으로 Windows 예약 작업을 자동 등록하지 않습니다.

### CLI로 Agent 설치

Hermes가 설치된 서버의 터미널에서 실행합니다. 이 명령만으로 다른 기기의 Desktop UI까지 설치되지는 않습니다.

```powershell
hermes plugins install https://github.com/human30/hermes-usage-plus.git --enable
hermes plugins doctor usage-plus
```

웹 Dashboard는 새로고침하면 Usage/Analytics 상단과 Usage+ 탭에서 사용할 수 있습니다. Desktop 메뉴가 안 보이면 Plugins 화면의 **Rescan**을 누른 뒤 위의 두 스위치를 확인하세요. 플러그인 활성화는 앱/gateway 전체 재시작 없이 가능합니다.

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
- [Z.ai 공식 pricing](https://docs.z.ai/guides/overview/pricing) — `glm-5.3-flash`
- [StepFun 공식 pricing](https://platform.stepfun.ai/docs/en/guides/pricing/details) — Step 5 Preview, 3.7 Flash, 3.5 Flash/2603, 1o Turbo Vision
- [Meta 공식 pricing](https://dev.meta.ai/docs/pricing-rate-limits) — Muse Spark 1.1/1.2/1.3 Standard, 1.2/1.3 Contributor, Muse Wonder 1.0
- [DeepSeek 공식 pricing](https://api-docs.deepseek.com/quick_start/pricing/) — Flash/Pro의 PEAK 참고 단가
- [OpenCode Console 공식 pricing](https://opencode.ai/v2/docs/console/models/) — OpenCode 경로 우선; Go는 Console API 참고액이며 구독 청구액 아님
- [Hive 공식 pricing](https://thehive.ai/pricing) — Hive 경로의 게시된 활성 단가; 취소선 정가와 구분
- [Google Gemini 공식 pricing](https://ai.google.dev/gemini-api/docs/pricing) — Gemini 4 Argon, 3.8/3.6 Flash, 3.5/3.1 Flash-Lite, 3.1 Pro Preview, 3 Flash Preview, 2.5 Pro/Flash/Flash-Lite의 Standard 텍스트 단가

정해진 parser로만 읽습니다. 문서 형식이 바뀌거나 실패하면 source별 마지막 정상 가격과 원래 확인 시각을 유지합니다. 가격이 없으면 **미확인**입니다. 정확한 지원 범위와 단가 조건은 [README-pricing.md](README-pricing.md)를 참고하세요.

## 범위와 한계

- 7/30/90일은 **그 기간에 시작한 세션의 생애 누적값**입니다. 그 기간 중의 모든 소비량 또는 일별 청구 명세가 아닙니다. Hermes 외부 호출은 사용량 집계에 포함되지 않습니다.
- 공식 가격은 현재의 기본 구간 참고 단가입니다. 과거 가격, long-context·Fast·Batch·지역 요율·도구 비용을 재구성하지 않습니다. cache TTL도 source의 명시된 기본 조건만 적용합니다.
- 유료 경로는 해당 제공자의 공식 단가를 우선하며, 무료·가격 미공개 경로는 모델 원제공자의 공식 API 단가로 환산합니다. `900k` 같은 GPT context variant, Fast variant, Gemini effort variant는 정확히 게시된 별도 단가가 없으면 표준 모델 참고가를 사용합니다. 원래 모델명과 실제 청구액(무료 0 포함)은 변경하지 않습니다.
- 미확인 모델은 환산 합계에서 제외하며 부분 합계로 표시합니다. 구독의 API 환산액은 실제 청구액이나 환급 가능한 금액이 아닙니다.
- 속도는 첫 토큰 대기까지 포함하며 순수 decode 속도가 아닙니다. 속도는 provider/model 전체 표본 기준이며 billing mode별 측정이 아닙니다. 동일 provider/model의 여러 billing mode 행에서 같은 통계가 보일 수 있습니다.
- 과거 캐시 0은 미보고일 수 있어 0%로 단정하지 않습니다. 과거 속도를 소급 생성하지 않습니다.
- DeepSeek 원제공자 환산은 **PEAK 단가 기준 참고액**입니다. 사용 시간대·중국 공휴일을 소급 적용한 실제 청구액이 아닙니다. 날짜 없는 V4.1-Flash는 현재 공식 표의 같은 generation 참고 단가를 사용하며, 다른 날짜·generation 모델로 임의 치환하지 않습니다.
- 계정 adapter: Nous, OpenAI Codex, Anthropic, OpenRouter, OpenCode Go. 실제 가용성은 인증과 서비스 응답에 따라 다릅니다. 현재 선택된 credential 기준이며 모든 계정 pool을 합산하지 않습니다.
- Infron, Hive 등 검증된 balance adapter가 없는 서비스의 계정 카드는 표시하지 않습니다. 사용량 행 자체는 보존합니다. 공식 단가로 API 참고액만 계산하며 실제 청구액은 바꾸지 않습니다. 가격표·모델 대응·필수 cache 단가가 확인되지 않으면 환산 합계에서 제외합니다.

## 안전 구조

API handler는 async이며 SQL·계정 HTTP 요청은 별도 일회성 Python 프로세스에서 실행합니다. 30초 제한 후 kill/reap, 조회 동시성 2개, 사용량 60초/계정 300초 cache, 실패 시 마지막 정상값을 유지합니다. UI에는 React error boundary가 있습니다.

원본 `state.db`는 읽기 전용이며 메시지 본문을 검색하지 않습니다. 별도 `cache/usage-plus/metrics.db`에는 session/request 식별자, 모델/provider, 시간·token 숫자만 기록합니다. prompt/response/URL/key는 저장하지 않습니다. 쓰기 큐와 표본 수는 제한됩니다.

공개 저장소에는 소스·문서·합성 테스트 데이터만 포함합니다. 사용자의 인증 파일, 대화 DB, 실제 사용량·잔액 응답, 가격 캐시, 로그, 백업은 포함하지 않습니다. 실행 시 사용자 데이터는 각 설치 환경에서 처리하며, 공식 가격 문서 조회와 기존 Hermes 계정 adapter의 서비스 조회 외에 개발자 서버로 전송하는 기능은 없습니다. `.gitignore`는 일반적인 인증 파일·키·로컬 데이터 경로를 제외하지만, 이미 추적된 파일이나 과거 커밋의 민감정보를 자동으로 제거하지는 않습니다.

이것은 OS 보안 sandbox가 아닙니다. 메모리·CPU 고갈에 대한 하드 제한은 없으므로 장애 가능성 0을 보장하지 않습니다. 프로필의 전역 `plugins.isolation` 설정은 변경하지 않습니다.

## 검증

GitHub 저장소를 대상으로 Hermes의 실제 `probePluginRepo`에서 **Agent + Desktop** 탐지를 확인했습니다. 별도 임시 디렉터리에 `installDesktopPluginFromGit`으로 Desktop을 설치하고 원본 UI 파일·통합 패키지 marker를 확인했으며, 격리된 `HERMES_HOME`에서 CLI Agent 신규 설치와 `hermes plugins doctor`도 통과했습니다. 이 설치 검증은 기존 운영 설정을 덮어쓰지 않습니다. 인증된 Windows 환경에서 검증했으며 다른 기기의 GitHub 인증이나 Mac 설치 완료를 의미하지는 않습니다.

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

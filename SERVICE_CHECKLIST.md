# TA Builder 서비스 점검 체크리스트

> 이 프로그램 관련 작업 요청 시, 아래 항목을 코드 기준으로 확인 후 보고한다.

---

## A. 핵심 기능 — 강의 자료 컨텍스트 주입 (RAG)

| # | 점검 항목 | 확인 방법 | 현재 상태 |
|---|-----------|-----------|-----------|
| A1 | PDF 페이지별 추출 후 `pdf_pages`가 세션에 저장되는가 | `app.py:69` `create_session(agents, pdf_pages=all_pages, pdf_bytes_map=pdf_bytes_map)` | **정상** |
| A2 | `stream_chat()`에서 RAG 검색 후 관련 페이지만 전달되는가 | `chat.py:253` `search(tfidf_index, user_message, top_k=3)` | **정상** |
| A3 | 컨텍스트 한도 | RAG 구조로 top-k=3 페이지만 주입 — 제한 개념 무의미 | **해결 (RAG로 구조적 해소)** |
| A4 | `/agents/import` 후 채팅 시 강의 자료 없음 | import는 에이전트 정의만 복원, pdf_bytes 없음 — 설계상 허용 | **허용된 동작 (치명 버그 아님)** |

---

## B. 모델 (gemini-2.5-flash @ Google AI)

| # | 점검 항목 | 확인 방법 | 현재 상태 |
|---|-----------|-----------|-----------|
| B1 | 모델 ID `gemini-2.5-flash`가 유효한가 | `gemini_client.py:11` `MODEL = "gemini-2.5-flash"` | **정상** — Google AI 공식 지원 |
| B2 | 불필요한 태그(`<think>` 등)가 응답에 포함되지 않는가 | Gemini는 해당 없음, 필터 코드 제거됨 | **정상** |
| B3 | `top_p=0.8` 파라미터가 허용되는가 | `chat.py:305` `GenerateContentConfig(top_p=0.8)` | **정상** — Gemini API 정식 지원 |

---

## C. 에이전트 생성 품질

| # | 점검 항목 | 확인 방법 | 현재 상태 |
|---|-----------|-----------|-----------|
| C1 | 생성된 `system_prompt`가 500~1000자 범위인가 | 에이전트 생성 후 JSON 확인 | 프롬프트 지침 적용됨, 실제 길이는 LLM 의존 |
| C2 | `system_prompt`에 강의 자료 핵심 내용이 반영되는가 | 생성된 system_prompt 텍스트 검토 | 검증 필요 |
| C3 | JSON 파싱 오류 시 사용자에게 명확한 안내가 가는가 | `generator.py` `json.JSONDecodeError` 처리 | **정상** (HTTPException 반환) |
| C4 | CJK 필터 후 텍스트가 의도치 않게 비어버리는 경우 | `generator.py` CJK 필터 | **잠재적 문제** — 중국어 강의 자료 업로드 시 내용 소실 가능 |

---

## D. 세션 관리

| # | 점검 항목 | 확인 방법 | 현재 상태 |
|---|-----------|-----------|-----------|
| D1 | Render 재배포/슬립 후 세션 복원 | `chat.py:_restore()` + 대화 변경 시 `_persist()` 호출 | **정상** — `pdf_pages`/대화 히스토리 복원, 이미지(`pdf_bytes_map`)는 소실 후 텍스트 RAG fallback |
| D2 | 세션 만료/cleanup 로직 없음 | `chat.py` sessions dict | **잔존 위험** — 장기 운영 시 파일/메모리 증가 가능 |
| D3 | 대화 히스토리 `MAX_HISTORY=20` 제한 | `chat.py:128` | **정상** |

---

## E. PDF 처리

| # | 점검 항목 | 확인 방법 | 현재 상태 |
|---|-----------|-----------|-----------|
| E1 | 이미지 기반(스캔) PDF 처리 | PyMuPDF + Gemini 멀티모달 이미지 주입 | **부분 해결** — 도표·수식 이미지는 Gemini가 이해. 순수 스캔 텍스트 OCR은 미지원 |
| E2 | generator/chat 컨텍스트 불일치 | RAG 구조로 동일한 PageData 기반 운영 | **해결** — 불일치 개념 소멸 |
| E3 | 다중 PDF 업로드 시 파일별 구분 저장 | `app.py` `pdf_bytes_map`, `PageData.filename` | **정상** — 파일명 기준으로 구분 |
| E4 | 서버 재시작 후 이미지 주입 불가 | `pdf_bytes_map`은 직렬화 제외 → 텍스트 RAG만 동작 | **알려진 한계 (설계상 허용)** |

---

## F. API 키 및 인프라

| # | 점검 항목 | 확인 방법 | 현재 상태 |
|---|-----------|-----------|-----------|
| F1 | Render 환경변수에 `GOOGLE_API_KEY`(권장) 또는 `GEMINI_API_KEY` 설정 여부 | Render 대시보드 Environment + `/health/config` | **외부 확인 필요** |
| F2 | 429 응답 시 재시도 동작 | `generator.py`, `chat.py` — 60초 대기 × 최대 3회 | **정상** |
| F3 | 로컬 `.env`에 `GOOGLE_API_KEY` 설정 | `.env` 파일 | **정상** |

---

## 우선순위 정리

| 우선순위 | 항목 | 영향 |
|---------|------|------|
| ~~완료~~ | ~~A1~A4 — 강의 자료 주입~~ | RAG 구조로 전면 해결 |
| ~~완료~~ | ~~B1~B3 — 모델 전환~~ | Gemini 2.5 Flash 정상 동작 |
| ~~완료~~ | ~~E2 — 컨텍스트 불일치~~ | RAG로 구조적 해소 |
| **즉시 확인** | F1 — Render 환경변수 `GOOGLE_API_KEY` 또는 `GEMINI_API_KEY` | 미설정 시 서비스 전체 불가 |
| **장기 개선** | D2 — 세션 cleanup | 장기 운영 시 메모리/파일 증가 |
| **장기 개선** | E1 — 스캔 PDF OCR | 이미지 전용 PDF 텍스트 추출 불가 |
| **저위험** | C4 — CJK 필터 부작용 | 중국어 강의 자료 사용 시만 해당 |

---

*최종 업데이트: 2026-03-06 (세션 대화 영속화 반영)*

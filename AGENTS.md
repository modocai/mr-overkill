develop 브랜치 외에서 작업할떄는 항상 커밋/푸시 해서 작업을 종료할것.

## 커밋 메시지

기계적인 나열 대신, **왜 이 변경이 필요한지** 의도를 담아 쓸 것.

나쁜 예:
- `fix(review-loop): eliminate temp file leak and unify error-code handling`
- `fix(review-loop): distinguish file-not-found from parse error, remove global pollution`

좋은 예:
- `fix: 조기 종료 시 temp 파일이 남는 문제 해결`
- `fix: Codex 실패와 JSON 파싱 실패를 구분할 수 있도록 exit code 분리`
- `refactor: 메인 루프가 400줄 넘어 읽기 어려워서 헬퍼 함수로 분리`

원칙:
- 제목은 한국어로, 변경의 동기/맥락을 담는다
- conventional commit prefix (fix, feat, refactor 등)는 유지
- 본문이 필요하면 빈 줄 후 상세 설명 추가

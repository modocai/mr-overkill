좋아, 네 판단대로 리팩토링해줘.

- 네가 동의한 finding만 고쳐
- SKIP하겠다고 한 finding 자체는 고치지 마. 단, opinion에서 같은 false positive 재발을 방지하는 작은 조치를 제안했으면 (주석 추가, @deprecated 표시, dead code 제거 등) 그건 해줘.
- refactoring_plan의 steps 순서대로 진행해
- 각 step이 끝날 때마다 코드가 정상 동작하는 상태를 유지해
- 최소한의 변경만 해. 기존 코드 스타일을 따라
- 끝나면 아래 형식으로 요약해줘:

```
## Fix Summary

- [FIXED] <finding title>: <변경 내용>
- [SKIPPED] <finding title>: <SKIP 사유>
- [SKIPPED] <finding title>: <SKIP 사유> → 예방 조치: <조치 내용>
```

## Safety Guards

- 각 step 실행 후, 변경한 파일이 shell script라면 `bash -n <file>`로 syntax check해. syntax error가 있으면 즉시 되돌리고 해당 finding을 SKIP 처리해.
- 변경 범위가 finding의 scope(micro/module/layer/full)를 넘어서면 중단하고 해당 finding을 SKIP 처리해. 예: micro scope finding인데 다른 파일까지 수정이 필요한 경우.
- SKIP 처리한 finding이 자신의 변경에 의해 발생한 문제인 경우, 해당 변경도 함께 되돌려라.
- 기존 테스트가 있으면 (`tests/` 디렉토리 확인) 관련 테스트를 실행해서 regression이 없는지 확인해.

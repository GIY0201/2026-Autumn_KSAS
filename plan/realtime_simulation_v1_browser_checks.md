# 실시간 비행 시뮬레이션 Browser 확인

2026-09-08. 로컬 8088 In-app Browser에서 수행한 실제 조작과 상태 API 대조 기록이다. FPS는 브라우저 rolling diagnostics의 표본이며 장시간 최저 성능 보장이 아니다.

| 실행 run ID | 확인 |
|---|---|
| 7381752e-4043-4fd3-a746-a59a506892a5 | CPU GRU 연결, VTOL 지상 W 이륙, T 즉시 CTOL 조작·연속 가속, 다시 T로 VTOL 감속·hover, 예측 경로 PNG 저장 |
| 049074b8-1250-4776-9008-12612135114a | 시나리오 16대·모델 없음, 25.34초 상태에서 객체 16개, 약 60.00 FPS, state 요청 표본 163.2 ms |
| 34c35d42-5ffb-4ab1-8d1c-d4a186981782 | 시나리오 16대·실제 GRU, 22.58초 상태에서 객체 16개·성숙 horizon 평가, 약 60.00 FPS, state 요청 45.8 ms, 추론 표본 4.37 ms, skipped 0 |
| 6fd5e920-e2fe-4258-b358-facc9efdb8f1 | 헬기 공중 1 m에서 S 지속, landed·고도 0·terminal false; 약 60.00 FPS, 키 전송 표본 5.4 ms |
| 1af84ef5-55af-4a0d-bae7-3417dea4a830 | 헬기 공중 20 m에서 S 지속, crashed·고도 0·terminal true, 실행 completed |
| 1c75f4ca-65e4-4cd0-8fe2-9ccd643b5b2d | 최종 서버에서 고정익 지상 W 활주 후 ↑ 이륙, 속도 30 m/s·고도 188.45 m 상승 상태, CPU GRU 예측, Pause·PNG·종료 |

GRU는 `20260908T111823_97a15ae85379 / best`, 명시적 CPU를 사용했다. 단일 기체 모델 연결 시에도 약 60 FPS를 확인했다. 16대 표본의 키 전송 왕복 diagnostics는 85.7–93.2 ms였다. 이 값은 물리 적용 시점의 end-to-end 계측이 아니며 화면 frame rate, 요청 왕복, 추론 latency를 구별한다. 브라우저 diagnostics 오류는 각 측정에서 0이었다.

실제 숫자 input의 화살표 조절로 1→16→1 기체 수를 변경했다. 자동화 도구의 fill만으로 native change가 발생하지 않는 경우가 있어 실제 키 조절과 생성된 row 수를 대조했다. 직접 조종 대상 1대 실행은 객체 1개로 확인했다.

고정 시간 운동·방향별 키·대상 변경·정보 경계·미래 시각 평가·replay는 자동 테스트와 offline 실행 기록으로 별도 확인한다. 위 브라우저 시험은 모든 시나리오 조합이나 모든 checkpoint, CUDA 지원 검증이 아니다.

최종 실행은 약 60.01 FPS, state 요청 11.9 ms, 키 전송 12.3 ms를 표본으로 기록했다. dataset `a34a96f7-a807-4e80-aee7-579d50aeffb3`의 receipt는 `complete`, error null, inference ready·skipped 0이다. dataset/visualization의 files.csv에 기재된 파일 17개의 SHA-256을 전부 다시 계산하여 일치를 확인했다. receipt SHA-256: `258b5f22d9179efbd876217c2133d908791c46f86e061fcfd579b11963f641d2`.

최종 고정익 수동 실행의 예측은 총 370개 중 전체 horizon 확보 295개, 미완료 75개였다. truth 기준 ADE 42.87794 m / FDE 97.10042 m, 미래 관측 기준 ADE 42.90185 m / FDE 97.10090 m를 별도로 저장했다. VTOL 데이터로 학습한 checkpoint의 수동 고정익 궤적 결과이므로 일반화 성능이나 모델 채택 기준으로 해석하지 않는다.

# CryptoLab R2-SHADOW — QA FIX-001

**OFFLINE_PREPARED / VPS_PENDING.** Parent: a2f1820d9b6da02c3f24cc8a5c1680016ea2b54e. Исходник R2:815d6884980791309cb37770ac96a9a93a2c48c0.

1. Текущая MTM-телеметрия использует последний полный набор котировок из исходного Observer.markets() и один read-only snapshot остатков. Дополнительных HTTP/poll, финансовых записей и изменений истории нет. Source quote age<=120 секунд; timestamp не продлевается экспортом, при пропуске/устаревании null. Тест воспроизводит весь час:60 poll,24 исходных часовых Equity, актуальные оценки во всех60 проверенных минутах.
2. Запуск требует host/boot/path/cohort/code-bound evidence packet с pinned SHA256. Проверяются Linux lock/SQLite crash trace, фактический functional replay из9 полных raw bodies на24 профиля и измеренные/текущие ресурсы. Boolean-флаги без результатов не проходят. Явное согласие пользователя отдельно обязательно. Фактические VPS-результаты пока отсутствуют.
3. Nonzero recovery:132 ORDER events, ненулевые операции всех24 профилей, os._exit до и после COMMIT. Все checkpoints/events/decisions совпадают с контрольным прогоном, retry идемпотентен. Полные состояния и базы доступны в архиве evidence.
4. Повторены37 исходных,34 прежних и49 QA-тестов:120 passed,0 failed/errors/skipped; сеть запрещена.
5. R2/T01/оригинальный Observer/24 профиля/R2-DEMO не изменялись. Telemetry schema/wire-format не изменены; TORG_BOT не обновлялся.

Воспроизведение: `python research/hunter001_dg001/r2_shadow/verify.py`.

Доказательства:
- [Полная проверка](evidence/VERIFICATION.json), [QA verdict](evidence/QA_FIX_001.json).
- [Ненулевой recovery](evidence/RECOVERY_NONZERO.json), [полные inputs/ledgers/checkpoints](evidence/RECOVERY_NONZERO_INPUTS_AND_LEDGERS.zip).
- JUnit: [original](evidence/original_tests.xml), [shadow](evidence/shadow_tests.xml), [QA](evidence/qa_fix_tests.xml).
- [Frozen manifest](FROZEN_MANIFEST.json), [runtime manifest](evidence/BUNDLE_MANIFEST.json), [SHA256](SHA256SUMS.txt).
- [Архитектура](R2_SHADOW_ARCHITECTURE.md), [проверки](R2_SHADOW_VERIFICATION.md), [план](R2_SHADOW_DEPLOYMENT_PLAN.md).

Положительные readiness tests — синтетические локальные fixtures, не реальные VPS PASS. Все разрешения false/null; отдельный collector с разрешающей policy и daemon не запускались. SSH/VPS/systemd/install/service actions:0; real/Demo orders:0. Экономический статус INCONCLUSIVE / REAL CAPITAL NO-GO.

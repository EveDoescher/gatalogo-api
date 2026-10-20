# Inventário estático de qualidade

Gerado por `scripts/audit_quality.py` na API. Candidatos exigem revisão; ausência de referência não prova inutilidade.

Arquivos versionados ou novos (respeitando .gitignore): 166. Fontes Python/Dart: 129.

## Distribuição das fontes

| Diretório | Arquivos | Linhas |
|---|---:|---:|
| alembic | 1 | 55 |
| alembic/versions | 14 | 617 |
| app | 9 | 1315 |
| app/models | 3 | 297 |
| app/prompts | 2 | 34 |
| app/routers | 11 | 3808 |
| app/services | 39 | 5519 |
| scripts | 12 | 1618 |
| tests | 38 | 5384 |

## Fontes com mais de 500 linhas

| Arquivo | Linhas |
|---|---:|
| `app/routers/social.py` | 1475 |
| `tests/test_quintal_production.py` | 769 |
| `app/services/quintal_production.py` | 654 |
| `app/routers/product.py` | 535 |

## Dependências entre rotas Python

- `app/routers/public_search.py:57` → `app.routers.product`
- `app/routers/quintal.py:57` → `app.routers.habitat`
- `app/routers/quintal.py:72` → `app.routers.habitat`

## Corpos de funções Python idênticos


## Dart fora do grafo de lib/main.dart

Inclui componentes acessíveis somente por testes ou ferramentas. Confirmar antes de remover.


## Arquivos rastreados que exigem classificação

- `docs/session_rotation_review.patch`

## Inventário completo das fontes

| Arquivo | Linhas |
|---|---:|
| `alembic/env.py` | 55 |
| `alembic/versions/20260827_01_accounts_sync.py` | 105 |
| `alembic/versions/20260827_02_cat_avatars.py` | 47 |
| `alembic/versions/20260828_03_recognition_social.py` | 139 |
| `alembic/versions/20260908_04_recognition_evidence.py` | 27 |
| `alembic/versions/20260908_05_product.py` | 19 |
| `alembic/versions/20260916_06_chat_rescue.py` | 40 |
| `alembic/versions/20260922_07_report_poster.py` | 16 |
| `alembic/versions/20260923_08_habitat.py` | 58 |
| `alembic/versions/20260923_09_habitat_yard.py` | 23 |
| `alembic/versions/20260924_10_habitat_care.py` | 23 |
| `alembic/versions/20260927_11_progression.py` | 58 |
| `alembic/versions/20261006_12_quintal_production.py` | 28 |
| `alembic/versions/20261009_13_quintal_build_start.py` | 17 |
| `alembic/versions/20261018_14_session_rotation.py` | 17 |
| `app/__init__.py` | 0 |
| `app/config.py` | 144 |
| `app/database.py` | 35 |
| `app/dependencies.py` | 17 |
| `app/exceptions.py` | 35 |
| `app/main.py` | 250 |
| `app/models/__init__.py` | 0 |
| `app/models/analysis.py` | 251 |
| `app/models/vision.py` | 46 |
| `app/persistence_models.py` | 374 |
| `app/prompts/__init__.py` | 0 |
| `app/prompts/coat_analysis.py` | 34 |
| `app/quintal_models.py` | 76 |
| `app/routers/__init__.py` | 0 |
| `app/routers/auth.py` | 369 |
| `app/routers/habitat.py` | 327 |
| `app/routers/product.py` | 535 |
| `app/routers/progression.py` | 165 |
| `app/routers/public_search.py` | 60 |
| `app/routers/quintal.py` | 79 |
| `app/routers/social.py` | 1475 |
| `app/routers/sync.py` | 394 |
| `app/routers/vision.py` | 272 |
| `app/routers/websocket.py` | 132 |
| `app/schemas.py` | 384 |
| `app/services/__init__.py` | 0 |
| `app/services/analysis_cache.py` | 49 |
| `app/services/analysis_service.py` | 490 |
| `app/services/audience.py` | 22 |
| `app/services/auth_service.py` | 205 |
| `app/services/avatar_generation_service.py` | 88 |
| `app/services/avatar_service.py` | 250 |
| `app/services/color_service.py` | 224 |
| `app/services/conversations.py` | 55 |
| `app/services/dense_features.py` | 111 |
| `app/services/fcm_service.py` | 136 |
| `app/services/fusion_service.py` | 73 |
| `app/services/habitat_encounters.py` | 115 |
| `app/services/habitat_interactions.py` | 145 |
| `app/services/habitat_pricing.py` | 16 |
| `app/services/habitat_service.py` | 256 |
| `app/services/milestones.py` | 226 |
| `app/services/missing_posters.py` | 19 |
| `app/services/preprocessing_service.py` | 83 |
| `app/services/progression.py` | 333 |
| `app/services/quality_service.py` | 43 |
| `app/services/quintal_catalog.py` | 69 |
| `app/services/quintal_games.py` | 108 |
| `app/services/quintal_notifications.py` | 40 |
| `app/services/quintal_production.py` | 654 |
| `app/services/quintal_progress.py` | 58 |
| `app/services/quintal_push.py` | 89 |
| `app/services/quintal_return.py` | 53 |
| `app/services/quintal_rewards.py` | 57 |
| `app/services/quintal_scheduler.py` | 80 |
| `app/services/realtime_events.py` | 67 |
| `app/services/recognition_features.py` | 127 |
| `app/services/recognition_service.py` | 263 |
| `app/services/report_presenter.py` | 26 |
| `app/services/segmentation_service.py` | 351 |
| `app/services/storage_service.py` | 121 |
| `app/services/vision_jobs.py` | 28 |
| `app/services/vision_worker.py` | 227 |
| `app/services/websocket_manager.py` | 162 |
| `scripts/audit_quality.py` | 159 |
| `scripts/compare_cats.py` | 67 |
| `scripts/evaluate_oxford_pet_segmentation.py` | 196 |
| `scripts/export_quintal_visual_fixture.py` | 49 |
| `scripts/inspect_photo_gallery.py` | 54 |
| `scripts/prepare_cat_model.py` | 78 |
| `scripts/prepare_own_cats_benchmark.py` | 166 |
| `scripts/prepare_oxford_pet_benchmark.py` | 167 |
| `scripts/reid_benchmark.py` | 213 |
| `scripts/reindex_recognition.py` | 32 |
| `scripts/run_own_cats_calibration.py` | 194 |
| `scripts/simulate_quintal_balance.py` | 243 |
| `tests/conftest.py` | 13 |
| `tests/test_account_settings.py` | 161 |
| `tests/test_avatar_presets.py` | 71 |
| `tests/test_benchmark_helpers.py` | 69 |
| `tests/test_chat_rescue.py` | 135 |
| `tests/test_color_and_fusion.py` | 104 |
| `tests/test_dense_features.py` | 100 |
| `tests/test_exceptions.py` | 30 |
| `tests/test_feed_models.py` | 70 |
| `tests/test_feed_pagination.py` | 39 |
| `tests/test_gamification.py` | 114 |
| `tests/test_habitat.py` | 423 |
| `tests/test_habitat_encounters.py` | 143 |
| `tests/test_message_reactions.py` | 14 |
| `tests/test_milestones_v2.py` | 216 |
| `tests/test_missing_poster_flow.py` | 87 |
| `tests/test_preprocessing_quality.py` | 94 |
| `tests/test_product.py` | 176 |
| `tests/test_progression.py` | 292 |
| `tests/test_progression_migration.py` | 40 |
| `tests/test_quintal_activity_calendar.py` | 69 |
| `tests/test_quintal_live_api.py` | 401 |
| `tests/test_quintal_postgres.py` | 176 |
| `tests/test_quintal_production.py` | 769 |
| `tests/test_quintal_push.py` | 147 |
| `tests/test_quintal_rewards.py` | 88 |
| `tests/test_quintal_router.py` | 130 |
| `tests/test_quintal_routines.py` | 134 |
| `tests/test_quintal_scheduler.py` | 60 |
| `tests/test_realtime_events.py` | 29 |
| `tests/test_recognition.py` | 195 |
| `tests/test_recognition_database.py` | 224 |
| `tests/test_screen_corrections.py` | 86 |
| `tests/test_segmentation_service.py` | 150 |
| `tests/test_social_graph.py` | 181 |
| `tests/test_social_posts.py` | 60 |
| `tests/test_storage_service.py` | 42 |
| `tests/test_websocket_manager.py` | 52 |

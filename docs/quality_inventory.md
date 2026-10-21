# Inventário estático de qualidade

Gerado por `scripts/audit_quality.py` na API. Candidatos exigem revisão; ausência de referência não prova inutilidade.

Arquivos versionados ou novos (respeitando .gitignore): 211. Fontes Python/Dart: 166.

## Distribuição das fontes

| Diretório | Arquivos | Linhas |
|---|---:|---:|
| alembic | 1 | 55 |
| alembic/versions | 14 | 617 |
| app | 10 | 1346 |
| app/models | 3 | 297 |
| app/prompts | 2 | 34 |
| app/routers | 16 | 3431 |
| app/services | 56 | 7883 |
| app/services/quintal_commands | 8 | 735 |
| scripts | 14 | 1901 |
| tests | 42 | 6356 |

## Fontes com mais de 500 linhas

| Arquivo | Linhas |
|---|---:|
| `tests/test_quintal_production.py` | 769 |
| `app/routers/product.py` | 502 |

## Dependências entre rotas Python

- `app/routers/social.py:8` → `app.routers.social_profiles`
- `app/routers/social.py:15` → `app.routers.social_profiles`
- `app/routers/social.py:16` → `app.routers.social_friendships`
- `app/routers/social.py:24` → `app.routers.social_friendships`
- `app/routers/social.py:25` → `app.routers.social_feed`
- `app/routers/social.py:42` → `app.routers.social_feed`
- `app/routers/social.py:43` → `app.routers.social_devices`
- `app/routers/social.py:44` → `app.routers.social_devices`
- `app/routers/social.py:45` → `app.routers.social_chat`
- `app/routers/social.py:58` → `app.routers.social_chat`

## Corpos de funções Python idênticos

- app/services/quintal_scheduler.py:75 (stop); app/services/reference_analysis.py:208 (stop)

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
| `app/main.py` | 265 |
| `app/models/__init__.py` | 0 |
| `app/models/analysis.py` | 251 |
| `app/models/vision.py` | 46 |
| `app/persistence_models.py` | 374 |
| `app/prompts/__init__.py` | 0 |
| `app/prompts/coat_analysis.py` | 34 |
| `app/quintal_models.py` | 76 |
| `app/routers/__init__.py` | 0 |
| `app/routers/auth.py` | 369 |
| `app/routers/habitat.py` | 288 |
| `app/routers/product.py` | 502 |
| `app/routers/progression.py` | 165 |
| `app/routers/public_search.py` | 62 |
| `app/routers/quintal.py` | 80 |
| `app/routers/social.py` | 80 |
| `app/routers/social_chat.py` | 182 |
| `app/routers/social_devices.py` | 48 |
| `app/routers/social_feed.py` | 167 |
| `app/routers/social_friendships.py` | 294 |
| `app/routers/social_profiles.py` | 475 |
| `app/routers/sync.py` | 331 |
| `app/routers/vision.py` | 273 |
| `app/routers/websocket.py` | 115 |
| `app/schemas.py` | 384 |
| `app/services/__init__.py` | 0 |
| `app/services/analysis_cache.py` | 96 |
| `app/services/analysis_service.py` | 490 |
| `app/services/audience.py` | 22 |
| `app/services/auth_service.py` | 205 |
| `app/services/avatar_generation_service.py` | 88 |
| `app/services/avatar_service.py` | 250 |
| `app/services/catalog_changes.py` | 10 |
| `app/services/catalog_sync_queries.py` | 69 |
| `app/services/chat_commands.py` | 276 |
| `app/services/chat_presenter.py` | 47 |
| `app/services/chat_queries.py` | 234 |
| `app/services/color_service.py` | 224 |
| `app/services/conversations.py` | 108 |
| `app/services/dense_features.py` | 111 |
| `app/services/fcm_service.py` | 136 |
| `app/services/fusion_service.py` | 73 |
| `app/services/habitat_encounters.py` | 116 |
| `app/services/habitat_interactions.py` | 146 |
| `app/services/habitat_presenter.py` | 117 |
| `app/services/habitat_pricing.py` | 16 |
| `app/services/habitat_service.py` | 256 |
| `app/services/milestones.py` | 226 |
| `app/services/missing_posters.py` | 19 |
| `app/services/preprocessing_service.py` | 83 |
| `app/services/progression.py` | 333 |
| `app/services/quality_service.py` | 43 |
| `app/services/quintal_care.py` | 302 |
| `app/services/quintal_catalog.py` | 69 |
| `app/services/quintal_clock.py` | 22 |
| `app/services/quintal_commands/__init__.py` | 44 |
| `app/services/quintal_commands/cats.py` | 211 |
| `app/services/quintal_commands/construction.py` | 90 |
| `app/services/quintal_commands/context.py` | 29 |
| `app/services/quintal_commands/garden.py` | 57 |
| `app/services/quintal_commands/receipts.py` | 31 |
| `app/services/quintal_commands/residents.py` | 120 |
| `app/services/quintal_commands/tasks.py` | 153 |
| `app/services/quintal_games.py` | 110 |
| `app/services/quintal_inventory.py` | 100 |
| `app/services/quintal_notifications.py` | 40 |
| `app/services/quintal_production.py` | 88 |
| `app/services/quintal_progress.py` | 59 |
| `app/services/quintal_projection.py` | 295 |
| `app/services/quintal_push.py` | 89 |
| `app/services/quintal_return.py` | 53 |
| `app/services/quintal_rewards.py` | 57 |
| `app/services/quintal_scheduler.py` | 80 |
| `app/services/quintal_simulation.py` | 174 |
| `app/services/realtime_events.py` | 78 |
| `app/services/recognition_features.py` | 127 |
| `app/services/recognition_service.py` | 264 |
| `app/services/reference_analysis.py` | 213 |
| `app/services/report_media.py` | 50 |
| `app/services/report_presenter.py` | 26 |
| `app/services/segmentation_service.py` | 463 |
| `app/services/social_feed_commands.py` | 367 |
| `app/services/social_feed_queries.py` | 247 |
| `app/services/social_graph.py` | 55 |
| `app/services/social_presenter.py` | 70 |
| `app/services/storage_service.py` | 121 |
| `app/services/vision_jobs.py` | 50 |
| `app/services/vision_worker.py` | 251 |
| `app/services/websocket_manager.py` | 169 |
| `app/social_schemas.py` | 16 |
| `scripts/audit_quality.py` | 159 |
| `scripts/compare_cats.py` | 67 |
| `scripts/evaluate_oxford_pet_segmentation.py` | 196 |
| `scripts/export_quintal_visual_fixture.py` | 49 |
| `scripts/export_runtime_lock.py` | 69 |
| `scripts/inspect_photo_gallery.py` | 54 |
| `scripts/prepare_cat_model.py` | 78 |
| `scripts/prepare_own_cats_benchmark.py` | 166 |
| `scripts/prepare_oxford_pet_benchmark.py` | 167 |
| `scripts/reid_benchmark.py` | 213 |
| `scripts/reindex_recognition.py` | 32 |
| `scripts/run_integration_checks.py` | 214 |
| `scripts/run_own_cats_calibration.py` | 194 |
| `scripts/simulate_quintal_balance.py` | 243 |
| `tests/conftest.py` | 13 |
| `tests/test_account_settings.py` | 161 |
| `tests/test_architecture_boundaries.py` | 58 |
| `tests/test_avatar_presets.py` | 71 |
| `tests/test_benchmark_helpers.py` | 69 |
| `tests/test_chat_rescue.py` | 135 |
| `tests/test_color_and_fusion.py` | 104 |
| `tests/test_conversation_events.py` | 149 |
| `tests/test_dense_features.py` | 100 |
| `tests/test_exceptions.py` | 30 |
| `tests/test_feed_models.py` | 70 |
| `tests/test_feed_pagination.py` | 39 |
| `tests/test_gamification.py` | 114 |
| `tests/test_habitat.py` | 423 |
| `tests/test_habitat_encounters.py` | 143 |
| `tests/test_message_reactions.py` | 14 |
| `tests/test_milestones_v2.py` | 217 |
| `tests/test_missing_poster_flow.py` | 87 |
| `tests/test_preprocessing_quality.py` | 94 |
| `tests/test_product.py` | 187 |
| `tests/test_progression.py` | 292 |
| `tests/test_progression_migration.py` | 40 |
| `tests/test_quintal_activity_calendar.py` | 69 |
| `tests/test_quintal_live_api.py` | 405 |
| `tests/test_quintal_postgres.py` | 176 |
| `tests/test_quintal_production.py` | 769 |
| `tests/test_quintal_push.py` | 147 |
| `tests/test_quintal_rewards.py` | 88 |
| `tests/test_quintal_router.py` | 130 |
| `tests/test_quintal_routines.py` | 134 |
| `tests/test_quintal_scheduler.py` | 60 |
| `tests/test_realtime_events.py` | 29 |
| `tests/test_recognition.py` | 195 |
| `tests/test_recognition_database.py` | 238 |
| `tests/test_reference_analysis.py` | 355 |
| `tests/test_screen_corrections.py` | 86 |
| `tests/test_segmentation_service.py` | 207 |
| `tests/test_social_graph.py` | 181 |
| `tests/test_social_posts.py` | 61 |
| `tests/test_storage_service.py` | 42 |
| `tests/test_sync_query_performance.py` | 322 |
| `tests/test_websocket_manager.py` | 52 |

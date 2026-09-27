# Execution ledger
User approved design and start execution.
Ruling: preserve existing checkout and current user workflow; no new isolated server or duplicate task.
Preflight: T1/T2 share person_id contract only; root owns production files, implementer owns portrait files. Root integrates UI mounts and admin scripts. T3 verifies cross-module behavior.

Task1 implemented: API/provider/worker tests and browser6 accessories1440/390 passed; scene+hair legacy1440/390 passed.
Task2 implemented: explicit type, official upload/import, uncertain creation, library UI+admin; virtual browser1440/390 real API+fake upstream passed.
Whole suite 324 tests passed before review fixes; bare pytest collection conflicts with user's untracked exported video-production-module/tests. Canonical tests/ directory passes.
Review: two P2 findings. Recovery labels fixed with failing-first test, then33 target tests passed. Existing import reconciliation assigned to implementer.
Live read checks: no running jobs, AIGC ListAssetGroups/ListAssets succeed, 18 groups and Active images. No live generation yet.

Review fixes closed by independent scoped review. Final330 pytest tests passed; virtual real-API browser1440/390 passed. Existing Starlette deprecation warning only.

Release e26beff plus official asset-host fix 3a781b0 deployed locally and to cloud release 20260927-accessories-virtual. Final 331 tests passed. Actual cloud desktop/mobile checks passed. Official download domain ark-media-asset.tos-cn-beijing.volces.com verified via existing Active asset (image bytes validated).
Live AIGC group creation, TOS upload and official GetAsset Active succeeded. Two isolated 4-second acceptance submissions failed before provider task creation with InvalidParameter: specified asset not found. Original user draft preserved. The user confirmed the model API key and AK/SK belong to the same account; API-key project remains to be confirmed. This is not a successful generation acceptance.
Do not enumerate API keys: an attempted ListApiKeys diagnostic was rejected by automatic approval review and was not executed. Continue only with known asset metadata, safe public configuration and user-confirmed project information.

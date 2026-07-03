# ZedUploader — Launch-Readiness Report

Final whole-system QA audit before go-live. Evidence: the cross-cutting
end-to-end suite `tests/integration/test_e2e_qa.py` (14 tests, real Postgres),
run as part of the full suite — **341 passed** with
`TEST_DATABASE_URL` pointing at a real Postgres. `python -m compileall app`
clean; `docker compose config` valid; all app modules import with no env.

## Migration history — linear, single head

`alembic heads` → **`0026_panel_roles`** (single). Full linear chain:

```
0001_initial → 0002_phase2 → 0003_phase3 → 0004_phase4 → 0005_phase5
→ 0006_broadcast_ledger → 0007_user_uploads → 0008_folders
→ 0009_media_search_indexes → 0010_payment_providers → 0011_provider_config
→ 0012_ads → 0013_stats_indexes → 0014_telegram_stars → 0015_backup_jobs
→ 0016_media_reports → 0017_license → 0018_bot_commands → 0019_multitenant
→ 0020_bot_plans → 0021_panel_tenant → 0022_superadmin
→ 0023_tenant_log_settings → 0024_support_tickets → 0025_invoices
→ 0026_panel_roles (head)
```

`alembic upgrade head` on a fresh Postgres runs clean end-to-end (verified);
the autogenerate no-drift check passes (`tests/integration/test_migrations.py`).

## The eight audit areas

| # | Area | Verdict | Evidence (test) |
|---|------|---------|-----------------|
| 1 | **All five payment methods together** — card, CentralPay, Zarinpal, Zibal, Stars: initiate → settle → credit/activate → invoice; each credits **exactly once**, exactly **one invoice** each, per-tenant ledger invariant `SUM(tx)==balance` holds after the mixed batch, invoice numbering is gap-free sequential | **PASS** | `test_all_five_methods_end_to_end_mixed_batch` |
| 2 | **Idempotency under concurrency** (separate sessions on real PG) — double gateway callback → `{credited, already}`, one credit, one invoice; double Stars `successful_payment` same charge id → one payment row, one activation, one invoice; double-tap purchase → `{ok, duplicate}`, one debit; double bot-creation → one tenant, one charge | **PASS** | `test_concurrent_double_gateway_callback`, `test_concurrent_double_stars_same_charge`, `test_concurrent_double_tap_purchase`, `test_concurrent_double_bot_creation` |
| 3 | **Cross-tenant isolation under load** — 3 tenants: media/users/payments/invoices/tickets each invisible from another tenant; crediting a foreign tenant's wallet raises (row invisible to `WalletService`); a missing tenant context **fails closed** (`NoTenantContext`); the gateway HTTP return callback settles under the *payment's own* tenant | **PASS** | `test_isolation_battery_across_three_tenants`, `test_pay_callback_resolves_right_tenant_on_pg`; workers covered by `tests/integration/test_worker_tenant.py` |
| 4 | **Role + platform isolation (H1/I2)** — a customer owner is 403 on every platform-only surface (platform dashboard/tenants/support/broadcast, bot-plans pricing, backups, license) yet 200 on their own owner surfaces; support/finance role matrix enforced; super-admin reaches everything; the bot factory refuses any non-platform tenant | **PASS** | `test_platform_and_role_gating_on_pg`, `test_bot_factory_stays_platform_only`; full matrices in `tests/test_role_isolation.py`, `tests/test_panel_roles.py` |
| 5 | **User blocking (I1)** — a blocked user is refused at the middleware (message/callback/pre-checkout), refused a deep-link delivery, and excluded from broadcast snapshots; a blocked **admin** bypasses interactive blocking (delivery works) but is still excluded from broadcasts | **PASS** | `test_blocked_user_full_battery_on_pg`; full battery in `tests/test_blocked_users.py` |
| 6 | **License degradation (E)** — `LICENSE_DISABLED` (default) allows everything; an expired/revoked license disables only **new paid actions** (`paid_features_allowed → False`) while delivery of an approved file still completes (`DELIVERED`) and data stays readable; offline grace honored inside the window, degraded beyond it | **PASS** | `test_license_degrades_paid_actions_only` |
| 7 | **Panel + API security** — a mutating panel POST without a CSRF token is 403 (with it, 302); `/api/v1` binds the caller's tenant and returns only that tenant's rows; `password_hash` never serialized; a decrypted bot token is never rendered on any platform page | **PASS** | `test_csrf_required_and_api_v1_tenant_bound`, `test_panel_never_renders_decrypted_bot_token`; login lockout/rate-limit/audit in `tests/test_panel.py` |
| 8 | **Multi-bot routing** — `/tenant/{bot_id}/webhook`: unknown bot → 404; wrong secret → 403 with **no dispatch**; correct secret → dispatched with that tenant's id and that tenant's Bot; unregistering (suspension) stops serving (404) | **PASS** | `test_tenant_webhook_routing_secret_and_suspension` |

### Bugs found by this audit

**None in product code.** Two issues surfaced during the audit were test-side
only and fixed in the test file (a PG sequence not advanced by explicit-id
seeding in the new test helper, and an initially wrong expectation about
blocked admins in broadcast snapshots — the product behavior, excluding every
blocked row from broadcasts including blocked admins, is the specified I1
semantics). **No product changes and no new migration were needed.**

## Go-live checklist for the operator (not code)

Perform these on the production server before announcing:

1. **Gateway credentials (real, non-sandbox):** in `/panel/providers` enter the
   real CentralPay getLink/verify keys, the real Zarinpal merchant id
   (sandbox OFF), and the real Zibal merchant (sandbox OFF). Set the Telegram
   Stars toggle as desired.
2. **Card + limits:** set the real card number/holder in `/panel/settings`,
   the card-payment toggle, and a sensible `topup_min`.
3. **Pricing:** set plan prices + `stars_price` per plan (`/panel/plans`) and
   bot-creation/rental pricing (`/panel/bot-plans`, super-admin).
4. **One real small-amount test purchase through EACH gateway** (and one Stars
   purchase, and one card top-up + approval): verify each credits exactly once,
   the plan activates, the invoice appears (bot + panel + CSV), and the wallet
   ledger matches the balance in `/panel/users/{id}`.
5. **Backups:** enable the auto-backup schedule (`/panel/backups`, super-admin)
   with retention, then **test one restore** on a staging copy — a backup you
   have never restored is not a backup.
6. **TLS:** `certbot renew --dry-run` succeeds; cert auto-renewal timer active.
7. **Firewall:** limit inbound to 80/443/22 (e.g. `ufw allow 80,443,22/tcp`
   then `ufw enable`).
8. **Monitoring:** point an external monitor at `/health`; alert on non-200.
9. **Licensing (if selling licenses):** deploy the activation server, set
   `LICENSE_SERVER_URL`/`LICENSE_KEY` on customer installs, keep
   `LICENSE_DISABLED=false` in production.
10. **Webhook sanity:** confirm `setWebhook` was made with
    `allowed_updates=["message","callback_query","pre_checkout_query"]`
    (install.sh does this; without `pre_checkout_query` Stars payments fail
    silently).
11. **Business readiness:** publish a support channel (the in-bot 🎧 ticket
    system routes end-users to each tenant admin and resellers to you) and a
    ToS/refund policy; verify `TENANT_TOKEN_KEY` is backed up securely — losing
    it makes stored customer bot tokens undecryptable.

## Known limitations / risks (stated plainly)

- **Single-worker assumption:** the background worker (auto-delete, broadcast
  drain, album finalize, expiry sweeps) is single-instance by design; running
  two workers concurrently could double-send broadcasts. Do not scale the
  worker horizontally without adding a job-claim lock.
- **In-memory bot registry:** per-tenant webhooks resolve from an in-memory
  registry per API process. With multiple API replicas each keeps its own copy
  (loaded at startup, updated on panel actions in that process); a multi-replica
  deployment should add a shared invalidation channel before scaling out.
- **Redis in tests:** the automated suite uses fakeredis (real `SET NX EX`
  semantics for the purchase/creation locks, in-process). The locks' behavior
  on a real Redis is the same API, but a real-Redis smoke run on the server
  (item 4 above) is the final confirmation.
- **Manual card approval is human-gated:** card-to-card top-ups depend on an
  admin reviewing receipts; the panel enforces card-only manual approval and
  gateway payments can only be settled by the idempotent gateway verify.
- **This environment:** `docker compose run --rm api pytest` could not be
  executed here (no docker daemon in the QA sandbox); the identical suite was
  run in-venv against real Postgres. Run it once in-container on the server
  (`docker compose run --rm api pytest -q`) as part of go-live.

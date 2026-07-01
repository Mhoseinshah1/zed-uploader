# ZedUploader — QA Audit Report (Phases 2 & 3, money-safety focus)

**Scope:** verify Phase 2 (force-join, batch upload, admin management, broadcast)
and Phase 3 (wallet, plans, subscriptions, card-to-card payments, feature gating)
— with special focus on wallet/payment **money safety under concurrency**.
No features were added; **no product code was changed** (see "Bugs found").

## How to run

```bash
# unit tier only (no DB/Redis needed): integration tests auto-skip
pytest -q

# full suite incl. money-safety integration tests against a REAL Postgres:
docker compose up -d db redis
docker compose exec -T db psql -U "$POSTGRES_USER" -c "CREATE DATABASE zed_test;" || true
TEST_DATABASE_URL=postgresql+asyncpg://<user>:<pass>@localhost:5432/zed_test pytest -q
```

> In this audit environment the Docker daemon was unavailable, so a local
> Postgres 16 cluster was used instead (`TEST_DATABASE_URL=postgresql+asyncpg://zed:zed@127.0.0.1:5432/zed_test`).
> SQLite is **not** used for money tests — `SELECT … FOR UPDATE` / `RETURNING`
> semantics differ, and the concurrency proofs depend on them.

## Test layout

- `tests/unit/` — no DB/Redis locking (SQLite/fakes/mocks): feature-gate math,
  owner/admin resolution, `unjoined_channels`, batch finalize, broadcast guard
  + worker blocked-marking.
- `tests/integration/` — **real Postgres**, gated on `TEST_DATABASE_URL`
  (`test_money.py`, `test_migrations.py`).
- Existing `tests/*.py` (api/code-gen/menu/phase2/phase3/panel) kept green.

## Results

Full suite: **41 passed** with Postgres; **31 passed, 10 skipped** without
(`TEST_DATABASE_URL` unset → integration skips with a clear message).

### 2. Money-safety (integration, REAL Postgres)

| # | Item | Result |
|---|------|--------|
| 2.1 | Ledger invariant `SUM(amount)==balance` + each `balance_after`=running | ✅ PASS |
| 2.2 | Insufficient funds is atomic (no orphan row, balance unchanged) | ✅ PASS |
| 2.3 | **Concurrent** 50×credit + 50×debit, separate sessions → final exactly 10 000, never negative, invariant intact | ✅ PASS |
| 2.4 | Idempotent approval (sequential): credits once, one `deposit` row `payment:{id}` | ✅ PASS |
| 2.5 | **Idempotent approval (concurrent)**: two `approve()` racing → credited exactly once, no exception | ✅ PASS |
| 2.6 | Rejection credits nothing, status `rejected` | ✅ PASS |
| 2.7 | Subscription purchase consistency (insufficient → no change; sufficient → one debit + plan + expiry + one sub) | ✅ PASS |
| 2.8 | Plan expiry sweep downgrades expired plan to `free`, deactivates subs | ✅ PASS |

### 3. Feature gating + Phase 2

| # | Item | Result |
|---|------|--------|
| 9  | `FeatureService`: free→False, plus→True, expired-plus→`free`→False | ✅ PASS |
| 10 | `is_owner`/`is_admin`: env id owner; DB `admin` is admin not owner; inactive ignored | ✅ PASS |
| 11 | `unjoined_channels`: only `left`/`kicked`; **fails open** on bot error | ✅ PASS |
| 12 | Batch finalize → exactly ONE `Media` with N `MediaFile`s in `sort_order` | ✅ PASS |
| 13 | Broadcast: non-owner guard False; worker marks `is_blocked` on `TelegramForbiddenError` and advances cursor | ✅ PASS |

### 4. Static / structural audit

- **`users.balance` is assigned only in `WalletService`.** Grep of `app/` for
  `.balance =` returns a single site: `app/services/wallet_service.py:45`
  (`user.balance = new_balance`, inside `_apply`, under the row lock, paired with
  a ledger row). No other writer. ✅
- **`PaymentService.approve` checks status inside the row lock before crediting.**
  Path: `select(Payment)…with_for_update()` (line 44) → `if payment.status ==
  "approved": return "already"` (line 48) → set `approved` → `WalletService.credit`
  (line 53). The status re-check happens *after* the lock is granted, so a second
  concurrent approver blocks, then sees `approved` and no-ops. ✅
- **Alembic:** `alembic heads` → single head `0004_phase4`; `alembic upgrade head`
  on a clean DB applies `0001→0002→0003→0004` with no error; `alembic check`
  reports **"No new upgrade operations detected"** (no model/schema drift). ✅
- `python -c "import app.api.main, app.bot.main, app.workers.main, app.models"`
  succeeds with **no env set**. ✅

## Bugs found

**None.** Every money invariant held on the first run, including both
concurrency tests. No product code was modified — only the test suite and test
infrastructure were added (`pytest-asyncio`, split unit/integration tiers, the
real-Postgres harness).

The design already gets the hard parts right:
- **Wallet:** `_apply` locks the user row (`SELECT … FOR UPDATE`), computes the
  new balance, writes the ledger row + cached balance, and commits — so
  concurrent credits/debits serialize and the invariant can't be violated.
- **Payment approval:** row-locked status re-check makes double-approval a no-op
  (verified concurrently — exactly one credit).
- **Download claim (Phase 1):** single conditional `UPDATE … RETURNING` (not
  re-tested here; same money-safety family).

## Residual risks (documented, not blocking)

1. **Purchase debit/plan split-commit.** `SubscriptionService.purchase` debits
   (which commits inside `WalletService`) and then sets the plan + inserts the
   subscription in a *second* commit. A process crash between the two commits
   would leave the user debited (a valid, ledgered `purchase` row) without the
   plan applied — a consistency/UX gap, **not** a double-spend or invariant
   break. This matches the Phase-3 spec's prescribed "debit first, then set
   plan" pattern, so it was left as-is. Mitigation if desired: perform the debit
   and plan mutation in a single transaction (or an outbox/idempotency key).
2. **Broadcast is at-least-once.** On worker crash mid-page a few users may get
   a duplicate message (already documented in the README).
3. **Concurrency guarantees are Postgres-specific.** The proofs rely on
   READ COMMITTED + `FOR UPDATE` re-read; they do **not** hold on SQLite — hence
   the money tests are integration-only and refuse to run without a real PG.

## Verdict

The financial layer is **verified**: the wallet ledger invariant holds under
concurrent credits/debits, payment approval is idempotent under a real race
(credited exactly once), subscription purchase is consistent, and `users.balance`
is mutated only by `WalletService`. All 8 money-safety tests and all 5
feature/phase-2 items pass against a real Postgres.

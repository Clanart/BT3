# Q3598: `finalize` and secnonce single-use

## Question
Can an unprivileged depositor who funds a Bitcoin output and submits its `DepositParams` through the public aggregator send two aggregator requests (two deposits, or a deposit plus an optimistic payout) that make `finalize` in `core/src/builder/transaction/txhandler.rs` consume the same secnonce or the same aggregated nonce for two different messages, given `AllSessions::remove_oldest_session`, `get_new_unused_id` and `session.nonces.pop()`, allowing recovery of a verifier's key share from two partial signatures?

## Target
- File/function: `core/src/builder/transaction/txhandler.rs` -> `finalize` (This module defines the [`TxHandler`] abstraction, which wraps a protocol transaction and its metadata)
- Entrypoint: aggregator `NewDeposit` / `OptimisticPayout` -> `Verifier::nonce_gen` -> `finalize`
- Attacker controls: request timing, concurrency, session churn, and the number of nonces requested per session; attacker is an unprivileged depositor (funds a Bitcoin output, submits deposit params, holds no protocol role or key)
- Exploit idea: force nonce reuse across messages and solve for the secret share
- Invariant to test: every secnonce popped from a `NonceSession` signs exactly one message
- Expected Immunefi impact: Critical - direct theft of bridged BTC via a bypassed script/signature check on a bridge UTXO
- Fast validation: drive two concurrent signing sessions in a test harness and assert no secnonce is used twice

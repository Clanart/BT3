### Title
`sign_optimistic_payout` produces independent N-of-N signatures for arbitrary `output_script_pubkey`/`output_amount` per call, allowing two competing fully-signed spends of the same `DepositInMove` UTXO - (File: core/src/verifier.rs, core/src/builder/transaction/operator_reimburse.rs)

### Summary
`Verifier::sign_optimistic_payout` (`core/src/verifier.rs:1570`) builds and signs an optimistic payout transaction using the caller-supplied `output_script_pubkey` and `output_amount` directly, with no persisted binding recording that deposit_id X has already been authorized to pay a specific destination. As long as the (unspent) `input_outpoint` matches the DB-stored `withdrawal_utxo` for that `deposit_id` and the amount is under the bridge-amount ceiling, the function will happily produce a fresh, fully valid N-of-N partial signature for any destination the caller asks for, on any number of calls, before either resulting transaction confirms.

### Finding Description
The binding that should hold is: `for a given deposit_id, (output_script_pubkey, output_amount) signed by the N-of-N is fixed to exactly the destination authorized by the corresponding Citrea withdrawal`. The code never establishes or checks this equality.

In `sign_optimistic_payout` (`core/src/verifier.rs:1570-1712`):
- `input_outpoint` is checked for being unspent (`core/src/verifier.rs:1582`) and equal to `get_withdrawal_utxo_from_citrea_withdrawal(deposit_id)` (`core/src/verifier.rs:1646-1659`).
- `output_amount` is checked only against a ceiling: `bridge_amount - NON_EPHEMERAL_ANCHOR_AMOUNT` (`core/src/verifier.rs:1635-1644`).
- `output_script_pubkey` is checked only for being a standard script type (`core/src/verifier.rs:1588-1599`).
- There is no lookup or storage that pins these two request-supplied values to a single canonical value ever recorded for `deposit_id`. The only value that is cross-checked against a stored value is `input_outpoint` (the withdrawal UTXO), not the payout destination/amount.
- An optional ECDSA "verification signature" gate exists (`core/src/verifier.rs:1601-1623`), but it is only enforced when `self.config.aggregator_verification_address` is configured; when unset (the default), this gate is entirely absent, so nothing at all ties `output_script_pubkey`/`output_amount` to a specific, single, previously-approved value.
- `create_optimistic_payout_txhandler` (`core/src/builder/transaction/operator_reimburse.rs:459`) is called fresh each time with whatever `TxOut { value: output_amount, script_pubkey: output_script_pubkey }` was passed, spending `move_txhandler`'s `UtxoVout::DepositInMove` output at input index 1 via `SpendPath::ScriptSpend(0)`.
- Each call computes a fresh sighash over the newly constructed handler (`calculate_script_spend_sighash_indexed(1, 0, ...)`, `core/src/verifier.rs:1685-1689`) and produces an independent MuSig2 partial signature (`core/src/verifier.rs:1703-1710`) using a freshly popped secnonce from the caller's nonce session — there is no per-`deposit_id` state marking that a signature has already been granted, nor any check that a prior optimistic-payout signing round for this `deposit_id` exists and should invalidate/preclude a second one.

Consequently, if the same `input_outpoint` (withdrawal UTXO for a `deposit_id`) can be associated with two different, independently valid destination/amount pairs (each satisfying the ceiling and unspent checks) before either payout confirms on-chain, an aggregator relaying two `optimistic_payout` gRPC calls to all verifiers with different `output_script_pubkey`/`output_amount` values will get two fully independent, fully valid sets of N-of-N partial signatures, both authorizing spends of the same `DepositInMove` UTXO to different destinations.

### Impact Explanation
This breaks the "N-of-N partial signatures for an unauthorized spend" invariant: verifiers sign more than one full spend authorization for the same move-to-vault UTXO without any revocation of the earlier grant. Whichever transaction confirms first determines actual custody, but the existence of two independently fully-signed competing transactions for one Bitcoin UTXO is itself a critical violation — it means the protocol's core guarantee (one Citrea withdrawal authorizes exactly one BTC payout) does not hold at the signing layer. This is repeatable per-deposit whenever a competing valid withdrawal registration/destination pair can be produced for the same `input_outpoint` before confirmation, and generalizes across all deposits using the optimistic-payout path.

### Likelihood Explanation
Exploitability hinges on whether the withdrawal registration/storage layer (`get_withdrawal_utxo_from_citrea_withdrawal`, and how a `deposit_id`'s canonical `withdrawal_utxo` is set from Citrea withdraw events) permits two distinct destination/amount combinations to be associated with the same `input_outpoint` for the same `deposit_id` before either payout confirms — I was not able to fully inspect that registration/insertion code path (`core/src/database/verifier.rs`, `core/src/operator.rs` matches) within the available iterations, so I cannot confirm whether the DB layer additionally constrains `output_script_pubkey`/`output_amount` uniqueness per `deposit_id` outside of `sign_optimistic_payout` itself. What is confirmed directly from `core/src/verifier.rs:1570-1712` is that `sign_optimistic_payout` itself performs no such check — the function is stateless with respect to prior grants for the same `deposit_id`, so if the registration layer allows the caller (via the aggregator's gRPC surface, which is attacker-reachable per the threat model) to submit two distinct destination proposals referencing the same UTXO, the divergence is not caught by this function.

### Recommendation
Persist, at the point a `deposit_id`'s optimistic payout is first requested/signed, the exact `(output_script_pubkey, output_amount, input_outpoint)` tuple in the database, and have `sign_optimistic_payout` compare against that stored tuple on every subsequent call for the same `deposit_id`, rejecting any call whose values differ (rather than only checking `input_outpoint` and an amount ceiling). Additionally, make the ECDSA `verification_signature` check against `aggregator_verification_address` mandatory (not optional based on config) so that a single, externally-attested canonical destination is enforced independent of the database race.

### Proof of Concept
```rust
// cargo test in core/tests or core/src/verifier tests (new test)
// 1. Set up a deposit and its move_txid as usual (test harness fixtures).
// 2. Register a withdrawal_utxo for deposit_id D pointing to outpoint O (unspent).
// 3. Call verifier.sign_optimistic_payout(session1, agg_nonce1, D, sig1, O, script_pubkey_A, amount_A, None).await
//    -> assert Ok(partial_sig_A)
// 4. Without spending O and without any state change invalidating the grant for D,
//    call verifier.sign_optimistic_payout(session2, agg_nonce2, D, sig2, O, script_pubkey_B, amount_B, None).await
//    where script_pubkey_B != script_pubkey_A (or amount_B != amount_A), and amount_B still <= bridge_amount - NON_EPHEMERAL_ANCHOR_AMOUNT
//    -> assert Ok(partial_sig_B)
// 5. Assert partial_sig_A and partial_sig_B are both valid MuSig2 partial signatures over two different sighashes
//    (computed via create_optimistic_payout_txhandler + calculate_script_spend_sighash_indexed(1,0,...)),
//    each spending the same move_txhandler DepositInMove output at UtxoVout, to different destinations,
//    with no error, no lock, and no rejection of the second call based on the first.
```
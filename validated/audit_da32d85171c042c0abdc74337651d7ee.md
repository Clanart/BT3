### Title
Payout tx malleable via `SinglePlusAnyoneCanPay` sighash lets attacker rewrite the OP_RETURN operator key, permanently orphaning the funding operator's reimbursement - (File: core/src/builder/transaction/operator_reimburse.rs)

### Summary
The payout transaction's withdrawal input is signed with `TapSighashType::SinglePlusAnyoneCanPay`, which commits only to input 0 and output 0 (the user payout), leaving the anchor output and the OP_RETURN output (which encodes the funding operator's xonly-pk) completely unsigned. An attacker who observes the honest operator's broadcast payout transaction (or otherwise obtains the raw witness) can rebuild an equivalent transaction with the identical input/output-0/signature but an arbitrary, non-operator OP_RETURN payload, and get that version confirmed instead.

### Finding Description
The claimed binding is:
`payout_payer_operator_xonly_pk` (written into DB by `update_finalized_payouts`, parsed from the OP_RETURN of whichever payout tx actually confirms) `==` the funding operator's own `xonly_pk`, i.e. `self.operator.signer.xonly_public_key`, which is the only key `get_first_unhandled_payout_by_operator_xonly_pk` (core/src/database/verifier.rs:282-313) is ever queried with (core/src/task/payout_checker.rs:41-47).

`create_payout_txhandler` (core/src/builder/transaction/operator_reimburse.rs:407-436) builds:
- output 0: user payout (this is what `SinglePlusAnyoneCanPay` sighash commits to for input 0)
- output 1: anchor
- output 2: OP_RETURN with `operator_xonly_pk.serialize()`

The user's signature is verified/consumed with `sighash_type == TapSighashType::SinglePlusAnyoneCanPay` (enforced at core/src/rpc/parser/operator.rs:161-187, and again verified in `Operator::withdraw` at core/src/operator.rs:630-637 via `calculate_sighash_txin`). By BIP341 semantics, `SIGHASH_SINGLE` only commits to the output at the same index as the signed input (index 0 here); `ANYONECANPAY` only commits to that one input. Outputs 1 and 2 (anchor, OP_RETURN) are therefore never covered by the signature.

`Operator::withdraw` funds and broadcasts this transaction itself (`fund_raw_transaction` → `sign_raw_transaction_with_wallet` → `send_raw_transaction`, core/src/operator.rs:652-689). Once this transaction is broadcast (or its witness otherwise becomes known), the input-0 witness (a valid Schnorr signature under `SinglePlusAnyoneCanPay`) is public. An attacker can construct an entirely different transaction that: reuses the exact same input 0 outpoint+witness, reuses exactly output 0 (script_pubkey+value, required for the sighash to remain valid), but substitutes its own fee inputs/anchor and — critically — a different OP_RETURN payload containing an arbitrary syntactically-valid 32-byte value (e.g. the aggregator's key, a security-council-shaped key, or any non-operator key). Nothing in the signature prevents this substitution. If the attacker gets this version mined (e.g. by out-bidding via RBF/CPFP or direct submission with a competing fee, since it spends the same UTXO), `update_finalized_payouts` (core/src/verifier.rs:2283-2353) will parse this attacker OP_RETURN with `parse_op_return_data`/`XOnlyPublicKey::from_slice` and store it as `payout_payer_operator_xonly_pk` for that withdrawal index, entirely bypassing the gRPC-level `current_operator_xonly_pks.contains` check in `Aggregator::withdraw` because the attacker never calls that RPC.

Because `PayoutCheckerTask::run_once` only ever queries `get_first_unhandled_payout_by_operator_xonly_pk` with the real operator's own key (core/src/task/payout_checker.rs:41-47), and the stored key now matches no operator (or a different operator's key it never funded), the funding operator can never find/self-attribute this withdrawal, and `handle_finalized_payout`/`mark_payout_handled` are never invoked for it. This is a permanent, non-recoverable state since `payout_txid`/`payout_payer_operator_xonly_pk`/`payout_tx_blockhash` are only ever set once per withdrawal index by `update_payout_txs_and_payer_operator_xonly_pk` when a payout confirms for it (core/src/database/verifier.rs:198-251) — there's no re-scan or correction path.

None of the existing guards catch this: `Verifier::is_deposit_valid`, `SPV::verify`, and the withdrawal-utxo/db checks in `Aggregator::withdraw` all operate on the *intended* payout construction at request time, not on what actually gets mined; there is no on-chain commitment binding the OP_RETURN bytes to the signature, so any transaction spending the same input with the same output-0 is equally "valid" from the chain's perspective regardless of its OP_RETURN.

### Impact Explanation
The operator that genuinely fronted the withdrawal (its input/output-0 are reused verbatim, and it paid real BTC to the user) becomes permanently unable to be reimbursed for that payout, since the on-chain record used for self-attribution (`payout_payer_operator_xonly_pk`) no longer matches its key. This matches the Critical category "an honest operator permanently unable to be reimbursed." If the attacker instead sets the OP_RETURN to a *different real* operator's key (rather than a garbage key), that other operator could self-attribute and get reimbursed for a payout it never funded — matching "an operator reimbursed for a payout it never funded," also Critical. The attack is repeatable for every withdrawal broadcast by any operator, since the vulnerability is structural (sighash type, not per-tx data).

### Likelihood Explanation
Feasibility depends on the attacker being able to get their malleated transaction confirmed instead of (or racing) the honest operator's broadcast transaction — e.g., by monitoring the mempool for the operator's payout tx, extracting the input-0 witness, and rebroadcasting a fee-bumped variant with a different OP_RETURN (using RBF/CPFP dynamics or direct submission with higher feerate before the original confirms). This requires only standard Bitcoin capabilities available to any unprivileged party (mempool monitoring, transaction construction, fee payment) — no key compromise, no majority hashrate, and no gRPC access is needed. Cost is limited to fees for the replacement transaction.

### Recommendation
Change the sighash type used for the withdrawal input signature from `SinglePlusAnyoneCanPay` to a type that also commits to the OP_RETURN output (e.g. `AllPlusAnyoneCanPay`, or restructure so OP_RETURN/anchor are covered), so the operator-identifying OP_RETURN cannot be altered without invalidating the user's signature. Alternatively, bind the payout attribution to something the user signature does commit to (e.g. embed the operator xonly-pk in the covered output 0 via an additional commitment, or require output 0's script to already encode/derive the operator identity).

### Proof of Concept
```rust
// cargo test in core/src, using a regtest bitcoind + real operator flow (no mainnet, no live Citrea)
#[tokio::test]
async fn payout_op_return_malleability_orphans_operator() {
    // 1. Set up regtest, deposit, and withdrawal via existing test harness
    //    (create_test_config_with_thread_name, generate_withdrawal_utxo, sign_withdrawal_output)
    //    exactly as in core/src/test/common/setup_utils.rs.

    // 2. Call operator.withdraw(...) to build+sign+broadcast the honest payout tx,
    //    but intercept before send_raw_transaction to capture:
    //    - the funded_tx bytes (with input0 witness = user sig, SinglePlusAnyoneCanPay)
    //    - output0 (user payout) bytes

    // 3. Construct an attacker tx:
    //    - same input0 (outpoint + witness) taken verbatim from step 2
    //    - identical output0
    //    - attacker's own fee input(s) and anchor
    //    - OP_RETURN replaced with a random 32-byte value that parses as XOnlyPublicKey
    //      but is not any registered operator key (assert via fetch_operator_keys()).

    // 4. Mine the attacker's tx (rpc.generate_to_address) instead of the honest one.

    // 5. Run the honest operator's state manager processing of the block
    //    (triggers update_finalized_payouts), then poll:
    //    PayoutCheckerTask::new(db, operator.clone()).run_once()
    //    repeatedly across several rounds.

    // Assertions on both sides of the binding:
    let stored = db.get_payout_info_from_move_txid(None, move_txid).await.unwrap().unwrap();
    assert_ne!(stored.0, Some(operator.signer.xonly_public_key)); // binding broken
    assert!(!payout_checker.run_once().await.unwrap()); // never finds/handles it
    // repeat run_once() several polling intervals -> always false, proving permanent orphaning
}
```
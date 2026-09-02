### Title
Honest operator's payout attributed as "no operator xonly pk" via first-OP_RETURN-only parsing, triggering wrongful malicious-kickoff challenge/collateral burn - ([File: core/src/verifier.rs])

### Summary
`is_kickoff_malicious` and `update_finalized_payouts` bind the credited operator to `parse_op_return_data(get_first_op_return_output(payout_tx))` rather than to the operator xonly key actually referenced in the payout tx's committed output. Because the payout tx's input signature is `SinglePlusAnyoneCanPay` (enforced in `parse_withdrawal_sig_params`), only the output at index 0 is cryptographically bound by the signature; any other output — including its position relative to the operator's OP_RETURN — is unconstrained and can be altered by a third party via a fee-bumping replacement, letting an attacker insert a bogus, unparsable OP_RETURN ahead of the operator's real one in the transaction that ultimately confirms.

### Finding Description
The claimed binding is:
`payout_info.operator_xonly_pk (derived from parse_op_return_data(get_first_op_return_output(confirmed_payout_tx)))` == `kickoff_data.operator_xonly_pk` of the operator that actually fronted the withdrawal (i.e., whose signature honored output[0] of the payout tx).

`create_payout_txhandler` (`core/src/builder/transaction/operator_reimburse.rs:407-436`) builds the payout tx with output 0 = user payout, output 1 = anchor, output 2 = the operator's OP_RETURN, and signs only input 0 with `SpendPath::KeySpend`. The user's `in_signature` is required to use `TapSighashType::SinglePlusAnyoneCanPay`, enforced in `parse_withdrawal_sig_params` (`core/src/rpc/parser/operator.rs:174-187`). Under this sighash type, `calculate_pubkey_spend_sighash` (`core/src/builder/transaction/txhandler.rs:210-233`) commits only to the prevout of the signing input (`Prevouts::One`) and, per BIP-341, only to the output at the same index as the input (index 0) — no other output's content, count, or position is committed. This is explicitly acknowledged in the codebase: `crates/clementine-tx-sender/src/rbf.rs:162` places the change output "at last index (so that SinglePlusAnyoneCanPay signatures stay valid)", confirming that outputs other than index 0 are freely mutable by any party funding/replacing the transaction (standard BIP-125 RBF, since the input's sequence permits replacement).

An attacker (unprivileged, but capable of broadcasting and fee-bumping Bitcoin transactions) can observe the honest operator's broadcast, unconfirmed payout tx in the mempool, and construct a replacement transaction that: keeps output 0 byte-for-byte identical (preserving the valid user signature), adds their own funding input under `ANYONECANPAY`, and reorders/adds outputs so that a new, unparsable OP_RETURN precedes the operator's original OP_RETURN. Since output 0 is untouched, the replacement is a fully valid, higher-fee competing transaction and can be mined instead of the operator's original layout.

Once mined, `update_finalized_payouts` (`core/src/verifier.rs:2283-2353`) resolves the credited operator solely via `get_first_op_return_output` + `parse_op_return_data` on the confirmed transaction (`circuits-lib/src/bridge_circuit/mod.rs:609-617,688-692`). Because the first OP_RETURN encountered is now the attacker's malformed one, `parse_op_return_data` returns `None` for the intended operator commitment, and `operator_xonly_pk` is stored as `NULL` in the DB (verifier.rs:2319-2328). Which transaction actually gets recorded as "the payout tx" for the withdrawal is looked up purely by which txid spent the tracked `withdrawal_utxo_txid`/`vout` (`core/src/database/verifier.rs:170-196`), with no re-check against the operator's intended tx.

Subsequently, `Verifier::is_kickoff_malicious` (`core/src/verifier.rs:1859-1915`) reads this payout info: when `operator_xonly_pk_opt` is `None`, it unconditionally logs "assuming malicious" and returns `true` (lines 1882-1885) — regardless of which operator's `kickoff_data` triggered the check, and regardless of the fact that the true payout (output 0) was correctly honored by the honest operator's front. This flows into `handle_kickoff` (verifier.rs:1969-2026), which broadcasts a `Challenge` transaction against the honest operator's kickoff, ultimately enabling Disprove/ChallengeNACK to burn that operator's collateral.

None of the existing guards catch this: `SECP.verify_schnorr` in `withdraw`/`internal_withdraw` (`core/src/operator.rs:630-637`) only validates that output 0 matches the user's intent, not the rest of the transaction; `is_profitable` only checks amounts; there is no check that the confirmed payout tx's non-signed outputs match what the operator originally constructed.

### Impact Explanation
This directly matches the "Critical — honest operator's collateral burned" category. The attacker forces the bridge into treating a legitimate, correctly-fronted withdrawal as an unattributed/malicious kickoff, causing a `Challenge`/`Disprove`/`ChallengeNACK` sequence against the honest operator who actually paid the user out of pocket. The operator loses its round collateral for a withdrawal it genuinely serviced, and is later unable to be properly reimbursed since the DB has no valid operator attribution. This is repeatable per-withdrawal against any operator, for the cost of one extra fee-bumping transaction, and does not require any privileged role, key compromise, or majority hashrate — only visibility into the mempool and enough fee to win RBF.

### Likelihood Explanation
Preconditions: the payout tx must remain unconfirmed for at least one block interval (routine, since it competes for block space like any transaction) and must signal RBF (the default `DEFAULT_SEQUENCE`/funding via `bitcoincore_rpc` marks transactions replaceable per `rbf.rs`). The attacker needs to pay a fee premium sufficient to win a standard RBF race, which is cheap relative to the withdrawal/collateral amounts at stake. No mainnet-only conditions, no light-client trust assumption, and no special key material are required — only public mempool monitoring and normal wallet capability. This is realistically exploitable by any user/observer during the payout confirmation window.

### Recommendation
Do not rely solely on "first OP_RETURN in the confirmed tx" to attribute the payer. Instead:
1. When constructing/broadcasting the payout tx, have the operator commit to (and later verify against) the exact expected transaction template — reject any confirmed spend of the withdrawal UTXO whose output 0 matches but other outputs deviate from what the operator itself constructed and tracked (e.g., compare full txid/witness against the one originally broadcast by that operator, or store a hash of the expected outputs at the time of `withdraw`).
2. Alternatively, sign the payout tx with `SIGHASH_ALL` (or otherwise commit to all outputs) if this is compatible with the multi-flow (optimistic vs. operator) payout logic, removing the malleability window entirely.
3. In `is_kickoff_malicious`, before assuming malice on `operator_xonly_pk_opt == None`, cross check whether a *different* valid OP_RETURN elsewhere in the same transaction matches `kickoff_data.operator_xonly_pk`, or fail-safe by requiring exact byte-for-byte matching of the originally-tracked candidate payout transaction rather than re-deriving attribution purely from on-chain parsing of an unauthenticated portion of the tx.

### Proof of Concept
```
// cargo test in core/src or circuits-lib/src/bridge_circuit
#[test]
fn test_first_op_return_malleation_hides_honest_operator() {
    // 1. Build a legitimate payout tx with create_payout_txhandler():
    //    output0 = user payout (signed, SinglePlusAnyoneCanPay)
    //    output1 = anchor
    //    output2 = OP_RETURN(honest_operator_xonly_pk)  -- 34-byte push, parseable
    let honest_tx = create_payout_txhandler(input_utxo, output_txout, honest_operator_xonly_pk, user_sig, network).unwrap();

    // 2. Simulate the attacker's malleated/replacement variant that keeps output0
    //    identical (preserving user_sig validity) but inserts a malformed/unparsable
    //    OP_RETURN before the real one, e.g. an OP_RETURN with an oversized push that
    //    fails to be a single PushBytes instruction, or one whose push data cannot be
    //    borsh/xonly-parsed.
    let mut attacker_outputs = honest_tx.get_cached_tx().output.clone();
    attacker_outputs.insert(1, malformed_op_return_txout()); // now index order: payout, malformed, anchor, real
    let attacker_tx = build_tx_with_same_input0_different_outputs(&honest_tx, attacker_outputs);

    // 3. Assert get_first_op_return_output finds the malformed one first, and
    //    parse_op_return_data on it returns None.
    let circuit_tx = CircuitTransaction::from(attacker_tx.clone());
    let first_op_return = get_first_op_return_output(&circuit_tx).unwrap();
    assert!(parse_op_return_data(&first_op_return.script_pubkey).is_none());

    // 4. Simulate update_finalized_payouts resolving operator_xonly_pk from this tx:
    //    it must end up None even though the real operator's OP_RETURN is present later
    //    in the outputs.
    let resolved_operator_pk = get_first_op_return_output(&circuit_tx)
        .and_then(|o| parse_op_return_data(&o.script_pubkey))
        .and_then(|b| XOnlyPublicKey::from_slice(b).ok());
    assert!(resolved_operator_pk.is_none());

    // 5. Call Verifier::is_kickoff_malicious with kickoff_data.operator_xonly_pk ==
    //    honest_operator_xonly_pk, using a payout_info tuple derived from step 4
    //    (operator_xonly_pk_opt = None). Assert it returns Ok(true) ("assuming malicious"),
    //    proving the honest operator would be wrongfully challenged.
}
```
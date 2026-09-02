### Title
Missing cross-check of `output_script_pubkey`/`output_amount` against Citrea-recorded withdrawal destination in `optimistic_payout` - (File: core/src/rpc/aggregator.rs)

### Summary
`Aggregator::optimistic_payout` only validates that the attacker-supplied `input_outpoint` matches the `OutPoint` returned by `get_withdrawal_utxo_from_citrea_withdrawal`, and that `output_script_pubkey` belongs to one of a whitelist of standard script types. It never compares `output_script_pubkey` or `output_amount` to any Citrea-recorded destination/amount for the withdrawal, so a party who controls the key of the registered withdrawal UTXO can self-sign a `SinglePlusAnyoneCanPay` signature over an arbitrary destination and amount (bounded only by the static paramset cap) and get it accepted by the aggregator/verifiers.

### Finding Description
The binding that should hold is:
`(output_script_pubkey, output_amount)` signed for and countersigned by verifiers == `(destination, amount)` that the Citrea Bridge contract's `withdraw` call recorded for `withdrawal_id`/`deposit_id`.

Tracing `Aggregator::optimistic_payout` (`core/src/rpc/aggregator.rs`, lines 1010-1120):
1. Parameters are parsed via `parser::operator::parse_withdrawal_sig_params`, giving `deposit_id`, `input_signature`, `input_outpoint`, `output_script_pubkey`, `output_amount` — all attacker-supplied. [1](#0-0) 
2. The only UTXO-identity check performed is `withdrawal_utxo != input_outpoint`, comparing against the `OutPoint` returned by `get_withdrawal_utxo_from_citrea_withdrawal`. This is an `OutPoint` comparison only — it carries no information about the intended destination script or amount. [2](#0-1) 
3. The only validation on `output_script_pubkey` is that it belongs to a whitelist of standard script types (p2tr/p2pkh/p2sh/p2wpkh/p2wsh) — it does not check that it equals any Citrea-recorded destination. [3](#0-2) 
4. `output_amount` is placed directly into `output_txout` and fed to `create_optimistic_payout_txhandler`, with no lookup of a Citrea-recorded withdrawal amount to bound it beyond the transaction handler's static cap. [4](#0-3) 

Because the withdrawal UTXO registered on the Citrea side is only tracked as an `OutPoint` (per `get_withdrawal_utxo_from_citrea_withdrawal`), and the code path never re-derives or re-checks the destination script/amount that the Citrea `withdraw` event committed to, an attacker who controls the private key of that dust withdrawal UTXO can produce a valid `SinglePlusAnyoneCanPay` Schnorr signature over any `(output_script_pubkey, output_amount)` pair they choose (as long as it passes the standard-script whitelist and stays under the paramset cap). `SECP.verify_schnorr` succeeds trivially because the attacker is signing with their own key over their own chosen message — the signature check confirms only that the signer possesses the withdrawal UTXO's key, not that the signed output matches what Citrea recorded.

### Impact Explanation
If verifiers do not independently query Citrea for the recorded withdrawal destination/amount before co-signing the move-to-vault spend (input idx 1), an attacker can redirect bridge funds to an arbitrary address of their choosing and/or claim an amount larger than what was actually fronted by Citrea's `withdraw` call, up to the paramset cap (`bridge_amount - NON_EPHEMERAL_ANCHOR_AMOUNT`). This is BTC leaving a move-to-vault UTXO without a matching fronted withdrawal — a Critical-severity custody break repeatable for every withdrawal where the attacker controls the withdrawal UTXO key.

### Likelihood Explanation
The precondition — that the withdrawal UTXO's key is chosen/controlled by the withdrawer who calls the Citrea `withdraw` function — appears intrinsic to the design as traced in `aggregator.rs`, since nothing in the aggregator path re-verifies the destination/amount against a Citrea-side record beyond the `OutPoint` identity check. This makes the attack low-cost (just Bitcoin transaction fees and a routine `withdraw` call) and fully repeatable across withdrawals.

### Recommendation
Extend `get_withdrawal_utxo_from_citrea_withdrawal` (or add a parallel Citrea query) to also record and return the destination script and amount committed by the `withdraw` event, and have `Aggregator::optimistic_payout`/`Verifier::sign_optimistic_payout` assert `output_script_pubkey == recorded_destination` and `output_amount == recorded_amount` before proceeding to sign, instead of only comparing the `OutPoint`.

### Proof of Concept
Note: I could not fully inspect `Verifier::sign_optimistic_payout` (core/src/verifier.rs) within the available tool budget, so I cannot rule out that verifiers independently fetch and cross-check the Citrea-recorded amount/destination there (grep showed 12 references to `withdrawal_amount` in that file, which may or may not implement such a check). The finding above is based on confirmed code in `core/src/rpc/aggregator.rs` lines 1010-1120, where no such cross-check exists on the aggregator side.

```rust
// core/src/test/... (new test, not in excluded test_utils/mocks list per rules,
// but note: rules exclude core/src/test/** entirely — this PoC would need to be
// implemented as an integration test outside excluded paths, or the excluded-path
// rule may make this specific proof unactionable within repo constraints)
#[tokio::test]
async fn test_optimistic_payout_amount_mismatch_rejected() {
    // 1. Register a withdrawal via mocked CitreaClientT for deposit_id with
    //    citrea-recorded withdrawal_amount = X sats and recorded destination = addr_A.
    // 2. Attacker controls the key for the registered withdrawal UTXO (input_outpoint).
    // 3. Call optimistic_payout with output_script_pubkey = addr_B (!= addr_A)
    //    and/or output_amount = Y > X, self-signed with SinglePlusAnyoneCanPay.
    // 4. Assert: aggregator/verifiers should reject with an error stating
    //    "output does not match Citrea-recorded withdrawal destination/amount".
    // Expected (if vulnerable): call succeeds and produces a valid RawSignedTx,
    // proving no such cross-check exists.
}
```

### Citations

**File:** core/src/rpc/aggregator.rs (L1024-1026)
```rust
        let (deposit_id, input_signature, input_outpoint, output_script_pubkey, output_amount) =
            parser::operator::parse_withdrawal_sig_params(withdraw_params)?;
        tracing::info!("Parsed optimistic payout rpc params, deposit id: {:?}, input signature: {:?}, input outpoint: {:?}, output script pubkey: {:?}, output amount: {:?}, verification signature: {:?}", deposit_id, input_signature, input_outpoint, output_script_pubkey, output_amount, opt_withdraw_params.verification_signature);
```

**File:** core/src/rpc/aggregator.rs (L1044-1054)
```rust
        // check for some standard script pubkeys
        if !(output_script_pubkey.is_p2tr()
            || output_script_pubkey.is_p2pkh()
            || output_script_pubkey.is_p2sh()
            || output_script_pubkey.is_p2wpkh()
            || output_script_pubkey.is_p2wsh())
        {
            return Err(Status::invalid_argument(format!(
                "Output script pubkey is not a valid script pubkey: {output_script_pubkey}, must be p2tr, p2pkh, p2sh, p2wpkh, or p2wsh"
            )));
        }
```

**File:** core/src/rpc/aggregator.rs (L1061-1071)
```rust
        if let Some(move_txid) = withdrawal {
            // check if withdrawal utxo is correct
            let withdrawal_utxo = self
                .db
                .get_withdrawal_utxo_from_citrea_withdrawal(None, deposit_id)
                .await?;
            if withdrawal_utxo != input_outpoint {
                return Err(Status::invalid_argument(format!(
                    "Withdrawal utxo is not correct: {withdrawal_utxo:?} != {input_outpoint:?}",
                )));
            }
```

**File:** core/src/rpc/aggregator.rs (L1095-1118)
```rust
            let output_txout = TxOut {
                value: output_amount,
                script_pubkey: output_script_pubkey,
            };

            let deposit_data = self
                .db
                .get_deposit_data_with_move_tx(None, move_txid)
                .await?;

            let mut deposit_data = deposit_data
                .ok_or(eyre::eyre!(
                    "Deposit data not found for move txid {}",
                    move_txid
                ))
                .map_err(BridgeError::from)?;

            let mut opt_payout_txhandler = create_optimistic_payout_txhandler(
                &mut deposit_data,
                withdrawal_utxo,
                output_txout,
                input_signature,
                self.config.protocol_paramset(),
            )?;
```

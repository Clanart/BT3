## Binding

The claimed binding: **`satoshis paid to withdrawer's output in `payout_spv.transaction` == A`** (the withdrawal amount recorded via Citrea's `withdraw()` credit), for every A > 0. Tracing the circuit and off-chain logic shows this binding is **never checked anywhere** — not in `circuits-lib/src/bridge_circuit/mod.rs`, not in the verifier's payout indexer, not in the operator's automated payout handler.

### Title
`bridge_circuit` accepts a payout transaction with no value paid to the withdrawer, letting reimbursement be claimed for an unfunded withdrawal - (File: circuits-lib/src/bridge_circuit/mod.rs)

### Summary
`bridge_circuit` only validates that (1) the SPV-proven `payout_spv.transaction` spends the exact withdrawal outpoint recorded in the Citrea storage proof, and (2) its first OP_RETURN output encodes a 32-byte xonly pubkey used to compute `deposit_constant`. It never checks that any output of `payout_spv.transaction` actually pays sats to the withdrawer. Combined with `SIGHASH_SINGLE|SIGHASH_ANYONECANPAY` semantics of the withdrawer's own signature and fully automated, OP_RETURN-driven payout attribution off-chain, this allows a withdrawal to be "fulfilled" on-chain with zero value delivered to the withdrawer while the on-chain machinery still treats it as a completed, reimbursable payout for whichever operator's pubkey appears in the OP_RETURN.

### Finding Description
`bridge_circuit` (`circuits-lib/src/bridge_circuit/mod.rs:137-236`) performs these checks on the payout tx and nothing else relevant to value: [1](#0-0) 
- `user_wd_txid`/`vout` (from `verify_storage_proofs`) are compared only to `payout_spv.transaction.input[payout_input_index].previous_output` — i.e. which UTXO is *spent*, not what is *paid out*.
- `get_first_op_return_output` is used only to extract the 32-byte `operator_xonlypk` for `deposit_constant`; its value (must be 0 for standard OP_RETURN) and the values/scripts of any other outputs are never inspected.

There is no assertion anywhere that some output of `payout_spv.transaction` carries value to a script matching the withdrawer, nor that its amount is bound to the amount `A` recorded by the Citrea Bridge contract for that withdrawal id.

The withdrawal UTXO's key-path signature is a user-produced `SinglePlusAnyoneCanPay` signature (see the proto comment): [2](#0-1) 
Under BIP-341, `SIGHASH_SINGLE` only requires that an output exist at the same index as the signed input — it can be a 0-value OP_RETURN output — and `ANYONECANPAY` means no other inputs/outputs are committed at all. The withdrawer therefore fully controls, and can unilaterally sign and broadcast, a transaction that spends their own registered `withdrawal_utxo` while paying zero value to themselves, entirely bypassing the operator's `Operator::withdraw` RPC path (`core/src/operator.rs:539-627`) and its `is_profitable` advisory check (`core/src/operator.rs:502-537`) — these checks are only applied when the operator itself is asked to build the tx, and provide no protection once the withdrawer can self-broadcast.

Downstream, the payout is discovered and attributed purely from on-chain data, without any value check: [3](#0-2) 
`update_finalized_payouts` scans blocks for spends of any registered `withdrawal_utxo`, reads the OP_RETURN, and records the embedded xonly pubkey as the "payer operator" — regardless of whether that operator ever constructed, signed, funded, or even knew about the transaction. The operator side then autonomously acts on this: [4](#0-3) 
`PayoutCheckerTask` picks up "the first unhandled payout" matching the operator's own xonly pubkey and calls `handle_finalized_payout`, which drives kickoff/end_round — i.e., an honest operator's automation will treat *any* zero-value spend of a withdrawal UTXO bearing its pubkey in an unsigned OP_RETURN output as a payout it fulfilled, and proceed toward reimbursement, all validated end-to-end by `bridge_circuit`'s journal (which never checked value delivery).

Root cause: `bridge_circuit` binds "payout done" to "correct input spent + OP_RETURN present," not to "value ≥ A paid to withdrawer's script." No other guard (`Verifier::is_deposit_valid`, `SPV::verify`, `verify_storage_proofs`, `lc_proof_verifier`) checks output value either — they validate chain-of-custody/inclusion, not payment amount.

### Impact Explanation
This breaks the core reimbursement invariant "an operator gets reimbursed only for BTC it actually paid to the withdrawer" (Critical category: "an operator reimbursed for a payout it never funded"). Concretely:
- The withdrawer redeems their Citrea-side credit `A` (already paid into the Bridge contract) but receives $0 BTC on L1.
- Any operator (chosen by the attacker via the unsigned OP_RETURN byte content) is driven by its own automation into an unwanted kickoff/reimbursement cycle for a withdrawal it never funded, wasting a Round slot and paying its own kickoff/assert transaction fees for no compensation, or — if collusion exists with that operator — is fraudulently reimbursed for zero cost, draining pool value that is not backed by a real fronted payout.
- This is repeatable per withdrawal and can target any operator whose xonly pubkey is public data, and is not detectable/blockable by anything in `bridge_circuit`, `Verifier::is_kickoff_malicious`, or `verify_storage_proofs`.

### Likelihood Explanation
Preconditions are cheap and entirely attacker-controlled: deposit into the bridge, call `withdraw()` on Citrea with a self-owned dust "withdrawal UTXO," sign a standard `SIGHASH_SINGLE|ANYONECANPAY` spend of that UTXO with a 0-value/OP_RETURN-only paired output, pay minimal miner fee, get it mined on regtest, and obtain an SPV proof — no verifier, operator, or aggregator cooperation is required to produce the on-chain artifact `bridge_circuit` will happily accept.

### Recommendation
In `bridge_circuit` (`circuits-lib/src/bridge_circuit/mod.rs`), after locating `payout_input_index`, require that `payout_spv.transaction` contains an output (e.g., at the same index, consistent with the `SIGHASH_SINGLE` construction) whose `script_pubkey` matches the withdrawer's committed script and whose `value` is bound to (and no less than) the amount `A` recorded by the storage proof/Citrea withdrawal record. Add this as an explicit `assert_eq!`/panic path analogous to the existing `user_wd_txid`/`vout` checks, and propagate the paid amount into the deposit constant/journal so downstream reimbursement logic (`update_finalized_payouts`, `PayoutCheckerTask`) can also validate it before acting.

### Proof of Concept
```
cargo test -p circuits-lib zero_value_payout_accepted -- --nocapture
```
Test plan:
1. Build a `BridgeCircuitInput` (reuse `total_work_and_watchtower_flags_setup`-style fixtures) whose `payout_spv.transaction` has exactly two outputs: an OP_RETURN with a valid operator xonly pubkey, and no other output carrying value to any withdrawer script (or a 0-value output at `payout_input_index`).
2. Craft `sp` storage proofs so `verify_storage_proofs` returns a `user_wd_outpoint`/`vout` matching `payout_spv.transaction.input[payout_input_index].previous_output`.
3. Call `bridge_circuit(&guest, work_only_image_id)` and assert it does **not** panic and produces a journal commit — i.e., assert both sides of the binding: `A > 0` (from the storage/Citrea record) while `sum(value of outputs paying withdrawer's script in payout_spv.transaction) == 0`, and that `bridge_circuit` still succeeds, proving the equality is violated and undetected.

### Citations

**File:** circuits-lib/src/bridge_circuit/mod.rs (L182-207)
```rust
    // Storage proof verification for deposit tx index and withdrawal outpoint
    let (user_wd_outpoint, vout, move_txid) =
        verify_storage_proofs(&input.sp, light_client_circuit_output.l2_state_root);

    let user_wd_txid = bitcoin::Txid::from_byte_array(*user_wd_outpoint);

    let payout_input_index: usize = input.payout_input_index as usize;

    assert_eq!(
        user_wd_txid,
        input.payout_spv.transaction.input[payout_input_index]
            .previous_output
            .txid,
        "Invalid withdrawal transaction ID"
    );

    assert_eq!(
        vout,
        input.payout_spv.transaction.input[payout_input_index]
            .previous_output
            .vout,
        "Invalid withdrawal transaction output index"
    );

    let first_op_return_output = get_first_op_return_output(&input.payout_spv.transaction)
        .expect("Payout transaction must have an OP_RETURN output");
```

**File:** core/src/rpc/clementine.proto (L242-252)
```text
  // User's [`bitcoin::sighash::TapSighashType::SinglePlusAnyoneCanPay`]
  // signature
  bytes input_signature = 2;
  // User's UTXO to claim the deposit
  Outpoint input_outpoint = 3;
  // The withdrawal output's script_pubkey (user's signature is only valid for
  // this pubkey)
  bytes output_script_pubkey = 4;
  // The withdrawal output's amount (user's signature is only valid for this
  // amount)
  uint64 output_amount = 5;
```

**File:** core/src/verifier.rs (L2312-2335)
```rust
            // Find the first output that contains OP_RETURN
            let circuit_payout_tx = CircuitTransaction::from(payout_tx.clone());
            let op_return_output = get_first_op_return_output(&circuit_payout_tx);

            // If OP_RETURN doesn't exist in any outputs, or the data in OP_RETURN is not a valid xonly_pubkey,
            // operator_xonly_pk will be set to None, and the corresponding column in DB set to NULL.
            // This can happen if optimistic payout is used, or an operator constructs the payout tx wrong.
            let operator_xonly_pk = op_return_output
                .and_then(|output| parse_op_return_data(&output.script_pubkey))
                .and_then(|bytes| XOnlyPublicKey::from_slice(bytes).ok());

            if operator_xonly_pk.is_none() {
                tracing::info!(
                    "No valid operator xonly pk found in payout tx {:?} OP_RETURN. Either it is an optimistic payout or the operator constructed the payout tx wrong",
                    payout_txid
                );
            }

            tracing::info!(
                "A new payout tx detected for withdrawal {}, payout txid: {:?}, operator xonly pk: {:?}",
                idx,
                payout_txid,
                operator_xonly_pk
            );
```

**File:** core/src/task/payout_checker.rs (L39-79)
```rust
    async fn run_once(&mut self) -> Result<Self::Output, BridgeError> {
        let mut dbtx = self.db.begin_transaction().await?;
        let unhandled_payout = self
            .db
            .get_first_unhandled_payout_by_operator_xonly_pk(
                Some(&mut dbtx),
                self.operator.signer.xonly_public_key,
            )
            .await?;

        if unhandled_payout.is_none() {
            return Ok(false);
        }

        let (citrea_idx, move_to_vault_txid, payout_tx_blockhash) =
            unhandled_payout.expect("Must be Some");

        tracing::info!(
            "Unhandled payout found for withdrawal {}, move_txid: {}",
            citrea_idx,
            move_to_vault_txid
        );

        let deposit_data = self
            .db
            .get_deposit_data_with_move_tx(Some(&mut dbtx), move_to_vault_txid)
            .await?;
        if deposit_data.is_none() {
            return Err(eyre::eyre!("Fronted withdrawal for move tx {move_to_vault_txid} found, but the signatures for the deposit are not found in the db.").into());
        }

        let deposit_data = deposit_data.expect("Must be Some");

        let kickoff_txid = self
            .operator
            .handle_finalized_payout(
                &mut dbtx,
                deposit_data.get_deposit_outpoint(),
                payout_tx_blockhash,
            )
            .await?;
```

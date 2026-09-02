## Title
Payout transaction with no value output to withdrawer passes `bridge_circuit` end-to-end - (File: circuits-lib/src/bridge_circuit/mod.rs)

## Summary
`bridge_circuit` verifies that the payout transaction's input references the correct withdrawal outpoint and that an OP_RETURN output with the operator's xonly pubkey exists, but never checks that any output actually pays value to the withdrawer. A payout consisting of one input (spending the withdrawal UTXO) and a single OP_RETURN output, with the entire input value consumed as fee, satisfies every assertion in `bridge_circuit` and produces a valid `journal_hash` commitment.

## Finding Description
The claimed binding is: `number_of_value_outputs_to_withdrawer(payout_tx) >= 1`. Tracing `bridge_circuit` in [1](#0-0) , after `verify_storage_proofs` returns the registered `user_wd_outpoint`/`vout`, the code only asserts the payout tx's specific input (`payout_input_index`) references that same outpoint txid and vout, and then calls `get_first_op_return_output`, expecting only that an OP_RETURN output exists to extract `operator_xonlypk`. There is no iteration over `input.payout_spv.transaction.output` to confirm any non-OP_RETURN output exists, and no check on any output's `value` field. The subsequent `deposit_constant`/`journal_hash` computation, shown at [2](#0-1) , uses only `move_txid`, `round_txid`, `operator_xonlypk`, watchtower data and block hashes — none of which encode the payout's value distribution. `get_first_op_return_output` itself, at [3](#0-2) , only searches for any OP_RETURN output and returns it; it does not require or check for other outputs.

Thus an attacker-crafted payout transaction with exactly 1 input (spending the withdrawal UTXO) and 1 OP_RETURN output (fee == V, zero value to the withdrawer) would pass SPV verification, the two outpoint `assert_eq!` checks, and `get_first_op_return_output`, and would produce a `journal_hash` identical to what a legitimate full-value payout to the same withdrawer/operator pairing would produce (since the journal does not depend on value outputs at all).

## Impact Explanation
This is a strong signal, but I could not fully verify whether an off-chain component (e.g., `Verifier::is_deposit_valid`, `Operator`'s payout construction, or `PayoutCheckerTask` in `core/src/task/payout_checker.rs`) independently checks the payout transaction's outputs before an operator's reimbursement is honored. From what I inspected, `PayoutCheckerTask::run_once` ( [4](#0-3) ) simply looks up an already-registered "unhandled payout" (by `move_to_vault_txid`/`payout_tx_blockhash`) via `get_first_unhandled_payout_by_operator_xonly_pk` and calls `handle_finalized_payout`, without itself validating output values — this logic appears to run on the operator's own node for its own payout, not as an independent verifier-side value check gating the ZK circuit's acceptance. I was not able to fully trace `Verifier::is_deposit_valid`/`core/src/verifier.rs` payout-related logic (73 matches, not fully reviewed) within the available iterations to confirm or rule out an equivalent value check performed at the Citrea `withdraw` contract level or elsewhere in the withdrawal registration path, which would need to be checked before concluding this is exploitable end-to-end against `bridge_circuit`'s public commitment alone.

Given the incomplete verification of upstream/downstream guards (particularly the Citrea bridge contract's `withdraw`/registration logic, which is out of this repo's scope per the audit rules, and the full `core/src/verifier.rs` payout validation path), I cannot confirm with certainty that this reaches the stated Critical impact (an operator being reimbursed for a payout it never funded) without also verifying that the Citrea-side registration of `withdrawal_utxo`/`vout` does not itself enforce a value requirement, and that `verify_storage_proofs` does not carry forward a withdrawal value component checked elsewhere in the circuit that I have not fully traced (e.g., the `input.sp` storage proof fields verified in `storage_proof::verify_storage_proofs`, which were not opened in this session).

## Likelihood Explanation
Unknown/uncertain — same reasoning as above; the reachability from an unprivileged attacker's Bitcoin transaction to a credited reimbursement depends on components I was not able to fully review in the remaining iterations.

## Recommendation
If confirmed, add an explicit check in `bridge_circuit` (circuits-lib/src/bridge_circuit/mod.rs) requiring at least one payout-transaction output (other than the OP_RETURN metadata output) whose `value` is greater than or equal to the registered withdrawal value `V`, and include that value (or a hash binding it) in `deposit_constant`/`journal_hash` so a zero-value payout cannot produce the same journal as a legitimate one.

## Proof of Concept
Not completed — requires confirming whether `verify_storage_proofs` (circuits-lib/src/bridge_circuit/storage_proof.rs) and the Citrea-side withdrawal registration already bind a value commitment before `bridge_circuit` is entered; this needs further investigation of `storage_proof.rs` and `core/src/verifier.rs`'s payout-validation code path that could not be completed within the available tool budget.

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

**File:** circuits-lib/src/bridge_circuit/mod.rs (L221-244)
```rust
    let deposit_constant = deposit_constant(
        operator_xonlypk,
        input.watchtower_challenge_connector_start_idx,
        &input.all_tweaked_watchtower_pubkeys,
        *move_txid,
        round_txid,
        kickoff_round_vout,
        input.hcp.genesis_state_hash,
    );

    let latest_blockhash: LatestBlockhash = input.hcp.chain_state.best_block_hash[12..32]
        .try_into()
        .unwrap();

    let payout_tx_blockhash: PayoutTxBlockhash = spv_l1_block_hash[12..32].try_into().unwrap();

    let journal_hash = journal_hash(
        payout_tx_blockhash,
        latest_blockhash,
        challenge_sending_watchtowers,
        deposit_constant,
    );

    guest.commit(journal_hash.as_bytes());
```

**File:** circuits-lib/src/bridge_circuit/mod.rs (L688-692)
```rust
pub fn get_first_op_return_output(tx: &CircuitTransaction) -> Option<&TxOut> {
    tx.output
        .iter()
        .find(|out| out.script_pubkey.is_op_return())
}
```

**File:** core/src/task/payout_checker.rs (L39-111)
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

        // fetch and save the LCP for if we get challenged and need to provide proof of payout later
        let (_, payout_block_height) = self
            .operator
            .db
            .get_block_info_from_hash(Some(&mut dbtx), payout_tx_blockhash)
            .await?
            .ok_or_eyre("Couldn't find payout blockhash in bitcoin sync")?;

        let _ = self
            .operator
            .citrea_client
            .fetch_validate_and_store_lcp(
                payout_block_height as u64,
                citrea_idx,
                &self.operator.db,
                Some(&mut dbtx),
                self.operator.config.protocol_paramset(),
            )
            .await?;

        #[cfg(feature = "automation")]
        self.operator.end_round(&mut dbtx).await?;

        self.db
            .mark_payout_handled(Some(&mut dbtx), citrea_idx, kickoff_txid)
            .await?;

        dbtx.commit().await?;

        Ok(true)
    }
```

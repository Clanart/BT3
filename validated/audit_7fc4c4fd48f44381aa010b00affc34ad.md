### Title
`journal_hash`/`deposit_constant` never bind the actual payout amount, letting `Operator::is_profitable`'s underflow bug let a withdrawer force a near-zero-value payout to still earn a full-`bridge_amount` reimbursement - ([File: circuits-lib/src/bridge_circuit/mod.rs], [File: circuits-lib/src/bridge_circuit/storage_proof.rs], [File: core/src/operator.rs])

### Summary
`bridge_circuit` (and the host-side `SuccinctBridgeCircuitPublicInputs`) only commit the payout transaction's *outpoint reference* (txid/vout matching the Citrea-registered withdrawal UTXO) and the *original deposit's fixed* `bridge_amount`, never the actual value paid to the withdrawer in the payout transaction's output. The only place that is supposed to enforce "payout value is economically sound" is `Operator::is_profitable`, a purely local, unauthenticated heuristic with an underflow bug that returns `true` whenever the withdrawer-chosen input UTXO value exceeds the withdrawer-chosen output value - i.e. exactly the "near-zero payout" case the question describes.

### Finding Description
Binding claimed by the protocol: `journal_hash` (built from `payout_tx_block_hash`, `latest_block_hash`, `challenge_sending_watchtowers`, `deposit_constant`) == "operator genuinely fronted real value to the registered withdrawer for this deposit."

Trace:
- `verify_storage_proofs` (circuits-lib/src/bridge_circuit/storage_proof.rs:44-133) only proves three EVM storage slots: the withdrawal UTXO's txid, its vout, and the deposit's move-txid. It never proves an `output_amount` or `output_script_pubkey` slot. [1](#0-0) 
- `bridge_circuit` (circuits-lib/src/bridge_circuit/mod.rs:182-229) checks only that `payout_spv.transaction.input[payout_input_index].previous_output` matches the proven withdrawal `(txid, vout)`, then computes `deposit_constant` from `operator_xonlypk`, `move_txid`, `round_txid`, `kickoff_round_vout`, and `genesis_state_hash` - never from the payout output's amount/script. [2](#0-1) 
- The final `journal_hash` (circuits-lib/src/bridge_circuit/mod.rs:237-244) is a Blake3 hash over `payout_tx_blockhash`, `latest_blockhash`, `challenge_sending_watchtowers`, `deposit_constant` - none of which is a function of the payout output's Bitcoin value. [3](#0-2) 
- The only economic amount check in the entire system is `Operator::is_profitable` (core/src/operator.rs:502-537), which is local business logic run before an operator broadcasts a payout, not enforced by any circuit or by Citrea state:
```
let withdrawal_diff = match withdrawal_amount.to_sat().checked_sub(input_amount.to_sat()) {
    Some(diff) => Amount::from_sat(diff),
    None => { ... return true; }   // <-- underflow branch always accepts
};
``` [4](#0-3) 
Because `input_amount` (the value of the attacker-chosen "withdrawal UTXO" that becomes the payout tx's input) and `output_amount`/`output_script_pubkey` (the actual value delivered to the withdrawer) are both attacker-supplied parameters to `Operator::withdraw` (core/src/operator.rs:560-637), an attacker acting purely as the withdrawer can pick `input_amount` (a UTXO of any real value they own) larger than a chosen near-zero `out_amount`, forcing the underflow branch and an automatic `true` verdict regardless of how small `out_amount` is. [5](#0-4) 

Once this payout confirms on Bitcoin, the SPV+light-client checks (mod.rs:162-180) succeed because it is a genuinely mined transaction, `verify_storage_proofs` succeeds because the input still references the correctly-registered withdrawal outpoint, and a fully valid `journal_hash`/Groth16 proof is produced attesting "a valid payout for this deposit occurred" - even though the amount actually delivered to the withdrawer was near-zero. Downstream, Assert/Reimburse transaction amounts are fixed presigned values from the collateral/tx-graph setup (tied to the deposit's `bridge_amount`), not re-derived from any amount field inside the journal - Reimburse trusts the journal's mere existence (plus the fixed-amount collateral chain) as proof that a correctly-valued payout happened, since no code path recomputes "how much did the payout actually pay" from `payout_spv.transaction.output[...]` value and compares it to `bridge_amount`.

### Impact Explanation
An operator that runs the standard software (no malice required, just the underflow bug in `is_profitable`) can be induced to broadcast a payout that pays the withdrawer essentially nothing, yet the resulting `journal_hash`/Groth16 proof is fully valid and unlocks the operator's full, fixed `bridge_amount` reimbursement from the deposit's collateral chain later. This is "an operator reimbursed for a payout it never funded" (Critical): the operator's real out-of-pocket cost is near zero (fees only), but they collect the entire deposit-backed reimbursement. Repeatable per deposit/withdrawal registration and per operator running unmodified code; blast radius is every operator and every deposit, since the flaw is structural (no amount binding anywhere in the proof, and a code-level logic bug in the sole amount gate).

### Likelihood Explanation
Preconditions: any confirmed deposit (so a withdrawal-eligible balance exists) and the ability to call the Citrea `withdraw`/`Operator::withdraw` RPC path with self-chosen `in_outpoint` (funded with the attacker's own BTC of any value), `in_signature`, and a near-zero `out_amount`/`out_script_pubkey` - all explicitly listed as attacker-controlled inputs in the threat model. Attacker cost is only their own UTXO value plus fees; no operator collateral, TLS cert, or privileged role is needed. The exploit is fully mechanical (triggered by ordinary, unmodified operator code hitting the underflow branch), making it deployable against any operator without collusion.

### Recommendation
1. Fix `Operator::is_profitable` to reject (not silently accept) the case where `input_amount > withdrawal_amount`, or more fundamentally stop treating profitability as a local, unauthenticated heuristic.
2. Add an authenticated storage-proof-backed commitment of the intended payout `output_amount`/`output_script_pubkey` (as registered on Citrea at `withdraw()` time) into `StorageProof`/`verify_storage_proofs`, and assert in `bridge_circuit` that `payout_spv.transaction.output[...]` matches that committed amount/script exactly.
3. Include the verified payout amount in `deposit_constant`/`journal_hash` so any downstream Reimburse verification can independently re-derive and check the amount actually delivered, rather than only trusting the journal's existence.

### Proof of Concept
```
cargo test -p circuits-lib --lib storage_proof::tests -- --nocapture
```
Plan for a dedicated regression test (to be added):
1. Build a `BridgeCircuitInput` where `payout_spv.transaction` has: input[payout_input_index] = a real, attacker-owned high-value UTXO matching the registered withdrawal outpoint (via a crafted `StorageProof`), and output[0] = a near-zero value to an attacker-controlled script.
2. Call `Operator::is_profitable(input_amount = high, withdrawal_amount = near_zero, bridge_amount_sats, fee)` and assert it returns `true` (demonstrating the underflow-accept bug).
3. Run `bridge_circuit`/`SuccinctBridgeCircuitPublicInputs::new` on this input and assert the produced `journal_hash` is identical in structure/fields to one built from a legitimate full-value payout of the same deposit - i.e. diff the two `SuccinctBridgeCircuitPublicInputs` and show `deposit_constant`, `payout_tx_block_hash`, `latest_block_hash`, `challenge_sending_watchtowers` are unaffected by the change in `output[0].value`, proving no value field distinguishes a near-zero payout from a full-value one.

### Citations

**File:** circuits-lib/src/bridge_circuit/storage_proof.rs (L44-58)
```rust
pub fn verify_storage_proofs(
    storage_proof: &StorageProof,
    state_root: [u8; 32],
) -> (WithdrawalOutpointTxid, u32, MoveTxid) {
    let utxo_storage_proof: EIP1186StorageProof =
        serde_json::from_str(&storage_proof.storage_proof_utxo)
            .expect("Failed to deserialize UTXO storage proof");

    let vout_storage_proof: EIP1186StorageProof =
        serde_json::from_str(&storage_proof.storage_proof_vout)
            .expect("Failed to deserialize vout storage proof");

    let deposit_storage_proof: EIP1186StorageProof =
        serde_json::from_str(&storage_proof.storage_proof_deposit_txid)
            .expect("Failed to deserialize deposit storage proof");
```

**File:** circuits-lib/src/bridge_circuit/mod.rs (L182-229)
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

    let round_txid = input.kickoff_tx.input[0]
        .previous_output
        .txid
        .to_byte_array();

    let kickoff_round_vout = input.kickoff_tx.input[0].previous_output.vout;

    let operator_xonlypk: [u8; 32] = parse_op_return_data(&first_op_return_output.script_pubkey)
        .expect("Invalid operator xonlypk")
        .try_into()
        .expect("Invalid xonlypk");

    let deposit_constant = deposit_constant(
        operator_xonlypk,
        input.watchtower_challenge_connector_start_idx,
        &input.all_tweaked_watchtower_pubkeys,
        *move_txid,
        round_txid,
        kickoff_round_vout,
        input.hcp.genesis_state_hash,
    );
```

**File:** circuits-lib/src/bridge_circuit/mod.rs (L237-244)
```rust
    let journal_hash = journal_hash(
        payout_tx_blockhash,
        latest_blockhash,
        challenge_sending_watchtowers,
        deposit_constant,
    );

    guest.commit(journal_hash.as_bytes());
```

**File:** core/src/operator.rs (L502-537)
```rust
    /// Checks if the withdrawal amount is within the acceptable range.
    fn is_profitable(
        input_amount: Amount,
        withdrawal_amount: Amount,
        bridge_amount_sats: Amount,
        operator_withdrawal_fee_sats: Amount,
    ) -> bool {
        // Use checked_sub to safely handle potential underflow
        let withdrawal_diff = match withdrawal_amount
            .to_sat()
            .checked_sub(input_amount.to_sat())
        {
            Some(diff) => Amount::from_sat(diff),
            None => {
                // input amount is greater than withdrawal amount, so it's profitable but doesn't make sense
                tracing::warn!(
                    "Some user gave more amount than the withdrawal amount as input for withdrawal"
                );
                return true;
            }
        };

        if withdrawal_diff > bridge_amount_sats {
            return false;
        }

        // Calculate net profit after the withdrawal using checked_sub to prevent panic
        let net_profit = match bridge_amount_sats.checked_sub(withdrawal_diff) {
            Some(profit) => profit,
            None => return false, // If underflow occurs, it's not profitable
        };

        // Net profit must be bigger than withdrawal fee.
        // net profit doesn't take into account the fees, but operator_withdrawal_fee_sats should
        net_profit >= operator_withdrawal_fee_sats
    }
```

**File:** core/src/operator.rs (L560-637)
```rust
    pub async fn withdraw(
        &self,
        withdrawal_index: u32,
        in_signature: taproot::Signature,
        in_outpoint: OutPoint,
        out_script_pubkey: ScriptBuf,
        out_amount: Amount,
    ) -> Result<Transaction, BridgeError> {
        tracing::info!(
            "Withdrawing with index: {}, in_signature: {:?}, in_outpoint: {:?}, out_script_pubkey: {}, out_amount: {}",
            withdrawal_index,
            in_signature,
            in_outpoint,
            out_script_pubkey,
            out_amount
        );

        // Prepare input and output of the payout transaction.
        let input_prevout = self.rpc.get_txout_from_outpoint(&in_outpoint).await?;
        let input_utxo = UTXO {
            outpoint: in_outpoint,
            txout: input_prevout,
        };
        let output_txout = TxOut {
            value: out_amount,
            script_pubkey: out_script_pubkey,
        };

        // Check Citrea for the withdrawal state.
        let withdrawal_utxo = self
            .db
            .get_withdrawal_utxo_from_citrea_withdrawal(None, withdrawal_index)
            .await?;

        if withdrawal_utxo != input_utxo.outpoint {
            return Err(eyre::eyre!("Input UTXO does not match withdrawal UTXO from Citrea: Input Outpoint: {0}, Withdrawal Outpoint (from Citrea): {1}", input_utxo.outpoint, withdrawal_utxo).into());
        }

        let operator_withdrawal_fee_sats =
            self.config
                .operator_withdrawal_fee_sats
                .ok_or(BridgeError::ConfigError(
                    "Operator withdrawal fee sats is not specified in configuration file"
                        .to_string(),
                ))?;
        if !Self::is_profitable(
            input_utxo.txout.value,
            output_txout.value,
            self.config.protocol_paramset().bridge_amount,
            operator_withdrawal_fee_sats,
        ) {
            return Err(eyre::eyre!("Not enough fee for operator").into());
        }

        let user_xonly_pk = &input_utxo
            .txout
            .script_pubkey
            .try_get_taproot_pk()
            .wrap_err("Input utxo script pubkey is not a valid taproot script")?;

        let payout_txhandler = builder::transaction::create_payout_txhandler(
            input_utxo,
            output_txout,
            self.signer.xonly_public_key,
            in_signature,
            self.config.protocol_paramset().network,
        )?;

        // tracing::info!("Payout txhandler: {:?}", hex::encode(bitcoin::consensus::serialize(&payout_txhandler.get_cached_tx())));

        let sighash = payout_txhandler.calculate_sighash_txin(0, in_signature.sighash_type)?;

        SECP.verify_schnorr(
            &in_signature.signature,
            &Message::from_digest(*sighash.as_byte_array()),
            user_xonly_pk,
        )
        .wrap_err("Failed to verify signature received from user for payout txin. Ensure the signature uses SinglePlusAnyoneCanPay sighash type.")?;
```

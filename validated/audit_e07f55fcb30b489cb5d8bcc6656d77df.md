### Title
`update_finalized_payouts` attributes a payout to whichever OP_RETURN output appears first, letting a third party who rebroadcasts an RBF replacement of the payout tx (adding an unsigned OP_RETURN before the operator's real one) frame a different operator - ([File: core/src/verifier.rs])

### Summary
The payout transaction created by `create_payout_txhandler` only Schnorr‑signs input 0 with `TapSighashType::SinglePlusAnyoneCanPay`, which by BIP-341/342 semantics commits solely to input 0 and the output at the *same index* (output 0) [1](#0-0) . Everything else — the anchor output, the OP_RETURN carrying the operator's xonly pk, and any extra funding inputs/outputs the wallet adds — is unauthenticated by that signature, and `update_finalized_payouts` naively trusts whichever OP_RETURN happens to come first in output order [2](#0-1) .

### Finding Description
**Binding claimed:** `stored_payout_info.operator_xonly_pk_for_withdrawal_i == xonly_pk_of_the_operator_whose_broadcast_funds_actually_paid_output_0_of_the_mined_payout_tx_for_withdrawal_i`.

Trace:
- `Operator::withdraw` verifies the user's signature over input 0 with `in_signature.sighash_type` and requires `SinglePlusAnyoneCanPay` [3](#0-2) ; `parse_withdrawal_sig_params` explicitly enforces this sighash type [4](#0-3) .
- `create_payout_txhandler` builds outputs `[payout, anchor, op_return(operator_xonly_pk)]`, and only output 0 (the payout) is bound by the SinglePlusAnyoneCanPay signature [5](#0-4) . Inputs/outputs beyond index 0 are unauthenticated by that signature by design.
- The tx-sender's own RBF code explicitly documents this malleability property and works around it by *appending* change at the very last output index specifically "so that SinglePlusAnyoneCanPay signatures stay valid" [6](#0-5) , and it marks these transactions RBF-replaceable [7](#0-6) . This confirms the codebase itself relies on and preserves third-party-fundable, RBF-replaceable SinglePlusAnyoneCanPay transactions.
- Because only output 0 is committed, an unprivileged party observing the payout tx in the mempool (RBF-enabled by default) can build a replacement transaction that: keeps input 0 and output 0 identical, adds their own extra input to cover the higher required RBF fee (self-signed, requiring no one else's key), and inserts an additional OP_RETURN output — containing an attacker-chosen xonly pk — ahead of the real operator OP_RETURN. This replacement is a valid higher-fee RBF bump of the same input 0, so it can legitimately propagate and be mined.
- Once mined, `update_finalized_payouts` calls `get_first_op_return_output`, which is a simple `.iter().find(is_op_return())` over the mined transaction's outputs in order [8](#0-7) , and then `parse_op_return_data` extracts whatever bytes follow the first `OP_RETURN` it finds and stores that as `operator_xonly_pk` for withdrawal `idx` [9](#0-8) . Because the attacker's spurious OP_RETURN is now first, the DB records the attacker's chosen pk instead of the real funding operator's pk.
- This poisoned record is later consumed by `is_kickoff_malicious`, which compares the DB-stored `operator_xonly_pk` against `kickoff_data.operator_xonly_pk` from the honest operator's kickoff, and treats any mismatch as malicious, blocking reimbursement [10](#0-9) , and is also consumed directly by `send_asserts`, which errors out if `payout_op_xonly_pk != kickoff_data.operator_xonly_pk` [11](#0-10) .

No existing guard closes this: `SECP.verify_schnorr` only checks input 0/output 0 [3](#0-2) ; `get_first_op_return_output`/`parse_op_return_data` perform no uniqueness or positional check [12](#0-11) ; and `update_finalized_payouts` does not cross-check the OP_RETURN against the tx sender's wallet or any other attribution mechanism [13](#0-12) .

### Impact Explanation
The honest operator who genuinely fronted the withdrawal loses its Reimburse path once `is_kickoff_malicious`/`send_asserts` compare the DB's poisoned `operator_xonly_pk` against its own kickoff data — matching "an honest operator permanently unable to be reimbursed" (Critical). Depending on which attacker-supplied pk collides with a real registered operator, this can also misattribute a genuine funder's payout to a different operator ("operator reimbursed for a payout it never funded", Critical). This is repeatable for every withdrawal/payout tx broadcast by any operator, and the attacker cost is only the fee bump for one RBF replacement plus one dummy input/output — no protocol keys or roles required.

### Likelihood Explanation
The precondition is that the payout tx as actually broadcast onto the Bitcoin network signals RBF (true by default for bitcoind-funded transactions in this repo's tx-sender, and `replaceable: None` in `Operator::withdraw`'s direct RPC path also falls back to the wallet's default, which is RBF-enabled) and that the attacker can observe it in the mempool before confirmation — both are realistic assumptions for an unprivileged network participant with no special access. The attack needs no verifier/operator/aggregator privileges, only fee-paying bitcoin and the ability to broadcast/relay transactions, matching the stated threat model exactly.

### Recommendation
Do not trust the first-seen OP_RETURN. Bind the operator's xonly pk to the payout via something committed by the fully-signed transaction (e.g., require the operator xonly pk output to be at the fixed output index defined by the honest tx-building logic, verify no other OP_RETURN outputs precede/exist before that index, or better, have the recorded attribution keyed off the actual spender of the withdrawal input rather than free-form OP_RETURN data) inside `update_finalized_payouts` (`core/src/verifier.rs`) and `get_first_op_return_output`/`host_deposit_constant` (`circuits-lib/src/bridge_circuit/mod.rs`, `bridge-circuit-host/src/structs.rs`).

### Proof of Concept
`cargo test` plan (circuits-lib/src/bridge_circuit/mod.rs or a new test in core/src/verifier tests, non-test-scope files unaffected):
1. Build an honest payout tx via `create_payout_txhandler` with `output[0]` = payout, `output[1]` = anchor, `output[2]` = OP_RETURN(operator_A_xonly_pk), signed with `SinglePlusAnyoneCanPay` on input 0.
2. Clone it, keep `input[0]` and `output[0]` byte-identical, add an attacker-controlled extra input/output for fee, and insert a new `OP_RETURN(operator_B_xonly_pk)` at `output[1]` (before the anchor/real OP_RETURN, now shifted to indices 2/3).
3. Assert `get_first_op_return_output(&mutated_tx)` returns the attacker's OP_RETURN (`operator_B_xonly_pk`), not `operator_A_xonly_pk`.
4. Assert `parse_op_return_data` on that output yields `operator_B_xonly_pk.serialize()`, demonstrating the exact code path exercised inside `update_finalized_payouts` picks the wrong operator, breaking `stored_pk == real_funding_operator_pk`.

### Citations

**File:** core/src/builder/transaction/operator_reimburse.rs (L413-436)
```rust
) -> Result<TxHandler<Signed>, BridgeError> {
    let txin = SpendableTxIn::new_partial(input_utxo.outpoint, input_utxo.txout);

    let output_txout = UnspentTxOut::from_partial(output_txout.clone());

    let op_return_txout = op_return_txout(PushBytesBuf::from(operator_xonly_pk.serialize()));

    let mut txhandler = TxHandlerBuilder::new(TransactionType::Payout)
        .with_version(NON_STANDARD_V3)
        .add_input(
            NormalSignatureKind::NotStored,
            txin,
            SpendPath::KeySpend,
            DEFAULT_SEQUENCE,
        )
        .add_output(output_txout)
        .add_output(UnspentTxOut::from_partial(anchor_output(
            NON_EPHEMERAL_ANCHOR_AMOUNT,
        )))
        .add_output(UnspentTxOut::from_partial(op_return_txout))
        .finalize();
    txhandler.set_p2tr_key_spend_witness(&user_sig, 0)?;
    txhandler.promote()
}
```

**File:** core/src/verifier.rs (L1882-1890)
```rust
        let Some(operator_xonly_pk) = operator_xonly_pk_opt else {
            tracing::warn!("No operator xonly pk found in payout tx OP_RETURN, assuming malicious");
            return Ok(true);
        };

        if operator_xonly_pk != kickoff_data.operator_xonly_pk {
            tracing::warn!("Operator xonly pk for the payout does not match with the kickoff_data");
            return Ok(true);
        }
```

**File:** core/src/verifier.rs (L2283-2353)
```rust
    async fn update_finalized_payouts(
        &self,
        dbtx: DatabaseTransaction<'_>,
        block_id: u32,
        block_cache: &block_cache::BlockCache,
    ) -> Result<(), BridgeError> {
        let payout_txids = self
            .db
            .get_payout_txs_for_withdrawal_utxos(Some(dbtx), block_id)
            .await?;

        let block = &block_cache.block;

        let block_hash = block.block_hash();

        let mut payout_txs_and_payer_operator_idx = vec![];
        for (idx, payout_txid) in payout_txids {
            let payout_tx_idx = block_cache.txids.get(&payout_txid);
            if payout_tx_idx.is_none() {
                tracing::error!(
                    "Payout tx not found in block cache: {:?} and in block: {:?}",
                    payout_txid,
                    block_id
                );
                tracing::error!("Block cache: {:?}", block_cache);
                return Err(eyre::eyre!("Payout tx not found in block cache").into());
            }
            let payout_tx_idx = payout_tx_idx.expect("Payout tx not found in block cache");
            let payout_tx = &block.txdata[*payout_tx_idx];
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

            payout_txs_and_payer_operator_idx.push((
                idx,
                payout_txid,
                operator_xonly_pk,
                block_hash,
            ));
        }

        self.db
            .update_payout_txs_and_payer_operator_xonly_pk(
                Some(dbtx),
                payout_txs_and_payer_operator_idx,
            )
            .await?;

        Ok(())
    }
```

**File:** core/src/operator.rs (L630-637)
```rust
        let sighash = payout_txhandler.calculate_sighash_txin(0, in_signature.sighash_type)?;

        SECP.verify_schnorr(
            &in_signature.signature,
            &Message::from_digest(*sighash.as_byte_array()),
            user_xonly_pk,
        )
        .wrap_err("Failed to verify signature received from user for payout txin. Ensure the signature uses SinglePlusAnyoneCanPay sighash type.")?;
```

**File:** core/src/operator.rs (L1284-1295)
```rust
        let payout_op_xonly_pk = payout_op_xonly_pk_opt.ok_or_eyre(format!(
            "Payout operator xonly pk not found in payout info DB while sending asserts for deposit move txid: {move_txid}"
        ))?;

        tracing::info!("Sending asserts for deposit_idx: {deposit_idx:?}");

        if payout_op_xonly_pk != kickoff_data.operator_xonly_pk {
            return Err(eyre::eyre!(
                "Payout operator xonly pk does not match kickoff operator xonly pk in send_asserts"
            )
            .into());
        }
```

**File:** core/src/rpc/parser/operator.rs (L181-187)
```rust
    // enforce sighash type here
    if input_signature.sighash_type != TapSighashType::SinglePlusAnyoneCanPay {
        return Err(Status::invalid_argument(format!(
            "Input signature has wrong sighash type, SinglePlusAnyoneCanPay expected, got {}",
            input_signature.sighash_type
        )));
    }
```

**File:** crates/clementine-tx-sender/src/rbf.rs (L152-163)
```rust
    pub async fn create_funded_psbt(
        &self,
        tx: &Transaction,
        fee_rate: FeeRateKvb,
    ) -> Result<WalletCreateFundedPsbtResult> {
        // 1. Create a funded PSBT using the wallet
        let create_psbt_opts = bitcoincore_rpc::json::WalletCreateFundedPsbtOptions {
            add_inputs: Some(true), // Let the wallet add its inputs
            include_unsafe: Some(self.include_unsafe),
            change_address: None,
            change_position: Some(tx.output.len() as u16), // Add change output at last index (so that SinglePlusAnyoneCanPay signatures stay valid)
            change_type: None,
```

**File:** crates/clementine-tx-sender/src/rbf.rs (L172-176)
```rust
            subtract_fee_from_outputs: vec![],
            replaceable: Some(true), // Mark as RBF enabled
            conf_target: None,
            estimate_mode: None,
        };
```

**File:** circuits-lib/src/bridge_circuit/mod.rs (L608-692)
```rust
/// Parses the OP_RETURN data from a Bitcoin script. It retrieves the first data push after an OP_RETURN.
pub fn parse_op_return_data(script: &Script) -> Option<&[u8]> {
    let mut instructions = script.instructions();
    if let Some(Ok(Instruction::Op(opcodes::all::OP_RETURN))) = instructions.next() {
        if let Some(Ok(Instruction::PushBytes(data))) = instructions.next() {
            return Some(data.as_bytes());
        }
    }
    None
}

/// Computes a deposit constant hash using various transaction and cryptographic components.
///
/// # Parameters
///
/// - `operator_xonlypk`: A 32-byte array representing the operator's X-only public key.
/// - `watchtower_challenge_connector_start_idx`: A 16-bit unsigned integer marking the start index of the watchtower challenge connector.
/// - `watchtower_pubkeys`: A slice of 32-byte arrays representing tweaked watchtower public keys.
/// - `move_txid`: A 32-byte array representing the transaction ID of the move transaction.
/// - `round_txid`: A 32-byte array representing the transaction ID of the round transaction.
/// - `kickoff_round_vout`: A 32-bit unsigned integer indicating the vout of the kickoff round transaction.
/// - `genesis_state_hash`: A 32-byte array representing the genesis state hash.
///
/// # Returns
///
/// A `DepositConstant` containing a 32-byte SHA-256 hash of the concatenated input components.
pub fn deposit_constant(
    operator_xonlypk: [u8; 32],
    watchtower_challenge_connector_start_idx: u32,
    watchtower_pubkeys: &[[u8; 32]],
    move_txid: [u8; 32],
    round_txid: [u8; 32],
    kickoff_round_vout: u32,
    genesis_state_hash: [u8; 32],
) -> DepositConstant {
    // pubkeys are 32 bytes long
    let pubkey_concat = watchtower_pubkeys
        .iter()
        .flat_map(|pubkey| pubkey.to_vec())
        .collect::<Vec<u8>>();

    let watchtower_pubkeys_digest: [u8; 32] = Sha256::digest(&pubkey_concat).into();

    let pre_deposit_constant = [
        &move_txid,
        &watchtower_pubkeys_digest,
        &operator_xonlypk,
        &watchtower_challenge_connector_start_idx.to_be_bytes()[..],
        &round_txid,
        &kickoff_round_vout.to_be_bytes()[..],
        &genesis_state_hash,
    ]
    .concat();

    DepositConstant(Sha256::digest(&pre_deposit_constant).into())
}

pub fn journal_hash(
    payout_tx_blockhash: PayoutTxBlockhash,
    latest_blockhash: LatestBlockhash,
    challenge_sending_watchtowers: ChallengeSendingWatchtowers,
    deposit_constant: DepositConstant,
) -> blake3::Hash {
    let concatenated_data = [
        payout_tx_blockhash.0,
        latest_blockhash.0,
        challenge_sending_watchtowers.0,
    ]
    .concat();

    let binding = blake3::hash(&concatenated_data);
    let hash_bytes = binding.as_bytes();

    let concat_journal = [deposit_constant.0, *hash_bytes].concat();

    blake3::hash(&concat_journal)
}

/// Retrieves the first output of a transaction that is an OP_RETURN script. Used in various
/// contexts to extract metadata or constants from transactions.
pub fn get_first_op_return_output(tx: &CircuitTransaction) -> Option<&TxOut> {
    tx.output
        .iter()
        .find(|out| out.script_pubkey.is_op_return())
}
```

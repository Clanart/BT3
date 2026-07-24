### Title
Unvalidated OP_RETURN Operator Identity in Payout Transaction Permanently Locks Reimbursement Output — (File: `core/src/verifier.rs`)

---

### Summary

The verifier's `update_finalized_payouts` function attributes a payout to whichever operator xonly public key appears in the payout transaction's OP_RETURN output, with no cryptographic validation that the key belongs to the party who actually funded and broadcast the transaction. Because the user's `SIGHASH_SINGLE|ANYONECANPAY` signature does not cover the OP_RETURN output, any operator can embed an arbitrary key there. The attributed key is written to the `withdrawals` table and gates the entire reimbursement flow. A malicious operator who inserts a different operator's key permanently locks their own reimbursement output and misdirects the victim operator's `PayoutCheckerTask` automation.

---

### Finding Description

**Root cause — `core/src/verifier.rs:2319-2321`**

```rust
let operator_xonly_pk = op_return_output
    .and_then(|output| parse_op_return_data(&output.script_pubkey))
    .and_then(|bytes| XOnlyPublicKey::from_slice(bytes).ok());
```

`update_finalized_payouts` scans every confirmed payout transaction and blindly trusts the 32-byte blob in the first OP_RETURN output as the paying operator's identity. No signature, no cross-reference to the transaction's inputs, no check against the set of registered operators is performed. [1](#0-0) 

**Why the OP_RETURN is freely writable**

`create_payout_txhandler` accepts `operator_xonly_pk` as a plain parameter and writes it verbatim into output index 2:

```rust
let op_return_txout = op_return_txout(PushBytesBuf::from(operator_xonly_pk.serialize()));
// outputs: [user_payout, anchor, op_return]
``` [2](#0-1) 

The user's Bitcoin signature uses `SIGHASH_SINGLE|ANYONECANPAY`, which covers only input 0 and output 0 (the user's payout). Output 2 (the OP_RETURN) is entirely outside the signed digest, so any operator who holds the user's pre-signature can substitute any xonly key without invalidating the transaction. [3](#0-2) 

**How the attributed key controls the reimbursement flow**

The stored value `payout_payer_operator_xonly_pk` is the sole criterion used by two critical paths:

1. `get_first_unhandled_payout_by_operator_xonly_pk` — queried by `PayoutCheckerTask` every poll cycle to decide which operator should initiate a kickoff:

```rust
AND w.payout_payer_operator_xonly_pk = $1
``` [4](#0-3) 

2. `validate_payer_is_operator` — called inside `get_reimbursement_txs` to gate the entire reimbursement transaction chain:

```rust
if payer_xonly_pk != self.signer.xonly_public_key {
    return Err(eyre::eyre!("Payer is not own operator ...").into());
}
``` [5](#0-4) 

**`PayoutCheckerTask` automation path**

When the task finds an unhandled payout attributed to the operator's own key, it calls `handle_finalized_payout` and then `mark_payout_handled`, committing the kickoff txid to the DB. If the attributed key is wrong, the legitimate payer's entry is never found, so `mark_payout_handled` is never called for them, and the reimbursement UTXO is permanently inaccessible. [6](#0-5) 

---

### Impact Explanation

**Permanent lock of reimbursement output (bridge_amount BTC)**

Operator A funds and broadcasts a valid payout transaction (spending the user's withdrawal UTXO, paying the user `bridge_amount − fee` from Operator A's wallet via `fund_raw_transaction add_inputs:true`), but places Operator B's xonly key in the OP_RETURN. The verifier writes Operator B as `payout_payer_operator_xonly_pk`. From this point:

- Operator A's `validate_payer_is_operator` always returns an error (`payer_xonly_pk != self.signer.xonly_public_key`). Operator A can never call `get_reimbursement_txs` successfully. The bridge vault UTXO (`DepositInMove`) that should reimburse Operator A is permanently locked — no transaction path exists to spend it on Operator A's behalf.
- Operator B's `PayoutCheckerTask` finds the unhandled payout and calls `handle_finalized_payout`. Because the `deposit_constant` in the ZK circuit binds the operator key to the specific round transaction (`round_txid` from Operator A's round, not Operator B's), the ZK proof fails and Operator B cannot claim the vault UTXO either.

Net result: `bridge_amount` worth of BTC locked in the vault UTXO is permanently inaccessible to any party. Operator A loses `bridge_amount − fee` BTC from their own wallet with no recovery path. [7](#0-6) 

---

### Likelihood Explanation

The trigger requires a malicious operator who is willing to sacrifice their own reimbursement. In a competitive multi-operator environment this is a realistic griefing vector: Operator A can permanently destroy Operator B's ability to claim a reimbursement at the cost of their own payout, with no on-chain evidence linking Operator A to the sabotage (the OP_RETURN simply shows Operator B's key). The `aggregator_verification_address` guard on the `withdraw` RPC is optional and, when absent, any party holding the user's pre-signature can trigger the same outcome. [8](#0-7) 

---

### Recommendation

The operator identity must be derived from a source that is cryptographically bound to the transaction, not from freely-writable OP_RETURN data. Two concrete options:

1. **Require an operator Schnorr signature over the payout txid** embedded alongside the xonly key in the OP_RETURN. `update_finalized_payouts` verifies the signature before writing the key to the DB.
2. **Derive operator identity from the kickoff transaction** rather than the payout transaction. The kickoff is signed by the operator's key and its round input is deterministically tied to the operator's collateral chain, making substitution cryptographically impossible.

Additionally, `create_payout_txhandler` should enforce that `operator_xonly_pk` matches `self.signer.xonly_public_key` at the call site in `operator.rs:620-626` so the operator cannot pass an arbitrary key even accidentally. [9](#0-8) 

---

### Proof of Concept

1. Operator A calls `withdraw(withdrawal_index, user_sig, in_outpoint, out_script_pubkey, out_amount)`. Internally, `create_payout_txhandler` is called with `self.signer.xonly_public_key` — but Operator A patches this call to pass `operator_b_xonly_pk` instead.
2. The payout tx is funded via `fund_raw_transaction` (Operator A's wallet inputs), broadcast, and confirmed.
3. The verifier's `update_finalized_payouts` runs, reads `operator_b_xonly_pk` from OP_RETURN, and writes it to `withdrawals.payout_payer_operator_xonly_pk`.
4. Operator A calls `get_reimbursement_txs(deposit_outpoint)` → `validate_payer_is_operator` → `payer_xonly_pk (B) != self.signer.xonly_public_key (A)` → error. Operator A is permanently locked out.
5. Operator B's `PayoutCheckerTask` finds the unhandled payout, calls `handle_finalized_payout`, attempts to build the ZK proof with Operator B's round_txid — deposit_constant mismatch — proof fails. Vault UTXO remains unspent indefinitely. [10](#0-9) [6](#0-5)

### Citations

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

**File:** core/src/builder/transaction/operator_reimburse.rs (L418-432)
```rust
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
```

**File:** core/src/operator.rs (L620-626)
```rust
        let payout_txhandler = builder::transaction::create_payout_txhandler(
            input_utxo,
            output_txout,
            self.signer.xonly_public_key,
            in_signature,
            self.config.protocol_paramset().network,
        )?;
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

**File:** core/src/operator.rs (L1711-1717)
```rust
                if payer_xonly_pk != self.signer.xonly_public_key {
                    return Err(eyre::eyre!(
                        "Payer is not own operator for deposit, payer xonly pk: {:?}, operator xonly pk: {:?}",
                        payer_xonly_pk,
                        self.signer.xonly_public_key
                    )
                    .into());
```

**File:** core/src/database/verifier.rs (L226-245)
```rust
        let mut query_builder = QueryBuilder::new(
            "UPDATE withdrawals AS w SET
                payout_txid = c.payout_txid,
                payout_payer_operator_xonly_pk = c.payout_payer_operator_xonly_pk,
                payout_tx_blockhash = c.payout_tx_blockhash
                FROM (",
        );

        query_builder.push_values(
            converted_values.into_iter(),
            |mut b, (idx, txid, operator_xonly_pk, block_hash)| {
                b.push_bind(idx)
                    .push_bind(txid)
                    .push_bind(operator_xonly_pk)
                    .push_bind(block_hash);
            },
        );

        query_builder
            .push(") AS c(idx, payout_txid, payout_payer_operator_xonly_pk, payout_tx_blockhash) WHERE w.idx = c.idx");
```

**File:** core/src/database/verifier.rs (L287-296)
```rust
        let query = sqlx::query_as::<_, (i32, Option<TxidDB>, Option<BlockHashDB>)>(
            "SELECT w.idx, w.move_to_vault_txid, w.payout_tx_blockhash
             FROM withdrawals w
             WHERE w.payout_txid IS NOT NULL
                AND w.is_payout_handled = FALSE
                AND w.payout_payer_operator_xonly_pk = $1
                ORDER BY w.idx ASC
             LIMIT 1",
        )
        .bind(XOnlyPublicKeyDB(operator_xonly_pk));
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

**File:** core/src/rpc/operator.rs (L209-238)
```rust
        // if verification address is set in config, check if verification signature is valid
        if let Some(address_in_config) = self.operator.config.aggregator_verification_address {
            let verification_signature = params
                .verification_signature
                .map(|sig| {
                    PrimitiveSignature::from_str(&sig).map_err(|e| {
                        Status::invalid_argument(format!("Invalid verification signature: {e}"))
                    })
                })
                .transpose()?;
            // check if verification signature is provided by aggregator
            if let Some(verification_signature) = verification_signature {
                let address_from_sig =
                    recover_address_from_ecdsa_signature::<OperatorWithdrawalMessage>(
                        withdrawal_id,
                        input_signature,
                        input_outpoint,
                        output_script_pubkey.clone(),
                        output_amount,
                        verification_signature,
                    )?;

                // check if verification signature is signed by the address in config
                if address_from_sig != address_in_config {
                    return Err(BridgeError::InvalidECDSAVerificationSignature).map_to_status();
                }
            } else {
                // if verification signature is not provided, but verification address is set in config, return error
                return Err(BridgeError::ECDSAVerificationSignatureMissing).map_to_status();
            }
```

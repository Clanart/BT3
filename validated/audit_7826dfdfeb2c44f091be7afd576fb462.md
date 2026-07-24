### Title
Unvalidated OP_RETURN Payer Identity in Payout Transaction Allows Reimbursement Theft and Permanent Vault Lock — (File: `core/src/verifier.rs`)

---

### Summary

The payout transaction uses a `SIGHASH_SINGLE | ANYONECANPAY` user signature that does not commit to the OP_RETURN output. The verifier's `update_finalized_payouts` function blindly reads the OP_RETURN from any confirmed payout transaction and records its contents as the authoritative `payout_payer_operator_xonly_pk` — the identity that receives the vault reimbursement. An attacker who observes a legitimate payout transaction in the Bitcoin mempool can copy the user's `ANYONECANPAY` signature, substitute their own key (or an invalid key) in the OP_RETURN, and broadcast with a higher fee rate. If mined first, the attacker either steals the reimbursement from the legitimate operator or permanently locks the vault UTXO.

---

### Finding Description

**Step 1 — Payout transaction construction.**

`create_payout_txhandler` builds the payout transaction with the operator's `xonly_public_key` serialized into an OP_RETURN output at index 2: [1](#0-0) 

The user's input (index 0) is signed with `SIGHASH_SINGLE | ANYONECANPAY`, as confirmed by the verification error message in `operator.rs`: [2](#0-1) 

`SIGHASH_SINGLE | ANYONECANPAY` commits only to input 0 and output 0 (the user payout). It does **not** commit to output 1 (anchor) or output 2 (OP_RETURN). Any party can replace the OP_RETURN without invalidating the user's signature.

**Step 2 — Payer identity recorded from OP_RETURN without validation.**

When a block is processed, `update_finalized_payouts` finds the transaction that spent the withdrawal UTXO, reads the first OP_RETURN output, and stores whatever 32-byte value it finds as the authoritative payer: [3](#0-2) 

There is no check that the key is a registered operator, no check that it matches the actual transaction sender, and no check that it matches any pre-committed value. The result is written unconditionally to the `withdrawals` table: [4](#0-3) 

**Step 3 — Reimbursement flows to whoever is recorded.**

`PayoutCheckerTask` queries for unhandled payouts filtered by `payout_payer_operator_xonly_pk`: [5](#0-4) 

The operator whose key appears in the OP_RETURN triggers the kickoff/reimburse flow and receives the full `bridge_amount` from the vault: [6](#0-5) 

---

### Impact Explanation

**Scenario A — Competing operator steals reimbursement (High):**
Operator B observes Operator A's payout transaction in the mempool. Operator B copies the withdrawal UTXO input (with the user's `ANYONECANPAY` sig), adds their own wallet inputs, keeps output 0 (user payout), and replaces the OP_RETURN with their own `xonly_public_key`. Broadcasting with a higher fee rate causes Operator B's transaction to be mined first. The verifier records Operator B as the payer. Operator B sends a kickoff and receives the full `bridge_amount` reimbursement from the vault. Operator A's transaction is invalidated; their wallet inputs are returned but they receive no reimbursement for the economic opportunity they prepared.

**Scenario B — Griefing attacker permanently locks vault UTXO (Critical):**
An attacker (non-operator) front-runs the payout transaction with an invalid or non-operator key in the OP_RETURN. The verifier stores `None` or an unrecognized key as `payout_payer_operator_xonly_pk`: [7](#0-6) 

No operator's `get_first_unhandled_payout_by_operator_xonly_pk` query will ever match this row. No kickoff is sent. The `move_to_vault_tx` output — holding the full `bridge_amount` — is never spent by a reimburse transaction. The vault UTXO is permanently locked (or recoverable only via the user's timelock path, enabling a double-spend: the user was already paid on Citrea).

---

### Likelihood Explanation

The attack requires only passive mempool monitoring and the ability to broadcast a Bitcoin transaction. No privileged access, no key compromise, and no gRPC authentication is needed — the attacker operates entirely at the Bitcoin layer, bypassing all mTLS and `aggregator_verification_address` guards. The `ANYONECANPAY` sighash type is a documented property of the protocol, making the attack surface permanently open. Any operator or well-funded third party can execute this.

---

### Recommendation

1. **Bind the OP_RETURN to the transaction sender cryptographically.** Require the operator to include a Schnorr signature over `(withdrawal_utxo_outpoint || payout_txid)` using their operator key, placed in the OP_RETURN or witness. The verifier should verify this signature before accepting the payer identity.

2. **Alternatively, record the payer at the RPC layer, not from on-chain data.** When `withdraw()` is called, immediately write the calling operator's key to the DB as the provisional payer, keyed by `withdrawal_utxo_outpoint`. On-chain confirmation then only validates that the correct UTXO was spent, not who spent it.

3. **Validate the OP_RETURN key is a registered operator** before storing it. An unrecognized key should trigger an alert and a fallback recovery path, not silent storage of `None`.

---

### Proof of Concept

1. Operator A calls `withdraw(withdrawal_index, sig, utxo, script_pubkey, amount)`. The payout transaction is broadcast to the Bitcoin mempool. Its structure is:
   - Input 0: withdrawal UTXO (user's `ANYONECANPAY` sig)
   - Input 1+: Operator A's wallet UTXOs
   - Output 0: user payout (`out_amount`)
   - Output 1: anchor
   - Output 2: `OP_RETURN <operator_A_xonly_pk>`

2. Attacker (Operator B) observes the mempool transaction. Extracts input 0 (withdrawal UTXO + user sig). Constructs:
   - Input 0: same withdrawal UTXO + same user `ANYONECANPAY` sig (valid, since it only commits to input 0 and output 0)
   - Input 1+: Operator B's wallet UTXOs
   - Output 0: same user payout (required by `ANYONECANPAY`)
   - Output 1: anchor
   - Output 2: `OP_RETURN <operator_B_xonly_pk>` (or arbitrary bytes)

3. Attacker broadcasts with fee rate higher than Operator A's transaction.

4. Attacker's transaction is mined. `update_finalized_payouts` runs: [8](#0-7) 
   Records `operator_B_xonly_pk` (or `None`) as `payout_payer_operator_xonly_pk`.

5. Operator A's transaction is invalidated (withdrawal UTXO already spent). Operator A receives no reimbursement.

6. In Scenario A: Operator B's `PayoutCheckerTask` finds the unhandled payout, sends a kickoff, and receives the full `bridge_amount` from the vault.

7. In Scenario B: `payout_payer_operator_xonly_pk` is `None` or an unregistered key. No kickoff is ever sent. The vault UTXO holding `bridge_amount` is permanently locked.

### Citations

**File:** core/src/builder/transaction/operator_reimburse.rs (L373-384)
```rust
    Ok(builder
        .add_output(UnspentTxOut::from_partial(TxOut {
            value: move_txhandler
                .get_spendable_output(UtxoVout::DepositInMove)?
                .get_prevout()
                .value,
            script_pubkey: operator_reimbursement_address.script_pubkey(),
        }))
        .add_output(UnspentTxOut::from_partial(
            builder::transaction::anchor_output(paramset.anchor_amount()),
        ))
        .finalize())
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

**File:** core/src/verifier.rs (L2299-2342)
```rust
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
```

**File:** core/src/database/verifier.rs (L226-248)
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

        let query = query_builder.build();
        execute_query_with_tx!(self.connection, tx, query, execute)?;
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

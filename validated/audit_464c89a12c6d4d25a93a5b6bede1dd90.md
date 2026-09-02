## Analog Found

### Title
Unauthenticated OP_RETURN operator-attribution in `Payout` tx breaks the operator-credited-vs-operator-that-paid binding - (File: `core/src/builder/transaction/operator_reimburse.rs`)

### Summary
The Convex bug in the source report is a case where the on-chain value the code reads/attributes (`rewardToken()` vs `rewardToken().token()`) does not correspond to the party that is entitled to it, causing loss of otherwise-claimable value. The Clementine analog is the `Payout` transaction's operator attribution: the field used to determine *which operator gets reimbursement credit* for fronting a withdrawal is an unsigned `OP_RETURN` push that is not covered by the withdrawal signature, so anyone observing the transaction before confirmation can rewrite that attribution and permanently deny the honest operator its reimbursement.

### Finding Description
`create_payout_txhandler` builds the `Payout` transaction with a single signed input (the withdrawal UTXO) and three outputs: the user payout, an anchor, and an `OP_RETURN` containing the fronting operator's x-only pubkey: [1](#0-0) 

The witness for the single input is set with `user_sig` using `SinglePlusAnyoneCanPay` sighash, confirmed explicitly in `operator.rs`: [2](#0-1) 

`SIGHASH_SINGLE | SIGHASH_ANYONECANPAY` only commits the signature to the single signed input and to the output at the *same index* (output 0, the user payout). Outputs 1 (anchor) and 2 (`OP_RETURN`, which carries the operator's identity) are **not covered** by this signature, so they can be freely replaced by anyone who has seen the signature (e.g., in the mempool) without invalidating it.

The `OP_RETURN` content is later trusted, unauthenticated, as the sole source of truth for "who fronted this payout" when a block confirms: [3](#0-2) 

That value is persisted directly into `withdrawals.payout_payer_operator_xonly_pk`: [4](#0-3) 

Reimbursement eligibility for a given operator is then determined purely by matching this stored pubkey against the operator's own key: [5](#0-4) 

and `send_asserts` hard-fails if the kickoff's operator key does not match the DB-recorded payer key: [6](#0-5) 

**Attack path:** An honest operator broadcasts its signed `Payout` tx (with its own xonly pubkey in the `OP_RETURN`) to fund a withdrawal. Before it confirms, any observer of the mempool can construct a competing transaction that reuses the same signed input/output-0 pair (still valid under `SinglePlusAnyoneCanPay`) but substitutes a different `OP_RETURN` value — either garbage (so `XOnlyPublicKey::from_slice` fails and `payout_payer_operator_xonly_pk` is set to `NULL`) or an arbitrary other operator's public key — and rebroadcasts with a higher fee (RBF/fee-bump) to get mined first. Because the actual bitcoin value transfer to the user (output 0) is unchanged, the user is still paid, but whichever version confirms determines forever which xonly_pk is recorded as the payer.

### Impact Explanation
This breaks the binding `operator_credited == operator_that_actually_fronted_the_payout`. If the attribution is corrupted (NULL or wrong operator key), the honest operator that genuinely funded the withdrawal (paid the mempool fee to get the payout broadcast/confirmed) can never satisfy `get_first_unhandled_payout_by_operator_xonly_pk` nor pass the `payout_op_xonly_pk != kickoff_data.operator_xonly_pk` check in `send_asserts`, and therefore can never claim its reimbursement (the deposit's `MoveToVault` funds it was supposed to reclaim). This matches the Critical impact category "an honest operator permanently unable to be reimbursed."

### Likelihood Explanation
No privileged role is required — the attacker only needs to observe the mempool (or a relayed/broadcast `Payout` transaction before it confirms) and re-broadcast a fee-bumped variant with a different `OP_RETURN` push, which is a standard, unauthenticated Bitcoin mempool operation. The vulnerable window exists for every single non-optimistic withdrawal that goes through this fronting flow.

### Recommendation
Bind the operator attribution to the signature itself rather than to a freely-mutable `OP_RETURN` output — e.g. include the operator xonly pubkey inside the data actually signed with `SIGHASH_ALL` (or a separate signature/commitment scheme tied to the reimbursement claim), or use `SIGHASH_ALL` for the payout input so any modification of outputs (including the attribution `OP_RETURN`) invalidates the existing signature and requires the operator to re-sign, closing the malleability window.

### Proof of Concept
1. Honest operator O1 receives withdrawal params + `user_sig` (SIGHASH_SINGLE|ANYONECANPAY) and calls `withdraw`, producing and broadcasting `Payout` tx `T1` with `OP_RETURN = O1_xonly_pk` (`core/src/operator.rs:620-637`, `core/src/builder/transaction/operator_reimburse.rs:407-436`).
2. Attacker observes `T1` in the mempool, extracts the witness for input 0 (valid under `SinglePlusAnyoneCanPay`), and constructs `T2`: same input (same witness), same output 0 (user payout, unchanged, so signature stays valid), but `OP_RETURN = garbage_bytes` (or another operator's pubkey), plus a higher fee via additional funding input.
3. Attacker broadcasts `T2` with RBF; it replaces `T1` in mempool and confirms first.
4. On confirmation, `update_finalized_payouts` parses `T2`'s `OP_RETURN`, fails/mismatches, and stores `payout_payer_operator_xonly_pk = NULL` (or wrong key) for this withdrawal (`core/src/verifier.rs:2283-2353`, `core/src/database/verifier.rs:198-251`).
5. O1 can never locate this payout via `get_first_unhandled_payout_by_operator_xonly_pk` (`core/src/database/verifier.rs:282-313`), and any attempt to claim reimbursement fails the `payout_op_xonly_pk != kickoff_data.operator_xonly_pk` check in `send_asserts` (`core/src/operator.rs:1284-1295`) — O1 loses its ability to be reimbursed for a payout it legitimately funded.

### Citations

**File:** core/src/builder/transaction/operator_reimburse.rs (L407-436)
```rust
pub fn create_payout_txhandler(
    input_utxo: UTXO,
    output_txout: TxOut,
    operator_xonly_pk: XOnlyPublicKey,
    user_sig: taproot::Signature,
    _network: bitcoin::Network,
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

**File:** core/src/verifier.rs (L2312-2328)
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
```

**File:** core/src/database/verifier.rs (L198-251)
```rust
    /// Sets the given payout txs' txid and operator index for the given index.
    pub async fn update_payout_txs_and_payer_operator_xonly_pk(
        &self,
        tx: Option<DatabaseTransaction<'_>>,
        payout_txs_and_payer_operator_xonly_pk: Vec<(
            u32,
            Txid,
            Option<XOnlyPublicKey>,
            bitcoin::BlockHash,
        )>,
    ) -> Result<(), BridgeError> {
        if payout_txs_and_payer_operator_xonly_pk.is_empty() {
            return Ok(());
        }
        // Convert all values first, propagating any errors
        let converted_values: Result<Vec<_>, BridgeError> = payout_txs_and_payer_operator_xonly_pk
            .iter()
            .map(|(idx, txid, operator_xonly_pk, block_hash)| {
                Ok((
                    i32::try_from(*idx).wrap_err("Failed to convert payout index to i32")?,
                    TxidDB(*txid),
                    operator_xonly_pk.map(XOnlyPublicKeyDB),
                    BlockHashDB(*block_hash),
                ))
            })
            .collect();
        let converted_values = converted_values?;

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

        Ok(())
    }
```

**File:** core/src/database/verifier.rs (L282-313)
```rust
    pub async fn get_first_unhandled_payout_by_operator_xonly_pk(
        &self,
        tx: Option<DatabaseTransaction<'_>>,
        operator_xonly_pk: XOnlyPublicKey,
    ) -> Result<Option<(u32, Txid, BlockHash)>, BridgeError> {
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

        let results = execute_query_with_tx!(self.connection, tx, query, fetch_optional)?;

        results
            .map(|(citrea_idx, move_to_vault_txid, payout_tx_blockhash)| {
                Ok((
                    u32::try_from(citrea_idx).wrap_err("Failed to convert citrea index to u32")?,
                    move_to_vault_txid
                        .expect("move_to_vault_txid Must be Some")
                        .0,
                    payout_tx_blockhash
                        .expect("payout_tx_blockhash Must be Some")
                        .0,
                ))
            })
            .transpose()
    }
```

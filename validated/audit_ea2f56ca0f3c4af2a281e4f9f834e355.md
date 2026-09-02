### Title
Payout attribution is trusted from a self-declared OP_RETURN field, letting a third party front a withdrawal while a different operator is credited (and later reimbursed) - ([File: core/src/verifier.rs])

### Summary
### Finding Description
`create_payout_txhandler` (`core/src/builder/transaction/operator_reimburse.rs:407-436`) builds the Payout transaction with a single spendable input — the dust withdrawal UTXO, spent with the *user's* signature (`user_sig`) via key-spend — and appends an OP_RETURN output that simply embeds whatever `operator_xonly_pk` is passed in: [1](#0-0) 

Nothing in this construction cryptographically ties the embedded `operator_xonly_pk` to whoever actually supplies the real BTC value for the output (that value is added later at broadcast time via RBF/CPFP fee-bumping inputs, not part of the signed handler). The only Clementine-internal caller that sets this field is `Operator::withdraw`, which hardcodes it to `self.signer.xonly_public_key`: [2](#0-1) 

However, the protocol's bookkeeping of "who fronted this withdrawal" is derived purely by scanning confirmed Bitcoin blocks and parsing that OP_RETURN field back out, with no signature check on it at all: [3](#0-2) 

That parsed value is then persisted as `payout_payer_operator_xonly_pk` and used later as the authoritative "who paid" record: [4](#0-3) 

Because the withdrawal UTXO input is spendable with only the (off-chain-shared/relayed) `user_sig`, and the OP_RETURN payer field is unauthenticated free-form data, any party capable of constructing and broadcasting a valid Bitcoin transaction that spends this dust input, pays the correct amount to the correct recipient script, and embeds an arbitrary operator's x-only pubkey in the OP_RETURN output can satisfy the on-chain shape Clementine expects — without ever going through `Operator::withdraw` or being that operator at all. Each entity's `PayoutCheckerTask` polls for exactly this record, keyed only by the embedded pubkey: [5](#0-4) [6](#0-5) 

This breaks the binding "the operator credited (`payout_payer_operator_xonly_pk`) == the party that actually fronted the withdrawal." The bookkeeping records an operator as payer based only on a self-declared OP_RETURN byte string with no signature or on-chain proof tying it to the entity that supplied the real value.

### Impact Explanation
An operator whose x-only pubkey is embedded (whether they actually paid or not) will have their own automated `PayoutCheckerTask` pick up the record via `get_first_unhandled_payout_by_operator_xonly_pk` and drive it through `handle_finalized_payout` → kickoff → BitVM reimbursement flow, ultimately reclaiming `bridge_amount` worth of BTC from the vault as if they had fronted the withdrawal. If a third party (not that operator) actually supplied the real value in the transaction, the credited operator is reimbursed for a payout it never funded — a Critical-class outcome ("an operator reimbursed for a payout it never funded"). Conversely, if the operator that truly fronted the funds is *not* the one whose pubkey ends up recorded, that honest operator has no path to reimbursement for money it genuinely spent, since `validate_payer_is_operator`/`get_reimbursement_txs` gate the entire kickoff flow on `payout_payer_operator_xonly_pk` matching the caller: [7](#0-6) 
This is "an honest operator permanently unable to be reimbursed."

### Likelihood Explanation
Constructing the transaction requires knowledge of the dust withdrawal UTXO's outpoint and the `user_sig` for it, both of which are exchanged in the normal withdrawal flow (relayed to operators/aggregator "off-chain," per the design comments), and does not require crossing any mTLS/certificate boundary in Clementine's gRPC layer, since the attacker never needs to call Clementine's RPCs at all — they can build and broadcast the transaction directly with a Bitcoin node/wallet. I was not able to fully confirm within the available index whether `user_sig`/the withdrawal outpoint is made publicly retrievable (e.g., via a public Citrea contract call/event) prior to being consumed, versus being held in a private off-chain channel between the withdrawing user and operators only; this materially affects how easily an unprivileged third party could obtain the inputs needed to mount this specific broadcast-race variant.

### Recommendation
Do not trust the OP_RETURN payer field as bookkeeping truth. Instead, cryptographically bind the payer identity to the transaction, e.g., by requiring the payout transaction's OP_RETURN commitment (or an additional signature) to be verifiable against the claimed operator's key (for example, have the operator sign the payout transaction's txid/outputs with their own key and store/verify that signature alongside the OP_RETURN pubkey before crediting `payout_payer_operator_xonly_pk`), or bind attribution to actual value provenance (e.g., require that the additional inputs funding the output amount are traceable/committed to the claimed operator) rather than an unauthenticated plaintext field.

### Proof of Concept
1. Observe a registered Citrea withdrawal's dust UTXO outpoint and the `user_sig` needed to spend it (as shared for the normal front-and-reimburse flow).
2. Independently construct a Payout transaction spending that dust UTXO with `user_sig`, paying the correct recipient output, funding the rest of the value from one's own UTXOs (not through Clementine's `TxSender`), and setting the OP_RETURN output to embed an arbitrary registered operator B's x-only pubkey (see `create_payout_txhandler`, `core/src/builder/transaction/operator_reimburse.rs:407-436`).
3. Broadcast the transaction directly to Bitcoin.
4. Once confirmed, every entity's `update_finalized_payouts` (`core/src/verifier.rs:2283-2353`) parses the OP_RETURN and records operator B as `payout_payer_operator_xonly_pk` with no signature check.
5. Operator B's own `PayoutCheckerTask` (`core/src/task/payout_checker.rs:31-113`) detects the "unhandled payout" credited to itself and proceeds through the kickoff/reimbursement flow, reclaiming `bridge_amount` from the vault for a withdrawal it never funded.

### Citations

**File:** core/src/builder/transaction/operator_reimburse.rs (L407-418)
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

**File:** core/src/operator.rs (L1703-1719)
```rust
        // first check if the payer is the operator, and the kickoff is handled
        // by the PayoutCheckerTask, meaning kickoff_txid is set
        let (payout_blockhash, kickoff_txid) = match (
            payer_xonly_pk,
            payout_blockhash,
            kickoff_txid,
        ) {
            (Some(payer_xonly_pk), Some(payout_blockhash), Some(kickoff_txid)) => {
                if payer_xonly_pk != self.signer.xonly_public_key {
                    return Err(eyre::eyre!(
                        "Payer is not own operator for deposit, payer xonly pk: {:?}, operator xonly pk: {:?}",
                        payer_xonly_pk,
                        self.signer.xonly_public_key
                    )
                    .into());
                }
                (payout_blockhash, kickoff_txid)
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

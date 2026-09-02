### Title
Unauthenticated OP_RETURN operator attribution in payout tx lets a withdrawing user frame any operator for reimbursement of a payout it never funded - ([File: core/src/verifier.rs], [File: core/src/database/verifier.rs], [File: core/src/task/payout_checker.rs], [File: core/src/builder/transaction/operator_reimburse.rs])

### Summary
The binding the protocol needs is: `payout_payer_operator_xonly_pk` stored for withdrawal `i` == the xonly public key of the operator whose own funds paid output 0 of that withdrawal's payout tx. Because the payout tx's SIGHASH_SINGLE|ANYONECANPAY signature only commits to input 0's outpoint/txout and output 0, the OP_RETURN (output index 2, holding the "payer" attribution) and all funding inputs beyond input 0 are completely unauthenticated and attacker-controllable, so the withdrawing user themselves can self-fund the payout and write any operator's xonly_pk into the OP_RETURN, breaking this binding.

### Finding Description
`create_payout_txhandler` builds the payout tx with a single signed input (the user's dust withdrawal UTXO, signed off-chain with `SinglePlusAnyoneCanPay`) and three outputs: user payout (0), anchor (1), OP_RETURN with `operator_xonly_pk` (2) [1](#0-0) . `Operator::withdraw` verifies the user's signature via `calculate_sighash_txin(0, in_signature.sighash_type)`, i.e. only input 0 and output 0 are covered by the signature [2](#0-1) ; everything else (extra funding inputs added by `fund_raw_transaction`, the anchor, and the OP_RETURN) is unsigned and free to change without invalidating the user's signature.

`Verifier::update_finalized_payouts` finds, for each withdrawal, whichever transaction actually spent the registered `withdrawal_utxo_txid:vout` (via `get_payout_txs_for_withdrawal_utxos`, an unconditional join on `bitcoin_syncer_spent_utxos`) and blindly parses the first OP_RETURN output of that transaction into `operator_xonly_pk`, with no check that this operator actually funded the tx [3](#0-2) . This is persisted via `update_payout_txs_and_payer_operator_xonly_pk` into `withdrawals.payout_payer_operator_xonly_pk` [4](#0-3) .

`PayoutCheckerTask::run_once` (running inside operator C's own process) queries `get_first_unhandled_payout_by_operator_xonly_pk` keyed on `self.operator.signer.xonly_public_key` — i.e. C's own key — with no cross-check against C's own broadcast/tx-sender records that C actually created or funded that specific payout tx [5](#0-4) ; it directly calls `handle_finalized_payout` to assign a kickoff. `Operator::validate_payer_is_operator`, used later in `get_reimbursement_txs`, only checks that the DB's `payer_xonly_pk == self.signer.xonly_public_key` [6](#0-5)  — it re-checks the same attacker-poisoned DB column, not any operator-authenticated evidence of funding.

Exploit: an unprivileged attacker calls `withdraw` on the Citrea Bridge contract with their own `input_outpoint` (a dust UTXO they own) and signs it with `SinglePlusAnyoneCanPay`. Once this withdrawal is synced into the `withdrawals` table, the attacker (without ever calling any operator's gRPC `withdraw`) constructs and broadcasts their own transaction spending that exact input 0/output 0 (byte-identical, so the signature stays valid), adding their own fee-paying input(s) and an OP_RETURN naming an arbitrary operator C's xonly_pk. Once this attacker tx confirms, `update_finalized_payouts` attributes the payout to C, `PayoutCheckerTask` for C treats it as an unhandled payout it fronted, and C proceeds through kickoff/round/reimburse to be paid from the deposit's `MoveToVaultTx` output — despite C having spent nothing.

No existing guard blocks this: `SECP.verify_schnorr` only authenticates output 0/input 0 (by design of SIGHASH_SINGLE|ANYONECANPAY), `is_kickoff_malicious`/disprove logic only checks consistency between the kickoff's claimed operator and the on-chain OP_RETURN — which the attacker deliberately made consistent — and `validate_payer_is_operator` only re-reads the same poisoned column.

### Impact Explanation
BTC leaves the deposit's move-to-vault UTXO via the `Reimburse` transaction to operator C's `operator_reimbursement_address`, even though C never funded the corresponding payout — this is an "operator reimbursed for a payout it never funded," a listed Critical impact. The attack is repeatable per withdrawal slot/deposit and can target any specific operator by using that operator's public xonly_pk (all operator keys are discoverable via `fetch_operator_keys`). It requires no operator, verifier, or aggregator collusion, no key compromise, and no majority hashrate.

### Likelihood Explanation
The attacker only needs: (1) a small deposit into the bridge to obtain a withdrawal slot, (2) ability to call `withdraw` on the Citrea Bridge contract with a self-chosen input UTXO and `SinglePlusAnyoneCanPay` signature (explicitly in-scope attacker capabilities), and (3) ability to fund and broadcast one extra Bitcoin transaction. Cost is limited to the withdrawal amount (self-funded, largely recovered by the attacker) plus mining fees. No timing race against operators or verifiers is required since the attacker can be the only broadcaster of a valid spend for that UTXO.

### Recommendation
Bind operator attribution cryptographically to the operator's own action rather than to unauthenticated OP_RETURN bytes: e.g. require the OP_RETURN output (and ideally all outputs beyond output 0) to be covered by an operator signature/commitment recorded off-chain during the operator's own `withdraw` RPC call, and have `update_finalized_payouts`/`PayoutCheckerTask` cross-validate the on-chain payout tx against that operator-signed commitment (e.g. matching full txid or a signed hash of the funding inputs) before crediting an operator, instead of trusting whichever transaction happens to spend the withdrawal UTXO.

### Proof of Concept
`cargo test` outline (regtest, `MockCitreaClient`, no mainnet/live Citrea):
1. Run a normal deposit and register a withdrawal slot exactly as in `deposit_and_withdraw_e2e.rs`/`test/common/clementine_utils.rs`, obtaining `withdrawal_utxo` and `sig` (`SinglePlusAnyoneCanPay`) signed by the attacker over `(withdrawal_utxo, payout_txout)`.
2. Instead of calling any operator's `withdraw` gRPC, manually build a transaction with `create_payout_txhandler`-equivalent structure: input 0 = `withdrawal_utxo` with the attacker's pre-signed witness, output 0 = the exact signed `payout_txout`, but with attacker-funded extra input(s) for fees and an OP_RETURN set to operator C's `xonly_public_key` (a distinct, honest operator in the test's actor set that never called `withdraw`). Broadcast and mine it to finality.
3. Assert `operator_C.db.get_handled_payout_kickoff_txid` / `get_first_unhandled_payout_by_operator_xonly_pk(C_key)` becomes populated even though `operator_C`'s own `tx_sender`/db has no record of ever constructing or broadcasting this payout tx (i.e., binding LHS: `withdrawals.payout_payer_operator_xonly_pk == C`; RHS: "party whose funds paid output 0" == attacker, not C — assert these differ).
4. Let `PayoutCheckerTask` for operator C run and drive `get_reimbursement_txs`/kickoff/round flow to completion; assert the `Reimburse` tx for this deposit's `MoveToVaultTx` output is confirmed paying operator C's `reimburse_addr`, proving `validate_payer_is_operator` and the disprove/challenge path do not block C from being reimbursed for a withdrawal it never funded.

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

**File:** core/src/operator.rs (L620-637)
```rust
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

**File:** core/src/operator.rs (L1687-1729)
```rust
    async fn validate_payer_is_operator(
        &self,
        dbtx: Option<DatabaseTransaction<'_>>,
        deposit_id: u32,
    ) -> Result<(BlockHash, Txid), BridgeError> {
        let (payer_xonly_pk, payout_blockhash, kickoff_txid) = self
            .db
            .get_payer_xonly_pk_blockhash_and_kickoff_txid_from_deposit_id(dbtx, deposit_id)
            .await?;

        tracing::info!(
            "Payer xonly pk and kickoff txid found for the requested deposit, payer xonly pk: {:?}, kickoff txid: {:?}",
            payer_xonly_pk,
            kickoff_txid
        );

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
            }
            _ => {
                return Err(eyre::eyre!(
                    "Payer info not found for deposit, payout blockhash: {:?}, kickoff txid: {:?}",
                    payout_blockhash,
                    kickoff_txid
                )
                .into());
            }
        };
```

**File:** core/src/verifier.rs (L2298-2343)
```rust
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

### Title
Unauthenticated `operator_xonly_pk` in Payout Tx OP_RETURN Lets Anyone Misattribute a Fronted Withdrawal to an Uninvolved Operator - ([File: core/src/builder/transaction/operator_reimburse.rs])

### Summary
The `payout_tx` construction only cryptographically commits the user's withdrawal input and its own output via a `SIGHASH_SINGLE|SIGHASH_ANYONECANPAY` signature; the `OP_RETURN` output that records which operator "fronted" the peg-out is unsigned, freely settable data appended by whoever finalizes and broadcasts the transaction. The bridge's payer-attribution logic trusts this `OP_RETURN` value as the source of truth for "which operator paid", breaking the `operator credited == party that paid` binding, analogous to the GMX report's broken `withdrawETH flag interpreted == fee-handling code path taken` binding.

### Finding Description
`create_payout_txhandler` builds the payout transaction with:
- Input 0: the user's committed withdrawal UTXO, signed only by the user with `SpendPath::KeySpend` and the user-supplied `taproot::Signature` (`user_sig`), which per the docstring is intended to use `SinglePlusAnyoneCanPay`.
- Output 0: the user payout output (pinned by SIGHASH_SINGLE).
- Output 1: anchor output (CPFP).
- Output 2: `op_return_txout(operator_xonly_pk)` — the field the whole reimbursement pipeline treats as "the operator who fronted this withdrawal". [1](#0-0) 

With `SIGHASH_SINGLE|ANYONECANPAY`, the user's signature only commits to input 0 and output 0. It does **not** commit to any additional inputs used to fund the transfer, nor to outputs 1/2 (anchor, OP_RETURN). Consequently, whoever assembles the final on-chain transaction — adding their own funding input(s) to cover the payout amount — can freely choose (or forge) any 32-byte value to place in the OP_RETURN, including another operator's `xonly_pk`, without that operator's involvement or authorization.

The chain-sync logic then takes this unauthenticated data at face value:

```
// If OP_RETURN doesn't exist in any outputs, or the data in OP_RETURN is not a valid xonly_pubkey,
// operator_xonly_pk will be set to None...
let operator_xonly_pk = op_return_output
    .and_then(|output| parse_op_return_data(&output.script_pubkey))
    .and_then(|bytes| XOnlyPublicKey::from_slice(bytes).ok());
``` [2](#0-1) 

and persists it as `payout_payer_operator_xonly_pk` via `update_payout_txs_and_payer_operator_xonly_pk`. [3](#0-2) 

This value is then used as the authoritative "who paid" attribution across the reimbursement flow:
- `PayoutCheckerTask::run_once` polls for any unhandled payout where `payout_payer_operator_xonly_pk == self.operator.signer.xonly_public_key`, and automatically calls `handle_finalized_payout` for that operator's own service. [4](#0-3) 
- `validate_payer_is_operator` / `get_reimbursement_txs` gate an operator's manual/automated reimbursement flow purely on this DB-recorded pubkey matching the local operator's own key. [5](#0-4) 
- `send_asserts` and `is_kickoff_malicious` also key off this same field to bind a kickoff to "the operator who paid". [6](#0-5) [7](#0-6) 

None of these checks verify that the operator named in the OP_RETURN actually supplied the funding input(s) of the payout transaction — they only check that the *name* in an unsigned output matches. Anyone able to construct and broadcast a valid `payout_tx` (funding it with their own BTC and the user's pre-obtained `SinglePlusAnyoneCanPay` signature, which is handed out "off-chain" per the docstring) can therefore write an arbitrary operator's key into the OP_RETURN.

### Impact Explanation
An attacker who obtains a user's off-chain payout signature (the design already assumes this signature circulates to potential fronting operators, since the payout tx docstring says "operator will send a kickoff transaction to get reimbursed" after fronting) can front the withdrawal with their own BTC but stamp a victim, uninvolved operator's `xonly_pk` into the OP_RETURN. This:
- Causes the victim operator's own `PayoutCheckerTask` to autonomously treat the withdrawal as its own responsibility, consume one of its reserved, unused kickoff connectors for that deposit (`get_unused_and_signed_kickoff_connector`), and (with `automation` enabled) drive it through `end_round`, kickoff creation and eventually attempt `reimburse_tx`, which pays the `bridge_amount` from the `MoveToVault` UTXO to that operator's `operator_reimbursement_address` — i.e., an operator is credited/reimbursed for a payout it never funded, or is forced to expend its scarce, pre-committed kickoff connector/collateral on a withdrawal it did not choose and cannot decline, exposing it to slashing risk (`BurnUnusedKickoffConnectors`) if it cannot follow through with the full kickoff/challenge-timeout/assert sequence in time.
- Breaks the intended equality `operator credited (via payout OP_RETURN) == operator that fronted the withdrawal (funded the payout output)`.

This matches the in-scope Critical impact categories "an operator reimbursed for a payout it never funded" and "an honest operator's collateral burned / permanently unable to be reimbursed" if the misattributed connector is consumed and the operator cannot fulfill or catch the process in time.

### Likelihood Explanation
Exploitation requires only possession of a legitimate user's off-chain `SinglePlusAnyoneCanPay` payout signature (which, by protocol design, is handed to any operator willing to front the withdrawal — i.e., it is not restricted to a single trusted party) and the ability to fund and broadcast a Bitcoin transaction, both squarely within an unprivileged attacker's capability. No verifier, operator, or aggregator role, key compromise, or majority hashrate is needed — this is a caller unauthenticated for the specific field (`operator_xonly_pk` in OP_RETURN) that the protocol treats as an attribution/authorization signal.

### Recommendation
Do not derive payer attribution from unsigned transaction data. Either:
- Require the payout transaction's OP_RETURN (or an equivalent commitment) to be covered by a signature from the claimed operator (e.g., have the operator co-sign or commit to their own pubkey as part of the sighash), or
- Bind attribution to the actual source of the additional funding input(s) (e.g., require the extra input(s) to come from a UTXO tied to the claimed operator's collateral/signing key, and verify this on-chain), so that the operator recorded as "payer" cryptographically corresponds to whoever actually funded the payout.

### Proof of Concept
1. Attacker obtains the user's off-chain `input_signature` (SinglePlusAnyoneCanPay) and withdrawal parameters (as the flow assumes this signature is distributed to any operator willing to front the peg-out).
2. Attacker constructs `payout_tx` per `create_payout_txhandler`, adding their own funding input(s) to cover `output_amount`, and sets the `op_return_txout` to a **victim operator's** `xonly_pk` instead of their own. [1](#0-0) 
3. Attacker broadcasts the transaction; the user's signature only checks input 0/output 0, so the transaction is valid regardless of the OP_RETURN content or extra inputs.
4. The bridge's chain-sync (`update_finalized_payouts`) parses the victim operator's key from the OP_RETURN and records it as `payout_payer_operator_xonly_pk`. [8](#0-7) 
5. The victim operator's own `PayoutCheckerTask` detects this as its own unhandled payout (`get_first_unhandled_payout_by_operator_xonly_pk` matches its key) and automatically proceeds to consume a kickoff connector and attempt reimbursement for a withdrawal it never funded. [4](#0-3)

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

**File:** core/src/verifier.rs (L2312-2350)
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

**File:** core/src/operator.rs (L1275-1295)
```rust
        let (payout_op_xonly_pk_opt, payout_block_hash, payout_txid, deposit_idx) = self
            .db
            .get_payout_info_from_move_txid(Some(&mut dbtx), move_txid)
            .await
            .wrap_err("Failed to get payout info from db during sending asserts.")?
            .ok_or_eyre(format!(
                "Payout info not found in db while sending asserts for move txid: {move_txid}"
            ))?;

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

**File:** core/src/operator.rs (L1686-1740)
```rust
    /// For a deposit_id checks that the payer for that deposit is the operator, and the payout blockhash and kickoff txid are set.
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

        tracing::info!(
            "Payer xonly pk, payout blockhash and kickoff txid found and valid for own operator for the requested deposit id: {}, payer xonly pk: {:?}, payout blockhash: {:?}, kickoff txid: {:?}",
            deposit_id,
            payer_xonly_pk,
            payout_blockhash,
            kickoff_txid
        );

        Ok((payout_blockhash, kickoff_txid))
    }
```

### Title
Payout attribution to an operator is taken from an unauthenticated OP_RETURN output, letting an operator be credited/reimbursed for a withdrawal it never funded - (File: core/src/verifier.rs, core/src/builder/transaction/operator_reimburse.rs)

### Summary
The party credited with "fronting" a withdrawal (and therefore entitled to bridge reimbursement) is determined solely by reading a self-declared `OP_RETURN` output from the confirmed payout transaction, rather than by verifying who actually supplied the transaction's funding inputs. The user's payout authorization signature only covers the withdrawal input/output pair, not the `OP_RETURN` output that names the "paying" operator, so the binding between "party that funded the payout" and "operator credited for reimbursement" can be forged by anyone able to relay the user's signed withdrawal.

### Finding Description
`create_payout_txhandler` builds the payout transaction with the user's withdrawal UTXO as input, a user output, an anchor output, and an `OP_RETURN` output containing whichever `operator_xonly_pk` was passed in by the caller: [1](#0-0) 

The user only signs a `SinglePlusAnyoneCanPay`-type signature that authorizes spending their withdrawal UTXO to their desired output, as documented in the withdraw RPC parameters: [2](#0-1) 

Because the input's sighash is `SIGHASH_SINGLE | ANYONECANPAY`, the signature commits only to the single signed input and the output at the same index; it does not commit to the `OP_RETURN` output or to any other inputs added via `ANYONECANPAY`. Anyone who observes/relays this signature can therefore construct their own version of the payout transaction, add their own funding inputs (allowed by `ANYONECANPAY`), and write **any** operator's `xonly_pk` into the `OP_RETURN` — regardless of who is actually paying the network fee or funding the transaction.

Once such a transaction confirms, `update_finalized_payouts` extracts the `operator_xonly_pk` purely from that `OP_RETURN` field and persists it as the "payer" for the withdrawal, with no check that this operator actually broadcast, funded, or authorized the transaction: [3](#0-2) 

This value is stored via `update_payout_txs_and_payer_operator_xonly_pk` as `payout_payer_operator_xonly_pk`: [4](#0-3) 

The operator's own automation (`PayoutCheckerTask`) later picks up any withdrawal attributed to its own `xonly_public_key` and unconditionally proceeds to the kickoff/reimbursement flow for it: [5](#0-4) [6](#0-5) 

And `validate_payer_is_operator`/`send_asserts` gate the reimbursement/proof process purely on this same unverified `payout_payer_operator_xonly_pk` field matching the operator's own key, again with no cryptographic proof that the named operator actually funded the transaction: [7](#0-6) [8](#0-7) 

This is structurally the same class of bug as the Sherlock `Depositor#withdrawFromGauge` issue: the entity credited with an action (staking/deposit ownership in the original report; "who funded this payout" here) is inferred from an unauthenticated pointer (an NFT ID minted by any depositor there; a self-declared `OP_RETURN` value here) instead of being cryptographically bound to the party that actually performed/funded the action.

### Impact Explanation
This breaks the required binding "the operator credited versus the party that paid." Any unprivileged actor who can observe a user's signed withdrawal request (e.g., from gRPC traffic, mempool, or by colluding with the user) can front the withdrawal from their own funds while writing an arbitrary operator's `xonly_pk` into the `OP_RETURN`. The named operator's automation will then treat this as its own fronted payout and proceed through kickoff/challenge/reimbursement, ultimately being reimbursed by the bridge's escrowed collateral for a withdrawal it never funded. This matches the explicitly listed Critical impact: "an operator reimbursed for a payout it never funded."

### Likelihood Explanation
The action requires no privileged role, key, or node access — only the ability to observe a validly signed withdrawal (`WithdrawParams`/`input_signature`) that a user has already produced for the withdrawal flow, and the ability to fund/broadcast an alternate transaction using that same input under `ANYONECANPAY`. No verifier, operator, or aggregator collusion is needed; the attacker is an ordinary unprivileged party.

### Recommendation
Bind the credited operator to a value that is cryptographically committed by the funding party rather than self-declared in an unauthenticated `OP_RETURN`, e.g.:
- Require the payout transaction's `OP_RETURN` (or an equivalent commitment) to be signed by the operator being credited (so the named operator must produce a signature over the specific payout transaction, similar to how N-of-N signing works elsewhere in the protocol), or
- Determine the crediting operator from an input that only that operator can provide (e.g., require the operator to co-sign or supply an operator-controlled input to the payout transaction) rather than from an output that any funder can set arbitrarily.

### Proof of Concept
1. User signs a withdrawal request (`WithdrawParams` with `SinglePlusAnyoneCanPay` `input_signature`) for withdrawal `W`, intending operator `A` to front it.
2. Attacker observes/relays this signed input (e.g., via public gRPC endpoints or by colluding with the user) and constructs an alternative payout transaction: same signed input/output pair, but adds their own additional input (permitted by `ANYONECANPAY`) to cover fees, and sets the `OP_RETURN` output to operator `B`'s `xonly_pk` instead of `A`'s.
3. Attacker broadcasts this transaction; it confirms.
4. `update_finalized_payouts` parses the `OP_RETURN`, records `payout_payer_operator_xonly_pk = B` for withdrawal `W`, per [3](#0-2) .
5. Operator `B`'s `PayoutCheckerTask` sees an unhandled payout attributed to its own key [5](#0-4)  and proceeds through `validate_payer_is_operator` [7](#0-6)  and kickoff/reimbursement, ultimately being reimbursed from the bridge's collateral for a withdrawal it never funded — while operator `A` (whom the user actually intended) is bypassed entirely.

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

**File:** core/src/rpc/clementine.proto (L241-253)
```text
  uint32 withdrawal_id = 1;
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
}
```

**File:** core/src/verifier.rs (L2311-2350)
```rust
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

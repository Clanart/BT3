## Finding: Payout attribution (`OP_RETURN` operator pubkey) is not covered by the user's withdrawal signature

**Bug-class hint that matches**: "the operator credited versus the party that paid" — exactly the analog category described in the rules.

### Title
Reimbursement attribution is forgeable via `SIGHASH_SINGLE|ANYONECANPAY` output malleability - (File: core/src/builder/transaction/operator_reimburse.rs)

### Summary
The user's withdrawal-authorization signature only covers the input and the single output at the same index (`SIGHASH_SINGLE | ANYONECANPAY`). The `OP_RETURN` output that records *which operator* fronted the payout is appended **after** the signed output and is never covered by any signature, so anyone observing the payout transaction in the mempool can rebroadcast a variant with a different `OP_RETURN` payload before the legitimate transaction confirms, hijacking or erasing reimbursement attribution.

### Finding Description
`create_payout_txhandler` builds the payout transaction with:
- Input 0: the user's withdrawal UTXO, spent with `SpendPath::KeySpend` and the user's `taproot::Signature` (`in_signature`).
- Output 0: the user's payout (`output_txout`).
- Output 1: anchor (CPFP).
- Output 2: `OP_RETURN` containing `operator_xonly_pk.serialize()` — the field later used to determine *who gets reimbursed*. [1](#0-0) 

The signature's sighash type is enforced to be `SinglePlusAnyoneCanPay`: [2](#0-1) 

`SIGHASH_SINGLE` only commits to the output at the same index as the signed input (index 0 here); `ANYONECANPAY` means the signature does not commit to the rest of the inputs either. Consequently, output 1 (anchor) and output 2 (`OP_RETURN` operator attribution) are **completely unauthenticated** — any party who has seen this signature (which is intrinsically public once the payout tx is broadcast to the mempool, and is even accepted directly over gRPC by the `Withdraw`/`InternalWithdraw` operator endpoints) can construct an alternate transaction that:
- spends the exact same input with the exact same signature (still valid, since the signature doesn't cover it),
- pays the exact same amount to the exact same user output (still valid, since `SIGHASH_SINGLE` requires output 0 to be unchanged),
- but declares an **arbitrary** `operator_xonly_pk` in the `OP_RETURN` output, or a garbage value that fails to parse as a public key.

Downstream, `update_finalized_payouts` reads the confirmed transaction's `OP_RETURN` and blindly assigns credit based on whichever bytes are present on-chain: [3](#0-2) 

This value is persisted as `payout_payer_operator_xonly_pk` and drives reimbursement: [4](#0-3) [5](#0-4) 

and is also used to validate that a kickoff is not malicious, and to gate `get_reimbursement_txs`: [6](#0-5) [7](#0-6) 

Since the money that pays the withdrawing user is already locked in the pre-existing, Citrea-registered UTXO being spent (the input, whose script pubkey belongs to the user), and the operator does not need to contribute any of their own capital to the signed part of the transaction (only trivial CPFP fee bumping via the anchor, which itself is unauthenticated and addable by `ANYONECANPAY`), **anyone** — not just an operator, verifier, or any privileged actor — can win the race to get their `OP_RETURN`-modified variant mined and thereby control who is recorded as the payer.

### Impact Explanation
Two concrete Critical outcomes, both explicitly listed in scope:
1. **An operator reimbursed for a payout it never funded**: an unprivileged attacker rewrites the `OP_RETURN` to point at operator B's public key (public information from `fetch_operator_keys`). Operator B's `PayoutCheckerTask` will pick up the "unhandled payout" attributed to it via `get_first_unhandled_payout_by_operator_xonly_pk` and proceed through the Round/Kickoff/Reimburse flow to claim BTC reimbursement for a withdrawal it did not process.
2. **An honest operator permanently unable to be reimbursed**: the attacker instead writes garbage bytes so `parse_op_return_data`/`XOnlyPublicKey::from_slice` fails, `operator_xonly_pk` becomes `None`, and `is_kickoff_malicious` treats the true funding operator's later kickoff as malicious (`payout_info` will show no payer, or the payer won't match `kickoff_data.operator_xonly_pk`), permanently blocking that operator's reimbursement for a payout it genuinely serviced.

### Likelihood Explanation
The attack requires no privileged role, no key compromise, and no majority hashrate — only observing the broadcast (unconfirmed) payout transaction in the public mempool (or receiving it via the public gRPC `Withdraw`/`InternalWithdraw` endpoints if exposed) and replacing the unsigned `OP_RETURN`/anchor outputs, then getting the modified transaction mined first (e.g., via a competitive relay/fee-bump race, which is explicitly enabled by the `ANYONECANPAY` flag that lets anyone add fee-paying inputs without invalidating the signature).

### Recommendation
Bind the operator attribution to the transaction under the same signature scope that pays the user, e.g. by using a sighash type that commits to all outputs (`SIGHASH_ALL`) for the operator-attribution output, or by having the aggregator/verifiers co-sign the final payout transaction (as already done for `optimistic_payout`) rather than relying on an unauthenticated self-declared `OP_RETURN` field to determine reimbursement rights.

### Proof of Concept
1. Operator A calls `Withdraw`/`InternalWithdraw`, which builds and broadcasts a payout tx: input = withdrawal UTXO (user-owned, `SIGHASH_SINGLE|ANYONECANPAY` signed), output0 = user payout, output1 = anchor, output2 = `OP_RETURN(A_pubkey)` — see `create_payout_txhandler` (core/src/builder/transaction/operator_reimburse.rs:407-436).
2. Before this transaction confirms, an attacker (no special role required) observes it in the mempool/relay and constructs a variant reusing the exact same input and signature (both are still valid because they are the only sighash-committed parts) with output0 unchanged, but output2 replaced with `OP_RETURN(B_pubkey)` (a different, arbitrary operator's public xonly key) and adds their own fee-bump input via `ANYONECANPAY`.
3. Attacker gets their variant mined first (e.g., by paying a higher relay fee).
4. `update_finalized_payouts` (core/src/verifier.rs:2311-2342) parses the confirmed transaction, extracts `B_pubkey` from `OP_RETURN`, and persists `payout_payer_operator_xonly_pk = B` via `update_payout_txs_and_payer_operator_xonly_pk`.
5. Operator B's `PayoutCheckerTask::run_once` (core/src/task/payout_checker.rs:39-79) discovers this "unhandled payout" attributed to itself and proceeds to claim BTC reimbursement it never earned, while Operator A's original broadcast is rejected as a double-spend and A receives nothing.

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

**File:** core/src/rpc/parser/operator.rs (L174-187)
```rust
    if input_signature.sighash_type == TapSighashType::Default {
        tracing::warn!(
            "Input signature for withdrawal {} has sighash type default, setting to SinglePlusAnyoneCanPay", params.withdrawal_id,
        );
        input_signature.sighash_type = TapSighashType::SinglePlusAnyoneCanPay;
    }

    // enforce sighash type here
    if input_signature.sighash_type != TapSighashType::SinglePlusAnyoneCanPay {
        return Err(Status::invalid_argument(format!(
            "Input signature has wrong sighash type, SinglePlusAnyoneCanPay expected, got {}",
            input_signature.sighash_type
        )));
    }
```

**File:** core/src/verifier.rs (L1875-1890)
```rust
        let Some((operator_xonly_pk_opt, payout_blockhash, _, _)) = payout_info else {
            tracing::warn!(
                "No payout info found in db for move txid {move_txid}, assuming malicious"
            );
            return Ok(true);
        };

        let Some(operator_xonly_pk) = operator_xonly_pk_opt else {
            tracing::warn!("No operator xonly pk found in payout tx OP_RETURN, assuming malicious");
            return Ok(true);
        };

        if operator_xonly_pk != kickoff_data.operator_xonly_pk {
            tracing::warn!("Operator xonly pk for the payout does not match with the kickoff_data");
            return Ok(true);
        }
```

**File:** core/src/verifier.rs (L2311-2342)
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

**File:** core/src/operator.rs (L1703-1729)
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

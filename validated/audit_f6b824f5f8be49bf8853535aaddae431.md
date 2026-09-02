## Finding

Clementine's payout transaction uses a `SIGHASH_SINGLE | SIGHASH_ANYONECANPAY` user signature that only binds the withdrawal input to the user's payout output, leaving the transaction's other outputs — including the `OP_RETURN` output that names which operator gets credited as the payer — completely unauthenticated and freely malleable by anyone who observes the signature.

### Title
Unauthenticated OP_RETURN payer attribution in payout_tx allows reimbursement credit to be stolen from the fronting operator - (File: core/src/builder/transaction/operator_reimburse.rs)

### Summary
`create_payout_txhandler` builds the payout transaction with a user-provided `SinglePlusAnyoneCanPay` signature that only commits to the withdrawal input and the corresponding user-payout output. [1](#0-0)  The `OP_RETURN` output carrying the operator's x-only pubkey — the on-chain field that later determines which operator is credited as the payer for reimbursement — is added after the signed output and is not covered by that signature. [2](#0-1)  `Operator::withdraw` verifies the user signature only against input 0 with `SinglePlusAnyoneCanPay` semantics, then funds the transaction via `fund_raw_transaction`, which can add or replace outputs/inputs. [3](#0-2) 

### Finding Description
Because `ANYONECANPAY` does not commit to other inputs and `SIGHASH_SINGLE` only commits to the output at the same index as the signed input, once an operator's payout transaction (or its raw witness) becomes visible — e.g. broadcast to the Bitcoin mempool — any unprivileged party can:

1. Extract the user's `SinglePlusAnyoneCanPay` signature and the signed withdrawal input/output from the pending transaction.
2. Construct a new transaction reusing that exact signed input/output pair (satisfying the only committed constraint), but supplying their own fee-funding inputs and a different `OP_RETURN` output containing an arbitrary/victim operator's x-only pubkey.
3. Broadcast this with a higher fee so it replaces (RBF) the honest operator's original payout transaction.

The bridge's verifier-side ingestion later trusts whatever `OP_RETURN` pubkey appears in the *confirmed* transaction as ground truth for who fronted the withdrawal, with no check that this party actually funded/broadcast the original payout:

```
let operator_xonly_pk = op_return_output
    .and_then(|output| parse_op_return_data(&output.script_pubkey))
    .and_then(|bytes| XOnlyPublicKey::from_slice(bytes).ok());
...
self.db.update_payout_txs_and_payer_operator_xonly_pk(...)
``` [4](#0-3) 

This DB attribution is then used, independent of whether that operator itself submitted the withdrawal, to determine which operator's automation should proceed with kickoff/reimbursement (`get_first_unhandled_payout_by_operator_xonly_pk`, `validate_payer_is_operator`). [5](#0-4) [6](#0-5)  The verifier's malicious-kickoff check also only compares the kickoff's operator against this same unauthenticated `OP_RETURN` field, so a forged attribution is internally self-consistent and passes verification. [7](#0-6) 

This breaks the intended binding: `operator credited for reimbursement == operator that actually fronted/broadcast the payout`. An attacker (needing no Clementine role or credential, only Bitcoin network visibility and the ability to broadcast a replacement transaction) can rewrite that binding for any in-flight payout before it confirms.

### Impact Explanation
The honest operator who genuinely funded the withdrawal (adding their own wallet inputs via `fund_raw_transaction`) can have their transaction pre-empted by an RBF replacement that reassigns payer credit to a different operator's pubkey. Since the withdrawal UTXO is now spent by the replacement transaction, the honest operator can never resubmit or complete their own version — they are permanently unable to obtain reimbursement for that withdrawal (`validate_payer_is_operator` will reject their kickoff because the recorded payer no longer matches them). This matches the Critical-tier impact "an honest operator permanently unable to be reimbursed."

### Likelihood Explanation
Exploitation requires no privileged role in the protocol (no verifier/operator/aggregator/watchtower credential) — only the ability to observe an unconfirmed payout transaction (mempool visibility) and broadcast a replacement transaction with a higher fee, both of which are permissionless Bitcoin-network actions. The signature scheme (`SinglePlusAnyoneCanPay`) is explicitly designed to allow third-party fee-bumping/funding, which is exactly the mechanism that also allows unauthenticated modification of the `OP_RETURN` payer field.

### Recommendation
Bind the operator attribution to the same signed commitment as the payout, e.g. by having the user's signature (or an operator/aggregator co-signature) cover the `OP_RETURN` output as well (use `SIGHASH_ALL`-style coverage for that output, or commit the intended operator pubkey inside the data the user signs off-chain), so that no third party can rewrite who is credited as payer without invalidating the transaction.

### Proof of Concept
1. Operator A calls `withdraw`, producing `payout_tx_A` with input `in_outpoint` (signed `SinglePlusAnyoneCanPay`), user output, and `OP_RETURN(operator_A_xonly_pk)`, then broadcasts it (visible in mempool).
2. Attacker observes `payout_tx_A` in the mempool, extracts the witness for input 0.
3. Attacker builds `payout_tx_B` reusing the exact same signed input and user-payout output (index 0), but supplies their own additional funding input(s) for fees, and sets `OP_RETURN(operator_V_xonly_pk)` for an arbitrary victim/colluding operator V.
4. Attacker broadcasts `payout_tx_B` with a higher fee, causing it to replace `payout_tx_A` and confirm.
5. `update_finalized_payouts` parses `OP_RETURN` from the confirmed `payout_tx_B` and records operator V as `payout_payer_operator_xonly_pk` for this withdrawal. [8](#0-7) 
6. Operator A's later `get_reimbursement_txs`/kickoff attempts fail `validate_payer_is_operator` since the recorded payer is V, not A — A can never be reimbursed for the funds they attempted to front. [9](#0-8)

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

**File:** core/src/operator.rs (L1686-1729)
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
```

**File:** core/src/verifier.rs (L1857-1890)
```rust
    /// Checks if the operator who sent the kickoff matches the payout data saved in our db
    /// Payout data in db is updated during citrea sync.
    async fn is_kickoff_malicious(
        &self,
        kickoff_witness: Witness,
        deposit_data: &mut DepositData,
        kickoff_data: KickoffData,
        dbtx: DatabaseTransaction<'_>,
    ) -> Result<bool, BridgeError> {
        let move_txid =
            create_move_to_vault_txhandler(deposit_data, self.config.protocol_paramset())?
                .get_cached_tx()
                .compute_txid();

        let payout_info = self
            .db
            .get_payout_info_from_move_txid(Some(dbtx), move_txid)
            .await?;
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

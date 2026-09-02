Confirmed: `get_payout_txs_for_withdrawal_utxos` joins purely on `bitcoin_syncer_spent_utxos.txid/vout` matching the registered `withdrawal_utxo_txid/vout` [1](#0-0) , i.e. it picks up **whichever transaction actually spends that outpoint on-chain**, with zero regard for who funded the other inputs. Combined with `update_finalized_payouts` blindly trusting the OP_RETURN bytes of that transaction as the payer identity [2](#0-1) , and the fact that `create_payout_txhandler` signs only input 0 / output 0 under `SinglePlusAnyoneCanPay` while the OP_RETURN and any extra funding inputs are left completely uncommitted [3](#0-2) , this is a real, exploitable attribution-spoofing bug.

### Title
Attribution of `payout_payer_operator_xonly_pk` is derived from an unsigned, attacker-controllable OP_RETURN, letting anyone credit an arbitrary operator (or themselves) for a payout they never funded - (File: core/src/verifier.rs, core/src/builder/transaction/operator_reimburse.rs)

### Summary
The user's withdrawal signature uses `SinglePlusAnyoneCanPay`, which only commits to the withdrawal input and the single output at the same index; it leaves the payout transaction's OP_RETURN output (which the protocol treats as the sole proof of "which operator funded this payout") and any additional funding inputs completely unsigned and attacker-choosable. Because the DB join that finds "the" payout tx for a withdrawal keys purely off which transaction spends the registered `withdrawal_utxo` on-chain, any party who can construct a competing `ANYONECANPAY` variant that spends the same UTXO and reaches confirmation first can put any xonly public key (their own, or a real operator's) into the OP_RETURN and thereby dictate who the bridge later treats as the operator "owed" reimbursement.

### Finding Description
The broken binding: `operator_credited_for_withdrawal (payout_payer_operator_xonly_pk in DB)` should equal `operator_whose_funds_actually_paid_the_withdrawer`. In practice `Verifier::update_finalized_payouts` sets this value purely by parsing the OP_RETURN of whatever transaction is found via `get_payout_txs_for_withdrawal_utxos`, which itself is just "the transaction that happens to spend `withdrawal_utxo_txid:withdrawal_utxo_vout` in this block" [1](#0-0) [4](#0-3) .

`create_payout_txhandler` builds the canonical payout tx with input 0 = withdrawal UTXO (signed `SinglePlusAnyoneCanPay` by the withdrawer), output 0 = user payout (the only output committed by that signature under SIGHASH_SINGLE), output 1 = anchor, output 2 = OP_RETURN with `operator_xonly_pk` [3](#0-2) . Because the sighash type is `SinglePlusAnyoneCanPay`, the withdrawer's signature does **not** cover the OP_RETURN output or any additional inputs used to fund the payout amount/fee — this is enforced/expected exactly as documented at `parse_withdrawal_sig_params` [5](#0-4)  and at `Operator::withdraw`'s signature check [6](#0-5) .

The attacker (who is the withdrawer and thus already legitimately holds the withdrawal UTXO's private key and the ANYONECANPAY signature — this is exactly the RPC input flow described by `WithdrawParams`) can, entirely outside of any Clementine RPC, build their own transaction: same input 0 + same signature + same output 0 (script/amount fixed by the sighash commitment), but with attacker-chosen additional ANYONECANPAY inputs to cover fee/amount and an attacker-chosen OP_RETURN naming any xonly public key (their own, or a legitimate registered operator's, since operator xonly pks are public). If this transaction is broadcast with a higher fee and confirms before (or instead of) the real operator-funded payout_tx, `get_payout_txs_for_withdrawal_utxos`/`update_finalized_payouts` will record the attacker-forged pubkey as `payout_payer_operator_xonly_pk` for that withdrawal, with the recorded block hash being the real confirmation block of the attacker's tx [7](#0-6) .

Downstream, `PayoutCheckerTask` polls `get_first_unhandled_payout_by_operator_xonly_pk(self.signer.xonly_public_key)` [8](#0-7)  and `core/src/database/verifier.rs:282-313`. If the forged pubkey matches a real operator's key, that operator's own automation will find this "unhandled payout" and call `handle_finalized_payout`, initiating the kickoff/reimbursement flow. None of the existing guards catch the forgery:
- `is_kickoff_malicious` only checks that the DB's `operator_xonly_pk` (attacker-forged) equals `kickoff_data.operator_xonly_pk` (trivially true if the credited operator is the one kicking off) and that the committed blockhash matches the DB's blockhash (also true, since it's the genuine block of the attacker's tx) [9](#0-8) .
- `send_asserts` performs the same self-consistent (and therefore non-protective) check [10](#0-9) .
- Nothing anywhere verifies that the additional funding inputs of the payout tx were actually signed/provided by the operator named in the OP_RETURN — there is no cryptographic binding between "who funded the extra inputs" and "whose pubkey is in OP_RETURN."

### Impact Explanation
This directly matches the Critical category "an operator reimbursed for a payout it never funded." A named operator's automation is tricked into believing it fronted a withdrawal, and proceeds through kickoff/assert/reimburse to reclaim `bridge_amount` collateral from the presigned N-of-N transaction graph, even though the real fronting (if any) was done by the attacker's own extra inputs, not that operator's wallet. This is repeatable per withdrawal and works against any operator whose xonly public key is known (which is by design public), so the blast radius spans all deposits/operators in the system. It does not require compromising any key, since the OP_RETURN bytes are simply data pushed by whoever constructs the winning transaction.

### Likelihood Explanation
The attacker only needs to be the withdrawer (a normal, unprivileged capability explicitly listed) and be able to fund/broadcast a competing Bitcoin transaction spending their own withdrawal UTXO with a higher fee — both of which are Bitcoin-layer actions requiring no interaction with any Clementine RPC or privileged role. Cost is bounded by the fee needed to outbid the legitimate operator's payout tx in the mempool/next block, which is cheap and fully within attacker control since they choose the withdrawal amount/fee economics themselves as the withdrawer.

### Recommendation
Bind the OP_RETURN operator identity cryptographically to the actual funder of the payout, e.g. by requiring the operator to sign a commitment over the full payout transaction (including OP_RETURN and all non-withdrawal inputs) with their own key, and have `update_finalized_payouts`/`is_kickoff_malicious` verify that signature rather than trusting raw OP_RETURN bytes. Alternatively, require that the additional funding inputs' script_pubkeys resolve to the claimed operator's registered address before treating the OP_RETURN pubkey as authoritative, and reject/deprioritize the DB update when a withdrawal UTXO's spending tx contains inputs that cannot be attributed to any known operator.

### Proof of Concept
```
cargo test -p clementine-core --features automation forge_payout_attribution
```
Test plan:
1. Run a single deposit + withdrawal setup as in `run_operator_end_round`/`deposit_and_withdraw_e2e.rs`, obtaining `withdrawal_utxo`, the `SinglePlusAnyoneCanPay` signature, and `output_txout` from `generate_withdrawal_transaction_and_signature`.
2. Construct the legitimate operator's payout tx via `create_payout_txhandler` (operator A's xonly_pk) but do **not** broadcast it.
3. Construct an attacker-built alternate transaction: same input 0 (`withdrawal_utxo`) + same signature, same output 0, but with an attacker-funded extra input and an OP_RETURN containing operator B's (a different, uninvolved operator's) xonly_pk.
4. Broadcast the attacker's transaction first and mine it to finality.
5. Assert (equality check before/after): before, `payout_payer_operator_xonly_pk` for this withdrawal is `None`; after `Verifier::update_finalized_payouts` runs on the confirming block, assert `db.get_payout_info_from_move_txid(...).0 == Some(operator_B_xonly_pk)` even though operator B never funded/broadcast any transaction and operator A's real payout tx never confirmed.
6. Optionally continue: run operator B's `PayoutCheckerTask`/`handle_finalized_payout` and show it proceeds to kickoff without error, confirming the false credit is actionable for reimbursement.

### Citations

**File:** core/src/database/verifier.rs (L170-196)
```rust
    pub async fn get_payout_txs_for_withdrawal_utxos(
        &self,
        tx: Option<DatabaseTransaction<'_>>,
        block_id: u32,
    ) -> Result<Vec<(u32, Txid)>, BridgeError> {
        let query = sqlx::query_as::<_, (i32, TxidDB)>(
            "SELECT w.idx, bsu.spending_txid
             FROM withdrawals w
             JOIN bitcoin_syncer_spent_utxos bsu
                ON bsu.txid = w.withdrawal_utxo_txid
                AND bsu.vout = w.withdrawal_utxo_vout
             WHERE bsu.block_id = $1",
        )
        .bind(i32::try_from(block_id).wrap_err("Failed to convert block id to i32")?);

        let results = execute_query_with_tx!(self.connection, tx, query, fetch_all)?;

        results
            .into_iter()
            .map(|(idx, txid)| {
                Ok((
                    u32::try_from(idx).wrap_err("Failed to convert withdrawal index to u32")?,
                    txid.0,
                ))
            })
            .collect()
    }
```

**File:** core/src/verifier.rs (L1857-1914)
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

        let wt_derive_path = WinternitzDerivationPath::Kickoff(
            kickoff_data.round_idx,
            kickoff_data.kickoff_idx,
            self.config.protocol_paramset(),
        );
        let commits = extract_winternitz_commits(
            kickoff_witness,
            &[wt_derive_path],
            self.config.protocol_paramset(),
        )?;
        let blockhash_data = commits.first();
        // only last 20 bytes of the blockhash is committed
        let truncated_blockhash = &payout_blockhash[12..];
        if let Some(committed_blockhash) = blockhash_data {
            if committed_blockhash != truncated_blockhash {
                tracing::warn!("Payout blockhash does not match committed hash: committed: {:?}, truncated payout blockhash: {:?}",
                        blockhash_data, truncated_blockhash);
                return Ok(true);
            }
        } else {
            return Err(eyre::eyre!("Couldn't retrieve committed data from witness").into());
        }
        Ok(false)
```

**File:** core/src/verifier.rs (L2283-2352)
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
```

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

**File:** core/src/rpc/parser/operator.rs (L161-187)
```rust
#[allow(clippy::result_large_err)]
pub fn parse_withdrawal_sig_params(
    params: WithdrawParams,
) -> Result<(u32, taproot::Signature, OutPoint, ScriptBuf, Amount), Status> {
    let mut input_signature =
        taproot::Signature::from_slice(&params.input_signature).map_err(|e| {
            Status::invalid_argument(format!("Can't convert input to taproot Signature - {e}"))
        })?;

    // If the Taproot sighash type is Default (no explicit type attached; i.e. a 64-byte
    // signature without a sighash flag), normalize it to SinglePlusAnyoneCanPay.
    // Prior to v0.5 this was Clementine's implicit behavior; we retain it here for
    // backwards compatibility when a 64-byte signature is provided.
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

**File:** core/src/operator.rs (L628-637)
```rust
        // tracing::info!("Payout txhandler: {:?}", hex::encode(bitcoin::consensus::serialize(&payout_txhandler.get_cached_tx())));

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

**File:** core/src/task/payout_checker.rs (L41-47)
```rust
        let unhandled_payout = self
            .db
            .get_first_unhandled_payout_by_operator_xonly_pk(
                Some(&mut dbtx),
                self.operator.signer.xonly_public_key,
            )
            .await?;
```

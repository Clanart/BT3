This confirms the full attack chain. `is_kickoff_malicious` at `core/src/verifier.rs:1887` compares `operator_xonly_pk` (read from `payout_info`, which comes from the OP_RETURN of whichever tx spent the withdrawal UTXO) against `kickoff_data.operator_xonly_pk` — if they mismatch, it flags the kickoff as malicious, with no fallback to check who actually funded output 0.

### Title
Payout tx operator attribution can be hijacked via SIGHASH_SINGLE|ANYONECANPAY malleability, permanently denying honest operator reimbursement - (core/src/operator.rs:614-637, core/src/builder/transaction/operator_reimburse.rs:407-436)

### Summary
`Operator::withdraw` signs the payout transaction's input 0 using `TapSighashType::SinglePlusAnyoneCanPay`, which binds only input 0 and output 0. An attacker who observes the operator's unconfirmed payout tx in the mempool can extract the witness (`user_sig`), rebuild a new transaction spending the same input 0 with the same output 0 but a different output 1 (fee anchor) and output 2 (OP_RETURN naming themselves), and get it mined first with a higher fee rate. Because attribution of "who paid the withdrawal" is done purely by which transaction spends the withdrawal UTXO and what its OP_RETURN says, the attacker becomes credited as the payer, permanently blocking the honest operator's reimbursement path.

### Finding Description
The broken binding: `payout_payer_operator_xonly_pk` (the DB column set by `update_finalized_payouts`) should equal `self.signer.xonly_public_key` of the operator whose BTC funded `output_txout` (output 0) of the mined payout transaction. After the attack, `payout_payer_operator_xonly_pk` == attacker's key while the actual funder of output 0 is the honest operator — the equality is broken.

Root cause: `create_payout_txhandler` (`core/src/builder/transaction/operator_reimburse.rs:407-436`) builds a 3-output transaction (payout output 0, anchor output 1, OP_RETURN output 2 containing `operator_xonly_pk`) and calls `set_p2tr_key_spend_witness(&user_sig, 0)`. The witness for input 0 is just the raw Schnorr signature [1](#0-0) . In `Operator::withdraw` (`core/src/operator.rs:614-637`), the signature is verified against `payout_txhandler.calculate_sighash_txin(0, in_signature.sighash_type)` [2](#0-1) , and `calculate_pubkey_spend_sighash` shows that for `SinglePlusAnyoneCanPay` the `Prevouts::One(txin_index, ...)` variant is used [3](#0-2) . `parse_withdrawal_sig_params` enforces exactly this sighash type [4](#0-3) . Under BIP341, `SIGHASH_SINGLE|ANYONECANPAY` commits only to input 0's outpoint/amount/script and to output 0 — it says nothing about any other input or output. This means the signature remains valid for **any** transaction that keeps input 0 and output 0 unchanged, regardless of other inputs/outputs (fee anchor, OP_RETURN, additional funding inputs added by `fund_raw_transaction`).

Attribution of the payout to an operator does not use a specific pre-registered txid; it is derived purely from chain data: `get_payout_txs_for_withdrawal_utxos` (`core/src/database/verifier.rs:170-196`) joins on `bitcoin_syncer_spent_utxos` by `(txid, vout)` of the withdrawal outpoint — i.e., it returns whichever transaction the syncer observed spending that outpoint, not the txid the honest operator broadcast. `update_finalized_payouts` (`core/src/verifier.rs:2283-2352`) then reads the OP_RETURN of that tx and sets `payout_payer_operator_xonly_pk` accordingly [5](#0-4) .

Exploit flow:
1. Honest operator calls `withdraw`, producing and broadcasting payout tx T1 (input 0 = withdrawal UTXO with `user_sig` witness, output 0 = user payout, output 1 = anchor, output 2 = OP_RETURN(honest_pk)), unconfirmed.
2. Attacker (unprivileged, only needs to watch the mempool/network) extracts `user_sig` from T1's witness for input 0.
3. Attacker builds T2 reusing the identical input 0 (outpoint + `user_sig` witness) and identical output 0, but replaces output 1 with a higher fee-paying structure and output 2's OP_RETURN with `attacker_pk`. T2 is fully valid Bitcoin-consensus-wise since the signature only commits to input 0/output 0.
4. Attacker broadcasts T2 with a higher fee rate; it replaces/out-races T1 in mempool and gets mined.
5. `update_finalized_payouts` sees T2 spending the withdrawal outpoint, reads OP_RETURN = `attacker_pk`, sets `payout_payer_operator_xonly_pk = attacker_pk` [6](#0-5) .
6. `PayoutCheckerTask::run_once` calls `get_first_unhandled_payout_by_operator_xonly_pk(self.operator.signer.xonly_public_key)` for the honest operator — this query filters `WHERE payout_payer_operator_xonly_pk = $1`, so it never returns the payout for the honest operator [7](#0-6) . The honest operator can never call `handle_finalized_payout`, so `kickoff_txid`/`payout_blockhash` are never recorded for it.
7. If the honest operator (who actually funded output 0) attempts to kickoff anyway, `is_kickoff_malicious` (`core/src/verifier.rs:1859-1914`) compares `operator_xonly_pk` from `payout_info` (= attacker_pk) against `kickoff_data.operator_xonly_pk` (= honest operator) — mismatch → returns `true` (malicious), triggering a challenge against the honest operator [8](#0-7) . Alternatively `validate_payer_is_operator`, used by `get_reimbursement_txs`, will also reject the honest operator since `payer_xonly_pk != self.signer.xonly_public_key` [9](#0-8) .

No existing guard closes this gap: `SECP.verify_schnorr` only checks the signature is valid for input 0/output 0 (which it still is for the attacker's tx); `is_deposit_valid`, `is_profitable`, and the aggregator's `verification_signature` check are all performed at withdrawal-request time against the honest operator's own tx construction and cannot prevent a third party from broadcasting a conflicting spend of the public withdrawal UTXO afterward. `is_kickoff_malicious` and `validate_payer_is_operator` both trust the OP_RETURN-derived attribution as ground truth, with no fallback to inspect who actually funded output 0's value.

### Impact Explanation
This permanently and irrecoverably denies the honest operator (who fronted the real BTC to output 0, i.e. the full withdrawal amount, a class-10-BTC value) its Reimburse path for that specific withdrawal: the DB will never attribute the payout to the honest operator's key, `handle_finalized_payout`/`mark_payout_handled` never fire for it, and any kickoff it sends gets flagged malicious and challenged. This matches "Critical - an honest operator permanently unable to be reimbursed." The attack is repeatable per-withdrawal across any deposit/operator as long as the attacker can observe an operator's payout tx in the mempool before confirmation and outbid its fee — no bridge funds are needed from the attacker, only fee capital for T2, and no privileged role is required.

### Likelihood Explanation
Preconditions are default and require no special privileges: a normal Bitcoin mempool (default policy, no anti-fee-sniping or pinning protections assumed beyond standard rules), and the operator broadcasting payout via RBF-enabled `fund_raw_transaction`/`replaceable` as coded (`core/src/operator.rs:651-673`). The attacker only needs to observe an unconfirmed transaction on the network (any node/mempool.space) and pay a modestly higher fee — a very low-cost, highly feasible, and fully repeatable attack across every withdrawal processed by any operator.

### Recommendation
Bind the entire payout transaction's structure into the signed message, not just input 0/output 0. Options: require `SIGHASH_ALL` (or `Default`) instead of `SinglePlusAnyoneCanPay` so the operator's own OP_RETURN/anchor outputs are covered by the user's signature (this may need the user to sign a template with a fixed anchor value ahead of time), or have the operator additionally commit to (and the state machine verify) a specific pre-registered `payout_txid` per withdrawal index rather than attributing based on whichever tx spends the outpoint on-chain, and treat any other spend of that withdrawal UTXO as a failed/invalid payout attempt (falling back to optimistic payout or refunding) instead of silently attributing it to whatever OP_RETURN appears.

### Proof of Concept
```rust
// cargo test in core, regtest based, extending deposit_and_withdraw_e2e.rs harness
// 1. Set up deposit + withdrawal utxo as in existing e2e tests.
// 2. Have honest operator0 call withdraw() -> obtain T1 (unconfirmed, in mempool),
//    extract input0 witness (user_sig) via rpc.get_mempool_entry / get_raw_transaction.
// 3. Manually construct T2 using core::builder::transaction::TxHandlerBuilder:
//    - same input 0 (outpoint + witness = user_sig, taken verbatim from T1)
//    - same output 0 (payout output, byte-identical)
//    - different output 1 (higher fee, e.g. drop anchor / add attacker change)
//    - OP_RETURN output with attacker_xonly_pk instead of operator0's pk
//    Broadcast T2 with fee_rate higher than T1's, mine it (rpc.mine_blocks).
// 4. Assert rpc.get_tx_of_txid(T1) is not confirmed / not in the mined block (T2 replaced it).
// 5. Wait for verifier/operator bitcoin syncer to process the block containing T2.
// 6. Assert:
//    db.get_first_unhandled_payout_by_operator_xonly_pk(honest_operator_pk).await? == None
//    db.get_payout_info_from_move_txid(move_txid).await?.0 == Some(attacker_xonly_pk)
// 7. Have honest operator0 attempt internal_finalized_payout / kickoff for this deposit,
//    and assert verifier's is_kickoff_malicious(...) returns true for operator0's kickoff_data,
//    even though operator0 funded output 0 of the mined transaction.
```

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

**File:** core/src/operator.rs (L1710-1718)
```rust
            (Some(payer_xonly_pk), Some(payout_blockhash), Some(kickoff_txid)) => {
                if payer_xonly_pk != self.signer.xonly_public_key {
                    return Err(eyre::eyre!(
                        "Payer is not own operator for deposit, payer xonly pk: {:?}, operator xonly pk: {:?}",
                        payer_xonly_pk,
                        self.signer.xonly_public_key
                    )
                    .into());
                }
```

**File:** core/src/builder/transaction/txhandler.rs (L222-229)
```rust
        let prevouts = match sighash_type {
            TapSighashType::SinglePlusAnyoneCanPay
            | TapSighashType::AllPlusAnyoneCanPay
            | TapSighashType::NonePlusAnyoneCanPay => {
                bitcoin::sighash::Prevouts::One(txin_index, prevouts_vec[txin_index])
            }
            _ => bitcoin::sighash::Prevouts::All(&prevouts_vec),
        };
```

**File:** core/src/rpc/parser/operator.rs (L180-187)
```rust

    // enforce sighash type here
    if input_signature.sighash_type != TapSighashType::SinglePlusAnyoneCanPay {
        return Err(Status::invalid_argument(format!(
            "Input signature has wrong sighash type, SinglePlusAnyoneCanPay expected, got {}",
            input_signature.sighash_type
        )));
    }
```

**File:** core/src/verifier.rs (L1887-1890)
```rust
        if operator_xonly_pk != kickoff_data.operator_xonly_pk {
            tracing::warn!("Operator xonly pk for the payout does not match with the kickoff_data");
            return Ok(true);
        }
```

**File:** core/src/verifier.rs (L2312-2342)
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

**File:** core/src/task/payout_checker.rs (L39-51)
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
```

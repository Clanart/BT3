### Title
Payout tx's `SinglePlusAnyoneCanPay` signature only binds input 0 + output 0, letting anyone replace the operator-attribution OP_RETURN before confirmation - ([File: core/src/builder/transaction/operator_reimburse.rs])

### Summary
`create_payout_txhandler` puts the fronting operator's x-only pubkey in output 2 as a plain OP_RETURN push, but `Operator::withdraw` only Schnorr-verifies a `SinglePlusAnyoneCanPay` sighash that (per BIP-341) commits solely to input 0 and output 0. Anyone who observes the honest operator's unconfirmed payout tx in the mempool can clone input 0 (with its witness) and output 0 verbatim, attach an arbitrary/garbage OP_RETURN and a different anchor output, and get this variant mined instead (e.g. via RBF/CPFP fee-bumping on the freely-spendable P2A anchor), silently breaking the operator-attribution binding.

### Finding Description
The binding that must hold is:
`payout_payer_operator_xonly_pk[withdrawal_idx]` (stored by `Database::update_payout_txs_and_payer_operator_xonly_pk`) **must equal** `xonly_pk_of_operator_whose_signature_funded_output_0`.

Trace:
- `Operator::withdraw` builds the payout tx via `create_payout_txhandler` [1](#0-0) , which places `operator_xonly_pk.serialize()` in output 2 as a bare OP_RETURN push [2](#0-1) .
- The only cryptographic check on this transaction is `SECP.verify_schnorr` over `calculate_sighash_txin(0, in_signature.sighash_type)` [3](#0-2) , and the sighash type is enforced to be `SinglePlusAnyoneCanPay` [4](#0-3) .
- For `SinglePlusAnyoneCanPay`, `calculate_pubkey_spend_sighash`/`calculate_sighash_txin` uses `Prevouts::One(txin_index, ...)` [5](#0-4) , and per BIP-341 the resulting sighash commits only to input 0 and the single output at index 0 - **not** to output 1 (anchor) or output 2 (OP_RETURN operator attribution).
- Consequently, a third party who sees the broadcast, unconfirmed payout tx in the mempool can copy input 0 (outpoint + witness, which is public) and output 0 verbatim, then freely substitute output 2's OP_RETURN with 32 zero bytes (or any value) and change the anchor output, producing a still-valid, differently-txid'd transaction spending the same input.
- Since the anchor output is a permissionless P2A output explicitly designed for third-party CPFP fee bumping [6](#0-5) , the attacker can cheaply bump their variant's effective fee rate and win the confirmation race against the honest operator's original (which is a conflicting/double-spend of the same input).
- Once the variant confirms, `Verifier::update_finalized_payouts` parses the OP_RETURN of the transaction that actually spent the withdrawal UTXO: `parse_op_return_data` + `XOnlyPublicKey::from_slice` fails on 32 zero bytes (not a valid curve point), so `operator_xonly_pk` is `None`, and `payout_payer_operator_xonly_pk` is stored as `NULL` [7](#0-6) .
- `PayoutCheckerTask::run_once` looks up unhandled payouts filtered by `payout_payer_operator_xonly_pk = $1` (the operator's own key) [8](#0-7) [9](#0-8) ; with the column NULL, the honest operator's payout is never found, so `handle_finalized_payout`/`mark_payout_handled` never run for it.
- Separately, `Verifier::is_kickoff_malicious` reads `get_payout_info_from_move_txid`; if `operator_xonly_pk_opt` is `None` it explicitly treats the kickoff as malicious ("No operator xonly pk found in payout tx OP_RETURN, assuming malicious") [10](#0-9) , which drives verifiers toward Challenge/Disprove against the honest operator's later kickoff for that deposit, threatening collateral burn.

No existing guard revalidates that the mined payout tx's OP_RETURN matches the operator who actually signed/funded output 0; the check is purely "whatever confirmed on-chain says," and the signature scheme deliberately does not bind outputs 1/2.

### Impact Explanation
The honest operator who fronted the user's withdrawal from their own funds can be made permanently unable to be reimbursed (payout attribution lost forever once the substitute confirms, per-deposit and repeatable across every withdrawal/operator), and can additionally have their kickoff flagged malicious by `is_kickoff_malicious`, exposing their round collateral to Challenge/Disprove. This matches the Critical categories "an honest operator permanently unable to be reimbursed" and "an honest operator's collateral burned." The user's withdrawal output (output 0) is unaffected/unchanged, so no bridge value is stolen by this path directly - the damage is entirely to the operator's reimbursement bookkeeping and collateral safety.

### Likelihood Explanation
Preconditions: an operator must have broadcast an unconfirmed payout tx (a routine part of every withdrawal), and the substitute must confirm before/instead of the original - feasible via BIP-125 RBF-style replacement or by winning the natural propagation race, aided by the fact that the P2A anchor output is designed to be spent by anyone for CPFP. The attacker needs no privileged role, no key material beyond what is already public in the mempool transaction, and only pays incremental fees. This is repeatable for every payout tx broadcast by every operator, making the blast radius protocol-wide rather than a one-off event.

### Recommendation
Change `create_payout_txhandler`'s input signing/verification to use a sighash type that also commits to the OP_RETURN output (e.g. `AllPlusAnyoneCanPay`, or restructure so the operator's own signature over the full transaction - including the OP_RETURN and anchor - is what's checked/relied upon, rather than only the user-provided `SinglePlusAnyoneCanPay` signature). Additionally, `update_finalized_payouts`/`is_kickoff_malicious` should not unconditionally treat a missing/invalid operator xonly pk as proof the *committing* operator is malicious when the payer's own presigned/committed operator identity data (independent of the malleable OP_RETURN) can be cross-checked instead.

### Proof of Concept
`cargo test` plan (regtest, `core/src/test`):
1. Run an e2e deposit + withdrawal flow up to the point where `Operator::withdraw` broadcasts the payout tx to the regtest mempool but before it confirms (mine 0 blocks).
2. From the raw mempool tx, extract input 0 (with witness) and output 0 verbatim; construct a new transaction with an OP_RETURN output of `[0u8;32]` and a fresh anchor output; broadcast it with a higher fee (RBF) via `bitcoincore_rpc`.
3. Mine 1 block so only the substitute tx confirms.
4. Assert both sides of the binding:
   - `db.get_withdrawal_utxo_from_citrea_withdrawal(...)` still equals the same outpoint (user paid correctly).
   - After running `Verifier::handle_finalized_block` / `update_finalized_payouts`, assert `db.get_payer_xonly_pk_blockhash_and_kickoff_txid_from_deposit_id(deposit_id)` returns `(None, Some(blockhash), None)` instead of `(Some(operator_xonly_pk), ...)`.
   - Assert `PayoutCheckerTask::run_once` returns `false` / never calls `handle_finalized_payout` for that operator (`get_first_unhandled_payout_by_operator_xonly_pk` returns `None`).
   - Drive a kickoff for that deposit and assert `Verifier::is_kickoff_malicious` returns `true` despite the operator having genuinely funded the withdrawal.

### Citations

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

**File:** core/src/rpc/parser/operator.rs (L181-187)
```rust
    // enforce sighash type here
    if input_signature.sighash_type != TapSighashType::SinglePlusAnyoneCanPay {
        return Err(Status::invalid_argument(format!(
            "Input signature has wrong sighash type, SinglePlusAnyoneCanPay expected, got {}",
            input_signature.sighash_type
        )));
    }
```

**File:** core/src/builder/transaction/txhandler.rs (L222-233)
```rust
        let prevouts = match sighash_type {
            TapSighashType::SinglePlusAnyoneCanPay
            | TapSighashType::AllPlusAnyoneCanPay
            | TapSighashType::NonePlusAnyoneCanPay => {
                bitcoin::sighash::Prevouts::One(txin_index, prevouts_vec[txin_index])
            }
            _ => bitcoin::sighash::Prevouts::All(&prevouts_vec),
        };

        let sig_hash = sighash_cache
            .taproot_key_spend_signature_hash(txin_index, &prevouts, sighash_type)
            .wrap_err("Failed to calculate taproot sighash for key spend")?;
```

**File:** core/src/builder/transaction/mod.rs (L252-262)
```rust
/// Creates a P2A (anchor) output for Child Pays For Parent (CPFP) fee bumping.
///
/// # Returns
///
/// A [`TxOut`] with a statically defined script and value, used as an anchor output in protocol transactions. The TxOut is spendable by anyone.
pub fn anchor_output(amount: Amount) -> TxOut {
    TxOut {
        value: amount,
        script_pubkey: ScriptBuf::from_hex("51024e73").expect("statically valid script"),
    }
}
```

**File:** core/src/verifier.rs (L1882-1885)
```rust
        let Some(operator_xonly_pk) = operator_xonly_pk_opt else {
            tracing::warn!("No operator xonly pk found in payout tx OP_RETURN, assuming malicious");
            return Ok(true);
        };
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

**File:** core/src/task/payout_checker.rs (L39-52)
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

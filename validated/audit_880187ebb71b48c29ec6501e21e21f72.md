### Title
Payout OP_RETURN attribution is unauthenticated under `SinglePlusAnyoneCanPay`, letting anyone attribute a confirmed payout to an arbitrary operator and drain reimbursement - ([File: core/src/builder/transaction/operator_reimburse.rs])

### Summary
`create_payout_txhandler` only asks the withdrawing user to sign input 0 with `SinglePlusAnyoneCanPay`, which under BIP341/BIP143 rules commits only to input 0 and the output at the same index (output 0) [1](#0-0) . The anchor output and the OP_RETURN output (index 2, which encodes `operator_xonly_pk`) are never covered by that signature [2](#0-1) . `Verifier::update_finalized_payouts` blindly trusts whatever OP_RETURN appears in the transaction that actually confirms spending the tracked withdrawal UTXO, with no check that the named operator actually funded it [3](#0-2) .

### Finding Description
The claimed binding is: `operator_xonly_pk_recorded(i) == party_whose_funds_paid_output_0(i)`. Tracing the code shows this binding is never enforced.

- `Operator::withdraw` verifies the user's signature only over input 0 with `calculate_sighash_txin(0, in_signature.sighash_type)` and rejects anything other than `SinglePlusAnyoneCanPay` [4](#0-3) . `calculate_pubkey_spend_sighash`/`calculate_sighash_txin` for `*PlusAnyoneCanPay` sighash types use `Prevouts::One(txin_index, ...)`, i.e. BIP341 SIGHASH_SINGLE|ANYONECANPAY semantics: the signature commits to input 0 and only the output at index 0 [5](#0-4) . It does **not** commit to any other input or output — in particular not to the OP_RETURN at output index 2 that encodes `operator_xonly_pk` [6](#0-5) .
- `parse_withdrawal_sig_params` at the aggregator/operator gRPC boundary enforces this exact sighash type for `WithdrawParams.input_signature` [7](#0-6) , confirming this is the mandated and only accepted flag — the malleability is by design, not accidental misuse.
- Because output 0 (script_pubkey + amount, i.e. the withdrawing user's payment) and input 0 (the withdrawal UTXO + witness) are the only committed elements, anyone who has the signature — which trivially includes the withdrawing user themselves, since they are the entity who authored it, or anyone who observes it broadcast before confirmation — can build an entirely different transaction: same input 0, same output 0, but with their own extra funding inputs (their own money, signed by themselves) and an arbitrary output-2 OP_RETURN (any operator's `xonly_pk`, garbage, or omitted).
- `Verifier::update_finalized_payouts` determines "the" payout for withdrawal index `i` purely from whichever transaction is later found spending the tracked `withdrawal_utxo_txid`/`withdrawal_utxo_vout` on-chain (`bitcoin_syncer_spent_utxos` join) [8](#0-7) , then parses the OP_RETURN of that confirmed transaction and stores it verbatim as `payout_payer_operator_xonly_pk` with no cross-check against who actually supplied the funding inputs [9](#0-8) .
- `PayoutCheckerTask::run_once` runs autonomously per operator and, purely based on this DB column via `get_first_unhandled_payout_by_operator_xonly_pk`, triggers `handle_finalized_payout` → generates and sends the Kickoff/Reimburse chain for that operator, with no verification that operator itself broadcast or funded the payout tx [10](#0-9) .
- `is_kickoff_malicious` (the verifier's fraud check) only compares the DB-recorded `operator_xonly_pk` against the kickoff's claimed `operator_xonly_pk` [11](#0-10) ; since both sides read the same attacker-controlled OP_RETURN value, this check is fully satisfied and raises no fraud flag — it cannot detect that the named operator never funded the payment.

Exploit flow: the attacker (who may simply be the legitimate withdrawing user, since they hold the withdrawal key and craft the `SinglePlusAnyoneCanPay` signature themselves) builds and broadcasts a payout transaction entirely with their own funding inputs, using the genuine signature for input 0 and the correct output 0 (so the withdrawal is genuinely serviced and no funds are frozen), but stamps output 2's OP_RETURN with the `xonly_pk` of an arbitrary registered operator (public information). No operator RPC call, gRPC authentication, verifier signature, or musig2 session is required for this path — `create_payout_txhandler`'s output 2 is unauthenticated. Once this transaction confirms, that operator's automation will autonomously claim reimbursement it never funded.

### Impact Explanation
This lets an unprivileged attacker cause the bridge's `move_to_vault` funds to be paid out (via the presigned Reimburse transaction chain) to an operator identity of the attacker's choosing, for a withdrawal that operator never funded — i.e., "an operator reimbursed for a payout it never funded," a listed Critical impact. The named operator receives `bridge_amount` from the vault while the withdrawal itself was paid for solely by the attacker's own funds, representing a net drain of protocol collateral disconnected from actual capital fronted. It is repeatable per withdrawal event, and the blast radius spans every operator (any registered `xonly_pk` can be targeted) and every deposit/withdrawal cycle, since the trust point — an unauthenticated OP_RETURN byte string — is structural to `create_payout_txhandler` and not specific to any single deposit.

A secondary variant (racing an honest operator's own broadcast with a higher-fee competing spend of the same withdrawal UTXO, either overwriting the OP_RETURN with garbage or another operator's key) can also make the honest operator that genuinely funded output 0 unable to ever match `get_first_unhandled_payout_by_operator_xonly_pk`, though in that sub-case the honest operator's own added funding inputs are simply never spent (they remain in their wallet since their tx doesn't confirm), so it does not by itself demonstrate a collateral-burn via `is_kickoff_malicious` — that additional escalation was not confirmed in the available code and would require further tracing of exactly when/whether an operator commits to a kickoff before knowing the final DB attribution.

### Likelihood Explanation
The attack requires no privileged role: the attacker only needs to be a party capable of authoring a valid Taproot key-path `SinglePlusAnyoneCanPay` signature over their own withdrawal UTXO (something any withdrawing Citrea user already does) and enough of their own BTC to fund fees/output amount and broadcast directly to Bitcoin. It requires no interaction with the aggregator/operator RPCs at all, since the transaction can be constructed and broadcast independently once the signature exists. This is fully reproducible on regtest and does not depend on Citrea liveness, majority hashrate, or key compromise.

### Recommendation
Bind the OP_RETURN commitment cryptographically to the withdrawal authorization. Options: (1) require the user's signature to cover the full output set (e.g., `SIGHASH_ALL` or `SIGHASH_ALL|ANYONECANPAY`) once the operator's identity is fixed at signing time, or (2) require a second, operator-authenticated commitment (e.g., include `operator_xonly_pk` as a script-path condition enforced at the input, or have the aggregator's musig2 session co-sign a hash that binds output 0 to output 2) so a third party cannot alter the payer attribution while preserving output 0. Additionally, `Verifier::update_finalized_payouts` / `is_kickoff_malicious` should require independent proof that the claiming operator actually supplied the payout's funding inputs (e.g., verify the additional inputs' `scriptPubKey`s trace back to the operator's known wallet) rather than trusting the OP_RETURN byte string alone.

### Proof of Concept
```
cargo test --package core payout_op_return_malleability_regtest -- --nocapture
```
Test plan:
1. Set up regtest bridge with two registered operators, `pk_A` (an honest/target operator) and `pk_B`, and a deposit/withdrawal (index `i`) as in `core/src/test/manual_reimbursement.rs`.
2. Generate the withdrawal UTXO and the user's `SinglePlusAnyoneCanPay` signature via `generate_withdrawal_transaction_and_signature` (as in `core/src/test/common/setup_utils.rs:430-543`), producing `(input_utxo, output_txout, user_sig)`.
3. Build two competing payout transactions with `builder::transaction::create_payout_txhandler`:
   - `tx_honest`: same `input_utxo`/`output_txout`/`user_sig`, `operator_xonly_pk = pk_A`.
   - `tx_attacker`: same `input_utxo` (same outpoint), same `output_txout`, same `user_sig` (witness copied verbatim — the signature is valid for both because it only commits to input 0/output 0), but with extra attacker-funded inputs and `operator_xonly_pk = pk_B`, at a higher fee rate.
4. Broadcast `tx_attacker` first (or with RBF) so it confirms instead of `tx_honest`; mine to finality depth.
5. Run `Verifier::handle_finalized_block`/`update_finalized_payouts` and assert, via `db.get_payout_info_from_move_txid`, that `payout_payer_operator_xonly_pk == Some(pk_B)` (attacker-chosen), not `pk_A`.
6. Assert `db.get_first_unhandled_payout_by_operator_xonly_pk(pk_B)` returns `Some(i, ...)` while the same query for `pk_A` returns `None`, demonstrating the binding `operator_xonly_pk_recorded(i) == party_whose_funds_paid_output_0(i)` is violated (funds for output 0 came from the attacker, not `pk_B`).
7. Optionally, let operator `B`'s `PayoutCheckerTask` run automatically and confirm it produces a Kickoff/Reimburse chain and eventually a completed `Reimburse` transaction paying `pk_B`'s `reimburse_addr`, confirming BTC leaves the `move_to_vault` UTXO to an operator that never funded the payout.

### Citations

**File:** core/src/builder/transaction/txhandler.rs (L210-233)
```rust
    pub fn calculate_pubkey_spend_sighash(
        &self,
        txin_index: usize,
        sighash_type: TapSighashType,
    ) -> Result<TapSighash, BridgeError> {
        let prevouts_vec: Vec<&TxOut> = self
            .txins
            .iter()
            .map(|s| s.get_spendable().get_prevout())
            .collect();
        let mut sighash_cache: SighashCache<&bitcoin::Transaction> =
            SighashCache::new(&self.cached_tx);
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

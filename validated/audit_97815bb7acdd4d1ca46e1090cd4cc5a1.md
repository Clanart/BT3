## Analysis

The report's bug class ("Purge transaction via Overdraft Transactions") is about a check/validation of *who owns/paid for a resource* being decoupled from what actually gets executed on-chain. The closest reachable analog in this repository is in the **operator-payout attribution mechanism**, where the field used to decide *which operator gets reimbursed from the bridge vault* is taken from unauthenticated, unsigned transaction data that any unprivileged party can rewrite before confirmation.

### Root cause

`create_payout_txhandler` builds the payout transaction with the user's `SinglePlusAnyoneCanPay` signature covering only input 0 and output 0 (the user payout). The OP_RETURN output that records the fronting operator's `xonly_pk` is appended afterward and is **not committed to by the user's signature**: [1](#0-0) 

Because `SinglePlusAnyoneCanPay` only binds input 0 to output 0, anyone who observes the unconfirmed payout transaction (or its constituent signed input+output) can construct a conflicting transaction that keeps the same signed input/output pair but swaps in a **different OP_RETURN payload** — i.e., a different operator's `xonly_pk` — and fund it themselves.

Downstream, `update_finalized_payouts` blindly trusts whatever OP_RETURN happens to be in the *mined* transaction to decide who is credited as the payer: [2](#0-1) 

This value is persisted as `payout_payer_operator_xonly_pk` with no cryptographic binding to the actual funder of the transaction: [3](#0-2) 

Each operator's own automation (`PayoutCheckerTask`) polls for payouts attributed to its own key and, once found, unconditionally proceeds to consume a kickoff connector and start the reimbursement flow — without any check that *this operator* actually broadcast or funded the transaction: [4](#0-3) [5](#0-4) 

The reimbursement itself pays real bridge-vault funds (the `DepositInMove` output) to the credited operator's address: [6](#0-5) 

### Broken binding

`operator_credited (parsed from mutable OP_RETURN)` should equal `operator_that_actually_funded_the_payout`, but no signature or provenance check enforces this equality. An unprivileged party who sees an in-flight (unconfirmed) payout transaction can substitute the OP_RETURN and win the race (e.g. via RBF, since `fund_raw_transaction` is called with `replaceable: None`, i.e. wallet default), fronting the withdrawal themselves while attributing it to any registered operator's `xonly_pk`. That operator's own automation will then claim reimbursement for a payout it never funded.

### Title
Unsigned OP_RETURN operator-attribution in payout transactions allows misattributed reimbursement claims - (File: core/src/builder/transaction/operator_reimburse.rs)

### Summary
The payout transaction's operator-attribution field (OP_RETURN containing the fronting operator's `xonly_pk`) is not covered by the user's `SinglePlusAnyoneCanPay` signature, and the protocol trusts this field verbatim from the confirmed on-chain transaction to decide which operator is reimbursed from the bridge vault.

### Finding Description
`create_payout_txhandler` signs only input 0 and output 0 with the user's `SinglePlusAnyoneCanPay` signature ( [7](#0-6) ). The anchor and OP_RETURN outputs are appended by whoever constructs/funds the final transaction and are entirely unauthenticated. `update_finalized_payouts` parses this OP_RETURN from the mined transaction and writes it as `payout_payer_operator_xonly_pk` with no further verification ( [8](#0-7) ). `validate_payer_is_operator` and `PayoutCheckerTask` treat this DB field as ground truth for "who fronted the withdrawal" ( [9](#0-8) , [10](#0-9) ), driving consumption of a kickoff connector and eventual reimbursement of vault funds ( [11](#0-10) ).

Because the OP_RETURN is unsigned, any unprivileged party observing an unconfirmed payout transaction can rebroadcast a variant that reuses the same signed input/output-0 pair, but with its own funding inputs and an OP_RETURN naming a different operator's `xonly_pk`, and get it confirmed instead (e.g., via RBF, since `fund_raw_transaction`'s `replaceable` option is left at wallet default) ( [12](#0-11) ).

### Impact Explanation
This breaks the required equality "operator credited == party that actually funded the payout." A registered operator can be credited — and subsequently claim vault-funded reimbursement — for a withdrawal it never funded, while the operator that actually fronted the withdrawal fee/amount for its own broadcast can have its transaction pre-empted and never receive credit. This matches the Critical-impact category "an operator reimbursed for a payout it never funded" / "an honest operator permanently unable to be reimbursed."

### Likelihood Explanation
The attack requires no privileged role: the attacker only needs to observe a publicly broadcast, unconfirmed payout transaction (or independently construct one from a leaked signed input/output pair) and rebroadcast a fee-competitive variant with a different OP_RETURN before the original confirms. No verifier/operator/watchtower access, key compromise, or majority hashrate is needed.

### Recommendation
Bind the operator-attribution data to the transaction cryptographically (e.g., have the operator sign an additional commitment covering the OP_RETURN output, or move the attribution off-chain into a signed RPC record verified independently of on-chain parsing) instead of trusting mutable, unsigned OP_RETURN bytes as the sole source of truth for reimbursement eligibility.

### Proof of Concept
1. Operator X calls `withdraw()`, which builds and broadcasts a payout tx: input 0 = user's `SinglePlusAnyoneCanPay`-signed withdrawal UTXO, output 0 = user payout, OP_RETURN = X's `xonly_pk` ( [13](#0-12) ).
2. Attacker observes this unconfirmed transaction, extracts input 0's witness and output 0, and constructs a new transaction with the same input 0/output 0, attacker-supplied funding inputs/fee, and OP_RETURN = operator Y's `xonly_pk` (a different, uninvolved registered operator).
3. Attacker's transaction confirms (e.g., via RBF/fee competition) instead of X's.
4. Verifier's `update_finalized_payouts` records `payout_payer_operator_xonly_pk = Y` from the confirmed tx ( [14](#0-13) ).
5. Operator Y's own `PayoutCheckerTask` finds this "unhandled payout" attributed to itself and proceeds through `handle_finalized_payout`/kickoff/reimburse flow to claim vault funds it never fronted ( [4](#0-3) ).

### Citations

**File:** core/src/builder/transaction/operator_reimburse.rs (L341-385)
```rust
pub fn create_reimburse_txhandler(
    move_txhandler: &TxHandler,
    round_txhandler: &TxHandler,
    kickoff_txhandler: &TxHandler,
    kickoff_idx: usize,
    paramset: &'static ProtocolParamset,
    operator_reimbursement_address: &bitcoin::Address,
) -> Result<TxHandler, BridgeError> {
    let builder = TxHandlerBuilder::new(TransactionType::Reimburse)
        .with_version(NON_STANDARD_V3)
        .add_input(
            NormalSignatureKind::Reimburse1,
            move_txhandler.get_spendable_output(UtxoVout::DepositInMove)?,
            builder::script::SpendPath::ScriptSpend(0),
            DEFAULT_SEQUENCE,
        )
        .add_input(
            NormalSignatureKind::Reimburse2,
            kickoff_txhandler.get_spendable_output(UtxoVout::ReimburseInKickoff)?,
            builder::script::SpendPath::ScriptSpend(0),
            DEFAULT_SEQUENCE,
        )
        .add_input(
            NormalSignatureKind::OperatorSighashDefault,
            round_txhandler.get_spendable_output(UtxoVout::ReimburseInRound(
                kickoff_idx,
                paramset.num_kickoffs_per_round,
            ))?,
            builder::script::SpendPath::KeySpend,
            DEFAULT_SEQUENCE,
        );

    Ok(builder
        .add_output(UnspentTxOut::from_partial(TxOut {
            value: move_txhandler
                .get_spendable_output(UtxoVout::DepositInMove)?
                .get_prevout()
                .value,
            script_pubkey: operator_reimbursement_address.script_pubkey(),
        }))
        .add_output(UnspentTxOut::from_partial(
            builder::transaction::anchor_output(paramset.anchor_amount()),
        ))
        .finalize())
}
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

**File:** core/src/database/schema.sql (L269-281)
```sql
create table if not exists withdrawals (
    idx int primary key,
    move_to_vault_txid bytea not null,
    withdrawal_utxo_txid bytea,
    withdrawal_utxo_vout int,
    withdrawal_batch_proof_bitcoin_block_height int,
    payout_txid bytea,
    payout_payer_operator_xonly_pk text,
    payout_tx_blockhash text check (payout_tx_blockhash ~ '^[a-fA-F0-9]{64}'),
    is_payout_handled boolean not null default false,
    kickoff_txid bytea,
    created_at timestamp not null default now()
);
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

**File:** core/src/operator.rs (L620-674)
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

        let fee_rate = self
            .rpc
            .get_fee_rate_kvb(
                self.config.protocol_paramset.network,
                &self.config.mempool_api_host,
                &self.config.mempool_api_endpoint,
                self.config.tx_sender_limits.mempool_fee_rate_multiplier,
                self.config.tx_sender_limits.mempool_fee_rate_offset_sat_kvb,
                self.config.tx_sender_limits.fee_rate_hard_cap,
            )
            .await?;

        // send payout tx using RBF
        let funded_tx = self
            .rpc
            .fund_raw_transaction(
                payout_txhandler.get_cached_tx(),
                Some(&bitcoincore_rpc::json::FundRawTransactionOptions {
                    add_inputs: Some(true),
                    include_unsafe: Some(false),
                    change_address: None,
                    change_position: Some(1),
                    change_type: None,
                    include_watching: None,
                    lock_unspents: Some(false),
                    fee_rate: Some(Amount::from_sat(fee_rate.to_sat_per_kvb())),
                    subtract_fee_from_outputs: None,
                    replaceable: None,
                    conf_target: None,
                    estimate_mode: None,
                }),
                None,
            )
            .await
            .wrap_err("Failed to fund raw transaction")?
            .hex;
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

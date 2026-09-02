## Title
Payout attribution can be hijacked via SIGHASH_SINGLE|AnyoneCanPay malleability of the un-signed OP_RETURN output, permanently locking out the honest fronting operator from reimbursement - (File: core/src/builder/transaction/operator_reimburse.rs)

## Summary
The `payout_tx` that attributes a withdrawal's reimbursement to a specific operator is signed by the user with `TapSighashType::SinglePlusAnyoneCanPay`, which under BIP-341 only commits to the spent input and the *single* output at the matching index. The operator-identifying OP_RETURN output (`create_payout_txhandler`, output index 2) and the anchor output (index 1) are never covered by the user's signature. Any unprivileged party who observes the operator's broadcast payout transaction in the mempool can rebroadcast a variant spending the same withdrawal UTXO, keeping the signed user-payout output untouched but stripping or rewriting the OP_RETURN, and win the race to confirmation. This causes `Verifier::is_kickoff_malicious` to treat the honest operator's subsequent Kickoff as malicious, triggering `Challenge` and permanently blocking `create_reimburse_txhandler`'s Reimburse path for that operator/round.

## Finding Description
Binding claimed by the protocol: `payout_payer_operator_xonly_pk` stored for a withdrawal (derived from the OP_RETURN of whichever transaction spends `withdrawal_utxo`) `==` the xonly public key of the operator who actually fronted that withdrawal's payout to the user off-chain.

- The user signs the withdrawal-authorizing signature with `TapSighashType::SinglePlusAnyoneCanPay`, enforced in `parse_withdrawal_sig_params` [1](#0-0) .
- `create_payout_txhandler` builds the payout tx with output 0 = user payout (signed), output 1 = anchor, output 2 = OP_RETURN(operator_xonly_pk) [2](#0-1) .
- `Operator::withdraw` verifies the schnorr signature against the sighash of *that* specific constructed tx, but SIGHASH_SINGLE only commits the input and the output at the matching index (index 0); outputs 1 and 2 (including the operator-identifying OP_RETURN) are outside the signed message [3](#0-2) .
- Once the operator's payout tx is visible (mempool or on-chain), an attacker can construct a new transaction spending the same `withdrawal_utxo` with the identical signed input+output0 witness, but with an arbitrary/absent OP_RETURN, and win the race to confirmation (higher fee, first broadcast, etc.). The attacker only needs to broadcast Bitcoin transactions and pay fees — both explicitly in scope for an unprivileged attacker.
- `Verifier::update_finalized_payouts` records whichever transaction actually spent `withdrawal_utxo_txid`/`vout` as the payout, parsing its OP_RETURN for `operator_xonly_pk`; if missing/mismatched it stores `None` (or another key) [4](#0-3)  via `get_payout_txs_for_withdrawal_utxos` which joins strictly on the spent-UTXO, not the tx's origin [5](#0-4) .
- `Verifier::is_kickoff_malicious` then reads this DB row via `get_payout_info_from_move_txid`; if `operator_xonly_pk_opt` is `None` or doesn't match `kickoff_data.operator_xonly_pk`, it unconditionally returns `true` ("assuming malicious") [6](#0-5) .
- `handle_kickoff` then queues a `Challenge` tx against the honest operator's real kickoff [7](#0-6) .
- Because the corrupted OP_RETURN is baked into the *on-chain* payout tx, the same corrupted `operator_xonly_pk` is what any bridge-circuit proof (`host_deposit_constant`/`bridge_circuit`) would derive from `get_first_op_return_output` [8](#0-7) , so the honest operator cannot even defeat the challenge with a valid BitVM disprove-resistant proof — the attribution corruption is irreversible on-chain, and `create_reimburse_txhandler`'s `ReimburseInKickoff`/`ReimburseInRound` path [9](#0-8)  is never reachable for that operator/round.

No existing guard closes this gap: `SECP.verify_schnorr` only validates the exact sighash-committed fields (input + output 0), `is_deposit_valid`/`verify_storage_proofs` operate on Citrea-side deposit/withdrawal state, not on-chain OP_RETURN attribution, and there is no uniqueness/commitment enforced over outputs 1–2 of the payout tx.

## Impact Explanation
This is Critical: an honest operator (A) who genuinely fronted a withdrawal becomes permanently unable to be reimbursed for the front-funded amount (up to `paramset.bridge_amount`, e.g. 10 BTC), because:
1. The Challenge is queued against A's honest Kickoff.
2. A cannot produce a disprove-defeating proof because the corrupted on-chain OP_RETURN feeds the same deposit-constant computation used by the bridge circuit.
3. A's Round/Kickoff collateral remains exposed to `Disprove`/`OperatorChallengeNack` failure paths, and the Reimburse UTXO chain (`ReimburseInKickoff`, `ReimburseInRound`) is never spendable in A's favor.

The attack is repeatable per withdrawal and per operator; any withdrawal whose payout tx is observable before confirmation is exploitable, and it can additionally misattribute the reimbursement credit to a different (potentially attacker-colluding) operator if the injected 32 bytes happen to parse to a valid xonly pubkey belonging to another operator — matching the "operator reimbursed for a payout it never funded" Critical category as well.

## Likelihood Explanation
No privileged access, key material, or majority hashrate is required — only the ability to observe a broadcast/mempool transaction (or the confirmed block before finality) and rebroadcast a fee-competitive alternative spending the same UTXO, which is exactly within the stated unprivileged attacker capability set (broadcast transactions, pay fees, craft arbitrary scripts/OP_RETURNs). The attacker cost is limited to the fee needed to outcompete the operator's payout tx for block space, far less than the value at risk (the fronted withdrawal amount). Feasibility depends only on catching the operator's payout tx before it's deeply confirmed, which is the normal window between broadcast and confirmation.

## Recommendation
Have the user's off-chain withdrawal signature commit to *all* outputs of the payout transaction, not just the corresponding single output — e.g., require `TapSighashType::All` (or `AllPlusAnyoneCanPay` if additional funding inputs must remain flexible) so the OP_RETURN operator-attribution output cannot be altered without invalidating the user's signature. Alternatively, commit the operator xonly pubkey into the *signed* output (e.g., embed it in the user output's script or use `SIGHASH_SINGLE` only after moving OP_RETURN to output index 0), or attribute payout ownership via a mechanism cryptographically bound into the same signed message as the user's payout.

## Proof of Concept
```
cargo test payout_op_return_hijack_denies_reimbursement -- --nocapture
```
Plan for the test (to be added under core/src/test, non-mock path against real Bitcoin regtest via existing e2e harness):
1. Set up a deposit and withdrawal exactly as in `deposit_and_withdraw_e2e.rs`, obtaining `withdrawal_utxo`, `sig` (SinglePlusAnyoneCanPay), and `payout_txout` for operator A.
2. Call `create_payout_txhandler` to build A's legitimate payout tx (do not broadcast it yet).
3. From A's constructed tx, extract the witness for input 0 (the S+AP signature) and build an attacker transaction spending the same `withdrawal_utxo`, keeping output 0 identical (script_pubkey+amount) but replacing output 2 (OP_RETURN) with garbage bytes / a different operator's xonly pk, funding fees via an additional attacker-owned input (allowed by AnyoneCanPay).
4. Broadcast the attacker's tx with a higher fee rate so it confirms instead of A's tx (or simply confirm it first since A hasn't broadcast yet).
5. Mine to finality; run verifier's `update_finalized_payouts` / citrea sync so `payout_payer_operator_xonly_pk` for that withdrawal is set to `None`/wrong key. Assert on the DB row via `get_payout_info_from_move_txid` that `operator_xonly_pk_opt != Some(A.xonly_pk)`.
6. Have operator A send its real Kickoff tx (per protocol, since A did fund the withdrawal to the user in Citrea).
7. Call `Verifier::handle_kickoff` and assert it returns `is_malicious == true`, and assert a `Challenge` tx was queued (`tx_sender.add_tx_to_queue` invoked with `TransactionType::Challenge`).
8. Assert that no `Reimburse` transaction ever confirms for operator A's kickoff/round within the test's time bound, i.e., `create_reimburse_txhandler`'s output never gets spent to A's `operator_reimbursement_address`.

### Citations

**File:** core/src/rpc/parser/operator.rs (L170-187)
```rust
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

**File:** core/src/builder/transaction/operator_reimburse.rs (L341-371)
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

**File:** core/src/verifier.rs (L1987-2017)
```rust
        if is_malicious {
            tracing::warn!(
                "Malicious {} detected. {} Challenge tx: {} for deposit {}",
                kickoff_data,
                match challenged_before {
                    false => "This is the first malicious kickoff in the current round.",
                    true => "This is not the first malicious kickoff in the current round.",
                },
                bitcoin::consensus::encode::serialize_hex(&challenge_tx),
                deposit_outpoint
            );
            // do not automatically send challenge txs on mainnet or testnet4
            if !challenged_before
                && !matches!(
                    self.config.protocol_paramset().network,
                    bitcoin::Network::Bitcoin | bitcoin::Network::Testnet4
                )
            {
                #[cfg(feature = "automation")]
                self.tx_sender
                    .add_tx_to_queue(
                        dbtx,
                        TransactionType::Challenge,
                        &challenge_tx,
                        &[],
                        Some(tx_metadata),
                        self.config.protocol_paramset(),
                        None,
                    )
                    .await?;
            }
```

**File:** core/src/verifier.rs (L2296-2350)
```rust
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
```

**File:** core/src/database/verifier.rs (L168-196)
```rust
    /// Returns the withdrawal indexes and their spending txid for the given
    /// block id.
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

**File:** circuits-lib/src/bridge_circuit/mod.rs (L206-229)
```rust
    let first_op_return_output = get_first_op_return_output(&input.payout_spv.transaction)
        .expect("Payout transaction must have an OP_RETURN output");

    let round_txid = input.kickoff_tx.input[0]
        .previous_output
        .txid
        .to_byte_array();

    let kickoff_round_vout = input.kickoff_tx.input[0].previous_output.vout;

    let operator_xonlypk: [u8; 32] = parse_op_return_data(&first_op_return_output.script_pubkey)
        .expect("Invalid operator xonlypk")
        .try_into()
        .expect("Invalid xonlypk");

    let deposit_constant = deposit_constant(
        operator_xonlypk,
        input.watchtower_challenge_connector_start_idx,
        &input.all_tweaked_watchtower_pubkeys,
        *move_txid,
        round_txid,
        kickoff_round_vout,
        input.hcp.genesis_state_hash,
    );
```

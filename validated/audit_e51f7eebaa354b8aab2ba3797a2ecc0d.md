### Title
Attacker-chosen `in_signature` sighash flag lets a non-`SIGHASH_ALL` payout be output-malleated post-broadcast, corrupting `operator_xonly_pk` attribution and getting an honest operator's kickoff flagged malicious - ([File: core/src/verifier.rs, core/src/operator.rs])

### Summary
`Operator::withdraw` accepts an arbitrary `taproot::Signature` (including its embedded sighash flag) from the withdrawer without restricting it to `SIGHASH_ALL`/`Default`. Because the withdrawal input is spent via Taproot key-path spend, a non-`SIGHASH_ALL` flag (e.g. `SINGLE`) only commits to a subset of the transaction's outputs, allowing anyone who observes the broadcast payout in the mempool to construct and race a competing, still-validly-signed transaction that keeps the user payout output but corrupts or removes the OP_RETURN carrying `operator_xonly_pk`.

### Finding Description
The broken binding is: `operator_xonly_pk_in_db == operator_xonly_pk_of_operator_that_actually_fronted_the_payout`.

`Operator::withdraw` (`core/src/operator.rs:560-627`) takes `in_signature: taproot::Signature` directly from the caller and forwards it unchecked into `create_payout_txhandler` [1](#0-0) , which builds a single-input Taproot key-spend transaction with three outputs (user payout, anchor, OP_RETURN with `operator_xonly_pk`) and finalizes the witness with that exact signature [2](#0-1) . Nothing in this path enforces that the sighash flag embedded in `in_signature` is `SIGHASH_ALL`/`Default`.

Bitcoin's Taproot key-path sighash rules mean a non-`SIGHASH_ALL` flag (e.g. `SIGHASH_SINGLE`) commits only to the output at the matching index (the user payout) and not to the other outputs (anchor, OP_RETURN). Since the withdrawal UTXO's owner (the withdrawer) fully controls the signature and its sighash flag when calling Citrea's `withdraw`, an attacker who is the withdrawer can hand the honest operator a signature with a permissive sighash flag. The operator constructs and broadcasts the normal, correct payout transaction. Because outputs beyond the committed index are not bound by the signature, the attacker can build an alternate, still-signature-valid transaction spending the same withdrawal outpoint, with the same committed user-payout output but a malformed/missing OP_RETURN, and race it into the block ahead of (or replacing) the honest operator's broadcast.

Once that malleated transaction is what gets mined, `update_finalized_payouts` (`core/src/verifier.rs:2283-2353`) resolves the payout purely from `bitcoin_syncer_spent_utxos.spending_txid` for the withdrawal outpoint [3](#0-2)  — i.e., whichever transaction actually spends the UTXO on-chain, not necessarily the one the operator submitted. It then parses that mined transaction's first OP_RETURN via `get_first_op_return_output`/`parse_op_return_data`; if it fails to yield a valid xonly pubkey, `operator_xonly_pk` is set to `None` and persisted [4](#0-3) . `is_kickoff_malicious` then reads this `None` and unconditionally returns `Ok(true)` [5](#0-4) , causing the honest operator's subsequent kickoff for that same deposit to be treated as malicious and challenged, burning their collateral.

None of the listed guards defend against this: `is_deposit_valid`, `verify_storage_proofs`, `SPV::verify`, and the presigned tx graph all operate on deposit/kickoff structure and don't validate the sighash flag of the withdrawal signature; `SECP.verify_schnorr` only checks signature validity for whatever sighash was actually signed, which is satisfied by construction under `SIGHASH_SINGLE`.

### Impact Explanation
An honest operator's collateral is burned via a false-malicious kickoff determination, even though they correctly fronted the withdrawal — this matches the Critical category "an honest operator's collateral burned." The attack is repeatable per withdrawal the attacker controls (they must be the withdrawer supplying the malleable signature), and can target any operator that services that withdrawal, so the blast radius scales with the number of withdrawals an attacker is willing to fund/race.

### Likelihood Explanation
Preconditions: the attacker must be the entity calling Citrea's `withdraw` (trivial, self-service) and must supply a `taproot::Signature` with a non-`SIGHASH_ALL` flag; the codebase's `withdraw`/`create_payout_txhandler` path must not reject such flags (no such rejection was found in the reachable code). Cost is bounded to the withdrawal amount plus a mempool fee-race premium to get the malleated decoy mined instead of/ahead of the honest payout — feasible for any attacker willing to pay competitive fees, and fully repeatable across withdrawals/operators.

### Recommendation
Reject any `in_signature` whose embedded `TapSighashType` is not `Default`/`All` (and, if `ANYONECANPAY` variants aren't required, also reject those) before accepting a withdrawal request, both in `Operator::withdraw` and wherever the aggregator/gRPC layer first receives the withdrawal signature. Additionally, consider having verifiers, before marking a kickoff malicious purely on a missing/invalid OP_RETURN, cross-check whether an alternate (non-canonical) but sighash-consistent payout for the same UTXO exists that correctly attributes the honest operator, to avoid punishing operators for transaction malleability outside their control.

### Proof of Concept
```
cargo test -p core --test deposit_and_withdraw_e2e malleated_sighash_single_payout_burns_honest_operator_collateral
```
Plan:
1. Set up a deposit and withdrawal as in existing e2e harness (`core/src/test/deposit_and_withdraw_e2e.rs`).
2. Have the "attacker" withdrawer sign the withdrawal input with `TapSighashType::Single` instead of `Default`, and hand this signature to the honest operator's `withdraw` call.
3. Let the honest operator broadcast the correct payout tx (user output + anchor + valid OP_RETURN with its `operator_xonly_pk`).
4. Before confirmation, construct and broadcast a second transaction with the same single input/signature, identical user-payout output, but a corrupted OP_RETURN (or none), with a higher fee; mine it.
5. Assert equality-before: `db.get_payout_info_from_move_txid(...).0 == Some(honest_operator_xonly_pk)` fails to hold after `update_finalized_payouts` runs — assert it is instead `None`.
6. Assert `verifier.is_kickoff_malicious(...)` returns `Ok(true)` for the honest operator's subsequent kickoff, and that a `Challenge` tx targeting the honest operator's collateral is queued, despite the operator having correctly funded the payout.

### Citations

**File:** core/src/operator.rs (L605-626)
```rust
        if !Self::is_profitable(
            input_utxo.txout.value,
            output_txout.value,
            self.config.protocol_paramset().bridge_amount,
            operator_withdrawal_fee_sats,
        ) {
            return Err(eyre::eyre!("Not enough fee for operator").into());
        }

        let user_xonly_pk = &input_utxo
            .txout
            .script_pubkey
            .try_get_taproot_pk()
            .wrap_err("Input utxo script pubkey is not a valid taproot script")?;

        let payout_txhandler = builder::transaction::create_payout_txhandler(
            input_utxo,
            output_txout,
            self.signer.xonly_public_key,
            in_signature,
            self.config.protocol_paramset().network,
        )?;
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

**File:** core/src/verifier.rs (L1882-1885)
```rust
        let Some(operator_xonly_pk) = operator_xonly_pk_opt else {
            tracing::warn!("No operator xonly pk found in payout tx OP_RETURN, assuming malicious");
            return Ok(true);
        };
```

**File:** core/src/verifier.rs (L2312-2328)
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
```

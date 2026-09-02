### Title
Payout transaction's OP_RETURN operator identity is not bound to the funding party, allowing false attribution of payout-funder - (File: `core/src/builder/transaction/operator_reimburse.rs`, `circuits-lib/src/bridge_circuit/mod.rs`, `core/src/verifier.rs`)

### Summary
`create_payout_txhandler` signs the withdrawal input with `SinglePlusAnyoneCanPay`, which commits only to input 0 and output 0. Every other input/output — including the OP_RETURN carrying `operator_xonlypk` — is completely unauthenticated, so the party who owns the withdrawal signature (the withdrawing user, an unprivileged attacker under this scope) can build an alternative, still-validly-signed transaction that funds output 0 itself and inserts a forged OP_RETURN naming an arbitrary operator's public key. `get_first_op_return_output` and the downstream `deposit_constant`/`is_kickoff_malicious` logic take that OP_RETURN at face value with no check that the named operator actually supplied the funding.

### Finding Description
The broken binding: `payout_payer_operator_xonly_pk` (persisted from `get_first_op_return_output` in [1](#0-0) , and re-derived as `operator_xonlypk` inside `deposit_constant`/`journal_hash` in [2](#0-1) ) is assumed to equal "the xonly_pk of the operator whose own funds actually paid output 0 of the confirmed payout tx." This equality is never enforced.

`create_payout_txhandler` builds output 0 (user payout), output 1 (anchor), output 2 (operator's OP_RETURN with `operator_xonly_pk`) and signs input 0 only with `SinglePlusAnyoneCanPay`: [3](#0-2) . `calculate_script_spend_sighash`/Prevouts::One confirms only input 0's prevout is committed for `*PlusAnyoneCanPay` types: [4](#0-3) . Consequently the user's Schnorr signature (verified in `Operator::withdraw` via `SECP.verify_schnorr`) remains valid for *any* transaction that reuses the same input-0 outpoint/signature and the same output-0 script/amount, regardless of which other inputs fund it or what other outputs (including OP_RETURNs) it contains.

Because the withdrawing user (the attacker, per scope) controls the signature and the input-0 outpoint from the start, they can construct their own transaction: input 0 (their withdrawal UTXO, same signature) + an additional self-owned funding input covering output 0's amount, output 0 (identical to what was signed, paying themselves), and a single forged OP_RETURN naming any real operator's public xonly key (operators' keys are public). If this transaction gets mined instead of (or as a race against) the legitimate operator's constructed payout tx, `get_first_op_return_output` ( [5](#0-4) ) returns the attacker's forged output, and `update_finalized_payouts` stores that operator's key as `payer_operator_xonly_pk`: [6](#0-5) .

The named (victim) operator's own automation then picks this row up via `get_first_unhandled_payout_by_operator_xonly_pk` keyed on its own xonly pubkey ( [7](#0-6) , [8](#0-7) ) and proceeds to kickoff. `is_kickoff_malicious` only checks that the DB-stored `operator_xonly_pk` equals `kickoff_data.operator_xonly_pk` — both now equal the framed operator's real key, so the check passes: [9](#0-8) . The framed operator can then legitimately complete `Reimburse` and be paid the bridge amount for a payout it never actually funded, because nothing in the protocol ties the OP_RETURN's claimed pubkey to which party supplied the non-committed funding input.

### Impact Explanation
This matches the Critical category "an operator reimbursed for a payout it never funded." The attacker, acting as their own withdrawing user, can self-fund output 0 (net cost ≈ 0, since they pay themselves) while forging the OP_RETURN to name a victim operator, causing that operator's own PayoutChecker/kickoff automation to claim and receive bridge reimbursement funds for a payout it never made. This is repeatable per withdrawal and does not require compromising any operator, verifier, or aggregator key — only control over one's own withdrawal signature and enough capital/fee-priority to get the crafted transaction confirmed instead of (or as a replacement for) any competing broadcast.

### Likelihood Explanation
Feasibility is high in mechanism (SIGHASH_SINGLE|ANYONECANPAY malleability of non-committed inputs/outputs is a well-known technique) but requires the attacker to win a mempool race/fee-bump against any operator who might also try to front the same withdrawal, and to front the withdrawal amount themselves (though it returns to them). No privileged role, key share, or collateral is needed — only knowledge of a target operator's public xonly key, which is public protocol data.

### Recommendation
Cryptographically bind the OP_RETURN operator commitment to the actual funding party, e.g., require the operator's own protocol key to sign over the full output set (via `SIGHASH_ALL` for the operator-added funding input, or by adding a covenant/connector that only the correct operator can satisfy), and/or have `is_kickoff_malicious`/`deposit_constant` independently verify that the operator claiming reimbursement actually supplied the additional funding input(s) of the confirmed payout transaction, not merely that an OP_RETURN happens to name them.

### Proof of Concept
In `circuits-lib`, add a test that: (1) builds the legitimate payout tx via `create_payout_txhandler` with `operator_xonly_pk = op1_pk`, producing `tx_a` with OP_RETURN(op1_pk) at output 2; (2) builds a second transaction `tx_b` reusing the same input 0 (same `UTXO`/`user_sig`), same output 0, but with different additional funding input and a single OP_RETURN(op1_pk) at output 1 (attacker-constructed, no real op1 involvement); (3) verify `tx_b`'s input-0 sighash under `SinglePlusAnyoneCanPay` equals the one `user_sig` was produced for (i.e., the signature validates for both `tx_a` and `tx_b`); (4) call `get_first_op_return_output(&CircuitTransaction::from(tx_b))` and `parse_op_return_data`, asserting the recovered pubkey equals `op1_pk` even though `tx_b`'s extra funding came from a different, attacker-controlled key — demonstrating that `deposit_constant`'s `operator_xonlypk` input can be forged independent of who actually funded the payout.

### Citations

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

**File:** circuits-lib/src/bridge_circuit/mod.rs (L686-692)
```rust
/// Retrieves the first output of a transaction that is an OP_RETURN script. Used in various
/// contexts to extract metadata or constants from transactions.
pub fn get_first_op_return_output(tx: &CircuitTransaction) -> Option<&TxOut> {
    tx.output
        .iter()
        .find(|out| out.script_pubkey.is_op_return())
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

**File:** core/src/builder/transaction/txhandler.rs (L315-322)
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

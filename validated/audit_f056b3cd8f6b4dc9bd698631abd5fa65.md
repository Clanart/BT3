### Title
Payout OP_RETURN operator-attribution is unauthenticated and malleable, letting anyone strand a fronting operator's reimbursement - (File: `core/src/verifier.rs`)

### Summary
`create_payout_txhandler` signs only input 0 with `SinglePlusAnyoneCanPay`, which commits solely to input 0 and output 0 of the payout transaction. The OP_RETURN output carrying the fronting operator's `XOnlyPublicKey` (output 2) is completely uncommitted, so anyone who observes the unconfirmed payout tx can rebroadcast a fee-bumped variant that reuses input 0's witness and output 0 verbatim while substituting a different or unparsable OP_RETURN, permanently mis-attributing (or nulling) the operator recorded for that withdrawal.

### Finding Description
The broken binding is:
`withdrawals.payout_payer_operator_xonly_pk[idx]` (as written by `Verifier::update_finalized_payouts`) `==` xonly_pk of the operator whose wallet actually funded/authorized the mined payout at withdrawal index `idx`.

`create_payout_txhandler` builds the payout transaction with a single signed input (the withdrawal UTXO, `SpendPath::KeySpend`) and three outputs: user payout (0), anchor (1), and an OP_RETURN with the operator's `xonly_pk` (2) [1](#0-0) . The user's signature is `TapSighashType::SinglePlusAnyoneCanPay`, verified in `Operator::withdraw` against `sighash = payout_txhandler.calculate_sighash_txin(0, in_signature.sighash_type)` [2](#0-1) . Under `SIGHASH_SINGLE|ANYONECANPAY`, the signature commits only to input 0 and the output with the same index (output 0); all other inputs/outputs — including the OP_RETURN at output index 2 — are unconstrained.

`update_finalized_payouts` identifies the payout tx for a withdrawal purely by which transaction is recorded as spending the withdrawal UTXO on-chain (`get_payout_txs_for_withdrawal_utxos` joins `withdrawals` to `bitcoin_syncer_spent_utxos` by outpoint, with no reference to which operator broadcast it) [3](#0-2) . It then extracts the operator attribution solely from that mined tx's first OP_RETURN output via `get_first_op_return_output` and `parse_op_return_data`, falling back to `None` on any parse failure [4](#0-3) , and both helpers perform no cryptographic check tying the OP_RETURN to input 0's signer [5](#0-4) [6](#0-5) .

Exploit flow: an unprivileged attacker observes the honest operator's broadcast (but unconfirmed) `payout_tx` in the mempool/relay network. They copy input 0 (same outpoint, same witness — valid since it's the only thing the sighash commits to) and output 0 verbatim, then construct a new transaction with a different (or garbage) OP_RETURN at output 2 and a higher fee, and broadcast/mine it first. `bitcoin_syncer` records this attacker tx as the UTXO's spender; `update_finalized_payouts` parses the attacker's OP_RETURN and stores `payout_payer_operator_xonly_pk = None` or an unrelated key for that withdrawal index. No existing guard (`SECP.verify_schnorr` only checks input 0's user signature; `is_deposit_valid`, `is_profitable`, DB uniqueness constraints) validates that the recorded operator xonly_pk corresponds to whoever actually funded the payout, because that funding relationship was never cryptographically bound in the first place.

Downstream, `Operator::validate_payer_is_operator` requires `payer_xonly_pk == self.signer.xonly_public_key` before allowing any reimbursement transactions to be produced, and errors out otherwise [7](#0-6) . Since the DB now shows the wrong (or no) payer, the honest fronting operator can never pass this check for that deposit, and `get_reimbursement_txs`/automation never proceeds [8](#0-7) .

### Impact Explanation
The honest operator has already fronted real BTC to the user (spent from their own wallet as part of fee-funding the payout via `fund_raw_transaction`), but the on-chain OP_RETURN attribution — the only signal the verifier network uses to credit that funding — is fully attacker-controlled and unauthenticated. This permanently prevents the honest operator from reaching `Reimburse`, freezing their reimbursement (Critical: "an honest operator permanently unable to be reimbursed"). The attack costs the attacker only a relay/mempool race and a bumped fee; no collateral, deposit, or verifier privilege is required, and it is repeatable against any withdrawal/operator pair whenever the attacker can observe an unconfirmed payout tx before it confirms.

### Likelihood Explanation
Preconditions are minimal and match normal operation: an honest operator fronts a payout using `SinglePlusAnyoneCanPay`-signed input 0 (mandatory by protocol design, verified in `Operator::withdraw`), and the payout tx sits unconfirmed in the mempool momentarily (standard Bitcoin propagation delay). The attacker needs only mempool visibility (public), the ability to construct and fee-bump a transaction (funds for fees), and no aggregator/verifier/operator privilege — squarely within the defined unprivileged attacker capabilities (broadcast transactions, choose scripts/OP_RETURNs). This is reproducible on regtest with no reliance on majority hashrate, TLS interception, or key compromise.

### Recommendation
Bind the operator attribution cryptographically to the same signature that authorizes the withdrawal spend, e.g. by having the operator co-sign or commit to the OP_RETURN output as part of the input 0 witness (or require full `SIGHASH_ALL`/`SIGHASH_SINGLE` coverage of the OP_RETURN output rather than leaving it uncommitted), or by having the operator record intent (deposit_id → operator_xonly_pk) in the database/aggregator before broadcasting, and cross-validating that against the mined tx rather than trusting the mined OP_RETURN alone.

### Proof of Concept
```rust
// core/src/test/malleated_payout_attribution.rs (new test)
// 1. Run a real deposit + Citrea withdraw via existing e2e helpers to obtain:
//    - withdrawal_utxo (owned by user, ANYONECANPAY|SINGLE signature `sig`)
//    - honest operator's payout_tx (built via create_payout_txhandler),
//      broadcast but NOT yet mined.
// 2. Craft `attacker_tx`:
//    - input[0] = withdrawal_utxo.outpoint, witness copied verbatim from payout_tx.input[0].witness
//    - output[0] = identical TxOut to payout_tx.output[0] (payout to user)
//    - output[N] = OP_RETURN with a *different* XOnlyPublicKey (or garbage bytes)
//    - add attacker's own funding input/change output for fees, at a higher feerate
// 3. Broadcast attacker_tx and mine it (regtest), ensuring it lands in a block
//    before payout_tx is confirmed (attacker_tx will conflict/replace payout_tx
//    since they share input 0).
// 4. Let bitcoin_syncer index the block; call Verifier::update_finalized_payouts.
// 5. Assertions:
//    let (payer_pk, _, _) = db.get_payer_xonly_pk_blockhash_and_kickoff_txid_from_deposit_id(..., deposit_id).await.unwrap();
//    assert_ne!(payer_pk, Some(honest_operator_xonly_pk)); // attribution broken
//    let result = operator.get_reimbursement_txs(deposit_outpoint).await;
//    assert!(result.is_err()); // "Payer is not own operator" / "Payer info not found"
//    // demonstrates honest operator can never be reimbursed for the funds it fronted
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

**File:** core/src/operator.rs (L1710-1729)
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

**File:** core/src/operator.rs (L2116-2119)
```rust
        // validate payer is operator and get payer xonly pk, payout blockhash and kickoff txid
        let (payout_blockhash, kickoff_txid) = self
            .validate_payer_is_operator(Some(&mut dbtx), deposit_id)
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

**File:** circuits-lib/src/bridge_circuit/mod.rs (L608-617)
```rust
/// Parses the OP_RETURN data from a Bitcoin script. It retrieves the first data push after an OP_RETURN.
pub fn parse_op_return_data(script: &Script) -> Option<&[u8]> {
    let mut instructions = script.instructions();
    if let Some(Ok(Instruction::Op(opcodes::all::OP_RETURN))) = instructions.next() {
        if let Some(Ok(Instruction::PushBytes(data))) = instructions.next() {
            return Some(data.as_bytes());
        }
    }
    None
}
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

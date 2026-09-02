### Title
Payout attribution (`OP_RETURN` operator xonly pubkey) is not covered by the user's `SIGHASH_SINGLE|ANYONECANPAY` withdrawal signature, allowing unauthorised misattribution of who fronted a peg-out - (File: `core/src/builder/transaction/operator_reimburse.rs`)

### Summary
Analogous to the Nouns Builder bug (where `_moveDelegateVotes` moved raw token balance instead of the delegated destination, decoupling "who is credited with votes" from "who actually holds the tokens"), Clementine's payout transaction decouples "who is credited as the operator that fronted the withdrawal" from "who actually signed for the specific output." The `operator_xonly_pk` written into the `payout_tx`'s `OP_RETURN` output is not bound by the user's withdrawal signature, because that signature uses `SIGHASH_SINGLE | SIGHASH_ANYONECANPAY`.

### Finding Description
`create_payout_txhandler` builds a payout tx with: input 0 = user's withdrawal UTXO (spent via key-path with the user's pre-signed Taproot signature), output 0 = user payout, output 1 = anchor, output 2 = `OP_RETURN` push of `operator_xonly_pk` (the party credited for fronting the withdrawal, used later for reimbursement): [1](#0-0) 

The user's signature is explicitly required to use `SinglePlusAnyoneCanPay`: [2](#0-1) 

`SIGHASH_SINGLE` only commits to the output at the same index as the signed input (index 0 — the user payout output); `ANYONECANPAY` means only input 0 is committed. Neither the anchor output (index 1) nor, critically, the `OP_RETURN` output (index 2) carrying `operator_xonly_pk` is covered by this signature. Any party can therefore take the same signed input+signature and reconstruct a payout transaction that pays the user identically but sets an arbitrary `operator_xonly_pk` in the `OP_RETURN`, while adding their own funding input(s) via RBF (as legitimately done in `withdraw()`, which calls `fund_raw_transaction` and broadcasts).

Downstream, this `OP_RETURN` value is trusted as the sole source of truth for "who fronted the withdrawal and is owed reimbursement": the verifier scans confirmed blocks, parses the `OP_RETURN` xonly pubkey, and stores it as `payout_payer_operator_xonly_pk` with no further authentication: [3](#0-2) [4](#0-3) 

That stored value is later used (a) by the paying operator's own `PayoutCheckerTask`/`validate_payer_is_operator` to gate self-reimbursement, and (b) by every verifier's `is_kickoff_malicious` to decide whether a kickoff's claimed operator matches the payout's registered payer, which decides whether a challenge is warranted: [5](#0-4) [6](#0-5) 

Because the `OP_RETURN` content is unauthenticated by the user's signature, any unprivileged party who observes the withdrawal signature (e.g., broadcast by the aggregator to all operators per `withdraw()` fan-out in `core/src/rpc/aggregator.rs:1870-1886`, or visible in the mempool before confirmation) can:
1. Build a competing payout transaction with the same signed input and identical user-facing output, but their own (or an arbitrary/victim) `xonly_pk` in `OP_RETURN`.
2. Fund and broadcast it with a higher fee via RBF, winning the race to confirmation.

Because the attacker must still supply the actual BTC to the user's output (via `fund_raw_transaction`'s added inputs) to make the payout valid, this is not free money creation. However, it lets an unprivileged actor unilaterally decide *whose* xonly pubkey gets bound to `payout_payer_operator_xonly_pk` — including a pubkey belonging to a real, unwitting operator who never intended to front this withdrawal. Since `is_kickoff_malicious` treats `operator_xonly_pk != kickoff_data.operator_xonly_pk` as fraud evidence, and `validate_payer_is_operator` gates an operator's own reimbursement flow strictly on this DB field being equal to its own signer key, an attacker can:
- Register an honest, uninvolved operator as the "payer" for a withdrawal it never funded (framing), poisoning that operator's `get_first_unhandled_payout_by_operator_xonly_pk`/`PayoutCheckerTask` state and blocking or confusing its own legitimate kickoff/reimbursement bookkeeping for that deposit, since the framed operator now believes it must produce a kickoff for a payout it did not actually make (and cannot, since it did not sign/fund the payout, but its bookkeeping now expects one).
- More generally, this breaks the binding "operator credited in `OP_RETURN` == party that actually funded the payout output," which the whole reimbursement/challenge machinery assumes holds by construction.

### Impact Explanation
This breaks the equality the protocol relies on: `payout_payer_operator_xonly_pk (DB, derived from OP_RETURN) == the entity whose collateral/kickoff should be reimbursed`. Since neither the withdrawing user's signature nor any N-of-N/verifier signature authenticates the `OP_RETURN` content, an unprivileged party (not necessarily even a registered operator) can dictate this attribution. This can cause an honest operator to be permanently confused/frozen out of correctly tracking its own reimbursement obligations for a deposit it never actually paid out (`validate_payer_is_operator` / `PayoutCheckerTask` state corruption), and can affect verifier fraud-detection logic (`is_kickoff_malicious`) that keys off this same unauthenticated field. This matches the "honest operator permanently unable to be reimbursed" / misattributed-reimbursement class of Critical impact in scope, though the primary requirement (funding the output) limits it to a griefing/framing vector rather than a value-extraction one.

### Likelihood Explanation
Requires only: (1) knowledge of a pending/confirmed user withdrawal signature (routinely broadcast to all operators by the aggregator, or visible pre-confirmation in mempool), and (2) the ability to construct and broadcast a standard Bitcoin transaction with one's own funding, which is available to any unprivileged actor with capital, no special role (verifier/operator/aggregator) required. It does not require compromising any key or majority stake.

### Recommendation
Bind the `OP_RETURN` operator-attribution output to the same signature that authorizes the withdrawal, e.g., by having the user's signature use `SIGHASH_ALL` (or `SIGHASH_ALL|ANYONECANPAY`, or `SIGHASH_SINGLE` applied to a canonical output ordering that includes the `OP_RETURN`) so that the outputs, including the attribution `OP_RETURN`, cannot be altered without invalidating the signature. Alternatively, decouple attribution from an unauthenticated on-chain marker entirely: require the funding operator to additionally provide a signature (e.g., over the whole finalized `payout_tx`) that verifiers/operators check before trusting `payout_payer_operator_xonly_pk`, rather than trusting whatever appears in the `OP_RETURN` of the confirmed transaction.

### Proof of Concept
1. Aggregator's `withdraw` RPC broadcasts `WithdrawParamsWithSig` (containing the user's `SinglePlusAnyoneCanPay` signature) to some or all operators (`core/src/rpc/aggregator.rs:1870-1886`).
2. Attacker (any party, not necessarily a registered operator) observes this signature/params either via this fan-out or via the unconfirmed payout tx in the mempool.
3. Attacker constructs their own payout transaction using `create_payout_txhandler`-equivalent logic: same input (user's withdrawal UTXO) + same signature + identical output 0 (user payout), but sets `operator_xonly_pk` in `OP_RETURN` to an arbitrary value (e.g., a victim operator's real xonly pubkey) — this is valid because `SIGHASH_SINGLE|ANYONECANPAY` does not commit to output 2.
4. Attacker funds the transaction with their own BTC inputs and broadcasts with a higher fee, causing it to confirm before the legitimate operator's version.
5. Verifier's `update_finalized_payouts` parses the `OP_RETURN` and records the victim operator's pubkey as `payout_payer_operator_xonly_pk` in the `withdrawals` table (`core/src/verifier.rs:2312-2342`, `core/src/database/verifier.rs:198-251`).
6. The victim operator's `PayoutCheckerTask`/`validate_payer_is_operator` (`core/src/operator.rs:1686-1740`) now believes it must handle a kickoff/reimbursement for a payout it never made, corrupting its state; verifiers' `is_kickoff_malicious` (`core/src/verifier.rs:1857-1915`) also uses this poisoned attribution when evaluating any real kickoff from that operator for this deposit.

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

**File:** core/src/database/verifier.rs (L253-280)
```rust
    pub async fn get_payout_info_from_move_txid(
        &self,
        tx: Option<DatabaseTransaction<'_>>,
        move_to_vault_txid: Txid,
    ) -> Result<Option<(Option<XOnlyPublicKey>, BlockHash, Txid, i32)>, BridgeError> {
        let query = sqlx::query_as::<_, (Option<XOnlyPublicKeyDB>, BlockHashDB, TxidDB, i32)>(
            "SELECT w.payout_payer_operator_xonly_pk, w.payout_tx_blockhash, w.payout_txid, w.idx
             FROM withdrawals w
             WHERE w.move_to_vault_txid = $1
               AND w.payout_txid IS NOT NULL
               AND w.payout_tx_blockhash IS NOT NULL",
        )
        .bind(TxidDB(move_to_vault_txid));

        let result: Option<(Option<XOnlyPublicKeyDB>, BlockHashDB, TxidDB, i32)> =
            execute_query_with_tx!(self.connection, tx, query, fetch_optional)?;

        result
            .map(|(operator_xonly_pk, block_hash, txid, deposit_idx)| {
                Ok((
                    operator_xonly_pk.map(|pk| pk.0),
                    block_hash.0,
                    txid.0,
                    deposit_idx,
                ))
            })
            .transpose()
    }
```

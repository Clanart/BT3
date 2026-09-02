### Title
Unauthenticated OP_RETURN operator-attribution in payout transactions lets a withdrawing user credit an arbitrary operator for a payout they never funded - ([File: core/src/verifier.rs])

### Summary
`Verifier::update_finalized_payouts` attributes a confirmed payout transaction to whichever `XOnlyPublicKey` is embedded in the transaction's first OP_RETURN output, with no check that the named operator actually supplied the transaction's funding inputs or signature. Because the payout input is signed by the withdrawing user with `SIGHASH_SINGLE|SIGHASH_ANYONECANPAY` (which commits only to input 0 and output 0, not to the OP_RETURN at output 2 nor to any additional funding inputs), the withdrawing user themselves can self-front their own withdrawal and simply mislabel the OP_RETURN with an arbitrary honest operator's public key.

### Finding Description
The binding the protocol relies on is:
`withdrawals.payout_payer_operator_xonly_pk(i) == operator_that_actually_funded_the_payout_inputs(i)`

Trace of the code path:
- `Operator::withdraw` (core/src/operator.rs:560-637) builds the payout tx via `create_payout_txhandler` and verifies the user's signature with `SECP.verify_schnorr` against `sighash = payout_txhandler.calculate_sighash_txin(0, in_signature.sighash_type)`, explicitly expecting `SinglePlusAnyoneCanPay`: [1](#0-0) 
- `create_payout_txhandler` places the OP_RETURN (with the operator's xonly pk) as output index 2, after the user-payout output (index 0) and the anchor (index 1): [2](#0-1) 
- `SIGHASH_SINGLE` only commits to the single input being signed and the output at the *same index* (index 0); combined with `ANYONECANPAY` it does not commit to any other input. Consequently the OP_RETURN content (index 2) and the identity/ownership of any extra funding inputs are **not covered by the user's signature**.
- `Verifier::update_finalized_payouts` (core/src/verifier.rs:2283-2353) reads whatever confirmed transaction spends the registered withdrawal UTXO, extracts the OP_RETURN via `get_first_op_return_output`/`parse_op_return_data`, and unconditionally writes that key as `payout_payer_operator_xonly_pk` via `update_payout_txs_and_payer_operator_xonly_pk`: [3](#0-2) 
- Downstream, `PayoutCheckerTask::run_once` (each operator's own automation) fetches "unhandled payouts" filtered by `self.operator.signer.xonly_public_key` and, upon finding one, automatically calls `handle_finalized_payout` and eventually triggers the kickoff/reimburse flow using that operator's own collateral/round setup: [4](#0-3) 
- `Verifier::is_kickoff_malicious` only checks that `operator_xonly_pk` (taken from the same untrusted OP_RETURN) matches `kickoff_data.operator_xonly_pk`, and that the committed blockhash matches - it never validates who funded the payout's inputs: [5](#0-4) 

Exploit flow: the attacker registers a Citrea withdrawal for their own UTXO, signs the payout input themselves with `SinglePlusAnyoneCanPay` (output 0 = their own destination, as normal), adds their own extra funding inputs to cover fees/output value, embeds an arbitrary honest operator B's `XOnlyPublicKey` in the OP_RETURN, and broadcasts the resulting valid Bitcoin transaction directly - completely bypassing every operator's `withdraw` RPC and any verifier/aggregator gRPC call. No signature check anywhere requires the OP_RETURN operator key to correspond to the party who funded the extra inputs.

### Impact Explanation
Because operator B's own `PayoutCheckerTask` blindly trusts the DB attribution written by `update_finalized_payouts`, B's automation will treat this attacker-self-funded withdrawal as "my unhandled payout" and drive the kickoff → reimburse flow, resulting in `bridge_amount` BTC leaving the shared move-to-vault UTXO to reimburse B for a payout B never funded. This is BTC leaving a move-to-vault UTXO without a matching operator-fronted withdrawal (the withdrawal was actually funded by the attacker's own wallet, not by B), and it is an operator credited/reimbursed for a payout it never funded - both explicitly listed Critical impacts. The attack is repeatable per withdrawal registered by any user and can target any operator whose public xonly key is known (operator keys are public protocol data).

### Likelihood Explanation
Preconditions: an operator must run with the `automation` feature enabled so `PayoutCheckerTask` fires without manual review; the attacker needs only to be a normal Citrea withdrawer able to sign their own withdrawal input, costing only their normal withdrawal amount plus Bitcoin fees - no verifier, operator, or aggregator credentials are required. Feasibility is high: no additional cryptographic material is needed beyond what a legitimate withdrawing user already possesses, and the vulnerable code path (`update_finalized_payouts` trusting OP_RETURN unconditionally) is exercised on every finalized block.

### Recommendation
Do not treat the OP_RETURN operator key as authoritative attribution. Require that the named operator co-signs the payout transaction (e.g., via an operator signature covering the OP_RETURN output, or by requiring the operator to be one of the transaction's input signers under a sighash that commits to the OP_RETURN and to all inputs), so that `payout_payer_operator_xonly_pk` can only be set to an operator that cryptographically proves it supplied the funding inputs. Alternatively, cross-check that the additional funding inputs of the payout transaction belong to/are controlled by the operator named in the OP_RETURN before crediting them in `update_finalized_payouts`.

### Proof of Concept
```
cargo test -p clementine-core --features automation -- test_forged_op_return_attribution
```
Test plan (regtest):
1. Set up regtest with operators A and B and a funded deposit/move-to-vault.
2. As an unprivileged user/attacker, register a withdrawal via the Citrea client mock (`withdraw`) for a UTXO the attacker controls.
3. Construct the payout tx exactly as `create_payout_txhandler` does, but sign input 0 directly with the attacker's key using `SinglePlusAnyoneCanPay`, add an attacker-owned funding input, and set the OP_RETURN to operator B's `xonly_public_key` instead of the attacker's/any operator's.
4. Broadcast the tx directly via the regtest RPC (bypassing `Operator::withdraw`), mine it into a block, and drive `Verifier::handle_finalized_block`.
5. Assert `db.get_payout_info_from_move_txid(...)` returns `operator_xonly_pk == Some(B.xonly_public_key)`.
6. Assert (equality before/after the binding): before broadcast, `operator_that_funded_payout(i) == attacker`, `operator_credited(i) == None`; after, `operator_credited(i) == B` while `operator_that_funded_payout(i)` is still `attacker` - demonstrating the two sides of the binding no longer match.

### Citations

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

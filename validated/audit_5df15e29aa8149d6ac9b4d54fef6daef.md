### Title
Payout attribution to an operator is derived only from an unauthenticated OP_RETURN, letting anyone reroute reimbursement to an operator that never paid the withdrawal - (File: core/src/verifier.rs)

### Summary
`Verifier::update_finalized_payouts` attributes a withdrawal's payout to whichever operator's xonly pubkey happens to appear in the first OP_RETURN output of *any* transaction that spends the registered `withdrawal_utxo`, without checking who signed that input or what sighash type/amount/script it committed to. Because the withdrawal UTXO's private key is held by the withdrawer (an unprivileged actor), the withdrawer can spend it themselves with a self-chosen OP_RETURN naming an arbitrary registered operator, and that operator's own `PayoutCheckerTask` will automatically begin the kickoff/reimbursement flow for a payout it never made. `bridge_circuit` (`circuits-lib/src/bridge_circuit/mod.rs`) has no equivalent check either — it only verifies that `payout_spv.transaction.input[payout_input_index].previous_output` matches the registered `(txid, vout)`, never that the spend used `SinglePlusAnyoneCanPay` or that the resulting output value/script matches the amount the withdrawer signed off on.

### Finding Description
The intended binding is: `operator_xonly_pk recorded as payer for withdrawal W` == `operator who produced the SinglePlusAnyoneCanPay-signed output paying the exact amount/script the withdrawer committed to in WithdrawParams` (as enforced off-chain in `core/src/rpc/parser/operator.rs::parse_withdrawal_sig_params` and checked in `Operator::withdraw`, [1](#0-0) , [2](#0-1) ).

On-chain, this binding is never enforced. `Verifier::update_finalized_payouts` scans for any transaction spending a registered `withdrawal_utxo`, and unconditionally attributes it to whichever xonly pubkey is embedded in that transaction's OP_RETURN, with no signature or sighash check at all: [3](#0-2) 

That value is persisted and later trusted by `Operator::validate_payer_is_operator`, which merely compares `payer_xonly_pk == self.signer.xonly_public_key` before proceeding to reimbursement: [4](#0-3) 

The operator's own automation (`PayoutCheckerTask::run_once`) polls for "unhandled payouts" keyed purely by this attacker-influenced `payout_payer_operator_xonly_pk` column and immediately drives `handle_finalized_payout`/kickoff, with no further check that the operator itself broadcast the payout: [5](#0-4) 

Because the withdrawal UTXO's private key is controlled by the withdrawer (unprivileged), an attacker requesting a withdrawal can, instead of following `Operator::withdraw`'s `SinglePlusAnyoneCanPay` flow, build and broadcast their own key-path-spend transaction of that UTXO with any signature scheme, any (or no) real payment to themselves, and an OP_RETURN naming any operator registered for that deposit. `update_finalized_payouts` will mark that operator as the payer, and that operator's own automated software will start the kickoff/reimbursement process for a payout it never funded.

`bridge_circuit` provides no backstop for this in the challenge path either: it only asserts that the spent previous_output equals the storage-proof-derived `(txid, vout)`, never that the spending witness used `SinglePlusAnyoneCanPay` or that any output value/script matches what the withdrawer actually signed for: [6](#0-5) [7](#0-6) 

### Impact Explanation
An operator can be automatically driven into starting (and, absent a challenger who notices, completing) the reimbursement flow for a withdrawal it never funded, resulting in a move-to-vault UTXO's funds being paid out to an operator with no matching fronted payout to the withdrawer — Critical CUSTODY ("an operator reimbursed for a payout it never funded" / "BTC leaving a move-to-vault UTXO without a matching fronted withdrawal"). This is repeatable for every withdrawal where the attacker (the withdrawer) controls the dust UTXO key, across any operator registered for that deposit, since the OP_RETURN content is entirely attacker-chosen and unauthenticated both in `update_finalized_payouts` and in `bridge_circuit`.

### Likelihood Explanation
No special privileges are needed beyond being a legitimate withdrawer (deposit + call `withdraw` on the Citrea Bridge contract), which is explicitly within the unprivileged attacker model. The attacker only needs to know a target operator's public xonly pubkey (public information) and pay normal Bitcoin fees to broadcast a self-crafted spend of their own dust UTXO instead of using the SinglePlusAnyoneCanPay-gated `Operator::withdraw`/aggregator `withdraw` RPCs. No majority hashrate, key compromise, or operator collusion is required.

### Recommendation
`update_finalized_payouts` (and ultimately `bridge_circuit`) must not trust the OP_RETURN alone to attribute a payout to an operator. The payout transaction's spending witness for the withdrawal input must be verified to be a valid `SinglePlusAnyoneCanPay` (or equivalent binding) signature by the recorded operator's key, or the circuit must independently verify that a specific output of the payout transaction pays the exact amount/script the withdrawer committed to (which requires storing/committing that amount/script on L2, not just the outpoint). At minimum, `update_finalized_payouts` should validate the input signature/sighash before persisting `payout_payer_operator_xonly_pk`, and `bridge_circuit` should assert the sighash type and that the input's committed amount/script match the withdrawal's committed intent.

### Proof of Concept
```rust
// core/src/test/... (new test)
// 1. Run a normal deposit + withdrawal setup for deposit D with withdrawal dust UTXO U,
//    controlled by a test "attacker" keypair (simulating the withdrawer's own key).
// 2. Do NOT call operator.withdraw / aggregator.withdraw at all.
// 3. Attacker builds a raw transaction spending U via key-path spend with an arbitrary
//    signature (not SinglePlusAnyoneCanPay — e.g. Default/All), paying an output back to
//    themselves (no funds to any bridge participant), and includes an OP_RETURN with the
//    xonly pubkey of operator O (one of the operators registered for deposit D).
// 4. Broadcast and mine this transaction.
// 5. Poll verifier DB: assert `get_payout_info_from_move_txid` for D now returns
//    payout_payer_operator_xonly_pk == O's xonly pk (binding falsely "confirmed").
// 6. Poll operator O's PayoutCheckerTask / handle_finalized_payout: assert it begins
//    the kickoff/reimbursement flow for deposit D despite operator O never having built,
//    signed, or broadcast any payout.
// Assertion on both sides of the broken binding:
//   left  = payout_payer_operator_xonly_pk recorded by update_finalized_payouts
//   right = xonly pk of an operator that actually produced a SinglePlusAnyoneCanPay
//           signature over the correct amount/script (there is none)
//   assert_eq!(left, Some(O_pubkey)) while right == None  -> binding violated.
```

### Citations

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

**File:** core/src/operator.rs (L1703-1719)
```rust
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
```

**File:** core/src/verifier.rs (L2310-2335)
```rust
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

**File:** circuits-lib/src/bridge_circuit/mod.rs (L188-204)
```rust
    let payout_input_index: usize = input.payout_input_index as usize;

    assert_eq!(
        user_wd_txid,
        input.payout_spv.transaction.input[payout_input_index]
            .previous_output
            .txid,
        "Invalid withdrawal transaction ID"
    );

    assert_eq!(
        vout,
        input.payout_spv.transaction.input[payout_input_index]
            .previous_output
            .vout,
        "Invalid withdrawal transaction output index"
    );
```

**File:** circuits-lib/src/bridge_circuit/structs.rs (L348-360)
```rust
#[derive(Clone, Debug, BorshDeserialize, BorshSerialize)]
pub struct BridgeCircuitInput {
    pub kickoff_tx: CircuitTransaction,
    // Add all watchtower pubkeys as global input as Vec<[u8; 32]> Which should be shorter than or equal to 160 elements
    pub all_tweaked_watchtower_pubkeys: Vec<[u8; 32]>, // Per watchtower [u8; 34] or OP_PUSHNUM_1 OP_PUSHBYTES_32 <TweakedXOnlyPublicKey> which is [u8; 32]
    pub watchtower_inputs: Vec<WatchtowerInput>,
    pub hcp: BlockHeaderCircuitOutput,
    pub payout_spv: SPV,
    pub payout_input_index: u32,
    pub lcp: LightClientProof,
    pub sp: StorageProof,
    pub watchtower_challenge_connector_start_idx: u32,
}
```

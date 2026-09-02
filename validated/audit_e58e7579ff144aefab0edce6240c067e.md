Based on the evidence gathered, the vulnerability is confirmed. The `PayoutCheckerTask::run_once` (`core/src/task/payout_checker.rs:39-111`) automatically detects *any* on-chain confirmed transaction whose OP_RETURN embeds the operator's own `xonly_pk` (populated purely from parsing on-chain bytes via `update_finalized_payouts`, `core/src/verifier.rs:2283-2353` and the equivalent operator-side sync) and automatically triggers `handle_finalized_payout` → kickoff → reimbursement, with **no check that the operator itself broadcast or funded that transaction**. [1](#0-0) 

The user's withdrawal-authorizing signature uses `TapSighashType::SinglePlusAnyoneCanPay`, which per BIP-341 semantics only commits to the input being spent and the output at the *same index* (index 0, the user payout output) — it does **not** commit to the OP_RETURN output (index 2) that carries `operator_xonly_pk`. [2](#0-1) [3](#0-2) 

Because of this, anyone holding a valid `SinglePlusAnyoneCanPay` signature over a registered withdrawal UTXO — which an attacker trivially possesses for a withdrawal UTXO **they themselves registered and control** — can independently construct and broadcast (bypassing the operator's own RPC/wallet flow entirely) a payout transaction reusing that signature, but attaching **any OP_RETURN they choose**, including a real operator X's public key that X never signed or acknowledged funding. `PayoutCheckerTask` on operator X's node will then autonomously detect this as "its own" fronted payout and initiate the full kickoff/reimbursement chain, crediting X for a peg-out X never funded. [4](#0-3) 

This is also consistent with `bridge_circuit`'s own logic: it never checks a signature binding `operator_xonlypk` (extracted via `parse_op_return_data`) to whoever signed the payout's inputs — it only checks that the OP_RETURN commits *some* 32 bytes, and this data alone (matched against `kickoff_data.operator_xonly_pk` set at round/kickoff creation) is what verifiers use to accept the kickoff as legitimate. [5](#0-4) 

**Caveat on impact realization**: While the *binding break* (OP_RETURN operator_xonlypk not cryptographically tied to who funded the payout) is real and exploitable purely by an unprivileged attacker up to the point of getting an arbitrary transaction confirmed on-chain, turning this into actual value transfer (**"an operator reimbursed for a payout it never funded"**) requires operator X's own automation (`PayoutCheckerTask`) or manual action to complete the kickoff and reimbursement — this happens automatically for any honestly-run operator (no operator complicity/malice needed), which keeps the attacker fully unprivileged per the threat model, since the fund-credit occurs via the operator's normal automated logic reacting to attacker-shaped chain data, not via any privileged action by the attacker themselves.

### Title
Unauthenticated OP_RETURN operator_xonlypk lets an attacker frame any operator into an unfunded, auto-triggered reimbursement - ([File: circuits-lib/src/bridge_circuit/mod.rs, core/src/builder/transaction/operator_reimburse.rs, core/src/task/payout_checker.rs])

### Summary
The payout transaction's OP_RETURN output carrying `operator_xonly_pk` is not covered by the withdrawer's `SinglePlusAnyoneCanPay` signature, so an attacker who controls (registers) a withdrawal UTXO can broadcast their own payout transaction reusing that signature while attaching an arbitrary operator's real public key in the OP_RETURN. Because `bridge_circuit` and the verifier/operator's chain-sync logic treat this unsigned OP_RETURN field as the authoritative "who funded this payout" claim, the targeted operator's automation (`PayoutCheckerTask`) will autonomously detect and claim reimbursement for a payout it never made.

### Finding Description
Binding claimed (and broken): `operator_xonlypk` embedded in the payout tx's OP_RETURN (used inside `deposit_constant` in `circuits-lib/src/bridge_circuit/mod.rs::bridge_circuit`, lines 206-229) == the xonly public key of the party that actually signed/funded the payout transaction's inputs.

The payout tx is built via `create_payout_txhandler` (`core/src/builder/transaction/operator_reimburse.rs:407-436`), which places `operator_xonly_pk` in an unsigned OP_RETURN output (index 2) alongside the user's payout output (index 0) and a signed input (index 0). The only signature present, `in_signature`, is required and verified to use `TapSighashType::SinglePlusAnyoneCanPay` (`core/src/rpc/parser/operator.rs:174-187`, `core/src/operator.rs:630-637`). Per `calculate_pubkey_spend_sighash` (`core/src/builder/transaction/txhandler.rs:210-233`), `SinglePlusAnyoneCanPay` only commits to the single input being spent and the output at the matching index (index 0) — it never covers the OP_RETURN output. `parse_op_return_data` (`circuits-lib/src/bridge_circuit/mod.rs:608-617`) simply reads whatever 32 bytes are pushed there, with zero cryptographic check tying it to the actual funder.

Attacker flow: the attacker registers a withdrawal on Citrea (`withdraw` on the Citrea Bridge contract) for a UTXO they control, and produces their own valid `SinglePlusAnyoneCanPay` signature spending it (paying themselves, satisfying `output_txout` at index 0). Instead of submitting this via the operator/aggregator `withdraw` RPC flow (which would force the OP_RETURN to be built with the real serving operator's key, `core/src/operator.rs:620-626`), the attacker constructs and directly broadcasts their own transaction on Bitcoin, reusing the input+signature+output[0], but appending an OP_RETURN containing operator X's real, public xonly key (public bridge parameter). Because SIGHASH_SINGLE|ANYONECANPAY does not commit the OP_RETURN, this reused signature remains valid for the attacker's alternate transaction.

Once confirmed, chain-sync logic (`update_finalized_payouts`, `core/src/verifier.rs:2283-2353`, mirrored on the operator side) parses the OP_RETURN and records `operator_xonly_pk = X` as the "payer" for that withdrawal — purely from unauthenticated on-chain bytes. Operator X's `PayoutCheckerTask::run_once` (`core/src/task/payout_checker.rs:39-111`) then automatically finds this "unhandled payout" tagged with X's own key and calls `handle_finalized_payout`, which allocates a kickoff connector and proceeds through the full kickoff/reimbursement transaction graph (`core/src/operator.rs:839-` onward), ultimately reimbursing X from the round/reimbursement UTXO chain — for a payout X never funded or acknowledged.

The `is_kickoff_malicious` check (`core/src/verifier.rs:1859-1915`) does not catch this: it only verifies `operator_xonly_pk == kickoff_data.operator_xonly_pk` (i.e., that the OP_RETURN key matches the kickoff's declared operator) and a committed blockhash — both of which trivially match here since the attacker deliberately embedded X's real key.

### Impact Explanation
Operator X is credited/reimbursed (via the presigned reimburse transaction spending the round/move-to-vault UTXOs) for a peg-out payment it never made — this is bridge collateral/value moving to the wrong party ("an operator reimbursed for a payout it never funded", Critical). The attack is repeatable across any withdrawal the attacker registers and any operator whose public key is known (all operator keys are public protocol parameters), and does not require compromising any operator, verifier, or aggregator credential.

### Likelihood Explanation
The attacker needs only: (1) to register a withdrawal on Citrea for a UTXO they control, (2) produce one valid Schnorr signature with `SinglePlusAnyoneCanPay`, and (3) broadcast a standard Bitcoin transaction with attacker-chosen fees — all squarely within the unprivileged attacker capability set. It requires no operator/verifier compromise and works against default, honestly-run operator automation (`PayoutCheckerTask`), which is enabled by default whenever `automation` feature is on. The only "cost" is the withdrawal payout amount itself, which the attacker directs to their own address, so the attack is close to cost-free for the attacker while draining bridge collateral to reimburse operator X.

### Recommendation
Bind the `operator_xonly_pk` OP_RETURN commitment cryptographically to the payout's funding: either require the withdrawer's signature to cover the OP_RETURN output (e.g., by using `SIGHASH_ALL` instead of `SinglePlusAnyoneCanPay`, or by pre-committing to the operator's key as part of the signed message the user authorizes off-chain per-operator), or require an additional operator signature (covering the whole transaction, including the OP_RETURN) that the circuit verifies, so that reimbursement can only be claimed by whoever cryptographically co-signed the specific payout transaction.

### Proof of Concept
`cargo test` plan (in `core/src/builder/transaction/operator_reimburse.rs` or a new integration test alongside `core/src/test/deposit_and_withdraw_e2e.rs`):
1. Set up a deposit and a registered withdrawal UTXO owned by a test "attacker" keypair (reusing `generate_withdrawal_transaction_and_signature` helper style from `core/src/test/common/setup_utils.rs:499-543`), producing `in_signature` with `TapSighashType::SinglePlusAnyoneCanPay`.
2. Build two *different* payout transactions both spending the same withdrawal input with the same `in_signature`, output[0] identical (paying the attacker's own address): (a) OP_RETURN with operator X's real xonly_pk, (b) OP_RETURN with a random unrelated 32 bytes.
3. Assert both transactions pass `SECP.verify_schnorr` against the same signature (proving the OP_RETURN is not covered/bound).
4. Broadcast transaction (a) directly via RPC (not via `operator.withdraw()`), bypassing operator X entirely.
5. Run operator X's `PayoutCheckerTask::run_once` (or the full e2e harness) and assert it detects the payout as unhandled for X's own key, and that `handle_finalized_payout` proceeds to produce a `kickoff_txid`, followed by a successful `Reimburse` transaction crediting X — despite X never broadcasting or signing transaction (a).
6. Assert `is_kickoff_malicious` returns `false` for this kickoff (i.e., existing guards do not catch the forgery), confirming the exploit is undetected by current mitigations.

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

**File:** core/src/builder/transaction/txhandler.rs (L222-229)
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

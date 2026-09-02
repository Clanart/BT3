### Title
`bridge_circuit` never verifies that the withdrawer's registered payout output actually exists in the payout transaction - ([File: circuits-lib/src/bridge_circuit/mod.rs])

### Summary
`bridge_circuit` only checks that `input.payout_spv.transaction.input[payout_input_index]` spends the exact withdrawal outpoint (`txid`/`vout`) registered on Citrea via `verify_storage_proofs`. It never checks that any output of that same transaction pays the registered withdrawal amount/`script_pubkey` to the withdrawer. This breaks the intended binding "spending the withdrawal UTXO == withdrawer received the withdrawal amount," letting a dishonest operator front a dust/negligible payout while still passing all circuit checks and later claiming full `Reimburse`.

### Finding Description
The binding that should hold is: `output value paid to withdrawer's registered script_pubkey in payout_spv.transaction == withdrawal amount registered on Citrea (out_amount/out_script_pubkey from withdraw())`. Tracing the circuit:

- `verify_storage_proofs` only recovers `(user_wd_outpoint, vout, move_txid)` — the withdrawal UTXO's txid/vout and the deposit's move txid — from the Bridge contract storage. It never reads or checks a registered payout destination amount/script against the transaction's outputs. [1](#0-0) [2](#0-1) 

- In `bridge_circuit`, `payout_input_index` is used only to assert that `input[payout_input_index].previous_output.txid`/`vout` match the withdrawal outpoint from the storage proof: [3](#0-2) 

- After this, the circuit only extracts the first `OP_RETURN` output to derive the operator's x-only pubkey for `deposit_constant`, and never inspects any other output of `payout_spv.transaction` (no check that output[0] pays the correct amount to the correct `script_pubkey`): [4](#0-3) 

- There is no cross-check anywhere in the circuit binding "the value moved by spending the withdrawal input" to "the value received by the withdrawer's address." Because Bitcoin transactions do not attribute specific inputs to specific outputs, an attacker (a dishonest operator, who is an in-scope adversary for the bridge/BitVM2 dispute-resolution logic) can construct a multi-input payout transaction where:
  - `input[payout_input_index]` spends the exact registered withdrawal UTXO (satisfying the two `assert_eq!` checks),
  - a second, unrelated input funds the transaction's real value,
  - the outputs pay a dust amount (or nothing) to the withdrawer's registered address, while the bulk of value goes elsewhere (e.g., back to the operator or a third party),
  - a valid `OP_RETURN` with the operator's x-only pubkey is still included to satisfy `deposit_constant`/`get_first_op_return_output`.

- This transaction still satisfies SPV inclusion (`payout_spv.verify`), header-chain PoW checks, light-client proof checks, and storage proof checks — none of which reference the payout output amount/destination. The circuit therefore commits a valid `journal_hash`, and the corresponding BitVM2 Disprove path (`BridgeDisproveScript`/`ClementineDisproveScript`, per `docs/bridge-circuit.md`) has no assertion to catch this, since the guest program itself performs no such check. [5](#0-4) 

- The intended canonical payout transaction built by `create_payout_txhandler` only ever has one input (the withdrawal UTXO) and one payout output, which is why this attribution problem doesn't arise in the honest-operator code path: [6](#0-5) 
However, the circuit accepts *any* `payout_spv.transaction` structure as long as the input-index checks pass — it does not enforce the single-input, single-payout-output shape, nor does it verify the actual payout output.

### Impact Explanation
A malicious/dishonest operator can front a negligible (dust) real payment to the withdrawer while satisfying every check in `bridge_circuit`, then successfully claim full `Reimburse` (`create_reimburse_txhandler`, which pays out the full `bridge_amount` from the move-to-vault UTXO) without having funded the withdrawal: [7](#0-6) 
This is Critical — "an operator reimbursed for a payout it never funded." It is repeatable across every deposit/withdrawal handled by any operator, since the missing check applies to the shared `bridge_circuit` used by all operators/challengers.

### Likelihood Explanation
The attacker (an operator wishing to cheat) needs only:
- A legitimate deposit and a `withdraw()` call registering a withdrawal UTXO on Citrea (standard flow, no special privilege).
- The ability to construct and broadcast an arbitrary Bitcoin transaction spending that UTXO at `payout_input_index`, with additional inputs/outputs of their choosing (any Bitcoin user can do this; the withdrawal UTXO owner authorizes spending via a keypath Schnorr signature with an attacker-chosen sighash flag such as `SIGHASH_ALL`/`ANYONECANPAY`, so they need only the withdrawal-committed input's signature, not extra approvals).
- Sufficient fee for the transaction to confirm.

Cost is minimal (dust output + tx fees); no majority hashrate, key compromise, or TLS interception is required. Feasibility is high given the demonstrated absence of any output-value/script check in the circuit code.

### Recommendation
In `bridge_circuit` (circuits-lib/src/bridge_circuit/mod.rs), after resolving `payout_input_index` and validating the spent outpoint, add an explicit check that one of `input.payout_spv.transaction.output` pays at least the registered withdrawal amount to the registered `script_pubkey` (both of which must be committed/verifiable via the storage proof or an equivalent Citrea-committed value, analogous to how `vout`/`user_wd_outpoint` are already fetched from storage). Alternatively, constrain the accepted `payout_spv.transaction` shape to the canonical structure produced by `create_payout_txhandler` (single withdrawal input, one payout output with checked amount/script, one anchor, one OP_RETURN) and reject any transaction that doesn't match this shape/attribution.

### Proof of Concept
```
// In circuits-lib/src/bridge_circuit/mod.rs test module (or a new test file)
#[test]
fn test_payout_input_index_does_not_bind_output_value() {
    // 1. Build a BridgeCircuitInput as in total_work_and_watchtower_flags_setup()/existing tests,
    //    but construct payout_spv.transaction with 2 inputs and 2+ outputs:
    //    - input[0]: spends withdrawal UTXO (matches storage_proof-derived outpoint)
    //    - input[1]: an unrelated, attacker-controlled funding input (arbitrary prevout)
    //    - output[0]: dust value (e.g. 300 sats) to withdrawer's script_pubkey (paired conceptually with input[0])
    //    - output[1]: large value (e.g. 0.99 * bridge_amount) to an address controlled by the operator/attacker
    //    - output[2]: OP_RETURN with operator xonlypk
    //    payout_input_index = 0.
    //
    // 2. Craft storage_proof (sp) fixtures so verify_storage_proofs returns the outpoint
    //    matching input[0].previous_output (txid/vout) -- reuse existing storage_proof.bin fixture
    //    pattern from storage_proof.rs tests, adapted to this txid/vout.
    //
    // 3. Call bridge_circuit(&mock_guest, work_only_image_id) (using a MockZkvmGuest that
    //    trivially verifies hcp/lcp) and assert that it does NOT panic and DOES commit a journal_hash,
    //    despite output[0] (paired with the withdrawal-spending input) being dust.
    //
    // Binding check (both sides of the equality BEFORE/AFTER):
    // LHS (claimed): amount attributable to input[0] spending withdrawal UTXO == amount withdrawer receives (dust, e.g. 300 sats)
    // RHS (enforced by circuit): NOT CHECKED AT ALL -- circuit only checks input[0].previous_output.{txid,vout}
    // assert!(journal_hash committed successfully) proves the circuit has no assertion enforcing LHS == registered withdrawal amount.
}
```
This demonstrates that `bridge_circuit` commits a valid journal hash for a payout transaction in which the input spending the registered withdrawal UTXO is paired with only a dust output, while the bulk of transferred value is unrelated to the withdrawer, confirming the missing binding.

### Citations

**File:** circuits-lib/src/bridge_circuit/storage_proof.rs (L44-47)
```rust
pub fn verify_storage_proofs(
    storage_proof: &StorageProof,
    state_root: [u8; 32],
) -> (WithdrawalOutpointTxid, u32, MoveTxid) {
```

**File:** circuits-lib/src/bridge_circuit/storage_proof.rs (L118-132)
```rust

    let buf: [u8; 32] = vout_storage_proof.value.to_be_bytes();

    // ENDIANNESS SHOULD BE CHECKED THIS FIELD IS 4 BYTES in the contract
    let vout = u32::from_le_bytes(
        buf[28..32]
            .try_into()
            .expect("Vout value conversion failed"),
    );

    let wd_outpoint = WithdrawalOutpointTxid(utxo_storage_proof.value.to_be_bytes());

    let move_txid = MoveTxid(deposit_storage_proof.value.to_be_bytes());

    (wd_outpoint, vout, move_txid)
```

**File:** circuits-lib/src/bridge_circuit/mod.rs (L137-145)
```rust
pub fn bridge_circuit(guest: &impl ZkvmGuest, work_only_image_id: [u8; 32]) {
    let input: BridgeCircuitInput = guest.read_from_host();
    assert_eq!(
        HEADER_CHAIN_METHOD_ID, input.hcp.method_id,
        "Invalid method ID for header chain circuit: expected {:?}, got {:?}",
        HEADER_CHAIN_METHOD_ID, input.hcp.method_id
    );

    // Verify the HCP
```

**File:** circuits-lib/src/bridge_circuit/mod.rs (L186-204)
```rust
    let user_wd_txid = bitcoin::Txid::from_byte_array(*user_wd_outpoint);

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

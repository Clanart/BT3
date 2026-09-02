### Title
Withdrawer-crafted OP_RETURN-shaped output at payout output index 0 hijacks `get_first_op_return_output`, permanently making a fully-funded honest operator's payout unprovable in `bridge_circuit` - ([File: circuits-lib/src/bridge_circuit/mod.rs])

### Summary
The operator's normal (non-optimistic) `withdraw` path (`core/src/operator.rs::withdraw`) does not restrict the withdrawer-chosen `out_script_pubkey`, unlike the optimistic-payout paths which explicitly whitelist P2TR/P2PKH/P2SH/P2WPKH/P2WSH. A withdrawer can therefore choose `out_script_pubkey` to be an OP_RETURN script with a malformed/short data push. Because `create_payout_txhandler` places the withdrawer's output at index 0 and the operator's OP_RETURN (with the operator's x-only pubkey) at index 2, and `get_first_op_return_output` simply returns the *first* OP_RETURN output in the transaction, the withdrawer's malformed OP_RETURN at index 0 is picked up instead of the operator's real one at index 2, causing `parse_op_return_data(...).expect("Invalid operator xonlypk")` to panic in `bridge_circuit`.

### Finding Description
The binding that is claimed and that this bug breaks is:
`get_first_op_return_output(payout_tx) == payout_tx.output[2]` (the operator-authored OP_RETURN carrying `operator_xonlypk`), for every payout transaction produced by `create_payout_txhandler`.

Trace:
- `create_payout_txhandler` (`core/src/builder/transaction/operator_reimburse.rs:407-436`) builds outputs in order: `[0] output_txout` (withdrawer-controlled `out_script_pubkey`), `[1] anchor`, `[2] op_return_txout(operator_xonly_pk)`.
- The withdrawer supplies `out_script_pubkey` via the `withdraw` gRPC (`WithdrawParams.output_script_pubkey`, `core/src/rpc/clementine.proto:239-253`), parsed unchanged by `parser::operator::parse_withdrawal_sig_params` (`core/src/rpc/parser/operator.rs`), with **no script-type validation**.
- `Operator::withdraw` (`core/src/operator.rs:560-627`) only checks profitability (`is_profitable`) and verifies the user's Schnorr signature over the *input*; it never validates that `out_script_pubkey` is a standard, non-OP_RETURN script. This is in contrast to `Verifier::sign_optimistic_payout` (`core/src/verifier.rs:1588-1599`) and the aggregator's `optimistic_payout` (`core/src/rpc/aggregator.rs:1044-1054`), which do whitelist `is_p2tr/is_p2pkh/is_p2sh/is_p2wpkh/is_p2wsh` — that guard is absent on the normal `withdraw` path.
- `get_first_op_return_output` (`circuits-lib/src/bridge_circuit/mod.rs:688-692`) does `tx.output.iter().find(|out| out.script_pubkey.is_op_return())` — it returns the **first** OP_RETURN output by iteration order, not specifically output index 2.
- In `bridge_circuit` (`circuits-lib/src/bridge_circuit/mod.rs:206-219`):
```
let first_op_return_output = get_first_op_return_output(&input.payout_spv.transaction)
    .expect("Payout transaction must have an OP_RETURN output");
...
let operator_xonlypk: [u8; 32] = parse_op_return_data(&first_op_return_output.script_pubkey)
    .expect("Invalid operator xonlypk")
    .try_into()
    .expect("Invalid xonlypk");
```
If output[0] is shaped as `OP_RETURN <short/malformed push>`, `parse_op_return_data` (`mod.rs:609-617`) returns `None` (confirmed by existing unit tests `test_parse_op_return_data_short`/`test_parse_op_return_data_fail`, `mod.rs:1260-1276`, which show malformed pushes are correctly rejected by the parser but nothing stops the caller from feeding it the wrong output). The `.expect("Invalid operator xonlypk")` then panics, aborting the guest execution — no journal/proof can ever be produced for that fixed, already-mined `payout_spv.transaction`.

Existing guards fail to prevent this because: (1) there is no on-chain or protocol constraint that output 0's script cannot be OP_RETURN-shaped; standardness/mempool policy is a relay-layer concern, not enforced here, and once mined the transaction and its exact bytes are fixed; (2) `Operator::withdraw` performs no script-type check on the withdrawer-supplied output; (3) `get_first_op_return_output` has no positional/ownership binding to "the operator's own OP_RETURN output," it simply grabs the first OP_RETURN-shaped script in the transaction, regardless of who authored it.

The same root-cause bug independently corrupts `Verifier::update_finalized_payouts` (`core/src/verifier.rs:2283-2354`, using the identical `get_first_op_return_output`/`parse_op_return_data` pair): if the withdrawer's index-0 output is a *well-formed* (non-malformed) 32-byte OP_RETURN push of attacker-chosen bytes, the DB will record a bogus `operator_xonly_pk` for the payout, which later causes `is_kickoff_malicious` (`verifier.rs:1857-1915`) to flag the honest operator's own kickoff as malicious (`operator_xonly_pk != kickoff_data.operator_xonly_pk`, `verifier.rs:1887-1890`), independently blocking reimbursement even without any bridge_circuit proof requirement.

### Impact Explanation
An unprivileged withdrawer can, at zero incremental cost beyond normal withdrawal fees, choose their own payout output script to be OP_RETURN-shaped with a malformed push. This causes:
- `bridge_circuit` to panic permanently for that specific, already-mined payout transaction (the txid/output bytes cannot be changed after the fact), so the honest operator who fronted the withdrawal (e.g., 10 BTC) can never generate a valid Groth16 proof/journal to be reimbursed via the `Reimburse` path.
- Independently, `Verifier::is_kickoff_malicious` will misclassify the honest operator's kickoff as malicious via the same first-OP_RETURN confusion, blocking the normal (non-BitVM) reimbursement path as well.

This matches the Critical category "an honest operator permanently unable to be reimbursed." The attack is repeatable per withdrawal/operator: any withdrawer targeting any operator can do this on any withdrawal they control, at will, singling out withdrawals whose amount they want unprovable.

### Likelihood Explanation
Preconditions are minimal and fully within the stated unprivileged attacker capability: the attacker only needs to call the aggregator/operator `withdraw` RPC with a crafted `output_script_pubkey` (an OP_RETURN with a short/invalid push) and a valid signature over that exact script/amount (`SinglePlusAnyoneCanPay`), which they can produce themselves since it is their own withdrawal output. No majority hashrate, no TLS interception, no key compromise is needed — inclusion of the resulting payout transaction in a block only requires normal fee-paying broadcast (mining a non-standard-shaped but consensus-valid tx is achievable with fee-based inclusion or via a cooperating/any miner; the tx is otherwise consensus-valid). The operator side performs no script-type validation on this specific path, so the malicious output reaches `create_payout_txhandler` and gets signed/broadcast by the operator unmodified. Attacker cost is limited to withdrawal/network fees; the exploit is fully repeatable across withdrawals and operators.

### Recommendation
- In `circuits-lib/src/bridge_circuit/mod.rs`, do not rely on "the first OP_RETURN output" to locate the operator's commitment. Instead bind the operator commitment to a fixed, protocol-defined output index (e.g., always the last output, or an explicitly indexed output agreed by the transaction schema), and validate that no other output in the transaction is OP_RETURN-shaped, or that the withdrawer's output index is excluded from OP_RETURN candidacy.
- In `core/src/operator.rs::withdraw`, validate `out_script_pubkey` against the same standard-script whitelist already used in `sign_optimistic_payout`/`optimistic_payout` (`is_p2tr`/`is_p2pkh`/`is_p2sh`/`is_p2wpkh`/`is_p2wsh`), explicitly rejecting OP_RETURN or other non-standard scripts for the withdrawer's own output.
- Apply the same fix to `Verifier::update_finalized_payouts`'s use of `get_first_op_return_output`/`parse_op_return_data` to avoid misclassifying honest operators as malicious.

### Proof of Concept
`cargo test` plan in `circuits-lib`:
1. Build a `payout_spv.transaction` (or reuse `test_data/payout_tx.bin`) with three outputs matching `create_payout_txhandler`'s layout: `output[0]` = malformed OP_RETURN script (e.g. `OP_RETURN OP_PUSHDATA1 0x4f <79 bytes>` — a push-length byte claiming more bytes than actually present, matching the `test_parse_op_return_data_fail` fixture at `mod.rs:1269-1276`), `output[1]` = anchor, `output[2]` = well-formed `OP_RETURN <32-byte operator_xonlypk>`.
2. Assert binding before: manually confirm `get_first_op_return_output(&tx) == &tx.output[0]` (not `&tx.output[2]`), i.e. the equality claimed by the honest-operator invariant (`get_first_op_return_output(tx) == tx.output[2]`) is false.
3. Feed this transaction as `input.payout_spv.transaction` into a `BridgeCircuitInput` with an otherwise fully valid SPV proof, light client proof, storage proofs, and correct `payout_input_index`, so all checks preceding the OP_RETURN extraction pass.
4. Call `bridge_circuit(guest, work_only_image_id)` (or invoke `get_first_op_return_output` + `parse_op_return_data` directly as done in `mod.rs:206-219`) and assert it panics with `"Invalid operator xonlypk"`, using `#[should_panic(expected = "Invalid operator xonlypk")]`, demonstrating that a fully-funded honest payout (correct output[2]) becomes unprovable solely because of the attacker-controlled output[0].
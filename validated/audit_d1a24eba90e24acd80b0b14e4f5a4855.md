### Title
Payout attribution hijack via SinglePlusAnyoneCanPay output-malleability + order-agnostic OP_RETURN scan - ([File: core/src/verifier.rs], [File: circuits-lib/src/bridge_circuit/mod.rs])

### Summary
`create_payout_txhandler` signs the payout transaction's input with `TapSighashType::SinglePlusAnyoneCanPay`, which under BIP-341 only commits to output index 0 (the user payout) and the spent input's own prevout — it commits to neither the number nor the content of any other outputs. Since the withdrawal signature and RBF-replaceable payout tx are broadcast publicly before confirmation, any unprivileged party can build a conflicting/RBF-replacing transaction that reuses the same input0+witness, keeps output0 identical, but inserts an attacker-chosen OP_RETURN at an earlier index than the honest operator's real OP_RETURN. `get_first_op_return_output` in `circuits-lib/src/bridge_circuit/mod.rs` and its use in `Verifier::update_finalized_payouts` (`core/src/verifier.rs`) scan outputs in order and take the first OP_RETURN found, so they attribute the payout to the attacker-chosen key instead of the actual funding operator.

### Finding Description
The broken binding: `payout_payer_operator_xonly_pk` (recorded via `update_finalized_payouts` / `update_payout_txs_and_payer_operator_xonly_pk`, `core/src/verifier.rs:2283-2353`, `core/src/database/verifier.rs:198-251`) should equal the xonly public key of the operator O whose signed input funded output 0 of the confirmed payout transaction. In reality this value is derived purely from `get_first_op_return_output(&circuit_payout_tx)` (`circuits-lib/src/bridge_circuit/mod.rs:686-692`), which does `tx.output.iter().find(|out| out.script_pubkey.is_op_return())` — the first OP_RETURN by position, with no tie to which party actually signed/funded the transaction.

`create_payout_txhandler` (`core/src/builder/transaction/operator_reimburse.rs:407-436`) fixes the operator's OP_RETURN at output index 2 (output0=user payout, output1=anchor, output2=OP_RETURN(O)). The user's authorization is `SinglePlusAnyoneCanPay` (enforced exactly in `parse_withdrawal_sig_params`, `core/src/rpc/parser/operator.rs:161-187`), and `Operator::withdraw` verifies this signature against `sighash = payout_txhandler.calculate_sighash_txin(0, in_signature.sighash_type)` (`core/src/operator.rs:620-637`). Per BIP-341, `SIGHASH_SINGLE|ANYONECANPAY` commits only to: the single spent input's own prevout/script/sequence, and the "corresponding" output (index 0 here). It does **not** commit to output count or to the contents of outputs 1..N.

Exploit flow:
1. Operator O broadcasts its (RBF-eligible) payout tx: input0=withdrawal UTXO+user sig, output0=user payout, output1=anchor, output2=OP_RETURN(O's xonly pk).
2. An unprivileged attacker observes this unconfirmed transaction (mempool/relay is public) and copies input0 with its witness (the signature is valid regardless of who rebroadcasts it, since ANYONECANPAY makes it input-agnostic to context and SIGHASH_SINGLE only pins output0).
3. Attacker constructs a replacement transaction: input0 (same, same witness) + output0 (byte-for-byte identical, required for signature validity) + output1 = a new OP_RETURN pushing an attacker-chosen 32-byte value (which need not even be a valid, registered operator's xonly pk) + any further outputs (e.g. the real anchor/OP_RETURN moved to indices 2/3, or dropped entirely) + a higher fee.
4. Attacker broadcasts this as an RBF replacement (or simply gets it mined first if O's tx hasn't propagated widely) and it gets mined.
5. `Verifier::update_finalized_payouts` (`core/src/verifier.rs:2311-2321`) parses the now-confirmed tx, calls `get_first_op_return_output`, finds the attacker's OP_RETURN at output1 before O's real one, and calls `parse_op_return_data`/`XOnlyPublicKey::from_slice` on it, writing the attacker-chosen (or possibly invalid → `None`) value into `payout_payer_operator_xonly_pk`.

Downstream consumers rely on this DB field as ground truth:
- `Verifier::is_kickoff_malicious` (`core/src/verifier.rs:1857-1915`) compares `operator_xonly_pk != kickoff_data.operator_xonly_pk` and flags the kickoff as malicious if they don't match — since the attacker's value will not equal O's own key (or is `None`, itself treated as "assume malicious"), O's legitimate kickoff gets flagged malicious by verifiers, blocking the Reimburse path.
- `Operator::validate_payer_is_operator` (`core/src/operator.rs:1686-1739`) requires `payer_xonly_pk == self.signer.xonly_public_key`; with the hijacked value this check fails permanently for O.
- The same `get_first_op_return_output` scan is reused inside the bridge circuit itself (`circuits-lib/src/bridge_circuit/mod.rs:206-229`, and `bridge-circuit-host/src/structs.rs:482-516`) to compute `operator_xonlypk` for `deposit_constant`, so even O's own proof-generation path for challenge/disprove would bind to the wrong operator key if it re-derives from the on-chain confirmed (attacker-mutated) transaction.

None of the existing guards catch this: `SECP.verify_schnorr` only validates the signature against the sighash that itself excludes outputs 1..N under this sighash flag; there is no check anywhere that the OP_RETURN output is at the protocol-defined fixed index (2) rather than "first found"; no uniqueness/ordering constraint exists in the DB or state-machine layer tying attribution to the actual signer of output0.

### Impact Explanation
This can render an honest operator's fronted payout **permanently unreimbursable**: `is_kickoff_malicious` will mark the operator's true kickoff malicious (since the DB-recorded payer key won't match `kickoff_data.operator_xonly_pk`), which routes into the challenge/disprove path instead of allowing the Reimburse transaction, burning the operator's time/collateral exposure without recourse to reimbursement. This falls under the specified Critical category "an honest operator permanently unable to be reimbursed." It is repeatable for every future withdrawal/operator, requiring only that the attacker observe an unconfirmed payout transaction and beat it to confirmation (or successfully RBF-replace it) with a crafted variant — a capability available to any unprivileged actor who can broadcast Bitcoin transactions and pay fees, per the threat model.

### Likelihood Explanation
Preconditions are modest: the operator's payout transaction must be visible (mempool-relayed or otherwise obtainable) before it confirms, and must use standard sequence numbers that make it RBF-replaceable (or the attacker simply races to get their variant mined first). Attacker cost is limited to Bitcoin transaction fees needed to outbid/out-race the operator's fee (well below the withdrawal value, i.e., attacker profits are not needed — this is a griefing/sabotage attack, not requiring the attacker to gain funds). No key compromise, no privileged role, and no interaction with Citrea internals is required — purely a Bitcoin-transaction-malleability issue rooted in reliance on `SinglePlusAnyoneCanPay` plus positional (rather than fixed-index or signature-committed) OP_RETURN parsing.

### Recommendation
- Do not use `get_first_op_return_output`'s "first OP_RETURN anywhere in the transaction" semantics for attribution of value/authority. Instead, require the operator's OP_RETURN to be at a protocol-fixed output index (matching `create_payout_txhandler`'s output ordering, e.g., index 2) and reject/treat-as-`None` any payout tx whose OP_RETURN is not at that exact index.
- Alternatively/additionally, make the operator's identity part of what is committed by the signature: either have the operator co-sign (or otherwise cryptographically bind) the full set of outputs (e.g., by having verifiers refuse to build/broadcast on the operator's behalf unless output ordering/positions are exactly as specified, and by validating output-count/order server-side before broadcasting and again when parsing confirmed transactions), or bind attribution to a fixed vout instead of a linear scan.
- Add validation in `update_finalized_payouts` that the confirmed payout transaction's output layout (count, anchor position, OP_RETURN position) exactly matches the protocol's `create_payout_txhandler` template before trusting the parsed operator key; otherwise mark attribution as unresolved/`None` rather than acting on an attacker-supplied value in a way that can indict an honest operator via `is_kickoff_malicious`.

### Proof of Concept
```
cargo test (regtest-based, e.g. in core/src/test/deposit_and_withdraw_e2e.rs style harness):

1. Set up a deposit + withdrawal as in existing e2e tests (see core/src/test/common/clementine_utils.rs::payout_and_start_kickoff for reference).
2. Call operator.withdraw(...) to obtain the signed payout tx handler (do not broadcast).
3. Manually construct an "attacker" transaction: reuse tx.input[0] verbatim (same witness/signature),
   keep tx.output[0] identical, replace tx.output[1] with a new OP_RETURN pushing an arbitrary 32-byte
   value (NOT any real operator's xonly pk), append the original anchor+OP_RETURN(O) afterward, and
   bump fee (or simply skip broadcasting the honest tx and broadcast this one first).
4. rpc.send_raw_transaction(&attacker_tx); mine_blocks(1).
5. Run the verifier's block-processing / update_finalized_payouts equivalent path (or call the underlying
   DB flow directly) so that it ingests this mined tx.
6. Assert via db.get_payout_info_from_move_txid(...) that payout_payer_operator_xonly_pk equals the
   attacker's injected 32 bytes (parsed as an XOnlyPublicKey) or None — i.e., NOT equal to O's
   self.signer.xonly_public_key — despite O's Payout signature being the only one authorizing output0's
   value transfer.
7. Additionally assert that a subsequent call to a verifier's is_kickoff_malicious-equivalent check
   (constructing kickoff_data with O's real operator_xonly_pk) returns true (flagged malicious),
   demonstrating the attribution corruption blocks O's honest kickoff/reimburse path.
```
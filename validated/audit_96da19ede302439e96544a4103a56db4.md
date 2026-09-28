### Title
ReceivedOutput's outpoint/offset/TxOut tuple is never cross-validated, letting untrusted bytes cause the multisig to sign a transaction over mismatched prevouts - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
The reference bug is a caller-supplied identifier (voucher address) that can mismatch the identifier stored in the referenced object (offer), producing a position that mixes fields from two different objects. In `bitcoin-serai`, `ReceivedOutput` is a three-field claim — `offset`, `output` (a `TxOut`), and `outpoint` — that is accepted verbatim from `ReceivedOutput::read` or any caller, and `SignableTransaction::new` uses each field for a different purpose without verifying they describe the same on-chain UTXO. Only `SignableTransaction::multisig` performs a partial consistency check, binding `offset` to `output.script_pubkey` via `p2tr_script_buf(offset.group_key()) == prevouts[i].script_pubkey`; the `outpoint` is never bound to the `output` at all.

### Finding Description
`ReceivedOutput` deserializes `offset`, `output`, and `outpoint` independently from untrusted bytes with no relationship enforced between them (`networks/bitcoin/src/wallet/mod.rs:122-134`). `SignableTransaction::new` then consumes the fields independently: `outpoint` becomes `tx.input[i].previous_output` (line 180), `output` becomes `prevouts[i]` committed by the Taproot sighash via `Prevouts::All` (lines 253, 375-386), and `offset` selects the signing key (line 176). The only consistency check, in `multisig` (lines 276-279), verifies `offset * G` maps to `output.script_pubkey`; nothing verifies that `tx.input[i].previous_output` actually resolves to a UTXO containing `prevouts[i]`. This is the same structural flaw as the IVO bug: two independently supplied fields that must refer to the same object are not cross-checked, and the fix ("derive the field from the referenced object instead of accepting it") maps directly — the `TxOut` should be fetched/verified against the outpoint rather than trusted.

### Impact Explanation
An attacker who can feed bytes to `ReceivedOutput::read` (or otherwise supply `ReceivedOutput`s to `SignableTransaction::new`) can cause the FROST multisig to produce a fully-signed transaction committing to fabricated prevout data. Concretely: claim an `output` with Serai's own `p2tr` script and an inflated `value` (passing the `multisig` check), paired with an `outpoint` naming a real but differently-valued UTXO (or a nonexistent one). `Prevouts::All` commits to the fake `TxOut`, so every participant signs a sighash over attacker-chosen prevout values; the resulting transaction is signed but unspendable — the consensus-level prevout data won't match. This yields wasted/burned signing sessions, incorrect `fee()`/`input_sat` accounting (a fabricated large `value` inflates `input_sat`, letting `NotEnoughFunds` be bypassed and pushing the difference into the fee or change output), and "funds" that appear spendable in the constructed transaction but cannot move on-chain. It is a mismatch-induced signature over an unintended message, not key compromise — hence Medium, not High.

### Likelihood Explanation
Reachability requires the attacker to control the `ReceivedOutput` bytes. The rules explicitly classify untrusted bytes fed to `ReceivedOutput::read` as in-scope public input. The honest `Scanner` path always produces consistent tuples, so the bug requires an attacker-controlled or corrupted input path — a real but not universal precondition, consistent with Medium severity (mirroring the original finding's "requires an error by the fund manager" caveat, but here reachable purely from deserialized bytes).

### Recommendation
Either fetch the `TxOut` for each `outpoint` (via RPC/node data) inside `SignableTransaction::new` instead of trusting the embedded `output`, or validate each `prevouts[i]` against the referenced UTXO before constructing the sighash. At minimum, add a consistency assertion in `multisig` binding `outpoint → output` (not just `offset → script_pubkey`), mirroring the original fix of deriving the voucher address from the offer rather than accepting it as an argument.

### Proof of Concept
```rust
// Given a real Serai-owned UTXO at outpoint_real with TxOut{script_pubkey: p2tr(K), value: 1000},
// craft a mismatched ReceivedOutput:
let mut bytes = Vec::new();
bytes.extend(offset_for_K.to_bytes());          // offset consistent with script (passes multisig check)
bytes.extend(serialize(&TxOut {
  value: Amount::from_sat(1_000_000),            // inflated value
  script_pubkey: p2tr_script_buf(K).unwrap(),    // correct script -> multisig check passes
}));
bytes.extend(serialize(&outpoint_real));         // points at the 1000-sat UTXO
let fake = ReceivedOutput::read(&mut bytes.as_slice()).unwrap();

// SignableTransaction::new accepts it: input_sat counts 1_000_000,
// prevouts commits to the fake TxOut, input spends outpoint_real.
let stx = SignableTransaction::new(vec![fake], &payments, change, None, fee).unwrap();
let signed = multisig_sign(stx); // all participants sign Prevouts::All containing fabricated data
// `signed` is a fully-witnessed transaction that consensus rejects / spends 1000 sats
// while the protocol believes it spent 1_000_000.
```
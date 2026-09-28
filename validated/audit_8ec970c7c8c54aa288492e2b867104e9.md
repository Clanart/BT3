### Title
`SignableTransaction::new` does not check for duplicate inputs, inflating the reported input value and producing unspendable transactions - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` accepts a `Vec<ReceivedOutput>` and builds both the transaction's input list and its internal `prevouts`/`offsets`/`input_sat` accounting directly from that vector, with no check that any two entries reference the same `OutPoint`. A duplicated `ReceivedOutput` is counted twice in `input_sat` (line 175) while also producing two `TxIn`s spending the same outpoint (lines 177-185). This is the direct analog of the OlympusTreasury `addAsset` finding: a batch insertion path that pushes every element of a caller-supplied list without the duplicate check that exists elsewhere in the codebase (compare `Scanner::register_offset` in `networks/bitcoin/src/wallet/mod.rs` lines 187-191, which explicitly rejects a duplicate script, and the duplicate-participant checks in `crypto/frost/src/sign.rs` lines 305-310 and `crypto/dkg/src/lib.rs` lines 477-478).

### Finding Description
`ReceivedOutput` is deserializable from untrusted bytes via `ReceivedOutput::read` (`networks/bitcoin/src/wallet/mod.rs` lines 122-134), so a `Vec<ReceivedOutput>` is attacker-shapeable input. In `SignableTransaction::new` (`networks/bitcoin/src/wallet/send.rs` lines 150-256):

- `input_sat` is `inputs.iter().map(|input| input.output.value).sum()` — a duplicate outpoint's value is counted twice.
- `tx_ins` maps every entry to a `TxIn` with `previous_output: input.outpoint` — two identical outpoints yield a consensus-invalid transaction (a transaction spending the same UTXO twice is rejected by every Bitcoin node).
- The `NotEnoughFunds` check (line 215) and the change computation (lines 224-235) use the inflated `input_sat`, so a duplicated input can make an otherwise under-funded payment pass validation, and can fabricate a change output of `input_sat - payment_sat - fee` that is larger than the funds actually available.

`multisig()` (lines 273-285) then derives an offset key per input and only checks `p2tr_script_buf(offset.group_key()) == prevouts[i].script_pubkey`. A verbatim duplicate of a valid `ReceivedOutput` satisfies this check for both copies, so every signer produces valid Schnorr shares for both duplicate inputs and `complete` emits a fully signed — but permanently unbroadcastable — transaction.

### Impact Explanation
Two concrete harms, both reachable from untrusted `ReceivedOutput` bytes:

1. **Signed, never-confirming transaction with fabricated change.** The threshold multisig signs a transaction the Bitcoin network will always reject. If the change output was inflated by the duplicate, the protocol believes it retains change funds that do not exist — the change amount was computed against a double-counted input. Funds accounting diverges from reality.
2. **Bypass of `NotEnoughFunds`.** A duplicate input lets `input_sat >= payment_sat + needed_fee` pass where it otherwise would not, causing the multisig to sign a spend it has not actually funded. The resulting invalid transaction cannot pay the intended recipients.

This maps to "funds reported received/available that are not spendable" and "signing of an unintended message" — the signers' shares commit to a sighash over a transaction that can never exist on-chain, and any honest accounting derived from `input_sat`/`needed_fee`/`fee()` (lines 133-141) is wrong by the duplicated amount.

### Likelihood Explanation
Medium. Exploitation requires an untrusted party to influence the `inputs` vector passed to `SignableTransaction::new`, e.g. via bytes deserialized with `ReceivedOutput::read` rather than outputs produced by an honest `Scanner` (which only emits each on-chain outpoint once). Within the rules' reachability model — untrusted bytes fed to `ReceivedOutput::read` — no privileged position or validator misbehavior is needed; the bug is purely the missing uniqueness check, exactly as in the Olympus `addAsset` finding where the per-item insertion lacked the dedup present in the sibling function.

### Recommendation
Reject duplicate outpoints in `SignableTransaction::new`, mirroring the duplicate checks used elsewhere (`Scanner::register_offset`, `check_keys` in `crypto/dkg/musig/src/lib.rs` lines 54-60, `validate_map` in `crypto/frost/src/lib.rs`):

```rust
// In SignableTransaction::new, after the NoInputs check
{
  let mut seen = std::collections::HashSet::with_capacity(inputs.len());
  for input in &inputs {
    if !seen.insert(input.outpoint) {
      Err(TransactionError::DuplicateInput)?;
    }
  }
}
```

with a new `TransactionError::DuplicateInput` variant. Optionally also check duplicate `prevouts[i].script_pubkey`/`offset` pairs, though outpoint uniqueness is the consensus-relevant invariant.

### Proof of Concept
```rust
// Given any valid ReceivedOutput `out` (e.g. produced by ReceivedOutput::read
// on attacker-chosen bytes, or cloned from scanner output):
let dup = vec![out.clone(), out]; // same outpoint twice

// NotEnoughFunds passes using 2x the real balance, and change is inflated.
let stx = SignableTransaction::new(
  dup, &payments, Some(change_addr), None, fee_per_vbyte
).unwrap();

// Both inputs pass the script_pubkey check in multisig(); every signer signs
// both duplicate inputs. `complete` returns a fully signed Transaction that
// every Bitcoin node rejects for spending the same outpoint twice.
let tx = sign(&keys, &stx); // valid signatures, permanently invalid tx
```
The transaction fails Bitcoin consensus validation (`bad-txns-inputs-duplicate`) while `SignableTransaction::needed_fee`, `fee()`, and the emitted change output all reflect the double-counted input value.
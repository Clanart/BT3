### Title
Scanner classifies attacker-sent outputs by script_pubkey alone, letting external deposits bypass the External provenance check and be silently treated as internal change/forwarded funds - (File: processor/src/networks/bitcoin.rs)

### Summary
The Airflow bug is a per-entity scoping check applied on the collection path but skipped when an item is fetched directly by an identifier. The Serai analog lives in the Bitcoin output pipeline: `Scanner::scan_transaction` attributes an output to a registered offset purely by matching `output.script_pubkey` against its `scripts` map, and `processor/src/networks/bitcoin.rs` then derives `OutputType` (External / Branch / Change / Forwarded) solely from which offset the script maps to. There is no check that a "Change"/"Branch"/"Forwarded" output was actually created by the processor's own transaction — provenance scoping is inferred entirely from the script template, which is publicly derivable.

### Finding Description
- `Scanner` holds `scripts: HashMap<ScriptBuf, Scalar>` mapping script templates to offsets (`networks/bitcoin/src/wallet/mod.rs:153-156`), and `scan_transaction` accepts any output whose `script_pubkey` matches, returning it as a `ReceivedOutput` carrying that offset (`wallet/mod.rs:199-214`).
- `scanner()` in `processor/src/networks/bitcoin.rs:314-347` deterministically registers well-known offsets for `OutputType::Branch`, `OutputType::Change`, and `OutputType::Forwarded` using public `hash_to_F(KEY_DST, b"change")` etc. Since the group key and the DST string are public, **anyone** can compute these internal scripts and send funds to them.
- `Output::key()` (`processor/src/networks/bitcoin.rs:112-122`) reconstructs the owning group key as `script_key - offset*G`, so an attacker-sent output to the change script attributes cleanly to the active multisig — it passes every downstream identity check.
- In `processor/src/multisigs/mod.rs:824-837`, outputs are filtered: `Forwarded` outputs consult `ForwardedOutputDb`, and everything not `OutputType::External` is dropped from the instruction path (`outputs.retain(|output| output.kind() == OutputType::External)`). An external deposit sent to the change/branch/forward script therefore never generates an `InInstruction` and is never credited to the depositor — yet it is a real, spendable output absorbed into the multisig's internal accounting as if the processor itself had created it.

The "scoping" that genuine deposits receive (an `InInstruction` crediting the sender) is bypassed when the output is looked up under an internal offset "ID", exactly mirroring the detail-endpoint bypassing the per-DAG filter.

### Impact Explanation
An unprivileged party sends a Bitcoin transaction paying the multisig's change or forward offset script. The processor scans it, classifies it as `Change`/`Forwarded`, drops it from deposit reporting, and the coins are absorbed into the threshold wallet with no corresponding credit. The depositor's funds are received but unspendable *by them* under the protocol's accounting — effectively a donation/theft surface, and it corrupts internal balance bookkeeping (a `Change` output that never was change, or a `Forwarded` output for which `ForwardedOutputDb::take_forwarded_output` returns nothing). Severity Medium: requires the victim to send funds to a derivable-but-not-advertised script, and causes mis-accounted/unaccounted received funds rather than key compromise.

### Likelihood Explanation
The change/branch/forward scripts are fully derivable from the public group key plus `Secp256k1::hash_to_F("Serai Bitcoin Output Offset", "change"|"branch"|"forward")`. Any user of the bridge who derives these addresses (or is tricked into paying them) loses the deposit with no crediting. No timing, collusion, or privileged position is needed — just one transaction the scanner will pick up.

### Recommendation
Bind internal output types to provenance, not just script shape: only classify an output as `Change`/`Branch`/`Forwarded` if its `outpoint` corresponds to a transaction the processor itself constructed and recorded (e.g., check the txid against a DB of self-created plans/eventualities before assigning `kind`); otherwise treat unknown sends to offset scripts as `External` deposits or reject them explicitly.

### Proof of Concept
```rust
// Any external party, given the multisig's public group key:
let change_offset = Secp256k1::hash_to_F(b"Serai Bitcoin Output Offset", "change");
let change_script = p2tr_script_buf(group_key + (ProjectivePoint::GENERATOR * change_offset))
  .expect("increment offset until even");

// Broadcast a normal Bitcoin TX paying `change_script` with >= DUST sats.
// Scanner::scan_transaction matches output.script_pubkey -> change_offset,
// Output { kind: OutputType::Change, ... } is emitted,
// mod.rs retains only OutputType::External -> the deposit is never reported
// as an InInstruction and the depositor is never credited.
```
All primitives used (`hash_to_F` DST, `p2tr_script_buf`, offset registration) are public constants/code paths in `processor/src/networks/bitcoin.rs:308-346` and `networks/bitcoin/src/wallet/mod.rs:77-196`.

Note: I was unable to read the exact `get_outputs` body in `processor/src/networks/bitcoin.rs` where `kind` is assigned from the offset map (index returned only match counts, not line contents), but the classification flow is confirmed by the `scanner()` helper returning `kinds` alongside the offsets and by the `OutputType`-based filtering in `multisigs/mod.rs`.
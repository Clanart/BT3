### Title
`ReceivedOutput::read` / `Scanner` accept an arbitrary spend-offset without verifying it derives the output's `script_pubkey` - (networks/bitcoin/src/wallet/mod.rs)

### Summary
The analog of "missing permission checks" (CWE-862 — a missing authorization/validation step on a reachable code path) maps onto Serai as a missing consistency check on attacker-supplied bytes. `ReceivedOutput::read` deserializes a `ReceivedOutput` — a scalar `offset`, a `TxOut`, and an `OutPoint` — from untrusted bytes and performs no check that `key + offset·G` actually produces the `script_pubkey` contained in the `TxOut`. The scanner is the only place a correct `(offset, script)` binding is established (`scan_transaction` looks up `output.script_pubkey` in `self.scripts`), and that binding is not re-validated on deserialization.

### Finding Description
`Scanner::scan_transaction` is the sole enforcement point tying a registered offset to a script: it only emits a `ReceivedOutput` when `self.scripts.get(&output.script_pubkey)` returns a registered offset (`networks/bitcoin/src/wallet/mod.rs:199-214`). `register_offset` itself iterates `offset += 1` until `key + offset·G` yields an even-Y P2TR key and records the *used* offset (`networks/bitcoin/src/wallet/mod.rs:180-196`). The correctness of spending depends entirely on `output.offset()` matching the script.

`ReceivedOutput::read` (`networks/bitcoin/src/wallet/mod.rs:122-134`) reads `offset` via `Secp256k1::read_F`, then consensus-decodes `TxOut` and `OutPoint`, and returns the tuple verbatim. There is no check that `p2tr_script_buf(key + GENERATOR * offset) == output.script_pubkey`, and `read` cannot perform one anyway because `ReceivedOutput` doesn't store the base key. Downstream, `processor/src/networks/bitcoin.rs:112-122` (`OutputTrait::key`) recomputes the credited key as `read_G(script_key) − GENERATOR * offset`, which is self-consistent for *any* attacker-chosen offset — so a forged `ReceivedOutput` with a mismatched offset is silently attributed to a wrong key rather than rejected.

Separately, `Offset`/kind attribution in `get_outputs` (`processor/src/networks/bitcoin.rs:686-700`) trusts `kinds[offset_repr_ref]` — indexing on whatever offset bytes were scanned — and `Output::read` (`processor/src/networks/bitcoin.rs:145-166`) restores `kind`, `presumed_origin`, and `data` from the same unchecked byte stream.

### Impact Explanation
An unprivileged party who can feed bytes to `ReceivedOutput::read`/`Output::read` (an explicitly in-scope sink) can fabricate a `ReceivedOutput` claiming an arbitrary value at an arbitrary outpoint, or take a real on-chain output and pair it with a wrong offset. Because `key()` derives the multisig key as `script_key − offset·G` instead of verifying `script_key == intended_key + offset·G`, the output is reported as received funds under a key that cannot actually spend it — matching the accepted impact class "funds reported received that are not spendable" and causing output/misattribution across multisigs (the derived key can collide with or fall outside any registered multisig, crediting or hiding balance incorrectly). The deserialization path has no way to detect the mismatch since the binding established in `register_offset` is never re-checked.

### Likelihood Explanation
Reachability depends on an integrator deserializing `ReceivedOutput`/`Output` from untrusted input (network gossip, restored DB entries, coordinator messages) rather than solely from `scan_transaction`. Where that holds, the attack requires no secrets — only control of the serialized `(offset, TxOut, OutPoint)` tuple, all public encodings. The forgery is deterministic: any 32-byte canonical scalar and any valid consensus `TxOut`/`OutPoint` are accepted. Likelihood is moderate rather than high because the primary consumer (the processor scanner) produces `ReceivedOutput`s internally, so exploitation requires an integrator-facing or persisted path feeding `read` adversarial data.

### Recommendation
Store the base public key (or a transcript-bound commitment to it) inside `ReceivedOutput`, and in `ReceivedOutput::read`/`write` enforce `p2tr_script_buf(key + GENERATOR * offset) == Some(output.script_pubkey)`. Short of that, add a `ReceivedOutput::verify_consistency(key)` method and call it at every trust boundary where `read` output is consumed, including in `Output::read` before `key()`/`kind()` attribution, so a forged offset cannot silently re-attribute an output's owning key.

### Proof of Concept
Conceptual (no exploit framework available):

```rust
// Attacker-controlled bytes fed to Output::read / ReceivedOutput::read
let real: ReceivedOutput = scanner.scan_transaction(&tx).pop().unwrap(); // offset o, script S for key+oG
let forged = ReceivedOutput {
    offset: real.offset() + Scalar::ONE,     // wrong offset
    output: real.output().clone(),           // same script_pubkey S
    outpoint: *real.outpoint(),
};
// forged.serialize() -> victim calls ReceivedOutput::read -> accepted, no error
// victim's Output::key() returns read_G(S.x_only) - (o+1)G = key - G  (a bogus key)
// => value credited to key - G, which does not control S: funds reported, unspendable
```

Verified code facts supporting this: `read` performs only scalar/TxOut/OutPoint decoding with no script recomputation (`wallet/mod.rs:122-134`); `key()` subtracts `GENERATOR * offset` from the script key unconditionally (`processor/src/networks/bitcoin.rs:112-122`); the only `offset↔script` binding lives in `scan_transaction`/`register_offset` (`wallet/mod.rs:180-214`).

Caveat: I was unable to fully trace every consumer of `ReceivedOutput::read` within the available search iterations, so the precise deployment path where attacker bytes reach it is the main residual uncertainty.
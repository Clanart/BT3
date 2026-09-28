### Title
`ReceivedOutput::read` accepts an attacker-controlled scalar offset that is not bound to the output's script — deserialized outputs can be reported as received yet be unspendable or misattributed - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`ReceivedOutput` pairs a `Scalar` offset with a `TxOut`/`OutPoint`. The offset is what converts the group's tweaked Taproot key into the key that actually controls the output (`key + offset*G`). `ReceivedOutput::read` deserializes the offset, the `TxOut`, and the `OutPoint` independently and performs no check that the offset actually derives the x-only key committed inside `output.script_pubkey`. This is the Serai-shaped analog of the CVE-2023-6348 type confusion: a byte stream is split into typed fields whose semantic consistency is assumed but never verified, so an offset "of the wrong type" (one belonging to a different key/script) can be substituted into an otherwise well-formed object.

### Finding Description
`ReceivedOutput::read` reads `offset` via `Secp256k1::read_F` and then consensus-decodes `TxOut` and `OutPoint` with no cross-field validation (mod.rs:122-133). Contrast with the write side: `Scanner::scan_transaction` only constructs a `ReceivedOutput` when `output.script_pubkey` is present in `self.scripts`, i.e., when the script equals `p2tr_script_buf(key + offset*G)` for a *registered* offset (mod.rs:185-211), and `register_offset` itself iterates `offset += 1` until the derived point is even and rejects collisions (mod.rs:180-195). That invariant — offset ↔ script binding — exists only in memory and is completely absent from the serialization format.

Downstream, `SignableTransaction::multisig` re-derives the spend key as `keys.clone().offset(self.offsets[i])` and refuses to build a machine unless `p2tr_script_buf(offset.group_key()) == self.prevouts[i].script_pubkey` (send.rs:273-284). So a `ReceivedOutput` carrying a substituted offset `o'` ≠ `o` yields `key + o'*G` whose script does not match the prevout, `multisig` returns `None`, and the output — although accepted as a wallet output — can never be signed for. If `o'` is chosen so that `key + o'*G` *does* produce a valid even P2TR script equal to some other registered script, the output is additionally misattributed to that key.

### Impact Explanation
An unprivileged party able to feed bytes to `ReceivedOutput::read` (an explicitly in-scope sink; e.g., a relayed/deserialized output object) can cause funds to be reported as received that are not spendable: the wallet records value at an outpoint it can never sign for because the deserialized offset does not match the script key. This maps directly to the accepted impact class "funds reported received that are not spendable." Severity: Medium — integrity of wallet state is corrupted, but no key material or forgery results.

### Likelihood Explanation
The bug triggers whenever a `ReceivedOutput` is consumed from untrusted bytes rather than produced by `Scanner::scan_transaction`/`scan_block`. Within that reachable path it is deterministic: any altered offset byte breaks the offset↔script binding, and `multisig` will deterministically fail. Likelihood is gated entirely by whether deployments deserialize outputs from untrusted sources rather than rescanning, hence Medium rather than High.

### Recommendation
Either bind the offset to the output at deserialization — recompute `p2tr_script_buf(base_key + offset*G)` and compare against `output.script_pubkey` (requires storing/parameterizing the base key in `read`) — or drop the trust in the stored offset entirely and re-derive it by rescanning the script through `Scanner` (which maintains the `scripts → offset` map). Failing closed by treating an unverifiable offset as a scan miss is preferable to recording an unspendable output.

### Proof of Concept
```rust
// In-scope: networks/bitcoin/src/wallet/mod.rs, send.rs
// 1. Honest path: scanner registers offset o, observes a tx paying to
//    p2tr_script_buf(key + o*G), producing ReceivedOutput { offset: o, output, outpoint }.
// 2. Attacker takes output.serialize() and replaces the first 32 bytes
//    (the Scalar offset encoding) with a different canonical scalar o'.
// 3. ReceivedOutput::read succeeds — no binding check exists.
// 4. SignableTransaction::new(vec![forged_output], ...) succeeds;
//    the output is counted toward input_sat.
// 5. tx.multisig(&keys) returns None because
//    p2tr_script_buf(keys.offset(o').group_key()) != prevout.script_pubkey.
// Result: balance credited at the outpoint can never be signed/spent.
```

Uncertain: I could not fully trace every caller that feeds untrusted bytes into `ReceivedOutput::read` (e.g., the processor's `Output::read` path is outside the listed scope); the finding is conditioned on that sink being reachable with attacker-controlled bytes, as the rules stipulate.
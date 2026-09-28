### Title
Attacker-controlled `ReceivedOutput.offset` re-keys the FROST signing machine to an arbitrary tweak, bypassing the script-pubkey guard — (File: networks/bitcoin/src/wallet/send.rs)

### Summary
The Hermes bug class is a privileged write primitive whose guard checks the wrong target, letting attacker input overwrite a sensitive store. The Serai analog lives in `SignableTransaction::multisig`: the signing key (the sensitive store) is re-keyed by `keys.offset(self.offsets[i])` where `self.offsets` come from `ReceivedOutput`, which is deserializable from untrusted bytes via `ReceivedOutput::read`. The only guard — comparing `p2tr_script_buf(offset.group_key())` to the prevout's `script_pubkey` — validates attacker-supplied data against attacker-supplied data, so it never actually binds the offset to anything the `Scanner` legitimately registered. This mirrors a path denylist that excludes the credential file: the guard exists, but the sensitive target is reachable through it.

### Finding Description
`ReceivedOutput::read` (`networks/bitcoin/src/wallet/mod.rs:122-134`) reads `offset`, `output`, and `outpoint` entirely from a byte stream with no integrity check or binding to the `Scanner`'s `scripts` map (`mod.rs:153-196`), which is the only component that knows which offsets were securely generated.

In `SignableTransaction::multisig` (`send.rs:273-285`):

```rust
let offset = keys.clone().offset(self.offsets[i]);
if p2tr_script_buf(offset.group_key())? != self.prevouts[i].script_pubkey {
  None?;
}
sigs.push(AlgorithmMachine::new(Schnorr::new(), keys.clone().offset(self.offsets[i])));
```

An attacker who supplies a crafted `ReceivedOutput` chooses `offset = o` arbitrarily, then sets `prevout.script_pubkey = p2tr_script_buf(P + o·G)` (computable from the public group key `P`), trivially satisfying the guard. The machine then signs under `ThresholdKeys` offset by `o`. In `TransactionSignMachine::sign` (`send.rs:373-397`), the sighash is computed over `Prevouts::All(&self.tx.prevouts)` — the attacker-fabricated prevout set — and the threshold signature share is produced for that attacker-chosen message under the attacker-tweaked key.

The `Scanner` path (`scan_transaction`, `mod.rs:199-214`) is safe: it only emits offsets that were previously registered via `register_offset`, which rejects script collisions via `self.scripts.contains_key(&script)`. The deserialization path has no equivalent check — the "sensitive path" (arbitrary re-keying of the signer) is exactly what the `register_offset` collision guard was designed to prevent, and `multisig` re-opens it.

### Impact Explanation
The threshold signer produces a valid BIP-340 signature under group key `P + o·G` for a sighash the attacker fully controls (they choose the fabricated prevout values, outpoints, and outputs committed via `Prevouts::All`). The signers believe they are spending a scanner-registered output with a securely-generated offset; they are actually signing under an arbitrarily tweaked key for an input the wallet never received through the scanner. This is concrete signing of an unintended message: every invariant the wallet layer tries to maintain (only spend what `scan_transaction` returned, only under registered offsets) is void. Additionally, `Hram` negation/`tweak_keys` parity handling means an attacker-selected odd-parity tweak silently aborts (`None`), but an even-parity choice always succeeds, making the primitive deterministic for the attacker.

### Likelihood Explanation
Reachability requires the attacker to feed bytes into `ReceivedOutput::read` — explicitly in scope per the audit's allowed sinks — i.e., any path where received outputs are persisted, relayed, or reconstructed from untrusted data before `SignableTransaction::new`/`multisig`. No key material, no validator collusion, and no internal access is required; only public group key knowledge is needed to satisfy the guard. Once the crafted `ReceivedOutput` is in the input vector, the entire signing pipeline (preprocess → sign → complete → broadcastable tx) proceeds normally.

### Recommendation
Bind deserialization to registration: `ReceivedOutput::read` outputs should be re-validated against the `Scanner`'s `scripts` map (or carry proof of registered offset) before `SignableTransaction::new` accepts them. At minimum, `multisig` should reject any `offset`/prevout pair not produced by `scan_transaction`, rather than trusting a self-consistent attacker-supplied pair. Consider including the offset in an authenticated transcript when `ReceivedOutput`s are exchanged between components.

### Proof of Concept
1. Attacker obtains the group key `P` (public).
2. Chooses scalar `o`; computes `K = P + o·G`; if `needs_negation(K)`, increments `o` until `p2tr_script_buf(K)` returns `Some(script)` (~2 tries).
3. Crafts `ReceivedOutput { offset: o, output: TxOut { value: V, script_pubkey: script }, outpoint: any }` and serializes it; feeds it to `ReceivedOutput::read` on the target's ingestion path.
4. The builder constructs `SignableTransaction::new(vec![crafted], payments, change, data, fee)`. `multisig(&keys)` passes the guard since `p2tr_script_buf(P + o·G) == script` by construction.
5. `preprocess`/`sign` produce a signature share committing to `taproot_key_spend_signature_hash` over the fabricated `Prevouts::All`; `complete` yields a signed transaction under key `P + o·G` — a signature for an input never registered in `Scanner`, under an offset never securely generated, for a message the attacker chose.
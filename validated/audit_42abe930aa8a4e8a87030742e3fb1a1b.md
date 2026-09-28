### Title
Attacker-supplied `ReceivedOutput` grants access to arbitrary outpoints/offsets outside the registered scan set (absolute-identifier analog) - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
The node-git-server bug allowed an attacker to escape the intended repository root by supplying an absolute path, so the server operated on resources outside its configured scope. The analogous shape in Serai lives in `ReceivedOutput::read` (`networks/bitcoin/src/wallet/mod.rs:122-134`): the deserializer accepts a fully attacker-controlled `(offset, TxOut, OutPoint)` triple — an "absolute" identifier for a spendable output — with no check that the output's `script_pubkey` corresponds to any offset the `Scanner` registered, and no check that the outpoint actually belongs to the scanner's key tree.

### Finding Description
`Scanner::register_offset` (`mod.rs:180-196`) is the only sanctioned way to extend the set of spendable outputs: it starts from the scanner's `key`, walks offsets until `p2tr_script_buf` yields an even-Y tweaked key, and records `script_pubkey -> offset` in `scripts`. `Scanner::scan_transaction`/`scan_block` (`mod.rs:199-227`) only ever produce `ReceivedOutput`s whose `offset` came from that registered map, i.e., "relative" to the key.

`ReceivedOutput::read`, however, reconstructs the object from raw bytes: it calls `Secp256k1::read_F` for `offset` (any scalar accepted, no parity/even-key walk, no binding to `self.key`), then consensus-decodes an arbitrary `TxOut` and arbitrary `OutPoint`. Nothing ties `output.script_pubkey` to `p2tr_script_buf(key + offset*G)` nor proves the `outpoint` was ever scanned. This is the same root cause as GHSA-cv3v-7846-6pxm: an unvalidated absolute reference bypassing the scoping structure (`scripts` map ↔ repoDir root). The doc comment at `mod.rs:177-179` itself warns that arbitrary offsets "may introduce a script path into the output, allowing the output to be spent by satisfaction of an arbitrary script (not by the signature of the key)" — `register_offset` documents this danger, but `read` performs the equivalent operation with zero guards.

Additionally, because `read_F` accepts the offset directly, an attacker bypasses the parity correction loop (`offset += Scalar::ONE` at line 193), so deserialized offsets can reference keys that `register_offset` would never produce, further widening the reachable key space beyond the intended set.

### Impact Explanation
A party that can feed bytes into `ReceivedOutput::read` (e.g., an untrusted output being submitted into the wallet/spend pipeline) can cause the system to treat an output as "received" that was never derived through the scanner:

- Funds reported received that are not spendable: an output whose `script_pubkey` does not match `key + offset*G` will be reported as received with a stated value, but the corresponding spend key cannot be produced, so the reported balance is illusory.
- Arbitrary-outpoint claims: any `OutPoint` on the chain can be claimed, mirroring "access any git repository by absolute path."
- Where arbitrary offsets do introduce a spendable script path (per the documented warning), the claimed output may be spendable by a script the attacker controls rather than by the threshold key's signature.

### Likelihood Explanation
Reachable wherever untrusted `ReceivedOutput` bytes are deserialized into the spend flow — the rules explicitly classify bytes fed to `ReceivedOutput::read` as untrusted input. The attacker needs no key material, no collusion, and no validator role; only the ability to supply the serialized `ReceivedOutput`. The fix cost on the verifier side is small (re-derive `p2tr_script_buf(key + offset*G)` and compare against `output.script_pubkey`), which also shows the check was simply omitted.

### Recommendation
Make `ReceivedOutput` construction scope-aware, matching how `scan_transaction` works:

1. Either remove `ReceivedOutput::read` in favor of deserializing through a `Scanner` method that re-derives the script for `key + offset*G` (with the parity-increment walk) and rejects the input unless it equals `output.script_pubkey`, or
2. Keep `read` but add a `Scanner::verify_received(&ReceivedOutput)`/`scan_output`-style validation that callers must invoke before trusting the output, and document that raw `read` produces an unverified claim.

Also bound-check `vout` and consider canonical-scalar checks in `read_F` usage here for defense in depth.

### Proof of Concept
```rust
// networks/bitcoin/src/wallet/mod.rs context
// Attacker crafts bytes: offset = arbitrary scalar, TxOut with a script_pubkey
// NOT derived from scanner key, OutPoint pointing at someone else's UTXO.
let mut bytes = Vec::new();
bytes.extend(attacker_scalar.to_bytes());          // any F element accepted by read_F
bytes.extend(serialize(&txout_of_victim));         // script_pubkey not in scanner.scripts
bytes.extend(serialize(&victim_outpoint));         // absolute reference to foreign UTXO

let claimed = ReceivedOutput::read(&mut &bytes[..]).unwrap();
// `claimed` is accepted verbatim: value() reports victim funds,
// offset() yields a scalar never produced by register_offset,
// outpoint() references an output the scanner never scanned.
```
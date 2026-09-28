### Title
`ReceivedOutput::read` accepts an attacker-chosen key offset with no verification that the output's `script_pubkey` matches the tweaked key — ([File: networks/bitcoin/src/wallet/mod.rs])

### Summary
Analogous to the missing `onlyOwner` check on `extendTime` — where an authorization gate that should constrain a security-critical value was absent — `ReceivedOutput::read` deserializes an arbitrary `offset` scalar from untrusted bytes without enforcing the invariant that `output.script_pubkey` equals `p2tr(group_key + offset·G)`. That invariant is only enforced on the `Scanner` path (`scan_transaction` looks the offset up in a `script_pubkey → offset` map it built itself), so the byte path bypasses the check entirely.

### Finding Description
- `Scanner::register_offset`/`scan_transaction` maintain the binding between an offset and a P2TR script by construction: the script is derived from `key + GENERATOR * offset` and stored as `scripts[script] = offset` `networks/bitcoin/src/wallet/mod.rs:180-196`, `199-214`.
- `ReceivedOutput::read` reads `offset` (`Secp256k1::read_F`), a `TxOut`, and an `OutPoint` straight from the reader and returns them as a trusted spendable output, with no recomputation of `p2tr_script_buf(key + offset·G)` against `output.script_pubkey` `networks/bitcoin/src/wallet/mod.rs:120-134`.
- `SignableTransaction::new` consumes `ReceivedOutput`s and stores `offsets`/`prevouts` for signing `networks/bitcoin/src/wallet/send.rs:54-59`, `150-160`. `TransactionSignMachine::sign` then signs each input's Taproot key-spend sighash under the per-input re-keyed threshold key derived from that offset `networks/bitcoin/src/wallet/send.rs:355-398`.
- An attacker feeding crafted bytes to `ReceivedOutput::read` can pair a real (or fabricated) `TxOut`/`OutPoint` with an arbitrary `offset`. The resulting tweaked spending key will not equal the internal key committed in the output's `script_pubkey`, so the BIP-340 signature produced is invalid for that input; alternatively, an `offset` pointing to an unrelated script causes the wallet to treat output bytes it does not control as spendable inputs.

### Impact Explanation
Funds can be reported received/spendable that are not actually spendable, and the FROST signing round can be driven to produce signatures for inputs whose tweaked key does not match the on-chain script — burning a preprocess/signing session on an unspendable transaction, or crediting attacker-constructed outputs whose spend path is not the threshold key. This matches the accepted impact classes ("funds reported received that are not spendable").

### Likelihood Explanation
Reachable wherever `ReceivedOutput::read` is applied to peer/coordinator-supplied bytes (the in-scope untrusted-bytes surface). The attacker only controls public serialized data; no key material or validator status is required. Exploitation additionally depends on downstream code trusting the deserialized offset–output pair without re-deriving the script, which the type's API encourages (the check exists nowhere in the crate).

### Recommendation
Make `ReceivedOutput` enforce the offset↔script binding on construction: either verify `output.script_pubkey == p2tr_script_buf(key + offset·G)` inside `read` (requires threading the expected key), or move verification into `SignableTransaction::new` by re-deriving each input's expected script from the offset and rejecting mismatches.

### Proof of Concept
```rust
// Given a Scanner for `key` and an honest output paying to p2tr(key):
let mut scanner = Scanner::new(key).unwrap();
let honest = scanner.scan_transaction(&tx); // correct offset bound to script

// Attacker crafts bytes: same TxOut/OutPoint, but offset' = offset + k (or 0).
let mut buf = Vec::new();
buf.extend((honest[0].offset() + Scalar::ONE).to_bytes());
buf.extend(serialize(honest[0].output()));
buf.extend(serialize(honest[0].outpoint()));

let forged = ReceivedOutput::read(&mut buf.as_slice()).unwrap(); // no check fails

// SignableTransaction::new accepts it; TransactionSignMachine signs input 0 under
// keys.offset(offset + 1), whose tweaked internal key != script_pubkey's key.
// The resulting BIP-340 signature is invalid: input counted as spendable, isn't.
```

Caveat: I could not confirm the exact downstream call site where `ReceivedOutput::read` is fed untrusted bytes within the indexed scope; the vulnerability is in the missing invariant on the deserialization path itself, which is reachable per the stated scope.
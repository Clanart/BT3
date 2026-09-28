### Title
Missing offset-to-script binding in `ReceivedOutput::read` lets attacker-attributed spend keys diverge from the actual output - (networks/bitcoin/src/wallet/mod.rs)

### Summary
`ReceivedOutput` pairs a `TxOut`/`OutPoint` with the scalar `offset` used to derive the signing key (`group_key + offset*G`). `ReceivedOutput::read` deserializes `offset`, `output`, and `outpoint` independently and never checks that `output.script_pubkey` is the P2TR script of `key + offset*G` — the exact analog of the QuadraticFunding `vote` issue, where a decoded token field was never validated against the round's expected token. The invariant is only enforced implicitly inside `Scanner::scan_transaction`, which builds the `script_pubkey → offset` map itself.

### Finding Description
- `ReceivedOutput::read` at `networks/bitcoin/src/wallet/mod.rs:122-134` reads `offset = Secp256k1::read_F(r)` and then consensus-decodes an arbitrary `TxOut` and `OutPoint`, returning the triple with no consistency check.
- The only place the binding `p2tr_script_buf(key + G*offset) == script_pubkey` is guaranteed is `Scanner::scan_transaction`/`register_offset` (`mod.rs:180-214`), which constructs `ReceivedOutput` from the internal `scripts` map.
- Downstream, `SignableTransaction::new` (`send.rs:150-256`) trusts `input.offset` and `input.output` verbatim, and `SignableTransaction::multisig` (`send.rs:273-285`) is the *first* point where the script is compared to `p2tr_script_buf(offset.group_key())`. On mismatch it returns `None` — silently, with no error distinguishing "wrong keys" from "corrupted input".
- Worse, an attacker who can feed crafted bytes to `ReceivedOutput::read` (e.g., a relayed/stored serialized `ReceivedOutput`) can swap in an offset that corresponds to a *different registered offset* of the same key, or a `TxOut`/`OutPoint` pointing at a real deposit while the offset claims another key. Serialization round-trips are used by callers (e.g., `Output::read` in processor code wraps `ReceivedOutput::read`, and tests rely on `serialize`/`read` equality), so `read` is exposed to bytes not produced by the local `Scanner`.

### Impact Explanation
A `ReceivedOutput` whose `offset` does not match its `script_pubkey` is treated as spendable input:
- If passed to `SignableTransaction::multisig`, it returns `None` — funds that were "received" can no longer be spent through this API, with the failure indistinguishable from wrong keys (availability loss / stuck funds).
- If the `outpoint`/`TxOut` are attacker-chosen while the offset is honest, the wallet can be induced to build transactions committing (via `Prevouts::All` in `taproot_key_spend_signature_hash`, `send.rs:375-390`) to prevouts it never verified against its scanner, enabling framing-style griefing analogous to the report's "malicious users can frame an eligible project".
- Attributed-balance logic that keys off `offset` (which registered sub-address received the funds) is corruptible, since nothing in the decoded object binds the offset to the script.

### Likelihood Explanation
Reachable by any unprivileged party able to supply bytes to `ReceivedOutput::read` — the type exposes a public `serialize`/`read` round-trip intended for transport/storage, so any hop where a peer or persisted record is parsed is an attack surface. No key compromise or collusion is required; the attacker just flips or substitutes the scalar field.

### Recommendation
Bind the fields at deserialization. Since `ReceivedOutput` doesn't know the base `key`, either:
- store the expected `script_pubkey` keyed context in `read` (e.g., `read_with_key(r, key)` that verifies `p2tr_script_buf(key + G*offset) == output.script_pubkey`), or
- at minimum, validate `output.script_pubkey` is a well-formed P2TR script and document/enforce that consumers must re-check `multisig`'s invariant — while changing `multisig`'s silent `None` into a distinct error so offset/script mismatches aren't conflated with wrong keys.

### Proof of Concept
```rust
// Given a valid ReceivedOutput `o` for key K, offset d:
let mut bytes = o.serialize();
// Replace the scalar offset with a different registered offset d'
let d_prime = Scalar::from(7u64); // any offset also registered for K
bytes[..32].copy_from_slice(&d_prime.to_bytes());
let forged = ReceivedOutput::read::<&[u8]>(&mut bytes.as_ref()).unwrap();
// forged.offset() == d' while forged.output().script_pubkey is for K + d*G
assert_eq!(forged.offset(), d_prime);
// multisig() then returns None despite a genuine deposit existing:
assert!(SignableTransaction::new(vec![forged], &payments, None, None, 1)
        .unwrap().multisig(&keys).is_none());
// Funds received are now unspendable through the wallet API.
```

References: `ReceivedOutput::read` lacks the check at `networks/bitcoin/src/wallet/mod.rs:122-134`; the offset→script binding only exists in `Scanner::scan_transaction`/`register_offset` at `mod.rs:180-214`; the silent failure point is `SignableTransaction::multisig` at `networks/bitcoin/src/wallet/send.rs:273-285`.
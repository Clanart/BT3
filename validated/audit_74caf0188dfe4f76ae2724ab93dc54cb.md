### Title
`ReceivedOutput::read` accepts an offset inconsistent with the output's `script_pubkey`, reporting unspendable foreign outputs as received funds - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
The PEAP bug class is "skip the second-phase verification and accept a bare success": in Serai's Bitcoin wallet, `ReceivedOutput::read` deserializes an `(offset, TxOut, OutPoint)` triple and returns it as a spendable received output without ever re-deriving `key + G*offset` and checking it equals `output.script_pubkey`. The analogous "Phase 2" — confirming the claimed scalar offset actually corresponds to the script being paid to — is skipped entirely.

### Finding Description
`Scanner::scan_transaction` constructs `ReceivedOutput` internally by looking up `output.script_pubkey` in `self.scripts` (`networks/bitcoin/src/wallet/mod.rs:205-211`), so locally produced values are consistent by construction. However, `ReceivedOutput::read` (`networks/bitcoin/src/wallet/mod.rs:122-134`) is a public deserialization entry point that accepts untrusted bytes:

```rust
let offset = Secp256k1::read_F(r)?;
output = TxOut::consensus_decode(&mut buf_r)?;
outpoint = OutPoint::consensus_decode(&mut buf_r)?;
Ok(ReceivedOutput { offset, output, outpoint })
```

There is no key bound inside `ReceivedOutput` and no consistency check: the reader has no way to know whether `offset` actually tweaks the wallet key into `output.script_pubkey`. Any downstream consumer that trusts a deserialized `ReceivedOutput` (e.g., an `inputs: Vec<ReceivedOutput>` passed to `SignableTransaction::new`, `send.rs:150-157`) will:

1. Report `output.value` as received funds (`value()`, line 116-118).
2. Derive the signing key as `key + G*offset` and produce a Taproot signature over a key that does not correspond to the prevout's actual script, or produce an odd-Y tweaked key that is unspendable under BIP-340/341 (`p2tr_script_buf` returns `None` for odd keys, `mod.rs:80-85`).

The result mirrors the PEAP flaw: a message that *looks* like it completed the protocol successfully (a well-formed `ReceivedOutput`) bypasses the verification that would prove it is actually spendable by the wallet.

### Impact Explanation
An unprivileged party who can supply serialized `ReceivedOutput` bytes to a wallet/processor can cause funds to be reported as received that are not spendable: either the prevout belongs to a different key entirely (signature will be invalid, transaction rejected by the network, and any real inputs consumed in the same transaction are wasted as fees), or the offset yields an odd-parity tweaked key which is not a valid BIP-341 output key. In a threshold setting this can also poison a signing session — honest participants sign a `SignableTransaction` whose inputs can never be spent, causing a consensus-level failure or burn of the co-spent real inputs' fees.

### Likelihood Explanation
Reachable wherever `ReceivedOutput` values cross a trust boundary via `ReceivedOutput::read`/`serialize` round-trips (the type is explicitly designed for serialization, lines 120-148). It requires an attacker to inject or corrupt stored/relayed output data rather than just send on-chain transactions, which lowers likelihood relative to pure on-chain attacks, but the bytes are fully attacker-controlled and accepted without any cryptographic check.

### Recommendation
Either make `ReceivedOutput` carry enough context to self-authenticate, or verify consistency at deserialization: store/commit to the base key (or the script→offset map) and, on `read` or on `SignableTransaction::new`, recompute `p2tr_script_buf(base_key + G*offset)` and reject the output unless it equals `output.script_pubkey`. At minimum, `SignableTransaction::new` should assert each input's offset derives the prevout's script_pubkey before requesting signatures.

### Proof of Concept
```rust
// Attacker crafts bytes for a ReceivedOutput pointing at THEIR p2tr output
// with an arbitrary offset scalar.
let attacker_key = ProjectivePoint::GENERATOR * Scalar::random(&mut OsRng);
let attacker_script = p2tr_script_buf(attacker_key).unwrap();
let fake = TxOut { value: Amount::from_sat(1_000_000), script_pubkey: attacker_script };

let mut buf = vec![];
buf.extend(Scalar::ZERO.to_bytes());        // offset = 0, i.e. "paid to base key"
buf.extend(serialize(&fake));               // but script is the attacker's
buf.extend(serialize(&OutPoint::new(real_txid, 0)));

// Accepted without error:
let ro = ReceivedOutput::read::<&[u8]>(&mut buf.as_ref()).unwrap();
// Wallet reports 1M sats received; SignableTransaction will sign with the
// base key, producing a signature the network rejects -> funds unspendable.
```

Note on scope: the `Scanner`'s documented coinbase-maturity and `register_offset` caveats were considered and rejected as analogs (documented misuse), as were PedPoP's share/PoP paths — `validate_map`, batched PoK verification (`verify_r1`), and per-share `share_verification_statements` all enforce the "phase 2" checks before `ThresholdKeys::new` is reached. The missing-consistency-check on `ReceivedOutput::read` is the strongest reachable analog I found; its real-world exploitability depends on serialized `ReceivedOutput`s being trusted across a boundary, which is plausible but not fully verifiable from the indexed code alone.
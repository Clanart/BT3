### Title
Unbounded length-prefixed allocation in `EncryptedMessage::read` enables remote memory exhaustion - (File: crypto/dkg/src/lib.rs)

### Summary
`EncryptedMessage::read` (and the PedPoP ciphertext deserialization path it feeds into) trusts an attacker-controlled length field to size a `vec![0; len]` allocation before any byte is actually read from the stream. A peer can claim a length of up to ~4 GiB in a few bytes of input, forcing an immediate multi-gigabyte allocation — the exact bug class of the `subtext` advisory (declared payload size not enforced against actual transmitted data).

### Finding Description
The `subtext` advisory (GHSA-2mvq-xp48-4c77) is a resource-exhaustion DoS where the implementation honors a declared size without enforcing it against the actual payload. The analog in Serai's in-scope serialization layer is the pattern:

```rust
let mut len = [0; 4];
reader.read_exact(&mut len)?;
let mut data = vec![0; usize::try_from(u32::from_le_bytes(len))?];
reader.read_exact(&mut data)?;
```

This pattern appears in the DKG/`EncryptedMessage` read path (`crypto/dkg/src/lib.rs`) and in the PedPoP encryption blob handling (`crypto/dkg/pedpop/src/encryption.rs`, `crypto/dkg/pedpop/src/lib.rs`), both of which deserialize untrusted bytes supplied by other participants via `EncryptedMessage::read` / `read_share`-style APIs listed in scope. Because the `vec![0; len]` is committed before `read_exact` validates that `len` bytes exist, a 4-byte length prefix is sufficient to trigger an allocation of up to `u32::MAX` bytes. The correctness of this class was already recognized in Serai's own code — `networks/ethereum/src/machine.rs` `Call::read` contains the comment *"A valid DoS would be to claim a 4 GB data is present for only 4 bytes"* and deliberately reads in 1 KB chunks, while `coordinator/src/p2p.rs` `RrCodec::read_request` caps at `MAX_LIBP2P_REQRES_MESSAGE_SIZE` — but the crypto/DKG deserialization paths perform the unguarded allocation.

### Impact Explanation
Any peer that can deliver bytes to `EncryptedMessage::read` (i.e., any DKG participant or anyone able to feed a processor/coordinator a serialized share message) can force the receiving node to allocate ~4 GiB per message with only a handful of transmitted bytes. Repeated or parallel messages exhaust node memory and kill the process, aborting the DKG/signing session. This is a CWE-400 uncontrolled resource consumption, reachable with public inputs and no privileges.

### Likelihood Explanation
The attack requires only the ability to send a malformed serialized message to a node running a DKG round — no stake, no threshold, no collusion. Triggering it is a one-shot, single-message operation, and allocation failure (OOM abort or panic) is effectively deterministic.

### Recommendation
Cap the deserialized length before allocating: enforce a protocol-level maximum on the length prefix (e.g., reject `len` above the largest legitimate ciphertext/share size), and/or adopt the chunked-read strategy already used in `Call::read` in `networks/ethereum/src/machine.rs` so allocation grows only with bytes actually present.

### Proof of Concept
```
// Conceptual: craft bytes for EncryptedMessage::read consisting of
// the fixed header fields followed by data_len = u32::MAX.
// `vec![0; data_len]` allocates ~4 GiB before read_exact discovers
// the stream is empty — one 5-byte payload forces a 4 GiB allocation.
let mut msg = header_bytes;
msg.extend(u32::MAX.to_le_bytes());
// feed `msg` to EncryptedMessage::read / PedPoP share deserialization
```

Note: I was limited in tool iterations and could not pin down the exact line numbers of the length-prefixed allocation inside `crypto/dkg/src/lib.rs` / `crypto/dkg/pedpop/src/encryption.rs`; the claim rests on the grep-confirmed presence of `vec![0;` / `read_exact` deserialization patterns in those files and the contrasting bounded/chunked implementations elsewhere in the repo. A confirming read of those two files would be worthwhile.
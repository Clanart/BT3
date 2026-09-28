### Title
Attacker-controlled participant count in `ThresholdKeys::read` causes unbounded memory-allocation DoS before validation - ([File: crypto/dkg/src/lib.rs](crypto/dkg/src/lib.rs))

### Summary
The reported bug class is a blocking/resource-heavy operation performed in a context where it must not block (a tasklet calling a sleeping function, causing a deadlock / availability failure). The Serai analog is an allocation whose size is fully controlled by untrusted input bytes, performed during deserialization before any semantic validation, reachable via `ThresholdKeys::read`. A ~20-byte input can force a multi-megabyte allocation attempt plus tens of thousands of field/group element reads, amplifying attacker bandwidth into disproportionate memory and CPU consumption on the victim.

### Finding Description
`ThresholdKeys::<C>::read` reads the threshold parameters `t`, `n`, `i` directly from the byte stream (lines 591–602). It then, for `Interpolation::Constant`, executes `Vec::with_capacity(usize::from(n))` (line 608) and pushes `n` scalars read from the stream (lines 609–611), and subsequently loops `for l in 1..=n` inserting a `read_G` result per participant into a `HashMap` (lines 620–623). Crucially, `ThresholdParams::new(t, n, i)` — the only place `t`/`n`/`i` are sanity-checked — is invoked only at line 625, *after* the attacker-controlled `n` has already driven the `with_capacity` allocation and both read loops.

`n` is a `u16`, so a peer can claim up to 65535 participants. `Vec::with_capacity(65535)` on a `C::F` element (e.g., a 32-byte-plus field element) requests roughly 2 MB+ up front, and the `1..=n` loop performs up to 65535 `read_G` decodings (each doing a `from_bytes` plus canonicality re-encode comparison in `Ciphersuite::read_G`, crypto/ciphersuite/src/lib.rs:91-100). The actual `read_exact` calls will terminate early if the stream ends, but the `with_capacity` allocation happens immediately, and a sender who supplies the matching bytes forces the full O(n) point-decoding work — all with a claimed participant count that may bear no relation to any real validator set.

### Impact Explanation
An unprivileged party who can feed bytes to `ThresholdKeys::read` (deserialization of key material received over the wire, loaded from an untrusted source, or processed during key-reshare/recovery flows where serialized `ThresholdKeys` circulate) can cause repeated large allocations and large decode loops per small request. Repeated requests exhaust memory or stall the thread performing deserialization — the same availability impact class as the VMCI deadlock (a context that must make progress is made to block on attacker-controlled work). No key material is leaked and no forgery is enabled; impact is availability only. Severity: Medium, matching the source advisory's availability-only CVSS profile.

### Likelihood Explanation
Reachability is conditional: it requires an integrator to call `ThresholdKeys::read` on data influenced by an untrusted party. That is exactly the listed in-scope reachability ("untrusted bytes fed to ... `ThresholdKeys::read`"), and serialized threshold keys are transmitted/stored between participants during DKG, promotion (`dkg/promote`), and recovery (`dkg/recovery`). The primitive itself (unchecked `u16` driving `with_capacity` and loops before `ThresholdParams::new` validation) is unconditionally present in the code; exploitation cost is trivial — a few dozen crafted bytes.

### Recommendation
In `ThresholdKeys::<C>::read` (crypto/dkg/src/lib.rs):

- Validate parameters before allocating: construct/check `ThresholdParams::new(t, n, i)` immediately after reading `t`, `n`, `i` (before the `Interpolation` match), rejecting `n == 0`, `t > n`, `i > n`, etc.
- Do not call `Vec::with_capacity(n)`/`HashMap` loops sized by the raw `n`; either cap `n` at a protocol maximum (e.g., `MAX_PARTICIPANTS`) before use, or push into an empty `Vec`/`HashMap` so allocation grows only with bytes actually present (the same chunked-read approach used in `networks/ethereum/src/machine.rs` `Call::read`, which explicitly documents this DoS pattern at lines 51–60).
- Apply the same ordering to any sibling readers that size collections from on-wire counts.

### Proof of Concept
```rust
use std::io;
use ciphersuite::Ciphersuite;
use dkg::{ThresholdKeys, Participant};
use frost::curve::Ed25519; // any Ciphersuite impl

#[test]
fn threshold_keys_read_allocation_dos() {
    // Crafted stream: valid curve ID framing, then t, n = u16::MAX, i = 1,
    // interpolation = Constant — far fewer bytes than n * 32.
    let mut bytes = vec![];
    bytes.extend((Ed25519::ID.len() as u32).to_le_bytes());
    bytes.extend(Ed25519::ID);
    bytes.extend(1u16.to_le_bytes());            // t = 1
    bytes.extend(u16::MAX.to_le_bytes());        // n = 65535
    bytes.extend(1u16.to_le_bytes());            // i = 1
    bytes.push(0);                               // Interpolation::Constant

    // Vec::with_capacity(65535) for C::F (~2 MB) executes here — with only
    // ~15 attacker bytes — before ThresholdParams::new ever validates n.
    // Supplying n * 32 bytes additionally forces 65535 scalar decodes plus
    // 65535 read_G decodings in the verification_shares loop.
    let _ = ThresholdKeys::<Ed25519>::read::<&[u8]>(&mut bytes.as_ref());
}
```

Caveat: the allocation in `Vec::with_capacity` is reclaimed when the subsequent `read_exact` hits EOF and the `Result` is dropped, so the sustained-DoS variant requires the attacker to actually stream `n`-proportioned bytes — at which point the cost is the O(n) `read_G` work per ~32 bytes of input plus the upfront `with_capacity` burst. The unambiguous defect is performing attacker-sized allocation/loops before `ThresholdParams::new` validation at crypto/dkg/src/lib.rs:604-626.
### Title
Unbounded participant-count allocation and parse loop in `ThresholdKeys::read` enables denial of service via crafted serialization - ([File: crypto/dkg/src/lib.rs](crypto/dkg/src/lib.rs))

### Summary
`ThresholdKeys::<C>::read` trusts the attacker-controlled `n` field (a raw `u16`) in the serialized key blob before validating anything else. It uses `n` to (a) pre-allocate a `Vec` of `n` scalar elements for `Interpolation::Constant`, and (b) drive two parse loops that each execute `n` canonical `read_F` / `read_G` decodings. A small crafted buffer therefore forces megabytes of allocation and tens of thousands of point-deserialization attempts, enabling resource-exhaustion denial of service wherever untrusted bytes are fed to `ThresholdKeys::read`. This mirrors CVE-2017-14326's class: a deserialize-path resource-consumption DoS triggered by a crafted input.

### Finding Description
The `t`, `n`, `i` header fields are read straight from the stream with no bound other than `u16` range (crypto/dkg/src/lib.rs:591-602). `n` is then used in three ways before `ThresholdParams::new` ever runs:

1. `Interpolation::Constant` does `Vec::with_capacity(usize::from(n))` then pushes `n` `read_F` results (crypto/dkg/src/lib.rs:607-613). With `n = 65535`, a single call pre-allocates ~2-4 MB (32-64 B per `C::F`) off a ~13-byte header.
2. The verification-share map is filled by looping `read_G` `n` times (crypto/dkg/src/lib.rs:620-623), so even with `Interpolation::Lagrange` a truncated input still burns up to 65535 point-decoding attempts.

Only after this work does `ThresholdParams::new(t, n, i)` reject structurally invalid params (crypto/dkg/src/lib.rs:625-631) — the expensive work precedes the validation. There is also no check that `t <= n` or that `i <= n` prior to parsing, and no cap on the serialized length. Compare `SchnorrAggregate::read` (crypto/schnorr/src/aggregate.rs:77-88), which reads a `u32` element count — same pattern, wider field — though it does not pre-allocate.

### Impact Explanation
Any component that calls `ThresholdKeys::read` on peer-supplied or otherwise untrusted bytes (a listed reachable API in scope) can be forced into repeated multi-megabyte transient allocations and ~130k field/point decode operations per message. Repeated crafted messages cause CPU burn and allocation churn, degrading or stalling the host process — an availability loss consistent with the Medium-severity, `A:H` shape of CVE-2017-14326. No secret material or signature integrity is at stake; the impact is availability only.

### Likelihood Explanation
Reachability requires an attacker to cause a victim to run `ThresholdKeys::read` (or a caller wrapping it, e.g. deserialization of key material supplied over a wire/restore path) on bytes they control. That is within the permitted reach model (untrusted bytes fed to `ThresholdKeys::read`), but the effect is a transient allocation that is freed on error, not a retained leak, and each malicious input is bounded to ~65535 iterations and ~4 MB. That bounds severity at Medium rather than High.

### Recommendation
Validate `ThresholdParams::new(t, n, i)` immediately after reading the header and before any allocation or parse loop, so structurally invalid `n` is rejected up front. Additionally, cap `n` at a protocol-meaningful bound (e.g. `MAX_KEY_SHARES_PER_SET`), and avoid `Vec::with_capacity(n)` based on unverified input — either read in bounded chunks or grow the vector only as elements are successfully parsed, matching the chunked-read pattern used in `Call::read` (networks/ethereum/src/machine.rs:51-60).

### Proof of Concept
Conceptual PoC against `ThresholdKeys::<Ristretto>::read` (as exercised in crypto/frost/src/tests/vectors.rs:107-131, which shows the wire format):

```rust
// Serialized ThresholdKeys prefix:
//   [4B id_len][C::ID][2B t][2B n][2B i][1B interpolation][...]
let mut buf = vec![];
buf.extend(u32::try_from(Ristretto::ID.len()).unwrap().to_le_bytes());
buf.extend(Ristretto::ID);
buf.extend(1u16.to_le_bytes());        // t
buf.extend(u16::MAX.to_le_bytes());    // n = 65535
buf.extend(1u16.to_le_bytes());        // i
buf.push(0);                           // Interpolation::Constant -> Vec::with_capacity(65535)
// No further bytes needed: the call already allocated ~2-4 MB and now
// loops attempting 65535 canonical scalar decodes before failing.
let _ = ThresholdKeys::<Ristretto>::read(&mut buf.as_slice());
```

Each invocation consumes the allocation and decode work; an attacker streaming such messages forces sustained memory/CPU exhaustion. Caveat: I did not fully verify every caller path that exposes `ThresholdKeys::read` to remote bytes; reachability is asserted per the scope's own list of untrusted-byte entry points, and the allocation/loop behavior itself is confirmed by the source at crypto/dkg/src/lib.rs:574-631.
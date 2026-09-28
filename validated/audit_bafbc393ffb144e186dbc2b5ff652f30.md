### Title
Attacker-controlled `n` in `ThresholdKeys::read` enables unbounded pre-validation memory allocation leading to denial of service - (File: crypto/dkg/src/lib.rs)

### Summary
`ThresholdKeys::<C>::read` in `crypto/dkg/src/lib.rs` deserializes the threshold parameters `t`, `n`, and `i` directly from the byte stream, then immediately performs `Vec::with_capacity(usize::from(n))` and allocates a `HashMap` sized by `n` — all before `ThresholdParams::new(t, n, i)` is invoked to validate the values. An attacker can supply `n = 65535` in a message only a few bytes long, forcing a ~2 MB allocation (plus a `HashMap` populated in a 65535-iteration loop) per deserialization attempt. Repeated submissions cause memory exhaustion, analogous to the unbounded buffer-management flaw described in CVE-2024-33862 / CWE-770.

### Finding Description
At `crypto/dkg/src/lib.rs:574-632`, `ThresholdKeys::read` reads three `u16` values (`t`, `n`, `i`) from untrusted bytes. It then:

1. Executes `Vec::with_capacity(usize::from(n))` (line 608) for `Interpolation::Constant` — allocating `n * size_of::<C::F>()` (~2 MB for `n = 65535`) purely based on the declared count, before a single element is read and before `n` is validated.
2. Loops `for _ in 0 .. n` calling `C::read_F` (lines 609-611) and then `for l in (1 ..= n)` calling `read_G` (lines 621-623), performing up to 65535 group-element deserialization attempts per message.
3. Only at line 625 does `ThresholdParams::new(t, n, i)` validate `t <= n` and the participant index — after all allocation and parsing work has occurred.

`ThresholdKeys::read` is explicitly an untrusted-input sink: it is invoked from `processor/src/key_gen.rs`, `processor/src/signer.rs`, and `coordinator/src/p2p.rs` (the libp2p request/response codec in `coordinator/src/p2p.rs:256-273` passes remote peer bytes into these deserializers). An unprivileged peer sending a ~10-byte prefix declaring `n = 0xFFFF` causes a multi-megabyte allocation and up to 131070 field/group read attempts before any validation rejects the parameters.

### Impact Explanation
A remote, unauthenticated peer can force repeated large heap allocations and heavy deserialization loops on validators/coordinators by sending short messages with a maximal `n`. Memory grows proportionally to message rate rather than message size (allocation precedes the `read_exact` failures that would bound the loop by available bytes), enabling resource-exhaustion denial of service on nodes handling incoming key-material messages — mirroring the "excessive messages exhaust memory" pattern of the reference advisory.

### Likelihood Explanation
The reachability path is a `read`/`deserialize` of `ThresholdKeys` on bytes received over the coordinator's libp2p channel (`coordinator/src/p2p.rs`), which accepts remote requests bounded only by `MAX_LIBP2P_REQRES_MESSAGE_SIZE` — no lower bound prevents a tiny malicious payload. The vulnerability requires no authentication, no valid signature, and no protocol-state prerequisites; any `Interpolation::Constant`-tagged prefix with `n = 65535` triggers the allocation. Likelihood is moderate-to-high since the read path is directly exposed to peer input.

### Recommendation
Validate `t`, `n`, and `i` via `ThresholdParams::new` (or at minimum bounds-check `n` against a sane protocol maximum and against remaining input length) *before* performing `Vec::with_capacity(n)` or entering the `read_F`/`read_G` loops in `crypto/dkg/src/lib.rs`. Reorder so parameter validation occurs immediately after reading the three `u16`s, and size allocations based on validated parameters or the actual bytes available.

### Proof of Concept
Conceptually, a peer sends a serialized `ThresholdKeys` blob: `id_len`/`id` matching `C::ID`, followed by `t = 1`, `n = 0xFFFF` (65535), `i = 1`, `interpolation = 0` (Constant) — roughly a dozen bytes total. `ThresholdKeys::read` executes `Vec::with_capacity(65535)` (~2 MB) and begins the `0 .. 65535` `read_F` loop before `ThresholdParams::new` ever runs. Streaming many such messages exhausts node memory, satisfying CWE-770 allocation-without-limits on a reachable untrusted-deserialization path.
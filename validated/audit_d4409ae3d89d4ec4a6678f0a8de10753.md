### Title
`ThresholdKeys::read` deserializes attacker-controlled key material without semantic validation, allowing identity verification shares and an inconsistent secret share to be loaded as a "trusted" signing key - (File: crypto/dkg/src/lib.rs)

### Summary
The Artemis advisory is a trusted-class-loading bug: attacker-supplied artifacts are loaded into packages the security layer blindly trusts. The Serai analog is `ThresholdKeys::read` (`crypto/dkg/src/lib.rs:574-632`), which reconstructs a fully "trusted" `ThresholdKeys` object from raw bytes while performing only syntactic checks. Two semantic invariants are never enforced:

1. Verification shares are read with `<C as Ciphersuite>::read_G` (line 622), which accepts the identity point — only `Curve::read_G` rejects identity (`crypto/frost/src/curve/mod.rs:125-131`), and it is not used here.
2. `ThresholdKeys::new` (`crypto/dkg/src/lib.rs:349-391`) never checks that `secret_share` is consistent with `verification_shares[i]`, nor that any verification share is non-identity. The FROST `complete` path itself acknowledges this: "The only known way to cause this … is to deserialize a semantically invalid FrostKeys" (`crypto/frost/src/sign.rs:491-494`).

### Finding Description
`ThresholdKeys::read` parses `t`, `n`, `i`, an interpolation blob, a raw secret-share scalar, and `n` group elements, then hands them to `ThresholdKeys::new`. `ThresholdParams::new` checks only `t <= n` and `i <= n` (lines 166-179). `ThresholdKeys::new` checks only the share *count* and that `Constant` interpolation implies `t == n` (lines 355-374), then derives `group_key` by interpolating verification shares `1..=t` (lines 376-378). Nothing verifies:

- `verification_shares[l] != identity` for any `l`,
- `interpolation_factor * secret_share * G == verification_shares[i]` under the declared interpolation,
- that `group_key` itself is non-identity.

An attacker who supplies the serialized key blob (an explicitly in-scope input) can craft `t == n` `Constant` keys where every verification share is the identity and `secret_share == 0`. The resulting object loads without error, has `group_key == identity`, and is accepted by `AlgorithmMachine::new` as a legitimate signing key.

### Impact Explanation
With `group_key = identity` (possibly plus an ephemeral `offset` added at use sites via `ThresholdKeys::offset`, which is also just `offset * G`), the loaded "threshold" key has discrete log known to the attacker (0, or `offset`). The honest node will run the full FROST protocol — preprocess, sign, complete — under this degenerate key. Because `secret_share == 0` is consistent with `verification_share == identity` (`0 * G == identity`), `verify_share` statements hold and `complete` can return a signature instead of erroring. The net effect is that the node signs attacker-chosen messages under a key whose private key the attacker knows, and any downstream system (e.g., bitcoin-serai `Scanner`/wallet crediting outputs to `group_key`-derived addresses) will report funds as received at an address anyone can spend from — funds reported received that are not spendable by the honest set.

### Likelihood Explanation
Requires the attacker to control the bytes passed to `ThresholdKeys::read` (corrupted/malicious keystore blob or any protocol path that round-trips key material through `serialize`/`read` with attacker-influenced contents). It does not require a threshold of malicious validators, broken BFT, or leaked keys — the trust failure is entirely inside the deserializer, mirroring the Artemis bug where the trusted loader accepted attacker-placed classes.

### Recommendation
In `ThresholdKeys::new` (or `ThresholdKeys::read`), reject identity verification shares and identity `group_key`, and verify `secret_share * interpolation_factor(i, 1..=t) * G == verification_shares[i]` (i.e., that the secret share matches the declared verification share). This makes deserialization enforce the same semantic invariants the rest of the protocol assumes.

### Proof of Concept
1. Build a blob: `C::ID`, `t = n = 2`, `i = 1`, interpolation byte `0` (Constant), two scalar coefficients `c1 = c2 = 1`, `secret_share = 0` (canonical encoding of zero passes `read_F`), and two encodings of the identity point (canonical, so `Ciphersuite::read_G` accepts them).
2. Feed to `ThresholdKeys::<C>::read`. `ThresholdKeys::new` computes `group_key = id*1 + id*1 = identity` and returns `Ok`.
3. Instantiate `AlgorithmMachine::new(algorithm, keys)` and run `preprocess`/`sign`/`complete` for an attacker-chosen `msg`. Because `secret_share * λ * G == identity == verification_share`, share verification succeeds and `complete` emits a signature valid under public key `identity` — a key for which the attacker trivially knows the discrete log and can produce arbitrary signatures for any message, including ones the node never saw.
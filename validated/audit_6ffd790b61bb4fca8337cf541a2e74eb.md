### Title
`ThresholdKeys::read` accepts a secret share incoherent with the deserialized verification shares, yielding a `group_key` the holder can never sign for - (File: crypto/dkg/src/lib.rs)

### Summary
`ThresholdKeys` stores two representations of the same secret material — `secret_share` (scalar) and `verification_shares` (points) — plus a `group_key` derived from the verification shares. Analogous to `Edition.setFeeStrategy()` updating `strategy.royaltyBps` without updating the ERC2981 value that actually governs payments, `ThresholdKeys::new` derives `group_key` solely from `verification_shares[1..=t]` and never checks that the provided `secret_share` corresponds to `verification_shares[i]` or to that group key. `ThresholdKeys::read` feeds fully attacker-controlled bytes (t, n, i, interpolation, `secret_share`, all `verification_shares`) straight into `ThresholdKeys::new`, so a malformed blob produces a `ThresholdKeys` whose reported `group_key()` is unrelated to the key the `secret_share` can actually sign for.

### Finding Description
In `crypto/dkg/src/lib.rs`, `ThresholdKeys::new` computes the group key as `sum(verification_shares[i] * interpolation_factor(i, {1..=t}))` (lines 376-378) and stores it without any consistency check against `secret_share`. `ThresholdKeys::read` (lines 573-632) deserializes every field from untrusted bytes — including the `secret_share` via `C::read_F` (line 618) and the `verification_shares` via `read_G` (line 622) — then calls `ThresholdKeys::new` and returns the result.

Consequences downstream:

- `group_key()` returns `core.group_key * scalar + G * offset`, i.e. a key derived only from the attacker-chosen verification shares (lines 445-447). Any consumer that asks the loaded keys "what is our group key?" (e.g. to derive a Bitcoin address / scanner key) gets a key the holder's share does not correspond to.
- `view()` interpolates `secret_share` for signing (lines 493-521), so `sign_share` produces shares that will not verify against `view.verification_share(i)` nor aggregate to a signature under `group_key()`. `AlgorithmSignatureMachine::complete` even has a dedicated unreachable-in-theory path acknowledging this: "The only known way to cause this ... is to deserialize a semantically invalid FrostKeys" (`crypto/frost/src/sign.rs:491-494`).
- `recover_key` in `crypto/dkg/recovery/src/lib.rs` only catches the inconsistency at the very end via `C::generator() * res != group_key` (lines 80-82); nothing prevents creating or using the incoherent keys for signing/scanning first.

The DKGs (dealer, PedPoP, MuSig, promote) always construct consistent inputs, so this is only triggerable through `ThresholdKeys::read` on attacker-influenced bytes — e.g. a corrupted or maliciously supplied key-share file / network-delivered key blob.

### Impact Explanation
An unprivileged party who can influence the bytes fed to `ThresholdKeys::read` causes a victim to load `ThresholdKeys` where the derived `group_key` and the signable secret share are incoherent — the same "two sources of truth disagree" failure as royaltyBps vs `royaltyInfo()`. If the reported `group_key` is used to receive funds (e.g. passed to `Scanner::new` / a Bitcoin address in `networks/bitcoin`), the outputs are reported received under a key no valid `t`-of-`n` quorum of these shares can spend: signature shares fail `verify_share` and `recover_key` aborts with `Failure`. Funds are permanently unspendable, and every signing attempt deterministically blames honest participants (`InvalidShare`).

### Likelihood Explanation
Requires an attacker to supply or tamper with serialized `ThresholdKeys` bytes consumed by the victim. `ThresholdKeys::read` performs no integrity check (no MAC/commitment over `secret_share` vs `verification_shares`), so any channel delivering these bytes untrusted suffices. Where key material is provisioned by an untrusted coordinator or stored in attacker-writable storage, this is directly reachable; where the blob is fully trusted, it reduces to a robustness gap. Medium.

### Recommendation
In `ThresholdKeys::new` (or at minimum in `ThresholdKeys::read`), verify coherence between the secret and its public commitment: check `C::generator() * secret_share == verification_shares[&params.i()]` and reject with a new `DkgError` variant on mismatch. For Lagrange interpolation this also guarantees `group_key` consistency whenever the shares are honestly constructed; optionally also verify that `secret_share` is a valid interpolation of `verification_shares` by checking `verification_shares[i] == interpolation_factor-weighted recombination` is not needed beyond the per-share check plus the existing `view`-time interpolation.

### Proof of Concept
```rust
// Craft bytes: t=2, n=3, i=1, Lagrange interpolation,
// secret_share = random s, verification_shares = {1: A1, 2: A2, 3: A3}
// where A_j are attacker-chosen points unrelated to s.
let keys = ThresholdKeys::<C>::read(&mut blob).unwrap();
// Incoherent derived state, exactly like royaltyBps vs royaltyInfo:
let reported = keys.group_key();              // derived only from A1, A2
assert_ne!(C::generator() * keys.original_secret_share().deref(),
           keys.original_verification_share(Participant::new(1).unwrap()));
// Any signature share produced under `reported` fails verification,
// and recover_key returns RecoveryError::Failure — the group key
// "received funds" for is unspendable by these shares.
```
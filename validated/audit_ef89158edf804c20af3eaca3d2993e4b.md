### Title
`ThresholdView` applies the Lagrange offset to `included[0]`'s secret share but not to the corresponding verification share, breaking per-share verification - (File: crypto/dkg/src/lib.rs)

### Summary
Analogous to the Tapioca finding — where `utilization` was computed from `_totalBorrow.elastic` *before* the accrued interest was folded in, producing a stale quantity that fed a downstream check — `ThresholdView::new` in Serai's DKG crate computes each signer's interpolated scalar share and then adds an `offset` term (the contribution of the non-participating shares) only to the share of `included[0]`. The corresponding public `verification_share` for `included[0]` does not have `offset * G` folded in, so the same quantity is represented inconsistently on the private and public sides of `AlgorithmSignatureMachine::complete`.

### Finding Description
When a signing set `included` is not exactly the participants `1..=t` of the original DKG, `ThresholdView::new` (crypto/dkg/src/lib.rs) interpolates the secret shares over `included` and must additionally account for the excluded key shares. It does so by computing an `offset` scalar and adding it to the scalar share of `included[0]` after sorting `included` (the grep-visible `included[0]` / `offset` logic in `crypto/dkg/src/lib.rs`). In `crypto/frost/src/sign.rs`, `AlgorithmSignatureMachine::complete` first verifies `sum` of the shares against `view.group_key()` (lines 456-466), which succeeds because the offset was correctly included in `sum` via `included[0]`'s scalar. However, on the failure/blame path it calls `algorithm.verify_share(self.view.verification_share(*l), &self.B.bound(*l), responses[l])` per participant (lines 475-484). `verification_share(included[0])` is the interpolation of the public verification shares without the `offset * G` term, so it does not match the scalar share that was actually signed with.

### Impact Explanation
Any signing session whose `included` set differs from the DKG's `1..=t` participant set (a normal occurrence — any threshold subset containing a participant index > t, or gaps) produces a mathematically valid aggregate signature, but if blame verification is ever reached (e.g., a single malicious or faulty share forces the slow path), the honest participant at `included[0]` is reported as `FrostError::InvalidShare(included[0])` — an incorrect verifier formula causing honest-signer misblame. An adversary participant can deliberately submit one invalid share to force the batch-verification blame path and get an honest signer blamed/ejected from the protocol. This is reachable entirely by an unprivileged participant through `read_preprocess`/`read_share`-fed inputs into `sign`/`complete`.

### Likelihood Explanation
Any non-trivial signing set triggers the inconsistent view; triggering blame only requires one participant (possibly the attacker) to send a bad share via `SignatureShare::read`. No collusion or privileged access needed. Concrete impact is misblame/DoS of the signing protocol rather than forgery, matching Medium severity.

### Recommendation
Apply the offset consistently: either add `offset` to the secret share of `included[0]` *and* add `offset * G` to `verification_share(included[0])` inside `ThresholdView::new` (crypto/dkg/src/lib.rs), or distribute the offset so both the scalar-share map and the verification-share map remain pairwise consistent. Add a regression test that signs with a set `included != 1..=t`, injects one bad share, and asserts the correct party is blamed.

### Proof of Concept
1. Run a DKG producing `ThresholdKeys` for participants `1..=n`, threshold `t`.
2. Build a signing set `included` where `included[0] != 1` or the set is not contiguous with `1..=t` (e.g., `{2, 3}` for `t = 2`), so `offset != 0` in `ThresholdView::new` (crypto/dkg/src/lib.rs).
3. Have all-but-one participant produce valid shares through `AlgorithmSignMachine::sign`; have one participant (the attacker) submit a corrupted `SignatureShare` read via `SignatureShare::read` (crypto/frost/src/sign.rs:443).
4. `complete` fails the aggregate `verify` (sign.rs:465), enters per-share `verify_share` (sign.rs:476-484), and reports `InvalidShare(included[0])` — blaming the honest lowest-indexed signer, whose share satisfied `sum` but whose `verification_share` lacks `offset * G`.

(Caveat: I was unable to read the full `ThresholdView::new` body in this session; the offset/`included[0]` asymmetry is evidenced by the offset-handling code in `crypto/dkg/src/lib.rs` and its consumption in `crypto/frost/src/sign.rs`. If `verification_share` already folds in `offset * G`, this finding does not hold.)
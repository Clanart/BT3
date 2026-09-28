### Title
`SchnorrAggregate::verify` returns `true` for an empty aggregate (zero signers, `s = 0`), accepting a forged aggregate signature - ([File: crypto/schnorr/src/aggregate.rs](crypto/schnorr/src/aggregate.rs))

### Summary
The Stader bug class is: a component left uninitialized lets an attacker steer subsequent calls so they are *treated as successful without any real work being done*. In Serai, the reachable analog lives in `crypto/schnorr`: `SchnorrAggregate::verify` verifies the multiexp equation `sum(z_i·R_i + z_i·c_i·A_i) - s·G == 0`. When `keys_and_challenges` is empty and `self.Rs` is empty, the pairs list reduces to the single pair `(-s, generator)`, which sums to identity whenever `s == 0`. An unprivileged party can therefore supply `SchnorrAggregate { Rs: [], s: 0 }` — four zero bytes for `len` followed by a zero scalar — and have `verify` return `true` for *any* DST and any message, with no signer having produced anything.

### Finding Description
`SchnorrAggregate::read` (crypto/schnorr/src/aggregate.rs:77-88) reads a `u32` count, that many points via `C::read_G`, and a scalar. A count of `0` and `s = 0` is accepted. `SchnorrAggregate::verify` (lines 127-146) only checks `self.Rs.len() == keys_and_challenges.len()`, then builds `pairs` of length `2n + 1`. For `n = 0`, `pairs = [(-self.s, C::generator())]`, and `multiexp_vartime(&pairs).is_identity()` is true iff `s == 0`. Note the asymmetry: the producer side `SchnorrAggregator::complete` returns `None` when nothing was aggregated (lines 175-178), so an honest aggregator can never emit the empty aggregate — but the verifier happily accepts it. The verifier is stricter on the producing side than on the consuming side, the same shape as the Stader proxy (initializer guarded for honest flows, callable by anyone).

The reachable caller is `Validators::verify_aggregate` in `coordinator/tributary/src/tendermint/mod.rs:200-229`: it reads attacker-controlled bytes via `SchnorrAggregate::read`, checks `signers.len() == aggregate.Rs().len()`, and passes the pairs to `aggregate.verify`. With an empty `signers` slice and `Rs` empty, `verify` returns `true`. Whether the Tendermint layer independently requires quorum weight from the signer set determines end-to-end impact; the cryptographic verifier itself unconditionally accepts the forged empty aggregate, and `crypto/schnorr` is a general-purpose library whose contract is "verify ⇒ some signer produced a valid signature," which is violated.

### Impact Explanation
A forged aggregate signature: `SchnorrAggregate::verify` returns `true` for a signature no participant ever computed. Any downstream consumer relying on `verify`/`verify_aggregate` as proof that "at least the listed signers endorsed this message" is violated for the empty-set edge case — the verifier claims success with zero signatures and zero weight behind it. This mirrors the Stader impact ("calls treated like calls to an EOA and return `true`... though calls to functions on that proxy were never executed"): verification reports success while no verification work was ever bound to a real signer.

### Likelihood Explanation
Any unprivileged party can construct the forgery: 4 bytes of `0x00` for the length plus a canonical zero scalar encoding. No private key, no nonce, no signer cooperation is needed. Exploitability at the Tendermint layer depends on whether an empty `signers` set can be passed to `verify_aggregate` with an otherwise-accepted commit; as a library-level guarantee, the acceptance is unconditional.

### Recommendation
Reject the empty aggregate in `SchnorrAggregate::verify` (e.g., `if self.Rs.is_empty() || self.Rs.len() != keys_and_challenges.len() { return false; }`), and/or reject `Rs.len() == 0` in `SchnorrAggregate::read`. Additionally, consider rejecting `s == 0` / identity-only pair lists. This mirrors the Stader fix of disabling initialization on the implementation contract: the verifier should refuse the degenerate case the producer can never legitimately emit.

### Proof of Concept
```rust
// crypto/schnorr — forged aggregate accepted with zero signers
let forgery = SchnorrAggregate::<Ristretto> { Rs: vec![], s: Scalar::ZERO };
// Any dst, empty key/challenge list:
assert!(forgery.verify(b"any dst", &[]));

// Through the read path (untrusted bytes):
//   bytes = 00 00 00 00 || <32-byte zero scalar>
//   SchnorrAggregate::read(&mut bytes.as_ref()).unwrap().verify(dst, &[]) == true
```
`pairs` collapses to `[(-0, generator)]`, `multiexp_vartime` yields identity, and `verify` returns `true` despite no signature ever having been produced — matching `Validators::verify_aggregate` accepting it whenever the `signers` slice is empty.
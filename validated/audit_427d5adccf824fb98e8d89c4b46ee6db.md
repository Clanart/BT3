### Title
Remote panic (node crash) when a signature share / preprocess references a participant not present in the signing-set maps — ([File: crypto/frost/src/sign.rs](crypto/frost/src/sign.rs), [File: crypto/frost/src/nonce.rs](crypto/frost/src/nonce.rs), [File: crypto/dkg/src/lib.rs](crypto/dkg/src/lib.rs))

### Summary
The bug class of CVE-2019-2693 is a low-privileged network attacker causing a repeatable crash (availability loss) of a server via crafted protocol input. The Serai analog is reachable panics on attacker-controlled `Participant` keys: untrusted preprocesses and signature shares are parsed by `read_preprocess` / `read_share` and then consumed by indexing operations (`self.0[&l]`, `verification_shares[&l]`, `.unwrap()`) that panic instead of returning `FrostError`, crashing the signer/verifier task. This is a reachable denial of service an unauthenticated counterparty can trigger with malformed signing-protocol bytes.

### Finding Description
During the second FROST round, the sign machine builds `included` from the keys of the attacker-supplied `preprocesses: HashMap<Participant, Preprocess>` map and then indexes into maps keyed by `Participant`:

- `ThresholdKeys::view` indexes `self.core.verification_shares[i]` for every `i` in `included` (`crypto/dkg/src/lib.rs:502`), and `original_verification_share` does `self.core.verification_shares[&l]` (`crypto/dkg/src/lib.rs:458`). Any `Participant` in `included` that is `<= n` but has no entry in the map (e.g., keys were deserialized/promoted so the map lacks that index) panics on the `HashMap` index.
- `BindingFactor::bound` / `BindingFactor::nonces` index `self.0[&l]` and `self.0[&i]` (`crypto/frost/src/nonce.rs:176`, `nonce.rs:183`) and unwrap `binding_factors` (`nonce.rs:206`). A preprocess inserted under a `Participant` that was never inserted into the `BindingFactor` map, or whose `binding_factors` were not yet calculated, panics on `unwrap`/index.
- `AlgorithmSignMachine::sign` calls `self.params.keys.view(included.clone()).unwrap()` (`crypto/frost/src/sign.rs:312`). `view` returns `Err` for `IncorrectAmountOfParticipants`, `NotParticipating`, `DuplicatedParticipant`, and `InvalidParticipant` (`crypto/dkg/src/lib.rs:463-491`). The checks in `sign` (lines 297-310) cover most of these, but `view`'s error path also fires when `included.len() > n` vs. the count check performed there, and any residual `Err` becomes a panic via `unwrap` rather than a `FrostError`.
- Downstream, signature shares received over the wire are parsed only as a scalar (`read_share` / `SignatureShare<C>(C::F)`), while the `Participant` key under which they are inserted into the shares map is attacker-chosen protocol metadata. Verification indexes `view.verification_shares()` / the binding map by that key; a share claiming a `Participant` outside the interpolated set hits the same `HashMap` index panic.

Because `Commitments::read` (`crypto/frost/src/nonce.rs:133`) and `NonceCommitments::read` (`nonce.rs:74`) fix lengths from the *local* algorithm's generator list, the byte stream itself cannot be truncated to cause OOB — the panic vector is the attacker-controlled `Participant` identifiers used as map keys, combined with `unwrap`/index instead of error propagation.

### Impact Explanation
A single malformed preprocess or signature-share message — carrying a `Participant` key with no corresponding map entry — panics the FROST sign/verify code path. In the processor this aborts the signing attempt for the whole batch/cosign/slash-report; an attacker who can repeatedly submit such messages (any counterparty able to send preprocess/share bytes, including via relayed coordinator messages) causes a repeatable crash of the signing task, i.e., complete denial of signing availability, matching the CVE's availability-only impact. No secret material is needed and no privilege beyond sending protocol bytes is required.

### Likelihood Explanation
The panic sites use `HashMap` indexing and `unwrap` on values derived from untrusted map keys. Whether every adversarial `Participant` value is pre-filtered depends on `validate_map` and the share-map construction in `complete` (not fully verified within this review); the preprocess path in `sign` does guard participant range and duplicates before `view`, which reduces — but does not eliminate — the surface, since `view` still has fallible branches converted to panics by `unwrap`, and share-side indexing has no equivalent documented guard in `nonce.rs`/`dkg/lib.rs`. Likelihood is therefore conditional on the share-validation gap being real, which is the crux of the finding.

### Recommendation
- Replace all `HashMap` indexing on attacker-derived `Participant` keys (`self.0[&l]`, `verification_shares[&l]`, `verification_shares[i]`) with `get`/`ok_or` returning `FrostError`/`DkgError`.
- Replace `self.params.keys.view(included.clone()).unwrap()` in `crypto/frost/src/sign.rs:312` with `map_err` into `FrostError`, and validate `included.len() <= n` explicitly before calling `view`.
- In share verification/completion, run the equivalent of `validate_map` over the received shares map keys, rejecting any `Participant` not in `included` (and any `> n` or duplicated index) before indexing the binding/verification-share maps.

### Proof of Concept
Conceptual (library-level):

```rust
// 1. Honest signer holds ThresholdKeys and builds an AlgorithmSignMachine.
let (sign_machine, _preprocess) = machine.preprocess(&mut OsRng);

// 2. Attacker sends a preprocess map containing a Participant key for which
//    the local verification_shares / BindingFactor maps have no entry
//    (e.g., a valid-but-absent index <= n after a promoted/regenerated key set,
//    or a share map keyed by a participant outside the agreed signing set).
let mut preprocesses = HashMap::new();
preprocesses.insert(Participant::new(rogue_index).unwrap(), attacker_preprocess_bytes_parsed);

// 3. sign() / complete() reaches:
//      self.core.verification_shares[i]          // dkg/src/lib.rs:502  -> panic
//      self.0[&l].binding_factors.as_ref().unwrap() // nonce.rs:176,206 -> panic
//      keys.view(included).unwrap()              // sign.rs:312         -> panic
let _ = sign_machine.sign(preprocesses, b"msg"); // thread aborts: DoS
```

Concrete trigger bytes: a wire `Preprocess`/`SignatureShare` message wrapped in the coordinator's `SubstratePreprocesses`/share message whose `Participant` field names an index the receiver's maps do not contain; `read_preprocess`/`read_share` succeeds (the byte payload is well-formed), and the panic occurs on map indexing during `sign`/`complete`.

Caveat: I verified the panic-capable indexing sites (`crypto/dkg/src/lib.rs:378,458,502,521`, `crypto/frost/src/nonce.rs:176,183,204-206`, `crypto/frost/src/sign.rs:302-312`) and the attacker-controlled key flow, but did not fully confirm within this review that `AlgorithmSignatureMachine::complete`/`verify_share` lacks a `validate_map` equivalent over share keys — if shares are pre-validated to exactly the `included` set, the reachable vector narrows to the `view(...).unwrap()` and preprocess-key paths.
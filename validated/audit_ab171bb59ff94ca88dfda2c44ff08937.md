### Title
Deterministic reuse of the one-time FROST preprocess seed lets a distinct signing round recover a validator's private key share - (File: coordinator/src/tributary/signing_protocol.rs)

### Summary
`SigningProtocol::preprocess_internal` derives each validator's FROST preprocess (nonce pair) from a 32-byte `CachedPreprocess` seed stored in `CachedPreprocesses`, keyed only by `context`. The seed is generated once per context and is never rotated or deleted, so every call to `share_internal`/`complete` under the same context re-creates the identical secret nonces `d, e`. FROST's security relies on nonces being single-use; the crate's own documentation states that cached-preprocess reuse "will presumably cause the signer to leak their secret share" and the file header admits "it is explicitly unsafe to reuse nonces across signing sessions."

### Finding Description
In `preprocess_internal`, if `CachedPreprocesses::get(txn, context)` already returns a value, that same seed is decrypted and passed to `AlgorithmSignMachine::from_cache`, regenerating the same `nonces` (`crypto/frost/src/sign.rs:268-274`, `seeded_preprocess`). The map key is just `context = (b"DkgConfirmer", attempt)`, with no binding to the signing set, the received preprocesses, or the message.

`DkgConfirmer::share(preprocesses, key_pair)` (line 304) and `DkgConfirmer::complete(preprocesses, key_pair, shares)` (line 312) both re-execute `share_internal` on externally supplied, BFT-ordered inputs. Nothing in `SigningProtocol` records that a signature share was already produced for this context. If `share_internal` is ever invoked a second time with a different signer set, different participant preprocess bytes (which change the binding factors `rho` in `BindingFactor::calculate_binding_factors`, crypto/frost/src/nonce.rs:161-173), or a different `key_pair` (different `msg`), the same `d + rho*e` is signed under a different challenge.

The file's own safety argument (lines 34-48) is that distinct received messages can only occur via a logical flaw or a chain rebuild — i.e., the nonce-safety invariant is delegated entirely to the BFT layer rather than enforced by the signing code. That is exactly the "inappropriate implementation" bug class: a component whose correctness silently depends on an unstated external invariant, such that any divergence in the ordered inputs (a second `set_keys` attempt reusing `attempt`, a re-execution after partial state rebuild, a caller bug feeding a different preprocess map) turns it into a secret-key disclosure. The code itself flags remaining gaps: "we have to check the commitments generated from the decided nonces are in fact its commitments on-chain (TODO)" (line 51) — that check is absent, so a rebuilt or re-executed node does not even detect that it is about to reuse the nonces.

Mathematically: two shares over reused nonces give `s1 - s2 = lambda_i * x_i * (c1 - c2)`, yielding the MuSig/FROST private share `x_i` (and via `musig`, the validator's root-of-trust key share) directly.

### Impact Explanation
Recovery of a validator's Ristretto secret key share for the validator set's MuSig key — the root of trust used to confirm DKG results on Substrate. With a threshold number of leaked shares, the group key is fully compromised. The leaked material is the validator's own long-lived key, not a per-session secret.

### Likelihood Explanation
Reachable purely from on-chain/coordinator inputs: an attacker controls `preprocesses` maps and `key_pair` contents fed to `share`/`complete`. Exploitation requires the context-scoped seed to be reused with differing inputs — which the implementation structurally permits but relies on BFT finality to prevent. This is a Medium-to-High likelihood: the invariant is enforced nowhere in this code, the file itself lists unimplemented TODO safeguards, and any re-execution path (crash recovery between preprocess publication and share publication, duplicate message handling, attempt-number reuse) silently produces reuse rather than an error.

### Recommendation
- Bind the cached seed entry to the full signing inputs (hash of the signer set, preprocesses, and message), and refuse to sign if a second invocation under the same `context` presents different inputs.
- Record a "share published" flag per context and hard-error on any second `sign` call, rather than relying on BFT assumptions.
- Implement the noted TODO: before publishing a share, verify the on-chain preprocess commitments match the locally generated ones.

### Proof of Concept
1. Validator runs `DkgConfirmer` for `attempt = k`; `preprocess()` stores seed `S` in `CachedPreprocesses` for `("DkgConfirmer", k)` and publishes commitments `C_i`.
2. `share(P_1, kp_1)` is invoked with one ordered set of participant preprocesses `P_1` and key pair `kp_1`, producing share `s1` under nonce seed `S`.
3. Any re-execution path (partial rebuild, retry, or a second call) invokes `share(P_2, kp_2)` with `P_2 != P_1` or `kp_2 != kp_1`. `preprocess_internal` returns seed `S` again; identical `d, e` are used.
4. From `s1` and `s2` and the public challenges, compute `x_i = (s1 - s2) / (lambda_i * (c1 - c2))` — full recovery of the validator's MuSig secret share.
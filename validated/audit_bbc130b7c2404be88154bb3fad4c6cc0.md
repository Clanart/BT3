### Title
FROST `Commitments::read` accepts identity nonce commitments, enabling attacker-controlled aggregate nonce and parallel-session secret-share recovery - (File: crypto/frost/src/nonce.rs)

### Summary
`GeneratorCommitments::read` / `NonceCommitments::read` / `Commitments::read` deserialize the peer-supplied hiding/binding commitment pair `(D, E)` via `C::read_G` without rejecting the group identity (`crypto/frost/src/nonce.rs:33-80`, `133-139`). The FROST validation policy requires aborting when any commitment is the identity; here the "policy" is silently not enforced for the identity element — directly analogous to a sanitizer failing to enforce policy on specific elements.

### Finding Description
When a participant calls `SignMachine::read_preprocess` (`crypto/frost/src/sign.rs:276-281`), the peer's `Commitments` are read with `C::read_G`, which for the dalek ciphersuites bottoms out in `GroupEncoding::from_bytes` (`crypto/dalek-ff-group/src/lib.rs:429-437`). That decoder enforces decompression and (for Ed25519) torsion-freeness, but explicitly admits the identity — contrast `Group::random`, which loops to "Ban identity, per the trait specification" (`lib.rs:399-410`). No identity check exists anywhere in `Commitments::read`, `BindingFactor::insert`, or `AlgorithmSignMachine::sign` (`sign.rs:283-411`).

The aggregate nonce is computed as `R = Σ D_l + multiexp(rho_l, E_l)` per nonce/generator (`nonce.rs:194-212`). With `E = identity`, the attacker's binding contribution `rho_l · E` vanishes regardless of `rho_l`, so the attacker controls its contribution to `R` through `D` alone, *after the binding factors are fixed* only in form, not value — the attacker chooses `D` adaptively per signing session while the honest participant's `D`/`E` are committed by their preprocess.

### Impact Explanation
Concretely: an attacker participating in two (or more) concurrent signing sessions with the same victim can set `D_attacker = -Σ_{honest} (D_i + rho_i·E_i) + G·k` for a chosen `k`, i.e. cancel the honest contributions and force the same aggregate `R` across sessions whose messages (and therefore challenges `c`) differ. Because each share is `s = d + rho·e + λ·x·c`, two sessions sharing `R` but differing in `c` yield two linear equations in the victim's secret share `x`, recovering it. This is the standard parallel-session/ROS-style share-reuse attack which identity-commitment rejection (a required validation step in FROST) exists to prevent. Result: full recovery of a threshold participant's private key share — the highest-severity outcome for this code.

### Likelihood Explanation
Requires the attacker to be a signer in the multisig and to induce the victim to sign two attacker-influenced messages using preprocesses the attacker sees — entirely reachable by an unprivileged counterparty supplying public `Preprocess` bytes, within the intended threat model of FROST (all non-victim inputs are adversarial). No collusion, broken BFT, or leaked keys required. `read_preprocess` is explicitly a public-API entry point for untrusted bytes (`sign.rs:226-230`).

### Recommendation
In `Commitments::read` (or `GeneratorCommitments::read`), reject `io::Error` when `D` or `E` is the identity (`point.is_identity()`), matching the RFC's "abort if any commitment is the identity" validation. Additionally reject identity verification shares where shares are deserialized.

### Proof of Concept
1. Attacker `a` and victim `v` run `AlgorithmMachine::preprocess`; `v` broadcasts `Preprocess{(D_v, E_v)}`.
2. Attacker opens two signing sessions on messages `m1 ≠ m2`, both including `v`. In session 1 the attacker submits `E_a = identity` and `D_a` chosen so `R1 = D_v + rho_v·E_v + D_a` equals a target `R`. It computes `rho_v` (derivable: rho transcript commits to all preprocessed commitments — attacker computes it after seeing `v`'s preprocess) then sets `D_a = R - D_v - rho_v·E_v`.
3. In session 2 with a fresh honest preprocess `(D_v', E_v')` — or the same one if the victim reuses — attacker repeats to force `R2 = R`.
4. Shares collected: `s1 = d_v + rho_v·e_v + λ·x_v·c1`, `s2 = d_v' + rho_v'·e_v' + λ·x_v·c2` with identical effective aggregate nonce `R`; solving the linear system (standard FROST parallel-session algebra across the binding-factor difference) recovers `x_v`.

Root cause: `GeneratorCommitments::read` (`nonce.rs:34-36`) trusts `C::read_G`, which accepts the identity (`dalek-ff-group/src/lib.rs:429-437`), and neither `BindingFactor::insert` (`nonce.rs:157-159`) nor `AlgorithmSignMachine::sign` (`sign.rs:283-411`) adds the missing policy check.
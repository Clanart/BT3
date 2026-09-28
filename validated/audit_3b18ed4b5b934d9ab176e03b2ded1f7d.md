### Uninitialized Schnorrkel message state causes a remote denial of service - ([File: crypto/schnorrkel/src/lib.rs](crypto/schnorrkel/src/lib.rs))

### Summary
`Schnorrkel::verify` unconditionally unwraps `self.msg`. The field is only initialized by `sign_share`, while the public `verify` API can be called on a fresh `Schnorrkel` instance. If a caller verifies an attacker-supplied Schnorrkel aggregate/signature result before local signing has initialized the message state, the process panics.

### Finding Description
`Schnorrkel` stores the signed message in `msg: Option<Vec<u8>>`, initialized to `None` by `Schnorrkel::new`. The `verify` implementation later indexes `nonces[0][0]` and calls `self.msg.as_ref().unwrap()` without checking that `sign_share` ran or that a nonce exists.

Relevant flow:

```rust
// crypto/schnorrkel/src/lib.rs
msg: Option<Vec<u8>>,
```

```rust
// crypto/schnorrkel/src/lib.rs
fn sign_share(...) -> Scalar {
  self.msg = Some(msg.to_vec());
  ...
}
```

```rust
// crypto/schnorrkel/src/lib.rs
fn verify(...) -> Option<Self::Signature> {
  let mut sig = (SchnorrSignature::<Ristretto> { R: nonces[0][0], s: sum }).serialize();
  ...
  PublicKey::from_bytes(&group_key.to_bytes())
    .unwrap()
    .verify(&mut signing_context(self.context).bytes(self.msg.as_ref().unwrap()), sig)
}
```

The same issue exists for `nonces[0][0]`: an empty nonce vector causes an out-of-bounds panic before `Option` handling.

### Impact Explanation
This is a reachable null/uninitialized-state dereference analogue: the verifier assumes protocol state has been initialized, but the state is represented by `Option` and indexed collections rather than enforced by the type/API boundary. Triggering it aborts the calling process or task.

The strongest reachable path is through the FROST completion path in `AlgorithmSignatureMachine::complete`, which calls `algorithm.verify(...)`. In normal `AlgorithmSignMachine::sign` flow, `sign_share` has initialized `msg`, so the panic primarily affects direct algorithm misuse, alternate state-machine implementations, recovery/deserialization paths, or any caller that invokes `verify` independently.

### Likelihood Explanation
Low-to-medium. The core `AlgorithmSignMachine` sequence normally initializes `msg` before `complete` reaches `verify`. However, the API does not enforce that ordering, and the code explicitly documents that `verify` may panic if called before `sign_share`. A public-facing verifier or protocol adapter that reconstructs the algorithm state without carrying `msg` can be crashed with attacker-controlled verification inputs.

### Recommendation
Make invalid state explicit and non-panicking:

- Change `Schnorrkel::verify` to return `None` when `self.msg` is `None`.
- Check `nonces` has the expected shape before indexing.
- Prefer storing the message in a stateful type that cannot produce a verifier before `sign_share`, or encode verification state in a separate initialized struct.
- Avoid `unwrap` on attacker-influenced or protocol-state-dependent values in `verify`; return `None` or a structured error instead.

### Proof of Concept
A direct Rust-level trigger is:

```rust
let algorithm = Schnorrkel::new(b"substrate");
let _ = algorithm.verify(group_key, &[], Scalar::ZERO);
```

This panics at `nonces[0][0]`. With a well-formed nonempty nonce vector but no prior `sign_share`, it panics at `self.msg.as_ref().unwrap()`.
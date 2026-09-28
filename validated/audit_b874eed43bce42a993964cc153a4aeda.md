### Title
Assertion panic on duplicate encryption-key registration enables remote denial of service during the DKG - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
`Decryption::register` enforces one `EncryptionKeyMessage` per participant with a hard `assert!` instead of returning an error. Any participant in a PedPoP DKG session who submits a second encryption-key registration message causes the honest receiving party to panic and abort, mirroring the CVE-2017-0375 class (malformed/duplicated protocol message → assertion failure → daemon exit).

### Finding Description
In `Decryption::register`, the code does:

```rust
assert!(
  !self.enc_keys.contains_key(&participant),
  "Re-registering encryption key for a participant"
);
self.enc_keys.insert(participant, msg.enc_key);
``` [1](#0-0) 

This is reachable through `Encryption::register`, which is invoked once per incoming `EncryptionKeyMessage` during the PedPoP registration round. [2](#0-1)  The `EncryptionKeyMessage` itself is fully attacker-controlled bytes read via `EncryptionKeyMessage::read`, which performs no duplicate detection. [3](#0-2)  The library explicitly acknowledges it does not deduplicate per-participant messages itself ("If any participant sends multiple sets of commitments, they are faulty... this library is unable to detect if any participant is so faulty"), [4](#0-3)  yet it also panics when the caller (inevitably, given the message-oriented API) feeds a second registration from the same `Participant`.

Every other malformed-input path in these crates correctly returns `io::Error`/`FrostError` (`read_F`, `read_G`, `Commitments::read`, `validate_map`, `ThresholdParams::new`, etc.). [5](#0-4) [6](#0-5)  This assert is the outlier: a protocol message converts directly into a process abort.

### Impact Explanation
An unprivileged DKG participant crashes any honest party's process by sending a second `EncryptionKeyMessage`. In a deployment where the DKG runs inside a long-lived daemon (validator/coordinator process), this is an assertion-failure daemon exit — precisely the DoS primitive of CVE-2017-0375. Repeated invocation can permanently prevent DKG completion, halting key generation / validator set rotation. No secret material is leaked, but liveness of the honest node and of the whole DKG ceremony is lost.

### Likelihood Explanation
Triggering requires only that a participant emit two registration messages — trivially craftable bytes (`msg || enc_key` written twice, or two distinct messages). The honest node performs no length or count checks before calling `register` per message; the second call hits `contains_key` and panics. Likelihood is high for any protocol participant; it is limited to DKG session peers rather than arbitrary internet parties, but those peers are the in-scope threat surface (untrusted protocol messages).

A secondary panic exists on the blame path: `decrypt_with_proof` indexes `self.enc_keys[&decryptor]`, which panics if the named decryptor never registered — another attacker-influenced `HashMap` index abort. [7](#0-6) 

### Recommendation
Replace the `assert!` in `Decryption::register` with a fallible check returning a `FrostError`/`io::Error` (e.g., `FrostError::DuplicatedParticipant(participant)`), matching the duplicate-participant handling already used in `AlgorithmSignMachine::sign`. [8](#0-7)  Similarly, `decrypt_with_proof` should use `enc_keys.get(&decryptor)` and return `DecryptionError` instead of indexing. Per the crate's own stated expectation ("this library is expected not to panic"), [9](#0-8)  no reachable panic should exist on the message-processing path.

### Proof of Concept
1. An honest PedPoP participant `i` creates `Encryption::<C>::new(context, i, rng)` and enters the registration round, calling `encryption.register(l, EncryptionKeyMessage::read(bytes_from_l, params)?)` for each peer message.
2. Attacker `l` constructs two `EncryptionKeyMessage` values (each is `M::write` output followed by any valid non-identity `enc_key` point encoding, e.g., `C::generator()`) and sends both to `i`.
3. `i` processes the first: `enc_keys.insert(l, key)` succeeds. Processing the second reaches `assert!(!self.enc_keys.contains_key(&l))` → panic → process abort before the DKG completes.

```
// attacker side
let m1 = enc_key_msg_a.serialize(); // any Message + any point
let m2 = enc_key_msg_b.serialize();
send(i, m1); send(i, m2);         // both under sender index l

// honest side
for bytes in incoming {
  let msg = EncryptionKeyMessage::<C, M>::read(&mut bytes, params)?;
  encryption.register(l, msg);    // second call: panic!
}
```

*Uncertainty note:* whether a specific deployment permits a participant to deliver two registration messages depends on the caller's message routing, but the library's own API contract passes per-sender messages to `register` and documents that duplicate detection is the caller's responsibility — while still asserting internally — so the panic is reachable in any integration that delivers the attacker's second message.

### Citations

**File:** crypto/dkg/pedpop/src/encryption.rs (L57-59)
```rust
  pub fn read<R: io::Read>(reader: &mut R, params: ThresholdParams) -> io::Result<Self> {
    Ok(Self { msg: M::read(reader, params)?, enc_key: C::read_G(reader)? })
  }
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L356-361)
```rust
    assert!(
      !self.enc_keys.contains_key(&participant),
      "Re-registering encryption key for a participant"
    );
    self.enc_keys.insert(participant, msg.enc_key);
    msg.msg
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L381-390)
```rust
    if let Some(proof) = proof {
      // Verify this is the decryption key for this message
      proof
        .dleq
        .verify(
          &mut encryption_key_transcript(self.context),
          &[C::generator(), msg.key],
          &[self.enc_keys[&decryptor], *proof.key],
        )
        .map_err(|_| DecryptionError::InvalidProof)?;
```

**File:** crypto/dkg/pedpop/src/encryption.rs (L452-458)
```rust
  pub(crate) fn register<M: Message>(
    &mut self,
    participant: Participant,
    msg: EncryptionKeyMessage<C, M>,
  ) -> M {
    self.decryption.register(participant, msg)
  }
```

**File:** crypto/dkg/pedpop/src/lib.rs (L98-101)
```rust
/// Every participant should only provide one set of commitments to all parties. If any
/// participant sends multiple sets of commitments, they are faulty and should be presumed
/// malicious. As this library does not handle networking, it is unable to detect if any
/// participant is so faulty. That responsibility lies with the caller.
```

**File:** crypto/ciphersuite/src/lib.rs (L74-83)
```rust
  fn read_F<R: Read>(reader: &mut R) -> io::Result<Self::F> {
    let mut encoding = <Self::F as PrimeField>::Repr::default();
    reader.read_exact(encoding.as_mut())?;

    // ff mandates this is canonical
    let res = Option::<Self::F>::from(Self::F::from_repr(encoding))
      .ok_or_else(|| io::Error::other("non-canonical scalar"));
    encoding.as_mut().zeroize();
    res
  }
```

**File:** crypto/frost/src/sign.rs (L298-310)
```rust
    if included.len() < usize::from(multisig_params.t()) {
      Err(FrostError::InvalidSigningSet("not enough signers"))?;
    }
    // OOB index
    if u16::from(included[included.len() - 1]) > multisig_params.n() {
      Err(FrostError::InvalidParticipant(multisig_params.n(), included[included.len() - 1]))?;
    }
    // Same signer included multiple times
    for i in 0 .. (included.len() - 1) {
      if included[i] == included[i + 1] {
        Err(FrostError::DuplicatedParticipant(included[i]))?;
      }
    }
```

**File:** crypto/frost/src/curve/mod.rs (L86-88)
```rust
  // We could still panic on the 0-hash, preferring correctness to liveliness. Finding the 0-hash
  // is as computationally complex as simply calculating the group key's discrete log however,
  // making it not worth having a panic (as this library is expected not to panic).
```

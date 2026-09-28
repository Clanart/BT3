### Title
Malformed transaction signature-share vector causes index-out-of-bounds panic - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`TransactionSignatureMachine::complete` trusts every `Vec<SignatureShare<Secp256k1>>` supplied in its public `HashMap` input to contain one share for every transaction input. It calls `shares.remove(0)` while completing each input. A caller-controlled empty vector therefore panics before the inner FROST machine can validate the participant map or share count.

### Finding Description
For each transaction input, `complete` drains the inner Schnorr signature machine and builds that input’s per-participant share map with `shares.iter_mut().map(|(l, shares)| (*l, shares.remove(0)))`. If any supplied participant maps to an empty vector, `remove(0)` indexes a non-existent element and panics. The vulnerable operation is in `TransactionSignatureMachine::complete`. [1](#0-0) 

Although `TransactionSignatureMachine::read_share` normally constructs the vector by reading one scalar share per input, `complete` is a public API accepting an attacker-populated `HashMap<Participant, Vec<SignatureShare<Secp256k1>>>`. Callers that construct this map from peer messages rather than exclusively through `read_share`, or that forward protocol-level share vectors, can expose the panic to an unprivileged participant. The parser itself is defined at `networks/bitcoin/src/wallet/send.rs:409-411`. [2](#0-1) 

### Impact Explanation
A malformed share collection containing an empty per-participant vector aborts the calling thread/process while it is completing a Bitcoin transaction signature. This prevents the transaction from being completed and can repeatedly crash or disrupt signing infrastructure whenever the malformed completion input is submitted.

The impact is availability only: the panic occurs before signature verification, and no invalid transaction, forged signature, or key disclosure has been demonstrated.

### Likelihood Explanation
An unprivileged signing participant can induce this condition where the integration exposes `complete` to participant-controlled share collections. The triggering value is structurally simple: any `Participant` mapped to `vec![]`.

The issue does not appear reachable solely by sending a truncated byte stream through `read_share`, because that parser returns an `io::Error` rather than producing a shorter inner vector. Exploitation therefore depends on the surrounding protocol/API path preserving an empty or otherwise malformed `Vec<SignatureShare<Secp256k1>>` and passing it to `complete`.

### Recommendation
Validate every supplied share vector before calling `remove(0)`:

- Require each vector’s length to equal `self.sigs.len()`.
- Return `FrostError::InvalidShare(participant)` or another structured error for malformed vectors.
- Prefer `Vec::remove(0)` replacement with indexed access only after validation, or drain the vectors in a checked manner.
- Add a regression test calling `complete` with a participant mapped to an empty vector and asserting a structured error rather than a panic.

### Proof of Concept
```rust
// networks/bitcoin/src/wallet/send.rs

use std::collections::HashMap;
use frost::{curve::Secp256k1, Participant, ThresholdKeys};
use bitcoin::{ScriptBuf, Transaction};
use serai_bitcoin::wallet::SignableTransaction;

// Assumes an existing valid Secp256k1 `ThresholdKeys` and a valid spendable
// `ReceivedOutput`, both obtainable through the normal test/key-generation flow.
fn malformed_share_panics(
  keys: ThresholdKeys<Secp256k1>,
  received_output: serai_bitcoin::wallet::ReceivedOutput,
  payment_script: ScriptBuf,
) {
  let tx = SignableTransaction::new(
    vec![received_output],
    &[(payment_script, 1_000)],
    None,
    None,
    1,
  )
  .unwrap();

  let (sign_machine, _) = tx
    .multisig(&keys)
    .unwrap()
    .preprocess(&mut rand_core::OsRng);

  let (_, shares) = sign_machine
    .sign(HashMap::new(), b"")
    .unwrap();

  let signature_machine = shares; // Normally obtained as the first `sign` result.

  let mut malformed = HashMap::new();
  malformed.insert(
    Participant::new(2).unwrap(),
    Vec::new(), // Expected to contain one share per input.
  );

  // Panics at `shares.remove(0)` before inner FROST validation runs.
  let _ = signature_machine.complete(malformed);
}
```

The precise construction of `TransactionSignatureMachine` depends on the caller’s signing flow, but the panic condition itself is unambiguous: `complete` iterates all inputs and blindly removes element zero from each attacker-supplied share vector.

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L409-411)
```rust
  fn read_share<R: Read>(&self, reader: &mut R) -> io::Result<Self::SignatureShare> {
    self.sigs.iter().map(|sig| sig.read_share(reader)).collect()
  }
```

**File:** networks/bitcoin/src/wallet/send.rs (L413-420)
```rust
  fn complete(
    mut self,
    mut shares: HashMap<Participant, Self::SignatureShare>,
  ) -> Result<Transaction, FrostError> {
    for (input, schnorr) in self.tx.input.iter_mut().zip(self.sigs.drain(..)) {
      let sig = schnorr.complete(
        shares.iter_mut().map(|(l, shares)| (*l, shares.remove(0))).collect::<HashMap<_, _>>(),
      )?;
```

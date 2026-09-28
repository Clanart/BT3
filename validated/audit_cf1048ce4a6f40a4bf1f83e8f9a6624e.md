### Title
Malformed per-input signature-share vector panics during Bitcoin transaction completion - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`TransactionSignatureMachine::complete` accepts a `HashMap<Participant, Vec<SignatureShare<Secp256k1>>>`. For every transaction input, it blindly calls `shares.remove(0)` on each participant’s vector. A participant can provide a signature-share vector shorter than the number of inputs—most simply an empty vector—causing an index-out-of-bounds panic instead of a `FrostError`.

### Finding Description
`TransactionSignMachine::read_share` normally deserializes exactly one `SignatureShare` for each internal signing machine, producing a vector whose length equals the number of transaction inputs. However, `TransactionSignatureMachine::complete` exposes the vector directly and does not enforce that each supplied vector has the expected length.

The vulnerable path is:

```rust
shares.iter_mut().map(|(l, shares)| (*l, shares.remove(0)))
```

For each input, `remove(0)` is called for every participant. If any supplied vector is empty, or becomes empty before all inputs are processed, `Vec::remove` panics.

Relevant code: `networks/bitcoin/src/wallet/send.rs:409-420` [1](#0-0) 

### Impact Explanation
An unprivileged participant in a Bitcoin signing session can crash the process completing the transaction by supplying a malformed share vector through the public `complete` API. If this logic runs in a service where task panics are fatal, the malformed share can terminate the signer/processor process, analogous to the upstream bug class where malformed input causes an assertion failure and process exit.

### Likelihood Explanation
Likelihood is moderate because exploitation only requires control over a signature-share value supplied to transaction completion. The malformed value does not need to deserialize through `read_share`; `complete` accepts the vector directly. The required input is trivial to construct, but the impact is limited to availability rather than key recovery or transaction forgery.

### Recommendation
Validate every participant’s share vector before completing signatures. Specifically:

- Check `shares.len() == self.sigs.len()` before entering the loop.
- Return `FrostError::InvalidShare(participant)` or another structured error for malformed vectors.
- Avoid `Vec::remove(0)`; consume the vector with an iterator or index it after validating its length.
- Ensure trailing extra shares are also rejected so only exactly well-formed share vectors are accepted.

### Proof of Concept
Conceptually:

```rust
let mut shares: HashMap<Participant, Vec<SignatureShare<Secp256k1>>> =
    HashMap::new();

shares.insert(attacker_participant, vec![]);

// For a transaction with at least one input, this calls
// `vec![]`.remove(0)` and panics.
let _ = transaction_signature_machine.complete(shares);
```

The panic is caused by unconditional removal at `networks/bitcoin/src/wallet/send.rs:417-420`.

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L409-420)
```rust
  fn read_share<R: Read>(&self, reader: &mut R) -> io::Result<Self::SignatureShare> {
    self.sigs.iter().map(|sig| sig.read_share(reader)).collect()
  }

  fn complete(
    mut self,
    mut shares: HashMap<Participant, Self::SignatureShare>,
  ) -> Result<Transaction, FrostError> {
    for (input, schnorr) in self.tx.input.iter_mut().zip(self.sigs.drain(..)) {
      let sig = schnorr.complete(
        shares.iter_mut().map(|(l, shares)| (*l, shares.remove(0))).collect::<HashMap<_, _>>(),
      )?;
```

### Title
Missing per-input signature share causes panic in transaction completion - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`TransactionSignatureMachine::complete` assumes every participant supplied exactly one `SignatureShare` per transaction input. It indexes each participant’s `Vec<SignatureShare>` with `remove(0)` once per input. A signing-set participant can submit an empty or truncated share vector through the `complete` API, causing `remove(0)` to panic before FROST participant-set validation runs.

### Finding Description
`TransactionSignMachine` creates one inner FROST `AlgorithmSignatureMachine` per Bitcoin input and defines the outer `SignatureShare` as `Vec<SignatureShare<Secp256k1>>`. During completion, it iterates over inputs and drains one element from every participant’s vector with `shares.remove(0)`. [1](#0-0) 

This mirrors the report’s missing-entry invariant: a later loop assumes a value exists for every item, while an earlier stage can legitimately omit it. Here, the outer share vector can be empty or contain fewer than `tx.input.len()` elements. The panic occurs before the inner `schnorr.complete` call, which would otherwise return a `FrostError` for an invalid or missing participant. [2](#0-1) 

### Impact Explanation
A participant able to supply malformed `SignatureShare` values to `complete` can crash the signing process instead of producing a blameable `FrostError`. In a wallet/service context where completion runs in a persistent signer or coordinator task, this can abort the transaction-signing attempt and prevent funds from being spent until the panic path is avoided or the faulty input is filtered.

This is a denial-of-service impact rather than key recovery or signature forgery.

### Likelihood Explanation
The condition is reachable whenever `complete` is called with a participant map containing an empty or short `Vec<SignatureShare>`. The panic is deterministic: `shares.remove(0)` on an empty vector panics.

Mitigating factor: the provided `read_share` implementation reads exactly `self.sigs.len()` shares and errors on short encodings, so callers that strictly deserialize all peer shares through `read_share` and reject trailing bytes will not produce a short vector. [3](#0-2) 

### Recommendation
Validate lengths before removing elements. In `TransactionSignatureMachine::complete`, check that each participant’s share vector has exactly `self.sigs.len()` elements, reject empty/truncated vectors with `FrostError::InvalidShare(participant)`, and only then consume per-input shares. Alternatively, iterate over participant vectors by index and map out-of-bounds access to `FrostError::InvalidShare` instead of panicking.

### Proof of Concept
Conceptually:

1. Build a `SignableTransaction` with at least one input.
2. Instantiate `TransactionSignMachine` and complete preprocessing/signing so `self.sigs.len() >= 1`.
3. Call `TransactionSignatureMachine::complete` with `HashMap<Participant, Vec<SignatureShare<Secp256k1>>>` where an included participant maps to `vec![]`.
4. The loop reaches `shares.remove(0)` for the first input and panics on the empty vector. [2](#0-1)

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L406-411)
```rust
impl SignatureMachine<Transaction> for TransactionSignatureMachine {
  type SignatureShare = Vec<SignatureShare<Secp256k1>>;

  fn read_share<R: Read>(&self, reader: &mut R) -> io::Result<Self::SignatureShare> {
    self.sigs.iter().map(|sig| sig.read_share(reader)).collect()
  }
```

**File:** networks/bitcoin/src/wallet/send.rs (L413-424)
```rust
  fn complete(
    mut self,
    mut shares: HashMap<Participant, Self::SignatureShare>,
  ) -> Result<Transaction, FrostError> {
    for (input, schnorr) in self.tx.input.iter_mut().zip(self.sigs.drain(..)) {
      let sig = schnorr.complete(
        shares.iter_mut().map(|(l, shares)| (*l, shares.remove(0))).collect::<HashMap<_, _>>(),
      )?;

      let mut witness = Witness::new();
      witness.push(sig);
      input.witness = witness;
```

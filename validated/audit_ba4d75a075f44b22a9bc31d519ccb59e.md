### Title
Malformed per-input preprocess/share vector panics transaction signing - ([File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs))

### Summary
`TransactionSignMachine::read_preprocess` accepts any number of per-input FROST preprocesses, including zero. `sign` then indexes each participant’s vector by input index without checking its length. A signer can therefore send an empty or truncated preprocess vector and panic every processor attempting to sign a multi-input Bitcoin transaction.

### Finding Description
`read_preprocess` builds a `Vec` by calling each per-input machine’s `read_preprocess` and collecting the results. Because the stream ends when the peer’s bytes end, a peer can successfully return fewer than `self.sigs.len()` entries [1](#0-0) . During signing, the code iterates over every transaction input and indexes `commitments[c]` for every participant [2](#0-1) . If any participant supplied fewer vectors than inputs, this index panics before the remaining inputs can be signed.

The same pattern exists for signature shares: `read_share` accepts a short vector [3](#0-2) , and `complete` blindly removes element `0` once per transaction input [4](#0-3) .

### Impact Explanation
An unprivileged signing participant can halt construction of a valid Bitcoin spend by sending a syntactically valid but too-short preprocess or share vector. Rather than producing a per-participant `InvalidParticipant`/blame result, the node panics during transaction signing or completion. For a transaction with multiple inputs, one malformed participant prevents all inputs from being processed.

### Likelihood Explanation
The malicious input is ordinary public message bytes consumed through `read_preprocess` or `read_share`. No malformed curve point, forged proof, or invalid Bitcoin transaction is required; the peer only needs to truncate the serialized vector after fewer entries than the transaction input count.

### Recommendation
Validate that `read_preprocess` and `read_share` return exactly `self.sigs.len()` entries. Also avoid unchecked indexing/removal in `sign` and `complete`; return `FrostError::InvalidPreprocess(participant)` or `FrostError::InvalidShare(participant)` when a participant supplies a missing per-input item.

### Proof of Concept
1. Construct a `SignableTransaction` with two `ReceivedOutput` inputs and create a `TransactionSignMachine`.
2. For a remote participant, serialize only the first input’s `Preprocess` bytes, or an empty vector if the encoding permits EOF to produce `vec![]`.
3. Pass it through `TransactionSignMachine::read_preprocess`; deserialization succeeds with one item instead of two.
4. Call `TransactionSignMachine::sign`.
5. The participant map lookup at `commitments[c]` reaches input index `1`, where the malicious participant’s vector has no second entry, causing an out-of-bounds panic.

The share path is equivalent: provide a share vector shorter than `tx.input.len()`, then `shares.remove(0)` eventually panics while completing a later input.

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L351-353)
```rust
  fn read_preprocess<R: Read>(&self, reader: &mut R) -> io::Result<Self::Preprocess> {
    self.sigs.iter().map(|sig| sig.read_preprocess(reader)).collect()
  }
```

**File:** networks/bitcoin/src/wallet/send.rs (L364-369)
```rust
    let commitments = (0 .. self.sigs.len())
      .map(|c| {
        commitments
          .iter()
          .map(|(l, commitments)| (*l, commitments[c].clone()))
          .collect::<HashMap<_, _>>()
```

**File:** networks/bitcoin/src/wallet/send.rs (L409-411)
```rust
  fn read_share<R: Read>(&self, reader: &mut R) -> io::Result<Self::SignatureShare> {
    self.sigs.iter().map(|sig| sig.read_share(reader)).collect()
  }
```

**File:** networks/bitcoin/src/wallet/send.rs (L417-420)
```rust
    for (input, schnorr) in self.tx.input.iter_mut().zip(self.sigs.drain(..)) {
      let sig = schnorr.complete(
        shares.iter_mut().map(|(l, shares)| (*l, shares.remove(0))).collect::<HashMap<_, _>>(),
      )?;
```

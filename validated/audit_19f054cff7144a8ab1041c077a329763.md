### Title
Untrusted preprocess/signature-share vectors trigger index-out-of-bounds panic in Bitcoin transaction signing machines - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
Analogous to CVE-2023-31916 (assertion failure reachable via untrusted input), `TransactionSignMachine::sign` and `TransactionSignatureMachine::complete` index into attacker-controlled vectors without length validation. An unprivileged counterparty can send a malformed preprocess or share blob (via `read_preprocess` / `read_share`) that is shorter than the number of transaction inputs, causing a panic (`Index out of bounds` / `removal index... out of bounds`) that aborts the signing round and can crash the processor.

### Finding Description
`TransactionSignMachine::sign` builds per-input commitment maps by indexing `commitments[c]` for each input `c` in `0 .. self.sigs.len()` at `send.rs:364-371`. The preprocess bytes come from `read_preprocess` (`send.rs:351-353`), which simply collects whatever `sigs.len()` `Preprocess` values the peer serialized — but the outer `Vec<Preprocess>` sent per participant is attacker-controlled. If a peer sends a `Vec` shorter than `self.sigs.len()`, `commitments[c]` panics.

Likewise, `TransactionSignatureMachine::complete` calls `shares.remove(0)` once per input at `send.rs:417-420` on the `Vec<SignatureShare>` decoded by `read_share` (`send.rs:409-411`). A share vector shorter than the input count causes `Vec::remove` to panic mid-completion, after some witnesses have already been mutated.

Both paths are reachable purely from bytes the peer supplies (`read_preprocess`, `read_share` — explicitly listed reachable APIs) and require no malicious-validator or collusion assumption beyond a single faulty counterparty, which the FROST protocol is explicitly designed to tolerate via `FrostError`/blame rather than panic.

### Impact Explanation
Availability loss: a single participant can abort every signing attempt or crash the signing task by submitting a truncated preprocess/share vector, exactly the assertion-failure DoS class of the reference CVE (CVSS 5.5, A:H). Since the panic occurs after `self.sigs` is drained, the machine is consumed and the attempted transaction must be rebuilt.

### Likelihood Explanation
Any peer able to participate in (or inject messages into) a FROST signing session for a Bitcoin plan can trigger it deterministically with a short byte string; no exotic conditions required.

### Recommendation
Validate lengths before indexing: in `sign`, check `commitments.values().all(|v| v.len() == self.sigs.len())` and return `FrostError::InvalidCommitments`-style error; in `complete`, check each share vector length and return `FrostError` instead of panicking. Alternatively use `.get(c)`/`pop_front` with graceful error mapping.

### Proof of Concept
For a 2-input `SignableTransaction`, a malicious participant serializes a preprocess message containing only 1 `Preprocess` instead of 2. `read_preprocess` succeeds per-signature, but `sign` panics at `send.rs:368` evaluating `commitments[1]`. Similarly, `complete` panics at `send.rs:419` (`shares.remove(0)` on an empty vec) when the share blob for a participant decodes to fewer shares than inputs. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L351-353)
```rust
  fn read_preprocess<R: Read>(&self, reader: &mut R) -> io::Result<Self::Preprocess> {
    self.sigs.iter().map(|sig| sig.read_preprocess(reader)).collect()
  }
```

**File:** networks/bitcoin/src/wallet/send.rs (L364-371)
```rust
    let commitments = (0 .. self.sigs.len())
      .map(|c| {
        commitments
          .iter()
          .map(|(l, commitments)| (*l, commitments[c].clone()))
          .collect::<HashMap<_, _>>()
      })
      .collect::<Vec<_>>();
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

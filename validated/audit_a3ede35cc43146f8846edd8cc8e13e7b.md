### Title
Panic on out-of-bounds preprocess/share indexing crashes FROST signing for Bitcoin transactions - (File: networks/bitcoin/src/wallet/send.rs)

### Summary

`TransactionSignMachine::sign` and `TransactionSignatureMachine::complete` index and pop elements from attacker-controlled `Vec`s by position, assuming every participant's `Vec<Preprocess>` / `Vec<SignatureShare>` has exactly `self.tx.input.len()` elements. A participant supplying a shorter `Vec` causes an index-out-of-bounds panic (`commitments[c]`) or a `Vec::remove` panic on an empty vector (`shares.remove(0)`), crashing the signing task mid-session. This is the same bug class as CVE-2019-2685 (crafted input causing a crash/complete DoS), mapped onto Serai's threshold-signing path.

### Finding Description

In `sign`, each participant's preprocess is `Vec<Preprocess<Secp256k1, ()>>` — one entry per TX input. The machine splices them per-input by index:

```rust
// networks/bitcoin/src/wallet/send.rs:364-371
let commitments = (0 .. self.sigs.len())
  .map(|c| {
    commitments
      .iter()
      .map(|(l, commitments)| (*l, commitments[c].clone()))
      .collect::<HashMap<_, _>>()
  })
  .collect::<Vec<_>>();
```

`commitments[c]` panics if any participant's preprocess `Vec` has fewer than `self.sigs.len()` entries. The type is `Vec`, so nothing statically enforces the length.

In `complete`, the same positional assumption holds:

```rust
// networks/bitcoin/src/wallet/send.rs:417-420
let sig = schnorr.complete(
  shares.iter_mut().map(|(l, shares)| (*l, shares.remove(0))).collect::<HashMap<_, _>>(),
)?;
```

`shares.remove(0)` panics once a participant's share `Vec` is drained — i.e. whenever it contains fewer than `self.sigs.len()` shares.

Length mismatch is reachable, not just a hypothetical integrator misuse:

- `read_preprocess` / `read_share` (`send.rs:351-353`, `409-411`) size the `Vec` to the *reader's* `self.sigs.len()`. When two `TransactionMachine`s are instantiated for different plans — different input counts, different plan IDs — a preprocess/share produced by a machine with *fewer* inputs is well-formed, parses cleanly, and still carries fewer entries than this machine's `sigs.len()`. In a network where multiple plans/transactions are being signed concurrently (ROS-style parallel sessions are an expected operating mode), a participant can deliberately or accidentally submit a preprocess/share `Vec` generated against a different transaction shape.
- A participant can also hand-construct the serialized bytes: the preprocess is just a concatenation of `AlgorithmMachine::read_preprocess` items, so submitting a truncated-but-parseable blob under an older `sigs.len()` (e.g., from a stale `SignableTransaction` reused across a retried plan with different inputs) hits the panic without any cryptographic forgery.

Because `sign`/`complete` take `self` by value and the `unimplemented!` on `cache`/`from_cache` means preprocesses cannot be cached or replayed, the panic destroys the entire signing session's state.

### Impact Explanation

Unprivileged crash of the threshold-signing process. Each `AlgorithmSignMachine::sign` consumed a fresh FROST nonce pair; a panic at `commitments[c]` or `shares.remove(0)` aborts `TransactionSignMachine::sign`/`complete` mid-iteration, killing the signing task (and, depending on the embedding executor, the process). Any honest participant who already called `sig.sign(...)` for earlier inputs has burned preprocesses against a `SighashCache` bound to that TX (`send.rs:373-390`), so the session cannot be resumed — it must be restarted with fresh preprocesses. Repeating the malformed share each round gives a persistent, easily-reproduced denial of signing for the affected plan: a complete DoS of fund movement, directly analogous to the repeatable MySQL crash in the reference CVE.

### Likelihood Explanation

Medium. Exploitation requires only that a signing-set participant submit a structurally-valid `Vec` of the wrong length — no valid signature, no threshold collusion, no key material is needed. The length check gap exists because the code relies on `read_preprocess`/`read_share` to implicitly size vectors, but participants deserialize their *own* preprocesses against their *own* machines, which may legitimately have different input counts across concurrent plans or retried `SignableTransaction`s. It does require the attacker to be a participant in the signing set, which caps severity at Medium rather than High — matching the CVE's `PR:H`-style privilege requirement while keeping the same availability-only impact.

### Recommendation

Validate lengths before indexing:

- In `TransactionSignMachine::sign`, check `commitments.values().all(|v| v.len() == self.sigs.len())` and return `Err(FrostError::InvalidPreprocess(l))` (or equivalent) instead of indexing blindly.
- In `TransactionSignatureMachine::complete`, check `shares.values().all(|v| v.len() == self.sigs.len())` up front and return a `FrostError`, so a malformed share set is attributable to a participant for blame/slashing rather than crashing the machine.
- Consider encoding the expected input count in the serialized preprocess/share header so `read_preprocess`/`read_share` can reject cross-plan artifacts explicitly.

### Proof of Concept

```rust
// Conceptual: a 1-input SignableTransaction machine vs. a peer whose preprocess
// Vec was built (or crafted) for a 0-length / shorter input list.

// sign() path — panics at send.rs:368, commitments[c]:
let mut commitments: HashMap<Participant, Vec<Preprocess<Secp256k1, ()>>> = ...;
// Attacker's entry: a Vec that parses fine for their machine but is shorter:
commitments.insert(attacker_l, vec![]); // or len < self.sigs.len()
let _ = sign_machine.sign(commitments, &[]); // index out of bounds panic

// complete() path — panics at send.rs:419, shares.remove(0) on empty Vec:
let mut shares: HashMap<Participant, Vec<SignatureShare<Secp256k1>>> = ...;
shares.insert(attacker_l, vec![]); // fewer than self.sigs.len() shares
let _ = sig_machine.complete(shares); // Vec::remove panic after honest inputs consumed
```

The crash occurs after earlier `sig.sign(...)` calls have already consumed fresh FROST nonces bound to `SighashCache::taproot_key_spend_signature_hash` (`send.rs:373-390`), so the aborted session's preprocesses are unrecoverable and `cache()`/`from_cache()` are `unimplemented!` (`send.rs:333-349`), forcing a full restart — a repeatable signing DoS from a single malformed-length vector.

Uncertainty note: I did not fully trace whether `FrostError` has a dedicated variant for mismatched preprocess lengths upstream in `crypto/frost`, nor whether outer processor code catches panics around `sign`/`complete`; the panic itself is unconditional in the cited lines regardless.
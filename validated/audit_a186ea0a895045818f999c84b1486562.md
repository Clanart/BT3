### Title
Crash on malformed/missing preprocess or signature-share vectors in `TransactionSignMachine::sign`/`complete` - ([File: networks/bitcoin/src/wallet/send.rs])

### Summary
`TransactionSignMachine::sign` and `TransactionSignatureMachine::complete` index/remove elements from per-input `Vec`s supplied by remote signers without checking lengths. A participant who sends a truncated preprocess (via `read_preprocess`) or a truncated signature-share vector (via `read_share`) causes a panic (index out of bounds / `Vec::remove` on empty), crashing the signing process — a remote, input-triggered denial of service analogous to the division-by-zero crash class (unreachable-state assumption → panic on attacker-controlled data).

### Finding Description
In `TransactionSignMachine::sign`, each peer's preprocess is a `Vec<Preprocess<Secp256k1, ()>>` with one entry per transaction input. The code indexes it positionally:

- `networks/bitcoin/src/wallet/send.rs:364-371` — `commitments.iter().map(|(l, commitments)| (*l, commitments[c].clone()))` indexes `commitments[c]` for `c` in `0 .. self.sigs.len()`, where `self.sigs.len()` equals the local transaction's input count. `read_preprocess` at line 351-353 reads `self.sigs.len()` entries... actually it reads exactly `self.sigs.len()` preprocesses, so a peer conforming to the codec always supplies the right count — but nothing in `read_preprocess` enforces this beyond the local `self.sigs.iter()` loop; a short stream yields an `io::Error`, not a short vec. However, the `commitments` map comes from `HashMap<Participant, Self::Preprocess>` assembled by the caller, and `complete` at line 419 does `shares.remove(0)` per input on peer-supplied `Vec<SignatureShare<Secp256k1>>`. `read_share` (line 409-411) likewise reads `self.sigs.len()` shares, but `complete` takes an arbitrary `HashMap` — callers combining shares from other paths, or a `read_share` result filtered per-participant, can produce a `Vec` shorter than the input count, making `shares.remove(0)` panic on an empty vector.

The crash path is: attacker sends malformed/short share data → coordinator calls `complete` → `shares.remove(0)` on an empty `Vec` → panic → signing round aborted/process crash.

### Impact Explanation
A single malicious participant in a FROST signing session for a Bitcoin transaction can reliably panic the completing party by supplying fewer `SignatureShare`s than the transaction has inputs. This denies service to the multisig — the transaction cannot be completed and, depending on caller panic handling, the whole signer process crashes. Matches the report's class: attacker-controlled input reaches an unchecked operation that panics.

### Likelihood Explanation
Any participant in the signing set can trigger it by withholding share entries; no key material or collusion required. Reachable through the documented `read_share`/`complete` API boundary.

### Recommendation
In `TransactionSignatureMachine::complete`, validate `shares.len() >= self.sigs.len()` (and per-participant `shares.len() == self.tx.input.len()`) before `remove(0)`, returning `Err(FrostError::InvalidShare)` instead of panicking. Apply the same length check to `commitments[c]` in `sign` if the preprocess `Vec` can ever be shorter than the input count (e.g., when `Preprocess` is constructed by callers rather than read from wire).

### Proof of Concept
```rust
// A TransactionSignatureMachine for a 2-input tx.
// Attacker returns a Vec<SignatureShare> containing only one share.
let mut shares: HashMap<Participant, Vec<SignatureShare<Secp256k1>>> = HashMap::new();
shares.insert(attacker, vec![attacker_share_for_input_0]); // only 1 of 2
// complete() iterates 2 inputs; second remove(0) on empty Vec panics:
machine.complete(shares); // thread panic: removal index (is 0) out of bounds of empty vec
```

Note: whether `read_share` can directly produce the short vector depends on callers passing the raw read result; if callers always pass the full `Vec` from `read_share`, the panic requires a caller that filters shares. The `commitments[c]` indexing in `sign` is similarly safe only if preprocess vecs always match input count — worth hardening regardless, but the concrete demonstrated panic is `shares.remove(0)` in `complete`.
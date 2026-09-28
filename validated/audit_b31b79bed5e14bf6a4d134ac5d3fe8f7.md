### Title
Quadratic transaction-completion cost via `Vec::remove(0)` lets dust-output spam degrade multisig signing - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`TransactionSignatureMachine::complete` drains each participant's share `Vec` with `shares.remove(0)` once per transaction input, inside a loop over all inputs. Each `remove(0)` shifts the entire remaining vector, so completing a transaction with `I` inputs and `t` participants costs `O(t · I²)` in memory moves. An unprivileged user can inflate `I` at will by sending many ≥-dust outputs to the multisig Taproot address, turning every subsequent spend into a quadratic-cost operation — the direct analog of CVE-2023-5680's "many records for one name make node cleanup expensive".

### Finding Description
In `complete`, each Schnorr sub-signature machine is completed with a freshly built map formed by removing the first element of every participant's share vector [1](#0-0) . `Vec::remove(0)` is `O(I)` because it memmoves all remaining elements, and it is executed `I` times per participant — once per input — yielding `O(t · I²)` total element moves for a transaction spending `I` received outputs.

The same input count is also attacker-influenced upstream: `SignableTransaction::new` accepts `inputs: Vec<ReceivedOutput>` with no cap beyond `MAX_STANDARD_TX_WEIGHT` (~400k WU ≈ ~2700 P2TR inputs) [2](#0-1) [3](#0-2) . `sign` similarly materializes an `I × t` matrix of per-input commitment maps, cloning each `Preprocess` (`commitments[c].clone()`) `I` times per participant [4](#0-3) .

`I` is controlled by external parties: any Bitcoin user can pay the multisig's P2TR script (`p2tr_script_buf`) repeatedly; each received output becomes a `ReceivedOutput` that coin selection may bundle into one spend.

### Impact Explanation
A transaction spending `I` inputs requires `t · I²` element shifts plus `t · I` `Preprocess` clones. At the protocol maximum (~2700 inputs, `t` up to the multisig size), `complete` alone performs on the order of millions of vector shifts and `sign` clones millions of `Preprocess` structures — CPU/memory work orders of magnitude beyond the transaction's actual size. An attacker who dusts the multisig address (546 sats each) permanently inflates the cost of consolidating or spending those funds, degrading signing throughput for every validator processing the spend. Like CVE-2023-5680 (CVSS 5.3, availability-low), this is a resource-amplification DoS reachable purely with public inputs (ordinary Bitcoin transactions).

### Likelihood Explanation
Sending dust outputs to a known multisig Taproot address requires no privilege and no cooperation. Whether all such outputs land in a single `SignableTransaction` depends on the scheduler's coin selection, but consolidation of many small outputs into one transaction is a normal and expected operation — there is no input-count bound below the ~2700-input standardness cap.

### Recommendation
In `complete`, avoid front-removal: iterate each participant's share vector with an index (`shares[i]`) or convert to `VecDeque`/`Vec::drain(..)` once, so per-input extraction is `O(1)` and total work is `O(t · I)`. In `sign`, build the per-input commitment maps by zipping indexed iterators rather than re-cloning into a new `HashMap` per input, and consider bounding the number of inputs per transaction to a constant well below `MAX_STANDARD_TX_WEIGHT`.

### Proof of Concept
1. Attacker sends `N` transactions paying 546+ sats each to the multisig's `p2tr_script_buf` address (only public knowledge required).
2. The processor constructs `SignableTransaction::new` with all `N` received outputs as `inputs` — valid up to ~2700 inputs.
3. `TransactionSignMachine::sign` executes `commitments[c].clone()` `t · N` times, materializing `N` HashMaps of `t` entries.
4. `TransactionSignatureMachine::complete` executes `shares.remove(0)` `N` times per participant: `t · N²` element shifts. For `N = 2700`, `t = 100`: ~730 million element moves plus 270k map constructions — quadratic degradation of a public-input-reachable path, matching the CVE-2023-5680 bug class (cost proportional to the square of attacker-created records on a single logical node/key).

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L150-156)
```rust
  pub fn new(
    mut inputs: Vec<ReceivedOutput>,
    payments: &[(ScriptBuf, u64)],
    change: Option<ScriptBuf>,
    data: Option<Vec<u8>>,
    fee_per_vbyte: u64,
  ) -> Result<SignableTransaction, TransactionError> {
```

**File:** networks/bitcoin/src/wallet/send.rs (L241-243)
```rust
    if weight > u64::from(bitcoin::policy::MAX_STANDARD_TX_WEIGHT) {
      Err(TransactionError::TooLargeTransaction)?;
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

**File:** networks/bitcoin/src/wallet/send.rs (L417-420)
```rust
    for (input, schnorr) in self.tx.input.iter_mut().zip(self.sigs.drain(..)) {
      let sig = schnorr.complete(
        shares.iter_mut().map(|(l, shares)| (*l, shares.remove(0))).collect::<HashMap<_, _>>(),
      )?;
```

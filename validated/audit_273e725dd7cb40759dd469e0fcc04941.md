### Title
`Scanner::scan_block` reports immature coinbase outputs as spendable `ReceivedOutput`s - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
The external report's bug class is "a caller-supplied time parameter is consumed without being validated against the actual current time, letting an attacker choose a more favorable evaluation point." The analog in bitcoin-serai is `Scanner::scan_block`: it feeds every transaction in a block — including the coinbase — through `scan_transaction`, and returns coinbase outputs as ordinary `ReceivedOutput`s with no maturity check. Bitcoin requires coinbase outputs to be 100 blocks deep before they can be spent (BIP/consensus `COINBASE_MATURITY`), so a coinbase output paying a watched key is "funds reported received that are not spendable", exactly the class of consequence the original bug produces (an early/"past" exercise yielding an invalid payoff).

### Finding Description
`Scanner::scan_block` iterates `block.txdata` unconditionally and calls `scan_transaction` on each transaction, including `txdata[0]` (the coinbase). [1](#0-0)  `scan_transaction` has no awareness of whether the transaction is a coinbase; it emits a `ReceivedOutput` for any output whose `script_pubkey` matches a registered script. [2](#0-1)  A `ReceivedOutput` carries only `offset`, `output`, and `outpoint` — there is no maturity/height metadata, so downstream code cannot distinguish an immature coinbase output from a normal one. [3](#0-2)  These outputs flow directly into `SignableTransaction::new`, which treats every input's value as spendable balance and produces a transaction that will be consensus-invalid (rejected for violating coinbase maturity) if broadcast before the output is 100 blocks old. [4](#0-3)  The only mitigation is a doc comment telling callers to post-filter or scan `block.txdata[1 ..]` themselves — the API provides no enforcement and no marker on the returned outputs. [5](#0-4) 

### Impact Explanation
An unprivileged miner can include a coinbase output paying the vault/wallet's registered script (the base P2TR script is publicly derivable from the group key via `tweak_keys`/`p2tr_script_buf`). [6](#0-5)  The scanner then reports spendable funds that cannot actually be spent for 100 blocks. Consumers that credit balances or build transactions from `scan_block` results will produce transactions that fail consensus relay (`bad-txns-premature-spend-of-coinbase`), or mis-account funds during that window — mirrored by any reorg within the maturity window, where a "received" output disappears entirely after being counted.

### Likelihood Explanation
Reachability requires mining a block, which has real cost; however no permission or trust relationship is needed — any miner can direct a coinbase to the publicly known script. Misuse likelihood is high on the consumer side because `scan_block` is the natural whole-block API and the footgun is only described in a doc comment; nothing in the type system or API forces the caller to filter coinbase outputs. Severity Medium: availability/accounting impact rather than direct theft.

### Recommendation
Enforce maturity inside the library rather than documenting around it: in `scan_block`, either skip `block.txdata[0]` by default (or behind an explicit flag), or tag `ReceivedOutput` with a `coinbase`/`immature` marker so `SignableTransaction::new` can reject immature inputs. Additionally, `ReceivedOutput` could carry the confirming block height so callers can check maturity against current height instead of pattern-matching `is_coinbase` externally.

### Proof of Concept
```rust
// networks/bitcoin/src/wallet/mod.rs context
// Given Scanner::new(group_key) and a mined block whose coinbase pays
// p2tr_script_buf(group_key):

let received = scanner.scan_block(&block);
// received[0] is the coinbase output — indistinguishable from a normal output.
// ReceivedOutput { offset: ZERO, output, outpoint } has no maturity field.

// Downstream:
let stx = SignableTransaction::new(received, &payments, change, None, fee_rate)?;
// stx builds a transaction spending the immature coinbase.
// Broadcasting it fails with bad-txns-premature-spend-of-coinbase until
// the coinbase is 100 confirmations deep — funds were reported received
// but were never spendable.
```

<Caveat: the doc comment at `wallet/mod.rs:216-220` acknowledges the behavior, so if documented-callers-must-filter is treated as disqualifying, this analog does not stand on its own; I found no stronger timestamp/age-validation gap in the in-scope crates — FROST/DKG/DLEq code paths bind all attacker-supplied bytes into the transcript correctly (`sign.rs:361-379`, `nonce.rs:161-173`), and `send.rs` fixes `lock_time: LockTime::ZERO` so no attacker-chosen timelock enters the sighash.>

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L80-86)
```rust
pub fn p2tr_script_buf(key: ProjectivePoint) -> Option<ScriptBuf> {
  if key.to_encoded_point(true).tag() != Tag::CompressedEvenY {
    return None;
  }

  Some(ScriptBuf::new_p2tr_tweaked(TweakedPublicKey::dangerous_assume_tweaked(x_only(&key))))
}
```

**File:** networks/bitcoin/src/wallet/mod.rs (L89-97)
```rust
#[derive(Clone, PartialEq, Eq, Debug)]
pub struct ReceivedOutput {
  // The scalar offset to obtain the key usable to spend this output.
  offset: Scalar,
  // The output to spend.
  output: TxOut,
  // The TX ID and vout of the output to spend.
  outpoint: OutPoint,
}
```

**File:** networks/bitcoin/src/wallet/mod.rs (L199-214)
```rust
  pub fn scan_transaction(&self, tx: &Transaction) -> Vec<ReceivedOutput> {
    let mut res = Vec::new();
    for (vout, output) in tx.output.iter().enumerate() {
      // If the vout index exceeds 2**32, stop scanning outputs
      let Ok(vout) = u32::try_from(vout) else { break };

      if let Some(offset) = self.scripts.get(&output.script_pubkey) {
        res.push(ReceivedOutput {
          offset: *offset,
          output: output.clone(),
          outpoint: OutPoint::new(tx.compute_txid(), vout),
        });
      }
    }
    res
  }
```

**File:** networks/bitcoin/src/wallet/mod.rs (L216-220)
```rust
  /// Scan a block.
  ///
  /// This will also scan the coinbase transaction which is bound by maturity. If received outputs
  /// must be immediately spendable, a post-processing pass is needed to remove those outputs.
  /// Alternatively, scan_transaction can be called on `block.txdata[1 ..]`.
```

**File:** networks/bitcoin/src/wallet/mod.rs (L221-227)
```rust
  pub fn scan_block(&self, block: &Block) -> Vec<ReceivedOutput> {
    let mut res = Vec::new();
    for tx in &block.txdata {
      res.extend(self.scan_transaction(tx));
    }
    res
  }
```

**File:** networks/bitcoin/src/wallet/send.rs (L175-185)
```rust
    let input_sat = inputs.iter().map(|input| input.output.value.to_sat()).sum::<u64>();
    let offsets = inputs.iter().map(|input| input.offset).collect();
    let tx_ins = inputs
      .iter()
      .map(|input| TxIn {
        previous_output: input.outpoint,
        script_sig: ScriptBuf::new(),
        sequence: Sequence::MAX,
        witness: Witness::new(),
      })
      .collect::<Vec<_>>();
```

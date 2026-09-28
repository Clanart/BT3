### Title
Spoofed internal outputs via `Scanner` matching `script_pubkey` only cause real forwarded/change outputs to be dropped and funds locked - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`Scanner::scan_transaction` / `scan_block` classify an output solely by whether its `script_pubkey` appears in the internally-registered `scripts` map. Any unprivileged party can send Bitcoin to the branch/change/forward script pubkeys (which are derivable from the public group key plus the public `hash_to_F` offsets). The resulting `ReceivedOutput` is indistinguishable from a legitimately produced internal output, which lets an attacker poison the offset→kind classification that downstream logic relies on, causing real internal outputs to be discarded and their funds locked.

### Finding Description
`Scanner` stores a `HashMap<ScriptBuf, Scalar>` built from `Scanner::new` (external offset `Scalar::ZERO`) and `register_offset` calls for the Branch, Change, and Forwarded offsets (in the reference integration these are `Secp256k1::hash_to_F(KEY_DST, b"branch" | b"change" | b"forward")` — public constants). `scan_transaction` then returns a `ReceivedOutput` for *any* transaction output whose `script_pubkey` is in that map, regardless of who created the containing transaction or why [1](#0-0) [2](#0-1) .

`ReceivedOutput` carries only `offset`, `output`, and `outpoint` — nothing distinguishes an output the multisig itself created from an output a third party sent to an internal script [3](#0-2) . This mirrors the Sherlock finding: downstream code hard-codes its handling to the returned classification (here the `offset`/kind), so a value of an unexpected provenance is unhandled. Concretely, in the consuming logic a `Forwarded`-kind output is only "taken" if a matching saved instruction exists (`ForwardedOutputDb::take_forwarded_output`), and a `Change`-kind output is only kept if a matching plan/eventuality resolved in the same transaction; otherwise the output is silently dropped (`retain` returning `false`) and its coins are never credited or respent — i.e., locked in the multisig [4](#0-3) .

### Impact Explanation
An attacker can cause received funds to be permanently unspendable/unaccounted:

1. The attacker observes (or predicts) a forwarding or change output the multisig is about to produce — amounts are fixed by the plan — and sends an attacker-funded output of the same value to the forward/change script pubkey, landing in the same or an earlier block.
2. The spoofed output is scanned first and consumes the one-shot matching state (e.g., the saved forwarded instruction keyed by balance, or the scheduler's branch-slot accounting), so the honest internal output scanned afterwards has no matching record and is dropped.
3. The honest output's coins remain locked at the internal script address: they were reported as received but are never scheduled, credited, or respent — the direct analog of "reward locked because the returned token wasn't the expected one."

Even without a state collision, any output sent to a Change/Branch script with no matching plan is dropped by classification, so deposits made to derivable internal addresses are irrecoverably locked rather than credited.

### Likelihood Explanation
The script pubkeys are fully public (deterministic functions of the group key and public offsets), so no privileged position is needed. Exploiting the collision variant requires winning a minor race (landing the spoofed output before/in the same scan as the honest one) and matching the forwarded balance, which is publicly observable on-chain. Medium likelihood; Medium-High impact (locked user/multisig funds).

### Recommendation
Bind each scanned output to its provenance, not just its script:

- Tag `ReceivedOutput`s with whether the containing transaction is one the multisig produced (e.g., only treat Change/Branch/Forward-kind outputs as internal when the tx spends known inputs or matches a pending plan/eventuality ID), and report unrecognized payments to internal scripts as External-kind outputs so they are credited/refunded instead of dropped.
- Key `ForwardedOutputDb`/change matching by the full outpoint or eventuality ID rather than by balance alone, so a same-value spoofed output cannot consume the one-shot record.

### Proof of Concept
1. Multisig group key `K` is public. Attacker computes `p2tr_script_buf(K + G*FORWARD_OFFSET)` (odd-offset handling per `register_offset`) to obtain the forward address [5](#0-4) .
2. Attacker watches the chain for an in-flight forwarding plan of amount `A` and broadcasts a tx paying `A` to the forward script in an earlier block.
3. `scan_block` returns the attacker's output as `ReceivedOutput { offset: forward_offset, .. }` [6](#0-5) ; the processor consumes the saved forwarded instruction against it.
4. The honest forwarded output is scanned later, finds no saved instruction, fails the retain check, and is dropped — its funds are locked in the multisig despite having been "received".

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L80-97)
```rust
pub fn p2tr_script_buf(key: ProjectivePoint) -> Option<ScriptBuf> {
  if key.to_encoded_point(true).tag() != Tag::CompressedEvenY {
    return None;
  }

  Some(ScriptBuf::new_p2tr_tweaked(TweakedPublicKey::dangerous_assume_tweaked(x_only(&key))))
}

/// A spendable output.
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

**File:** networks/bitcoin/src/wallet/mod.rs (L180-196)
```rust
  pub fn register_offset(&mut self, mut offset: Scalar) -> Option<Scalar> {
    // This loop will terminate as soon as an even point is found, with any point having a ~50%
    // chance of being even
    // That means this should terminate within a very small amount of iterations
    loop {
      match p2tr_script_buf(self.key + (ProjectivePoint::GENERATOR * offset)) {
        Some(script) => {
          if self.scripts.contains_key(&script) {
            None?;
          }
          self.scripts.insert(script, offset);
          return Some(offset);
        }
        None => offset += Scalar::ONE,
      }
    }
  }
```

**File:** networks/bitcoin/src/wallet/mod.rs (L198-214)
```rust
  /// Scan a transaction.
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

**File:** networks/bitcoin/src/wallet/mod.rs (L221-228)
```rust
  pub fn scan_block(&self, block: &Block) -> Vec<ReceivedOutput> {
    let mut res = Vec::new();
    for tx in &block.txdata {
      res.extend(self.scan_transaction(tx));
    }
    res
  }
}
```

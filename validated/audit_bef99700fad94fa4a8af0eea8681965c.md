### Title
Scanner misses valid Taproot outputs paying to the untweaked x-only group key, locking received funds - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
The external bug class is "code assumes every valid target conforms to a single interface/encoding and rejects the rest" (`try pool.getLastInvariant() ... catch revert`). The Serai analog is in `Scanner`: it assumes every payment to the wallet arrives as a BIP-341 *tweaked* key-path P2TR output and matches only the exact `script_pubkey` produced by `p2tr_script_buf`. A standard, spendable P2TR output that commits directly to the untweaked x-only group key (`OP_1 <x_only(group_key)>`) is silently skipped by `scan_transaction`, so the funds are confirmed on-chain but never reported and can never be spent through Serai's signing path.

### Finding Description
`Scanner::new` registers exactly one script: `p2tr_script_buf(key)`, which builds `ScriptBuf::new_p2tr_tweaked(TweakedPublicKey::dangerous_assume_tweaked(x_only(&key)))` — the BIP-341 tweaked output key only [1](#0-0) . `scan_transaction` then does an exact `HashMap` lookup on `output.script_pubkey` and ignores every output not in that set [2](#0-1) . The same assumption is repeated for every registered offset [3](#0-2) .

On the spend side, `SignableTransaction::multisig` hard-requires the prevout's `script_pubkey` to equal `p2tr_script_buf(offset.group_key())`, returning `None` otherwise [4](#0-3) . There is no alternate code path that recognizes or spends an output whose output key equals the untweaked internal key, even though BIP-340 key-path semantics make `OP_1 <x>` spendable by the holder of the corresponding secret (signing directly with `s` rather than the tweaked `s + H(P)`). An unprivileged payer can create such an output with any Bitcoin wallet that sends to a raw x-only Taproot key.

### Impact Explanation
Any party can send BTC to the multisig's x-only key as a plain (untweaked) P2TR output. The transaction confirms, the UTXO exists and is economically controlled by the group key's discrete log, yet `scan_transaction`/`scan_block` never emit a `ReceivedOutput` for it, and `multisig()` would refuse to sign for it even if one were constructed. The result is funds irrevocably unreported and unspendable — the direct analog of the Olympus pools being unusable because they lack `getLastInvariant()`.

### Likelihood Explanation
Reachable by any unprivileged external sender with a standard Bitcoin transaction; no validator cooperation, malformed encoding, or leaked material is required. Whether payers actually use untweaked x-only outputs is a wallet-behavior question, but the encoding is valid consensus-wise and requires no cooperation from Serai.

### Recommendation
Extend the scanner/recognition logic to also match (or provide an explicit API for) outputs paying to the untweaked x-only group key and registered offset keys, and add a corresponding signing path that signs with the untweaked offset key when `prevout.script_pubkey` is `ScriptBuf::new_p2tr(x_only(internal))` rather than the tweaked script. Alternatively, document the invariant that only BIP-341 tweaked key-path outputs are supported so integrators do not treat arbitrary P2TR payments to the key as receivable.

### Proof of Concept
```rust
// Given an even group_key `key`:
let scanner = Scanner::new(key).unwrap();
// A payer sends to the untweaked x-only key (valid, spendable by key-path):
let untweaked = ScriptBuf::new_p2tr_tweaked(
    TweakedPublicKey::dangerous_assume_tweaked(x_only_of_internal)
); // vs. a raw `OP_1 <x>` built without TapTweak:
let raw = ScriptBuf::from(vec![0x51, 0x20, /* x_only(key) bytes */]);
let tx = Transaction { /* ... output[0].script_pubkey = raw ... */ };
assert!(scanner.scan_transaction(&tx).is_empty()); // UTXO invisible; funds locked
```
The exact-match lookup at `networks/bitcoin/src/wallet/mod.rs:205` drops the output, and `multisig()` at `networks/bitcoin/src/wallet/send.rs:277` rejects any `ReceivedOutput` whose `script_pubkey` is not the tweaked form, leaving no spend path.

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

**File:** networks/bitcoin/src/wallet/send.rs (L273-285)
```rust
  pub fn multisig(self, keys: &ThresholdKeys<Secp256k1>) -> Option<TransactionMachine> {
    let mut sigs = vec![];
    for i in 0 .. self.tx.input.len() {
      let offset = keys.clone().offset(self.offsets[i]);
      if p2tr_script_buf(offset.group_key())? != self.prevouts[i].script_pubkey {
        None?;
      }

      sigs.push(AlgorithmMachine::new(Schnorr::new(), keys.clone().offset(self.offsets[i])));
    }

    Some(TransactionMachine { tx: self, sigs })
  }
```

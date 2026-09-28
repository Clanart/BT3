### Title
Scanner only credits exact P2TR script_pubkeys, permanently missing funds sent to the same key via other output types - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
The external report describes a strict interface check (`require(token.transfer(...)) == true`) that rejects legitimate-but-nonconforming tokens (USDT), making them untransferable/stuck. The Serai analog lives in `networks/bitcoin/src/wallet/mod.rs`: the `Scanner` recognizes a received output only by exact byte-equality of `script_pubkey` against a precomputed map of `ScriptBuf::new_p2tr_tweaked` scripts. Any payment spendable by the same secp256k1 key but delivered via any other output type (P2PKH, P2WPKH, P2SH/P2WSH wrapping, raw P2TR keyed to the untweaked key, or a standard-looking variant) is silently ignored — the funds exist on-chain under key control, yet are never reported to the processor and can never be included in a Plan.

### Finding Description
`Scanner::new` inserts exactly one script — the tweaked P2TR script for the key — and `register_offset` adds only more `p2tr_script_buf` outputs [1](#0-0) [2](#0-1) . `scan_transaction` then credits an output only if `self.scripts.get(&output.script_pubkey)` hits [3](#0-2) .

This mirrors the audit finding's shape: an over-strict acceptance check that keys on an exact surface format rather than on the underlying spend authority. In the report, `transferERC20` required a `bool` return that legitimate tokens don't provide; here, crediting requires an exact P2TR-tweaked script that legitimate spendable payments don't necessarily use. An unprivileged external sender controls the `script_pubkey` of their transaction outputs — they can send BTC to `P2PKH(H(compressed_key))`, `P2WPKH`, or `P2TR` built on the untweaked x-only key, all of which are key-spendable given the threshold shares, yet `scan_transaction` returns an empty `Vec` for them [4](#0-3) . `scan_block` offers no fallback path either [5](#0-4) .

Because `ReceivedOutput`s are the only ingress into `Plan`s (the scheduler consumes scanned outputs as `inputs`), an output the scanner never emits is permanently unschedulable — identical in effect to the stuck-USDT outcome in the source report.

### Impact Explanation
Funds sent to the multisig via any non-P2TR-tweaked script type are received on-chain, remain under the threshold key's control, but are never reported as `ScannerEvent::Block` outputs and never become spendable inputs to any Plan. They are effectively burned from the protocol's perspective — the same "impacted tokens will not be transferrable and would be stuck in contract" impact, mapped onto Bitcoin receipt detection rather than ERC20 dispatch.

### Likelihood Explanation
Any external party constructing a Bitcoin transaction to the multisig controls the output script. Reaching the bug requires only sending funds with a script type other than the one exact `ScriptBuf` registered — no validator cooperation, no malformed encoding, no private state. Whether this is exploitable in practice depends on how depositors learn the deposit address; if Serai publishes only the P2TR address, external senders using that address are safe, but nothing cryptographically or economically prevents anyone (mistakenly or deliberately) from paying the same key via a different script type.

### Recommendation
Document and enforce at the address-distribution layer that only the tweaked-P2TR deposit address is valid, or broaden `scan_transaction` to also recognize other standard output types bound to the same key material (e.g., P2WPKH/P2PKH over the compressed key, P2TR over the untweaked x-only key). If strictness is intentional, add an explicit recovery/bailout path so funds sent to key-controlled non-P2TR scripts can be reclaimed, rather than relying on the scanner's exact-match map.

### Proof of Concept
1. Multisig key `K` is established; `Scanner::new(K)` registers `scripts = { p2tr_tweaked(K) }` [1](#0-0) .
2. An external sender crafts a Bitcoin transaction paying 1 BTC via `OP_DUP OP_HASH160 <H(K_compressed)> OP_EQUALVERIFY OP_CHECKSIG` (P2PKH to the same key) or `ScriptBuf::new_p2tr` on the untweaked x-only key.
3. `scanner.scan_block(block)` iterates outputs; `self.scripts.get(&output.script_pubkey)` returns `None` for every output, so `res` is empty [6](#0-5) .
4. No `ScannerEvent` is emitted for the block's outputs; the scheduler never sees the input; the BTC is unspendable by the protocol despite being controlled by the threshold key — funds received that are not spendable.

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L162-166)
```rust
  pub fn new(key: ProjectivePoint) -> Option<Scanner> {
    let mut scripts = HashMap::new();
    scripts.insert(p2tr_script_buf(key)?, Scalar::ZERO);
    Some(Scanner { key, scripts })
  }
```

**File:** networks/bitcoin/src/wallet/mod.rs (L185-195)
```rust
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

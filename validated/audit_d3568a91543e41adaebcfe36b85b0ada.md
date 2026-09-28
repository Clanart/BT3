### Title
Anyone can forge privileged `Branch`/`Change`/`Forwarded` outputs by paying to deterministically derivable offset scripts - ([File: networks/bitcoin/src/wallet/mod.rs](networks/bitcoin/src/wallet/mod.rs))

### Summary
Analogous to Astaria's missing whitelist on `newPublicVault` (anyone can create a privileged vault object the system trusts), Serai's `Scanner` grants privileged status — internal `OutputType`s — to any received output purely by `script_pubkey` match, while the scripts for those privileged kinds are deterministically derivable by any unprivileged party. There is no access control distinguishing self-created outputs from attacker-created ones.

### Finding Description
`Scanner::scan_transaction` reports any transaction output whose `script_pubkey` exists in `self.scripts` as a `ReceivedOutput` bound to the registered offset, with no further authentication of who created it or why [1](#0-0) . The privileged offset scripts are produced by `register_offset`, which deterministically maps `key + offset*G` to a P2TR script [2](#0-1) . The offsets for the privileged `OutputType::{Branch, Change, Forwarded}` kinds are fixed public values — `Secp256k1::hash_to_F("Serai Bitcoin Output Offset", b"branch"|"change"|"forward")` — incremented only until the key is even [3](#0-2) . Any observer who knows the group key (public on-chain) can recompute all three scripts locally and pay to them. `get_outputs` then stamps the attacker-funded output with the internal `kind` from `kinds[offset_repr]` [4](#0-3) , exactly as `scan_transaction` cannot distinguish a legitimately produced change output from a forged one.

### Impact Explanation
An unprivileged Bitcoin user can mint `ReceivedOutput`s carrying the internal `Branch`, `Change`, or `Forwarded` classifications — objects the downstream pipeline treats as protocol-generated state (change from its own spends, branch/forwarding flows), not as external deposits. This lets the attacker inject phantom internal outputs into the scanner's emitted stream at will, corrupting output classification and accounting the same way unwhitelisted public vaults corrupted Astaria's trusted vault registry. The outputs carry only dust cost to the attacker (`N::DUST` filter is 10,000 sats) [5](#0-4) .

### Likelihood Explanation
The offset scripts are fully computable from public data (group key + fixed hash-to-F DSTs), so no secret or validator privilege is needed — only a standard Bitcoin transaction. Any party can do this at any time for any registered key.

### Recommendation
Do not rely on `script_pubkey` equality alone to assign internal `OutputType`s. Internal kinds should be confirmed out-of-band — e.g., the wallet/processor should only classify an output as `Change`/`Branch`/`Forwarded` if its outpoint was produced by a transaction the multisig itself signed (track expected change outpoints at spend time), and treat unsolicited payments to those scripts as `External` deposits or reject them.

### Proof of Concept
1. Observe the multisig group key `K` on chain (or from the external address).
2. Locally compute `branch = hash_to_F(KEY_DST, "branch")`, incrementing until `K + branch*G` is even, and build `p2tr_script_buf(K + branch*G)` — identical to `register_offset`'s derivation [6](#0-5) .
3. Broadcast any Bitcoin transaction paying ≥ dust to that script.
4. `Scanner::scan_transaction` returns a `ReceivedOutput` with `offset == branch`; `get_outputs` emits it as `OutputType::Branch` — a privileged internal output created without any authorization.

### Citations

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

**File:** processor/src/networks/bitcoin.rs (L333-344)
```rust
  register(
    OutputType::Branch,
    *BRANCH_OFFSET.get_or_init(|| Secp256k1::hash_to_F(KEY_DST, b"branch")),
  );
  register(
    OutputType::Change,
    *CHANGE_OFFSET.get_or_init(|| Secp256k1::hash_to_F(KEY_DST, b"change")),
  );
  register(
    OutputType::Forwarded,
    *FORWARD_OFFSET.get_or_init(|| Secp256k1::hash_to_F(KEY_DST, b"forward")),
  );
```

**File:** processor/src/networks/bitcoin.rs (L562-566)
```rust
}

// Bitcoin has a max weight of 400,000 (MAX_STANDARD_TX_WEIGHT)
// A non-SegWit TX will have 4 weight units per byte, leaving a max size of 100,000 bytes
// While our inputs are entirely SegWit, such fine tuning is not necessary and could create
```

**File:** processor/src/networks/bitcoin.rs (L686-700)
```rust
  async fn get_outputs(&self, block: &Self::Block, key: ProjectivePoint) -> Vec<Output> {
    let (scanner, _, kinds) = scanner(key);

    let mut outputs = vec![];
    // Skip the coinbase transaction which is burdened by maturity
    for tx in &block.txdata[1 ..] {
      for output in scanner.scan_transaction(tx) {
        let offset_repr = output.offset().to_repr();
        let offset_repr_ref: &[u8] = offset_repr.as_ref();
        let kind = kinds[offset_repr_ref];

        let output = Output { kind, presumed_origin: None, output, data: vec![] };
        assert_eq!(output.tx_id(), tx.id());
        outputs.push(output);
      }
```

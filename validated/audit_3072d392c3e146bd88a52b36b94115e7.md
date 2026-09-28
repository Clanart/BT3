### Title
Attacker can send to publicly-derivable internal offset addresses and have funds misclassified as internal outputs (Branch/Change/Forwarded) - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`Scanner` attributes a received output's semantic kind purely via a `script_pubkey -> offset` lookup (`Scanner.scripts`). The offsets registered for internal output kinds (branch, change, forwarded) are deterministic outputs of `Secp256k1::hash_to_F(KEY_DST, b"branch" | b"change" | b"forward")` with a public, fixed domain separator (`b"Serai Bitcoin Output Offset"`). Any unprivileged party who knows the group key can therefore compute the exact internal `script_pubkey`s, send a transaction output to one of them, and `Scanner::scan_transaction`/`scan_block` will emit a `ReceivedOutput` carrying the corresponding internal offset — identical in shape to a crafted "attachment filename" resolving to an unintended internal entry.

### Finding Description
`Scanner::register_offset` inserts `p2tr_script_buf(key + offset*G) -> offset` into `self.scripts`, and `scan_transaction` returns a `ReceivedOutput` whose `offset` is whatever the map holds for a matching `script_pubkey` — with no further authentication of intent: [1](#0-0) [2](#0-1) 

The offsets for `Branch`, `Change`, and `Forwarded` are derived from a fixed public DST via `hash_to_F`, so the internal scripts are publicly computable for any known group key: [3](#0-2) 

Additionally, `ReceivedOutput::read` accepts an arbitrary `offset` scalar with no consistency check against `output.script_pubkey`, so deserialized outputs trusted downstream carry an attacker-influenceable re-keying scalar: [4](#0-3) 

### Impact Explanation
An attacker can cause outputs they fully control to be reported by the scanner as internal outputs (kind-specific offsets such as the forwarded or change offset). Downstream consumers attribute semantics (external deposit vs. internal change/forward, attached `data` field, per-input offset re-keying when spending) to the scanned `offset`. This yields funds "reported received" under an internal classification the protocol did not itself create, influencing how the output is later aggregated, re-keyed (`key + offset*G`), and spent. Because `scan_block` also scans the coinbase transaction and defers maturity handling to the caller, miner-influenced outputs are similarly accepted by label rather than context.

### Likelihood Explanation
Reachable by any unprivileged party sending a standard Bitcoin transaction to a computable P2TR script — no collusion, no privileged position, and no malformed encoding required. Exploitation only requires knowledge of the group key (public) and the fixed DST (in the source). Cost is only the deposited value, which remains spendable by the group key, meaning the attacker does not burn funds; the impact lands on protocol bookkeeping/attribution.

### Recommendation
Bind internal-output offsets to unpredictable or context-authenticated values (e.g., derive kind offsets from per-session or per-output secrets rather than a fixed public DST), or tag internal outputs on-chain in a way not forgeable by external senders. In `ReceivedOutput::read` / spending paths, re-derive `p2tr_script_buf(key + offset*G)` and assert equality with `output.script_pubkey` before treating the offset as authoritative. Consider restricting `scan_block` to non-coinbase transactions internally rather than relying on callers' post-processing.

### Proof of Concept
```rust
// Attacker knows the group key `key` and the public DST.
let change_offset = Secp256k1::hash_to_F(b"Serai Bitcoin Output Offset", b"change");
let internal_script = p2tr_script_buf(key + (ProjectivePoint::GENERATOR * change_offset))
    .expect("registered offsets are bumped until even");

// Attacker broadcasts a TX paying `internal_script` with amount >= dust.
// Scanner::scan_transaction finds `internal_script` in `self.scripts` and
// emits ReceivedOutput { offset: <registered change offset>, .. } —
// an externally-created output carrying the internal "change" classification.
let outputs = scanner.scan_transaction(&attacker_tx);
assert_eq!(outputs[0].offset(), change_offset); // misclassified as internal
```

Caveat: I verified the scanner-side mechanics and the public-derivability of offsets, but the exact downstream handling of kind-misclassified outputs (e.g., forwarding behavior) lives in `processor/src/networks/bitcoin.rs`, which is outside the stated in-scope set — the root cause (`scripts` keyed solely on `script_pubkey` plus publicly derivable offsets) is in-scope in `networks/bitcoin/src/wallet/mod.rs`.

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L120-134)
```rust
  /// Read a ReceivedOutput from a generic satisfying Read.
  #[cfg(feature = "std")]
  pub fn read<R: Read>(r: &mut R) -> io::Result<ReceivedOutput> {
    let offset = Secp256k1::read_F(r)?;
    let output;
    let outpoint;
    {
      let mut buf_r = BufReader::with_capacity(0, r);
      output =
        TxOut::consensus_decode(&mut buf_r).map_err(|_| io::Error::other("invalid TxOut"))?;
      outpoint =
        OutPoint::consensus_decode(&mut buf_r).map_err(|_| io::Error::other("invalid OutPoint"))?;
    }
    Ok(ReceivedOutput { offset, output, outpoint })
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

**File:** networks/bitcoin/src/wallet/mod.rs (L199-213)
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
```

**File:** processor/src/networks/bitcoin.rs (L308-346)
```rust
const KEY_DST: &[u8] = b"Serai Bitcoin Output Offset";
static BRANCH_OFFSET: OnceLock<Scalar> = OnceLock::new();
static CHANGE_OFFSET: OnceLock<Scalar> = OnceLock::new();
static FORWARD_OFFSET: OnceLock<Scalar> = OnceLock::new();

// Always construct the full scanner in order to ensure there's no collisions
fn scanner(
  key: ProjectivePoint,
) -> (Scanner, HashMap<OutputType, Scalar>, HashMap<Vec<u8>, OutputType>) {
  let mut scanner = Scanner::new(key).unwrap();
  let mut offsets = HashMap::from([(OutputType::External, Scalar::ZERO)]);

  let zero = Scalar::ZERO.to_repr();
  let zero_ref: &[u8] = zero.as_ref();
  let mut kinds = HashMap::from([(zero_ref.to_vec(), OutputType::External)]);

  let mut register = |kind, offset| {
    let offset = scanner.register_offset(offset).expect("offset collision");
    offsets.insert(kind, offset);

    let offset = offset.to_repr();
    let offset_ref: &[u8] = offset.as_ref();
    kinds.insert(offset_ref.to_vec(), kind);
  };

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

  (scanner, offsets, kinds)
```

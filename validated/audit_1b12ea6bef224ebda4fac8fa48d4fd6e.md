### Title
`ReceivedOutput::read` accepts an untrusted scalar `offset` and `TxOut` without verifying the offset actually derives the output's `script_pubkey` — breaking the invariant that a received output is spendable by the threshold key - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary

The external report describes a missing membership check: `claimRewards`/`distributeRewards` accept a user-supplied `_reward` token without checking it is in `rewardTokens`, breaking the `pointCorrection` invariant and enabling repeated claims that drain the pool. The Serai analog lives in the Bitcoin wallet scanner: `Scanner` maintains the authoritative `scripts: HashMap<ScriptBuf, Scalar>` whitelist mapping `P2TR(key + offset*G)` scripts to their spending offsets [1](#0-0) , and `scan_transaction` only emits `ReceivedOutput`s whose `script_pubkey` is in that map [2](#0-1) . However, `ReceivedOutput::read` deserializes `offset` via `Secp256k1::read_F` and the `TxOut`/`OutPoint` via consensus decoding with no check whatsoever that `offset` is a registered offset or that `output.script_pubkey == P2TR(key + offset*G)` [3](#0-2) . The whitelist invariant enforced by `Scanner` is silently dropped on the deserialization path — the exact same "identifier not checked against the allowed list" bug class.

### Finding Description

`Scanner::register_offset` carefully maps each registered scalar to the script `p2tr_script_buf(self.key + GENERATOR * offset)` and even rejects script collisions [4](#0-3) . `scan_transaction` then treats membership in `self.scripts` as proof that an output is spendable via `offset` [5](#0-4) . But a `ReceivedOutput` can also be constructed purely from attacker-controlled bytes through `ReceivedOutput::read`, which is explicitly reachable per the scope (it is listed among the read sinks). There, `offset`, `output` (including `script_pubkey` and `value`), and `outpoint` are all taken verbatim — the triple is never re-validated against the scanner's `scripts` map or against the group key. The result is a `ReceivedOutput` where `offset` and the on-chain spend condition are uncorrelated, exactly like a `_reward` address not in `rewardTokens` being accepted into the accounting path.

### Impact Explanation

Because `ReceivedOutput` is the unit consumed by the transaction-building path (`send.rs`) to produce spends, any downstream consumer that loads `ReceivedOutput`s from serialized storage or an untrusted channel will treat forged entries as real deposits:

- **Funds reported received that are not spendable:** an attacker crafts `offset = δ` plus a `TxOut`/`outpoint` for a real UTXO paying to an unrelated script (e.g., the attacker's own key). The wallet credits the output's `value` [6](#0-5)  but the signature produced with `key + δ·G` is meaningless for that UTXO. Mirroring the report, the pool/wallet's accounting ("received" set) diverges from what is actually claimable/spendable, and repeated forged entries poison the UTXO set — the wallet will select unspendable inputs, burning fees on transactions that can never confirm, and inflating the reported balance.
- **Misdirected spend attempts:** with `outpoint` also attacker-chosen, the wallet can be induced to attempt spending arbitrary outpoints, enabling the same "repeat the claim" amplification as the report's loop (steps 2–4): each forged `ReceivedOutput` is independently accepted.

This satisfies the acceptance criterion "funds reported received that are not spendable" — the `offset`/`output` pairing is never re-derived from the group key the way `Scanner` guarantees it.

### Likelihood Explanation

Reachable whenever `ReceivedOutput`s cross a trust boundary — serialized scanner output stored in a DB shared with less-trusted components, relayed between processes, or reconstructed from coordinator-supplied data. The attacker needs only to supply bytes; no key material, validator status, or collusion is required. Exploitation is deterministic (no cryptographic assumptions), but impact is bounded to the wallet's accounting/spend path rather than direct private-key recovery, and a fully honest pipeline that only ever uses `Scanner::scan_transaction` results is unaffected — consistent with a Medium-severity analog.

### Recommendation

Enforce the whitelist invariant on the deserialization path, just as the report recommends `require(rewardTokensList[_reward], "Invalid reward")`:

1. Re-derive the binding at construction: given a `ReceivedOutput` and the scanner's key, require `output.script_pubkey == p2tr_script_buf(key + GENERATOR * offset)` — equivalent to checking `_reward ∈ rewardTokens`.
2. Prefer a fallible constructor `ReceivedOutput::new(offset, output, outpoint, key) -> Option<Self>` over the raw struct, and have `read` delegate to it, so the invariant cannot be bypassed by construction.
3. Alternatively, carry the `ScriptBuf → Scalar` map through to the consumer and look up `output.script_pubkey` in it rather than trusting the serialized `offset` field.

### Proof of Concept

```rust
// networks/bitcoin/src/wallet/mod.rs path
// Let `scanner = Scanner::new(threshold_group_key)` with no registered extra offsets.
// Whitelist contains only: p2tr_script_buf(key) -> Scalar::ZERO.

// Attacker picks a real UTXO paying to THEIR OWN key:
let attacker_out = TxOut {
    value: Amount::from_sat(100_000),
    script_pubkey: ScriptBuf::new_p2tr_tweaked(attacker_tweaked_key),
};
let outpoint = OutPoint::new(attacker_txid, 0);

// Attacker serializes a ReceivedOutput with arbitrary offset (e.g., 7)
// which never passed through Scanner::register_offset / scan_transaction:
let mut bytes = Vec::new();
bytes.extend(Scalar::from(7u64).to_bytes());          // read_F accepts it
bytes.extend(serialize(&attacker_out));               // unverified TxOut
bytes.extend(serialize(&outpoint));

let forged = ReceivedOutput::read(&mut bytes.as_slice()).unwrap();
// forged.value() == 100_000 -> wallet credits 100k sats
// No check that output.script_pubkey == p2tr_script_buf(key + 7*G)
// The spend attempt signs with key + 7*G, which does not control attacker_out:
// -> funds "received" are provably not spendable; repeats arbitrarily.
```

The fix corresponds directly to the report's remediation: the `offset` (the analog of `_reward`) must be proven a member of the registered-offset/script set before the `ReceivedOutput` is accepted into the spendable set.

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L116-118)
```rust
  pub fn value(&self) -> u64 {
    self.output.value.to_sat()
  }
```

**File:** networks/bitcoin/src/wallet/mod.rs (L122-134)
```rust
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

**File:** networks/bitcoin/src/wallet/mod.rs (L153-166)
```rust
pub struct Scanner {
  key: ProjectivePoint,
  scripts: HashMap<ScriptBuf, Scalar>,
}

impl Scanner {
  /// Construct a Scanner for a key.
  ///
  /// Returns None if this key can't be scanned for.
  pub fn new(key: ProjectivePoint) -> Option<Scanner> {
    let mut scripts = HashMap::new();
    scripts.insert(p2tr_script_buf(key)?, Scalar::ZERO);
    Some(Scanner { key, scripts })
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

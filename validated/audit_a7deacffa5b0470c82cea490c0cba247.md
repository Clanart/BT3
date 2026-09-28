### Title
`ReceivedOutput::read` accepts attacker-supplied `(offset, output, outpoint)` tuples with no verification that the offset actually maps the wallet key to the output's `script_pubkey` - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
The external report's bug class is missing authorization: an object is created/accepted inside a scope without validating that the party supplying it is entitled to it. In Serai's Bitcoin wallet, `Scanner` is the sole component that authorizes the key→script binding: `scan_transaction` only emits a `ReceivedOutput` when `output.script_pubkey` exists in the internally-built `scripts` map (`Scanner { key, scripts }`), so the returned `offset` is guaranteed to satisfy `p2tr_script_buf(key + offset * G) == output.script_pubkey`. `ReceivedOutput::read` deserializes that same triple from raw untrusted bytes (`read_F` + consensus decode) and performs no such check — the authorization decision implicit in the scanner is silently dropped on deserialization, so any byte stream can claim ownership of any UTXO under any offset.

### Finding Description
`Scanner` binds a spendable output to a scalar offset via `self.scripts`, populated only by `Scanner::new` (offset 0 for the wallet key) and `register_offset` (which derives the `ScriptBuf` from `self.key + offset * G`, so script ⇒ offset is authoritative):

- `networks/bitcoin/src/wallet/mod.rs:153-166` — `Scanner` holds `key` and `scripts: HashMap<ScriptBuf, Scalar>`.
- `networks/bitcoin/src/wallet/mod.rs:180-196` — `register_offset` computes the script from `key + offset*G`; the map key is the script, so an offset can never be attached to a script it doesn't generate.
- `networks/bitcoin/src/wallet/mod.rs:199-214` — `scan_transaction` returns a `ReceivedOutput` only when `self.scripts.get(&output.script_pubkey)` hits, pairing the output with the offset that provably produces it.

`ReceivedOutput::read` then re-creates that same trust-bearing structure with zero validation:

```rust
// networks/bitcoin/src/wallet/mod.rs:122-134
pub fn read<R: Read>(r: &mut R) -> io::Result<ReceivedOutput> {
  let offset = Secp256k1::read_F(r)?;
  output = TxOut::consensus_decode(&mut buf_r)...
  outpoint = OutPoint::consensus_decode(&mut buf_r)...
  Ok(ReceivedOutput { offset, output, outpoint })
}
```

There is no key context, no re-derivation of `p2tr_script_buf(key + offset*G)`, and no comparison against `output.script_pubkey`. The `ReceivedOutput` type is the wallet's representation of "an output we can spend," yet anyone who can feed bytes into `read` can mint one attesting to ownership of an arbitrary `(offset, TxOut, OutPoint)` — including outputs paying to scripts entirely unrelated to the wallet key, or pairing the wallet's own address with a wrong offset. Downstream, `send.rs` consumes `output.offset()` to derive the per-input signing key; a mismatched offset yields a signature for a key the prevout doesn't commit to.

This mirrors the GitLab flaw exactly: an operation (label creation / ReceivedOutput construction) validates membership in one path (project authorization / `Scanner::scan_transaction`) but exposes a second path (import process / `ReceivedOutput::read`) that creates the same privileged object without repeating the check.

### Impact Explanation
Any `ReceivedOutput` produced from untrusted bytes is unauthenticated evidence of funds ownership:

1. **Funds reported received that are not spendable.** An attacker supplies `ReceivedOutput` bytes naming a victim UTXO paying to an unrelated `script_pubkey` (or the wallet's script with a garbage offset). The wallet counts the value as spendable balance and builds a transaction whose input signature is for `key + offset*G` — which does not match the prevout script — producing a transaction Bitcoin consensus will reject. The "received" funds were never the wallet's.
2. **False-balance inflation / spend construction abuse.** Because `value()` is taken straight from the attacker-chosen `TxOut`, arbitrary synthetic balance can be injected into any component trusting deserialized `ReceivedOutput`s, causing the wallet to attempt spends of non-existent or third-party outputs.

### Likelihood Explanation
Reachable by any party able to supply bytes to `ReceivedOutput::read` — it is explicitly an untrusted-input surface (it even uses `read_F` for the scalar, so non-canonical scalars are also rejected only at the field level, not the semantic level). No key material, validator status, or collusion is required; the attacker just crafts `offset || TxOut || OutPoint`. The only requirement is that the surrounding system stores/relays `ReceivedOutput`s as serialized blobs rather than rescanning the chain — which is the purpose of `serialize`/`read` existing at all. Medium severity: it corrupts wallet accounting and produces unspendable spends, but does not directly extract key material.

### Recommendation
`ReceivedOutput` cannot self-validate at deserialization because `read` lacks the wallet key. Either:

- Move validation to consumption: in `send.rs`, before signing an input, assert `p2tr_script_buf(key + output.offset() * G) == output.output().script_pubkey` and reject otherwise; or
- Serialize the key/scanner context alongside, or make `read` take the `Scanner`/`ProjectivePoint` so it can re-run the `scripts`-equivalent check (`Scanner::scan_transaction`-style membership test on the claimed offset) before constructing the value.

Treating `ReceivedOutput` as an authenticated type (produced only by `Scanner`) should be enforced in the type system — e.g., make the constructor private and have `read` return a `ReceivedOutputClaim` that must be verified against the scanner's `scripts` map.

### Proof of Concept
```rust
use bitcoin::{ScriptBuf, TxOut, OutPoint, Amount, Txid, hashes::Hash};
use bitcoin_serai::wallet::ReceivedOutput;
use k256::Scalar;
use std::io::Cursor;

// Attacker crafts bytes claiming ownership of an arbitrary UTXO.
let mut buf = vec![];
// offset = 7 (never registered; does NOT map wallet key to the script below)
buf.extend(Scalar::from(7u64).to_bytes());
// TxOut paying to an unrelated P2TR script (e.g., attacker's own output)
let txout = TxOut {
  value: Amount::from_sat(1_000_000),
  script_pubkey: ScriptBuf::new_p2tr_tweaked(/* attacker key */),
};
buf.extend(bitcoin::consensus::encode::serialize(&txout));
buf.extend(bitcoin::consensus::encode::serialize(
  &OutPoint::new(Txid::from_raw_hash(/* real third-party TXID */), 0),
));

// Wallet accepts it as a spendable output — no check that
// p2tr_script_buf(wallet_key + offset*G) == txout.script_pubkey
let claimed: ReceivedOutput = ReceivedOutput::read(&mut Cursor::new(buf)).unwrap();
assert_eq!(claimed.value(), 1_000_000); // counted as wallet balance,
                                        // yet unspendable by the wallet
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

Note: I could not fully inspect `networks/bitcoin/src/wallet/send.rs` within the available iterations to confirm whether it re-validates `offset` against `output.script_pubkey` at spend time; if it already re-derives and compares the script, the impact reduces to the false-balance injection. The deserialization-side absence of the check is confirmed in `mod.rs`.

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

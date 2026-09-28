### Title
`ReceivedOutput::read` accepts arbitrary `TxOut`/`offset` bytes without verifying the output is controlled by the scanned key — ([File: networks/bitcoin/src/wallet/mod.rs](networks/bitcoin/src/wallet/mod.rs))

### Summary
The upstream bug class is "an unrestricted entry point accepts any asset identifier without validation, causing assets to be booked as received while being unrecoverable/unspendable". In `bitcoin-serai`, `ReceivedOutput::read` deserializes a scalar `offset`, an arbitrary `TxOut`, and an `OutPoint` from a byte stream with no check that `output.script_pubkey` equals `p2tr(key + offset·G)` for any registered offset — i.e., there is no "whitelist" tying the claimed output to the keys the wallet can actually sign for. This is reachable from untrusted bytes per the prompt's allow-list (`ReceivedOutput::read`).

### Finding Description
`Scanner` maintains the legitimate mapping in `self.scripts: HashMap<ScriptBuf, Scalar>` and only produces a `ReceivedOutput` inside `scan_transaction` when `output.script_pubkey` is a registered script (`networks/bitcoin/src/wallet/mod.rs:205-210`). That invariant — "a `ReceivedOutput`'s script is one of our P2TR key-path scripts at some registered `offset`" — is enforced on the scanning path but completely absent on the deserialization path:

```rust
pub fn read<R: Read>(r: &mut R) -> io::Result<ReceivedOutput> {
  let offset = Secp256k1::read_F(r)?;
  let output = TxOut::consensus_decode(...)?;
  let outpoint = OutPoint::consensus_decode(...)?;
  Ok(ReceivedOutput { offset, output, outpoint })
}
```

Any caller that rebuilds `ReceivedOutput`s from relayed/stored bytes gets an object indistinguishable from one produced by honest scanning, yet:

- `offset` may correspond to no registered script (or even make `key + offset·G` odd/infinity),
- `output.script_pubkey` may be a script belonging to an entirely different owner — or an unspendable script,
- `outpoint` may reference an output that does not match `output` at all (script and value are not bound to the outpoint).

The struct's private fields make the type nominal: once constructed, consumers trust `offset()`/`output()`/`value()` as a spendable wallet UTXO, exactly as the `deposit()` token parameter was trusted to map to an L2-receivable asset.

### Impact Explanation
An unprivileged party who can feed bytes to `ReceivedOutput::read` (the sole constructor alternative to `scan_transaction`) can cause the wallet to report a `ReceivedOutput` as a spendable received deposit when it is not:

- **Funds reported received that are not spendable**: an output with an arbitrary `script_pubkey` not derived from the group key is booked with a claimed `offset`; any later attempt to spend it via `offset()` will fail to produce a valid signature, so the "deposit" is phantom — the mirror image of tokens stranded in `MultipliBridger` with no automated recovery.
- **Mis-accounted value**: `value()` is taken verbatim from the attacker-chosen `TxOut`, with no consistency check against the chain output at `outpoint`.

This is not a malicious-validator or integrator-misuse analog: the reachability boundary is exactly the untrusted-bytes `read` API the prompt enumerates, and the missing validation is inside the library — `read` could verify `p2tr_script_buf(scanner_key + offset·G) == output.script_pubkey` but does not, nor does it re-check the script against any registered-offset set.

### Likelihood Explanation
Medium. Exploitation requires an attacker to influence the byte stream feeding `ReceivedOutput::read` — e.g., outputs gossiped between processors or persisted/replayed data — which is precisely the input surface the analog rules treat as reachable. No private key material, collusion, or broken consensus is needed. The cryptographic primitive failures (odd group key, infinity) are also unchecked on this path. Impact is bounded to false crediting / unspendable bookkeeping rather than direct theft, matching the Medium severity of the original finding.

### Recommendation
Bind deserialization to the scan invariant. Either:

1. Make `ReceivedOutput::read` private/crate-internal and provide `Scanner::read_received(&self, r)` which, after decoding, verifies `self.scripts.contains_key(&output.script_pubkey)` and that the decoded `offset` equals the registered offset for that script; or
2. Store the `Scanner` key on `ReceivedOutput` and assert `p2tr_script_buf(key + GENERATOR * offset) == Some(output.script_pubkey)` on read, rejecting mismatches with `io::Error`.

This is the direct analog of the recommended token whitelist: an explicit membership check at the trust boundary instead of trusting the type name.

### Proof of Concept
```rust
use bitcoin::{TxOut, OutPoint, ScriptBuf, Amount, Txid, hashes::Hash};
use bitcoin_serai::wallet::ReceivedOutput;
use k256::Scalar;
use std_shims::io::Write;

// Attacker bytes: offset = 1, a TxOut paying to the ATTACKER's script,
// and an arbitrary outpoint.
let mut bytes = vec![];
bytes.extend(Scalar::ONE.to_bytes());                        // offset
bytes.extend(bitcoin::consensus::encode::serialize(&TxOut {
  value: Amount::from_sat(100_000),
  script_pubkey: ScriptBuf::new_op_return(&[0xde, 0xad]),    // anyone's/unspendable
}));
bytes.extend(bitcoin::consensus::encode::serialize(&OutPoint::new(Txid::all_zeros(), 0)));

let ro = ReceivedOutput::read(&mut bytes.as_slice()).unwrap();
// ro is now a fully-typed "spendable" output: value() = 100_000 sats,
// offset() = 1 — yet no registered Scanner script matches its script_pubkey
// and key + 1·G cannot authorize spending it. Any caller trusting the
// ReceivedOutput type credits 100k sats that can never be signed for.
``` [1](#0-0) [2](#0-1) [3](#0-2)

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

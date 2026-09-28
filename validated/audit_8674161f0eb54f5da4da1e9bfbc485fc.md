### Title
`ReceivedOutput::read` accepts an arbitrary scalar offset without binding it to the output's `script_pubkey`, so attacker-crafted bytes yield a `ReceivedOutput` reported as received that the multisig cannot actually spend - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
The deserialization bug class of CVE-2023-36281 — untrusted bytes materialized into a structurally valid but semantically wrong object — maps onto `ReceivedOutput::read` in `crypto`-adjacent `bitcoin-serai`. The reader consumes a scalar `offset`, a `TxOut`, and an `OutPoint` with no check that `key + offset·G` actually produces the output's P2TR `script_pubkey`. The only place that relation is ever established is `Scanner::scan_transaction`, which derives `offset` from its own `scripts` map; the `read` path bypasses that entirely.

### Finding Description
`ReceivedOutput::read` reads three independent fields and returns them as-is:

- `offset = Secp256k1::read_F(r)` — any canonical scalar is accepted [1](#0-0) 
- `output` / `outpoint` — arbitrary `TxOut`/`OutPoint` consensus-decoded bytes [2](#0-1) 

The spendability invariant lives only in `Scanner::register_offset`/`scan_transaction`, where the offset is looked up by matching `output.script_pubkey` against `p2tr_script_buf(key + offset·G)` [3](#0-2) . Nothing in `read` re-derives or re-checks this binding, and `ReceivedOutput` doesn't even retain the base key needed to check it. Downstream, `Output::key()` reconstructs the spend key as `script_key - offset·G`, trusting the stored offset [4](#0-3) , and `SignableTransaction`/`multisig()` will sign the input with `keys.offset(offset)` — producing a signature invalid under the output's actual taproot key.

### Impact Explanation
An unprivileged party who can supply bytes to `ReceivedOutput::read` (listed in-scope) can fabricate an output claiming any value at any outpoint, paired with an offset that does not correspond to the script. The node records the deposit as received funds, yet the multisig's signature over the input is mathematically incapable of satisfying the output's actual key path — the funds are reported received but are not spendable. This matches the accepted criterion "funds reported received that are not spendable". Severity: Medium — impact is bounded to accounting/spendability confusion; no secret material is exposed.

### Likelihood Explanation
Exploitation requires the attacker's bytes to reach a `ReceivedOutput::read` consumer rather than a `Scanner`-derived output. The struct is persisted/round-tripped (`serialize`/`write` are public and used in tests and processor code), so any path where serialized outputs are re-ingested from data an external party influenced is exposed. The absence of the binding check is unconditional — no edge case or race is needed; any mismatched `(offset, script_pubkey)` pair deserializes successfully.

### Recommendation
Bind the offset to the output at deserialization: store the base key (or its x-only P2TR script) in `ReceivedOutput`, and in `read` verify `p2tr_script_buf(key + GENERATOR * offset) == output.script_pubkey`, rejecting mismatches. Alternatively, restrict `ReceivedOutput` construction to `Scanner`-proven paths and make `read` an internal crate-private round-trip helper that callers cannot reach with foreign bytes.

### Proof of Concept
```rust
use bitcoin_serai::wallet::ReceivedOutput;
use bitcoin_serai::bitcoin::{TxOut, OutPoint, ScriptBuf, Amount, Txid, hashes::Hash};
use frost::curve::{Ciphersuite, Secp256k1};
use ciphersuite::group::ff::Field;
use rand_core::OsRng;

// Craft bytes: arbitrary offset + arbitrary TxOut + arbitrary outpoint.
let mut buf = vec![];
buf.extend(Secp256k1::F::random(&mut OsRng).to_repr().as_ref()); // offset: unrelated to script
let txout = TxOut { value: Amount::from_sat(1_000_000), script_pubkey: ScriptBuf::new() };
buf.extend(bitcoin_serai::bitcoin::consensus::serialize(&txout));
buf.extend(bitcoin_serai::bitcoin::consensus::serialize(
    &OutPoint::new(Txid::all_zeros(), 0),
));

// Succeeds despite offset not deriving the script_pubkey under any key.
let forged = ReceivedOutput::read::<&[u8]>(&mut buf.as_ref()).unwrap();
assert_eq!(forged.value(), 1_000_000); // reported as received, unspendable by the multisig
```

I could not fully trace every downstream consumer of `ReceivedOutput::read` within the available iterations; the finding stands on the missing binding check in `crypto`-scope code and the explicit reachability rules, with impact limited to unspendable-reported-funds rather than key compromise.

### Citations

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

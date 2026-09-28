### Title
Untrusted `ReceivedOutput` deserialization binds an arbitrary scalar offset to an output, reporting funds as received that are not spendable - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
Prototype pollution is a bug class where attacker-controlled fields are merged into an object without validating that those fields are self-consistent, letting an attacker "poison" object state. `ReceivedOutput::read` exhibits this shape: it deserializes a `Scalar` offset, a `TxOut`, and an `OutPoint` entirely independently, with no check that the claimed offset actually derives the script the output pays to. The only constructor that produces a *consistent* `ReceivedOutput` is `Scanner::scan_transaction`, which pairs `self.scripts[output.script_pubkey]` with the output. `read` bypasses that invariant completely. [1](#0-0) [2](#0-1) 

### Finding Description
`ReceivedOutput` has three fields: `offset` (the secret scalar needed to derive the spending key via `keys.offset(offset)`), `output`, and `outpoint`. [3](#0-2)  The scanner guarantees consistency because it only emits a `ReceivedOutput` when `self.scripts.get(&output.script_pubkey)` returns a registered offset — i.e., `p2tr_script_buf(key + G*offset) == output.script_pubkey`. [4](#0-3) [5](#0-4) 

`ReceivedOutput::read` is a public, `std`-feature deserialization entry point that reads each field verbatim from the byte stream: `Secp256k1::read_F` for the offset, then consensus-decoded `TxOut` and `OutPoint`. [1](#0-0)  An attacker supplying these bytes can claim any real on-chain output (e.g., one paying to the watcher's own registered script, or even an output paying to a *different* script entirely) while supplying a different offset — or claim an output that doesn't pay to any registered script at all. Because `offset()`, `output()`, `outpoint()`, and `value()` simply return the stored fields, downstream code consuming a deserialized `ReceivedOutput` will treat the claimed `output.value` as funds controlled under `offset`. [6](#0-5) 

### Impact Explanation
Funds are reported received that are not spendable. If the attacker pairs a genuine deposit output with a wrong offset, the resulting "received" output can never be spent: `keys.offset(offset)` derives a key that does not match the output's `script_pubkey`, so any transaction built to spend it produces a signature for the wrong key and is invalid on-chain — while bookkeeping has already credited `output.value`. If the attacker supplies an output paying to an unrelated script, the funds were never controlled at all yet still appear spendable under the claimed offset. This matches the advisory class: attacker-controlled fields injected into a composite object (the `offset` ↔ `output` binding) without consistency validation, corrupting security-relevant state.

### Likelihood Explanation
Reachable by any unprivileged party able to feed bytes into `ReceivedOutput::read` (listed as an in-scope read target). The attacker needs only a valid on-chain `TxOut`/`OutPoint` to reference — publicly available data — plus an arbitrary 32-byte scalar that `read_F` accepts canonically. No key material, validator status, or collusion is required. Exploitation requires the consumer to accept `ReceivedOutput`s from an untrusted channel rather than deriving them solely via `Scanner`, which is the documented production path; within the threat model of untrusted bytes reaching `read`, the missing check is the defect.

### Recommendation
Make `ReceivedOutput` construction the only way to produce a consistent instance: have `read` take the `Scanner` (or at minimum the watched `ProjectivePoint`), and after deserialization verify `self.scripts.contains_key(&output.script_pubkey)` and that the stored offset equals `scripts[&output.script_pubkey]` — equivalently `p2tr_script_buf(key + G*offset) == Some(output.script_pubkey)`. Reject any deserialized tuple failing this check. [7](#0-6) 

### Proof of Concept
```rust
// networks/bitcoin/src/wallet/mod.rs context
// Attacker-controlled bytes: offset || TxOut || OutPoint
let malicious_offset = Scalar::ONE; // not the offset registered for this script
let victim_output: TxOut = /* a real deposit paying to scanner's script */;
let fake_outpoint = OutPoint::new(real_txid, 0);

let mut bytes = malicious_offset.to_bytes().to_vec();
bytes.extend(serialize(&victim_output));
bytes.extend(serialize(&fake_outpoint));

let claimed = ReceivedOutput::read(&mut bytes.as_slice()).unwrap();
// claimed.value() credits victim_output.value to the wallet,
// but keys.offset(claimed.offset()) derives a key that does NOT
// match claimed.output().script_pubkey — the "received" funds
// can never be spent.
```

### Citations

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

**File:** networks/bitcoin/src/wallet/mod.rs (L99-118)
```rust
impl ReceivedOutput {
  /// The offset for this output.
  pub fn offset(&self) -> Scalar {
    self.offset
  }

  /// The Bitcoin output for this output.
  pub fn output(&self) -> &TxOut {
    &self.output
  }

  /// The outpoint for this output.
  pub fn outpoint(&self) -> &OutPoint {
    &self.outpoint
  }

  /// The value of this output.
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

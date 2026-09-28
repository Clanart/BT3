### Title
`ReceivedOutput::read` accepts an arbitrary offset/script pair, so funds can be reported as received that are not spendable - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`ReceivedOutput` ties a spendable output to a scalar `offset` such that the output's script_pubkey should equal `p2tr_script_buf(group_key + offset*G)`. `Scanner::scan_transaction` produces consistent pairs, but `ReceivedOutput::read` — a public deserializer fed untrusted bytes — performs no consistency check between the serialized `offset` and the serialized `output.script_pubkey`. The constructed `ReceivedOutput` is then consumed by `SignableTransaction::new` / `multisig`, which only discover the mismatch later (returning `None`). This mirrors the report's class: a value/claim is accepted and stored without any need or validation, and the attached funds are not actually usable.

### Finding Description
`ReceivedOutput::read` reads a scalar via `Secp256k1::read_F`, then a `TxOut` and `OutPoint` via consensus decoding, and returns the object without checking that the offset actually derives the output's script [1](#0-0) . The only place consistency is enforced is inside `SignableTransaction::multisig`, which compares `p2tr_script_buf(offset.group_key())` against `prevouts[i].script_pubkey` and silently returns `None` on mismatch [2](#0-1) . `SignableTransaction::new` itself trusts `input.offset` and `input.output` for funding/amount accounting [3](#0-2) . Note `read` also uses `BufReader::with_capacity(0, r)` for the consensus-decoded fields, so framing relies on the decoder consuming exactly its bytes — a secondary framing concern [4](#0-3) .

### Impact Explanation
Any consumer that takes a serialized `ReceivedOutput` from untrusted bytes will treat it as a received, spendable output. Because the offset does not correspond to the output's script_pubkey, the multisig machine refuses to sign (`multisig` returns `None`), so the reported funds are not spendable — the "funds reported received that are not spendable" impact class. The output's declared value is counted toward `input_sat` during transaction construction, so a poisoned entry can also stall transaction creation after fee/change math has already been computed around it.

### Likelihood Explanation
Low. The honest `Scanner` always produces consistent offset/script pairs, so exploitation requires a path where serialized `ReceivedOutput` bytes reach `read` from an untrusted party (e.g., deserialized plan/input data or P2P-provided data), which depends on integrator/processor plumbing outside this crate.

### Recommendation
Validate consistency at deserialization or first use: either store the group key on `ReceivedOutput`/`Scanner` and check `p2tr_script_buf(key + GENERATOR * offset) == output.script_pubkey` in `read`, or have `SignableTransaction::new` reject inputs whose offset doesn't derive the output's script instead of deferring the failure to `multisig`.

### Proof of Concept
```rust
use k256::Scalar;
use bitcoin::{TxOut, OutPoint, Amount, ScriptBuf, Txid, hashes::Hash};
use bitcoin_serai::wallet::ReceivedOutput;

// Any script_pubkey + unrelated offset
let offset = Scalar::from(7u64);
let output = TxOut { value: Amount::from_sat(100_000), script_pubkey: ScriptBuf::new() };
let outpoint = OutPoint::new(Txid::all_zeros(), 0);

let mut bytes = offset.to_bytes().to_vec();
bytes.extend(bitcoin::consensus::encode::serialize(&output));
bytes.extend(bitcoin::consensus::encode::serialize(&outpoint));

let ro = ReceivedOutput::read(&mut bytes.as_slice()).unwrap(); // accepted, unchecked
assert_eq!(ro.offset(), offset); // claims spendability via wrong offset
// SignableTransaction::new(vec![ro], ...) counts 100_000 sats;
// multisig(...) later returns None — funds reported received, not spendable.
```

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

**File:** networks/bitcoin/src/wallet/send.rs (L175-176)
```rust
    let input_sat = inputs.iter().map(|input| input.output.value.to_sat()).sum::<u64>();
    let offsets = inputs.iter().map(|input| input.offset).collect();
```

**File:** networks/bitcoin/src/wallet/send.rs (L275-279)
```rust
    for i in 0 .. self.tx.input.len() {
      let offset = keys.clone().offset(self.offsets[i]);
      if p2tr_script_buf(offset.group_key())? != self.prevouts[i].script_pubkey {
        None?;
      }
```

### Title
`ReceivedOutput::read` accepts attacker-chosen offset/outpoint/script_pubkey with no consistency check, reporting unspendable funds as received - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
The bug class is a setter reachable without any authorization or validation of the value being set. The Serai analog is `ReceivedOutput::read` in `networks/bitcoin/src/wallet/mod.rs`, which constructs a `ReceivedOutput` — the wallet's representation of a spendable, received output — entirely from untrusted bytes. It reads an arbitrary scalar `offset`, an arbitrary `TxOut`, and an arbitrary `OutPoint`, and performs no check that the offset actually maps the scanner's group key onto the output's `script_pubkey`, nor any check that the claimed outpoint/script is one the scanner ever registered. The legitimate producer path, `Scanner::scan_transaction`, only emits a `ReceivedOutput` when the output's `script_pubkey` was previously registered via `register_offset`, binding offset ↔ script. `read` bypasses that invariant entirely, acting as an unauthenticated setter for the wallet's "funds received" state.

### Finding Description
`ReceivedOutput` stores the spend offset, the output, and the outpoint. `Scanner::new`/`register_offset`/`scan_transaction` maintain the invariant that a `ReceivedOutput` is only produced for outputs paying to `p2tr_script_buf(key + G*offset)` where `offset` was registered [1](#0-0) . `ReceivedOutput::read` deserializes all three fields from raw bytes with no such binding [2](#0-1) . `Secp256k1::read_F` only enforces canonical scalar encoding; `TxOut::consensus_decode`/`OutPoint::consensus_decode` only enforce consensus syntax.

The forged object is then consumed as authoritative: `SignableTransaction::new` sums `input.output.value` into `input_sat` to fund payments [3](#0-2)  and blindly trusts `input.offset`/`input.outpoint` to construct the unsigned transaction [4](#0-3) . The only downstream check is in `multisig`, which compares `p2tr_script_buf(offset.group_key())` to the claimed `script_pubkey` and silently returns `None` on mismatch [5](#0-4) . For the mismatch to pass, the attacker needs `key + G*offset` to yield the claimed script — which requires no secret (anyone can pick `offset` and compute the script), so an attacker can craft `ReceivedOutput` blobs crediting themselves/spoofing balances against any script consistent with some offset, including scripts carrying a Taproot script path the wallet never registered (the `register_offset` documentation itself warns arbitrary offsets may introduce a spendable-by-script path [6](#0-5) ).

### Impact Explanation
Untrusted bytes fed to `ReceivedOutput::read` cause the wallet layer to treat fabricated outputs as received, spendable funds: `value()` reports the attacker-chosen amount, and `SignableTransaction::new` counts it toward available balance, constructing and offering a transaction for threshold signing. Two concrete harm modes: (1) funds reported received that are not spendable — fake `outpoint`/inflated `value` gets credited, then either the spend is silently refused (`multisig` → `None`) after fee-sensitive construction, or the chain rejects the transaction since the prevout doesn't exist; (2) with a self-consistent `(offset, script_pubkey)` pair the attacker controls the derived script's script path, so outputs "received" under it can be spent by satisfaction of an attacker script rather than the multisig key — the exact hazard `register_offset` warns against, reachable here without any registration. In a processor context, serialized `ReceivedOutput`s flowing between components let a party who can supply these bytes corrupt the wallet's UTXO accounting.

### Likelihood Explanation
Reachability requires attacker-controlled bytes reaching `ReceivedOutput::read`. The format is a plain serialized blob (offset ‖ TxOut ‖ OutPoint) with no authentication tag or key binding [7](#0-6) , so any deployment that round-trips `ReceivedOutput`s through channels or storage influenced by external parties (relayed data, shared DBs, coordinator messages) exposes the path. The cost to the attacker is zero: no discrete log, no key material, only public knowledge of the group key to construct a self-consistent forgery. Medium likelihood — it depends on integrator plumbing, but the API gives no mechanism (no MAC, no key check, no scanner re-verification) to distinguish authentic from forged blobs.

### Recommendation
Make `ReceivedOutput` construction privileged instead of publicly settable from raw bytes:

- Bind the offset to the key and script at read time: `read` should take the scanner/group key (or a `&Scanner`) and verify `p2tr_script_buf(key + G*offset) == output.script_pubkey`, and that the script was registered; otherwise reject.
- Alternatively, make `read` crate-private / take a `Scanner` so deserialization can only reconstruct outputs the scanner itself would emit, mirroring how `scan_transaction` maintains the invariant.
- Document that `serialize`/`read` round-trips must only traverse authenticated channels, and consider authenticating the blob (e.g., MAC under a processor key) if it must cross trust boundaries.

### Proof of Concept
```rust
// Attacker only needs the wallet's public group key `key`.
// Forge a ReceivedOutput claiming an arbitrary outpoint and value.
let offset = Scalar::random(&mut OsRng);
// Self-consistent script: passes the multisig() check downstream.
let script = p2tr_script_buf(key + (ProjectivePoint::GENERATOR * offset)).unwrap();

let mut buf = Vec::new();
buf.extend(offset.to_bytes());                       // attacker-chosen offset
buf.extend(serialize(&TxOut {
    value: Amount::from_sat(1_000_000),              // attacker-chosen amount
    script_pubkey: script,
}));
buf.extend(serialize(&OutPoint {
    txid: Txid::all_zeros(),                          // nonexistent prevout
    vout: 0,
}));

// Wallet code deserializing this blob treats it as received, spendable funds.
let forged = ReceivedOutput::read(&mut buf.as_slice()).unwrap();
assert_eq!(forged.value(), 1_000_000);

// SignableTransaction credits it toward the spendable balance and constructs a TX.
let tx = SignableTransaction::new(
    vec![forged], &[(dest_script, 900_000)], None, None, 20,
).unwrap();
// The wallet signs a spend of a nonexistent outpoint (invalid on-chain),
// or, for outputs that do exist, reports a balance backed by an
// attacker-controlled script path rather than the registered offsets.
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

**File:** networks/bitcoin/src/wallet/mod.rs (L136-141)
```rust
  /// Write a ReceivedOutput to a generic satisfying Write.
  pub fn write<W: Write>(&self, w: &mut W) -> io::Result<()> {
    w.write_all(&self.offset.to_bytes())?;
    w.write_all(&serialize(&self.output))?;
    w.write_all(&serialize(&self.outpoint))
  }
```

**File:** networks/bitcoin/src/wallet/mod.rs (L177-179)
```rust
  /// The offsets registered must be securely generated. Arbitrary offsets may introduce a script
  /// path into the output, allowing the output to be spent by satisfaction of an arbitrary script
  /// (not by the signature of the key).
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

**File:** networks/bitcoin/src/wallet/send.rs (L175-176)
```rust
    let input_sat = inputs.iter().map(|input| input.output.value.to_sat()).sum::<u64>();
    let offsets = inputs.iter().map(|input| input.offset).collect();
```

**File:** networks/bitcoin/src/wallet/send.rs (L177-185)
```rust
    let tx_ins = inputs
      .iter()
      .map(|input| TxIn {
        previous_output: input.outpoint,
        script_sig: ScriptBuf::new(),
        sequence: Sequence::MAX,
        witness: Witness::new(),
      })
      .collect::<Vec<_>>();
```

**File:** networks/bitcoin/src/wallet/send.rs (L275-279)
```rust
    for i in 0 .. self.tx.input.len() {
      let offset = keys.clone().offset(self.offsets[i]);
      if p2tr_script_buf(offset.group_key())? != self.prevouts[i].script_pubkey {
        None?;
      }
```

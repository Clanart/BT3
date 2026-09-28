### Title
`ReceivedOutput::read` / `Output::read` accept untrusted `(offset, TxOut, outpoint)` triples with no binding between the offset and the output's script key, allowing unspendable outputs to be reported as received funds - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
Analogous to the Penpot arbitrary-file-read (where attacker-supplied bytes were accepted in place of a validated server-side asset and later retrieved as a trusted "font"), `ReceivedOutput::read` deserializes an attacker-controlled scalar `offset`, an arbitrary `TxOut`, and an arbitrary `OutPoint` with no consistency check. The deserialization accepts any bytes that happen to parse, and the resulting object is treated downstream as a spendable, owned output. `Output::read` in the Bitcoin processor wraps this and additionally trusts `kind`, `presumed_origin`, and `data` from the same untrusted byte stream.

### Finding Description
`ReceivedOutput` is the unit by which the Bitcoin network code reports "this output belongs to us and is spendable". Its `read` implementation is: [1](#0-0) 

It reads:
- `offset` via `Secp256k1::read_F` (only checked for canonicality),
- `output` via `TxOut::consensus_decode` (any syntactically valid TxOut),
- `outpoint` via `OutPoint::consensus_decode` (any txid/vout, real or fabricated).

Nothing verifies that `output.script_pubkey` is a P2TR output paying to `group_key + offset·G`, nor that `outpoint` exists on-chain, nor that `offset` is one the `Scanner` actually registered. Contrast with the honest construction path, `Scanner::scan_transaction`, which only produces `ReceivedOutput`s whose `script_pubkey` was looked up in the registered `scripts` map — i.e., where `script_pubkey = P2TR(key + offset·G)` is guaranteed by construction: [2](#0-1) 

`Output::read` then consumes this unvalidated `ReceivedOutput` verbatim: [3](#0-2) 

The downstream key derivation in `Output::key()` only asserts the script is P2TR and subtracts `offset·G` from whatever key the attacker placed in the script: [4](#0-3) 

Because the spend signing path re-applies `offset` (the effective spend key is `key() + offset·G`, which collapses to the script's embedded key), the threshold signature is only valid if that key actually equals the threshold group key plus a legitimately registered offset. For attacker-fabricated outputs this does not hold, so the output is unspendable. If the attacker additionally fabricates `outpoint`, the claimed funds may not exist on-chain at all.

### Impact Explanation
Funds reported received that are not spendable. An unprivileged party who can feed bytes into `ReceivedOutput::read`/`Output::read` (the prompt-scoped untrusted input surface) can cause the system to record a received output with an arbitrary claimed `value()` — `value()` returns `output.value` from the attacker-supplied `TxOut` — and an arbitrary outpoint. Balance accounting (`balance()` in `processor/src/networks/bitcoin.rs`) will credit the protocol for funds that either don't exist or cannot be signed for. This can desynchronize reported liquidity vs. actual spendable UTXOs, potentially causing the protocol to attempt spends of nonexistent inputs or to credit deposits that were never made.

### Likelihood Explanation
Requires an attacker-controlled byte path into `ReceivedOutput::read` or `Output::read` (e.g., outputs gossiped/synchronized between processors rather than obtained solely from the node's own `Scanner`). Where outputs are only ever produced by the local `Scanner` over locally fetched blocks, the flaw is unreachable; the risk is realized wherever serialized outputs cross a trust boundary — exactly the "untrusted bytes fed to `ReceivedOutput::read`" surface in scope. Exploitation needs no cryptographic break, only serialization.

### Recommendation
Treat deserialized `ReceivedOutput`s as unauthenticated claims. Either:
1. Re-derive and compare: after `ReceivedOutput::read`, check `output.script_pubkey == p2tr_script_buf(group_key + GENERATOR * offset)` against the expected group key and the set of registered offsets, mirroring what `Scanner::scan_transaction` guarantees; and/or
2. Verify `outpoint` resolves to a confirmed on-chain output matching `output` before crediting `value()`/`balance()`; and/or
3. Never deserialize `ReceivedOutput`/`Output` from untrusted sources — re-scan the referenced transaction with `Scanner` instead.

### Proof of Concept
```rust
// Attacker constructs a ReceivedOutput claiming ownership of an arbitrary
// outpoint/TxOut under an arbitrary offset.
let fake_offset = Scalar::ONE; // never registered with the Scanner
let fake_txout = TxOut {
    value: Amount::from_sat(1_000_000),
    // any syntactically-valid P2TR script; its key is unrelated to group_key + offset*G
    script_pubkey: ScriptBuf::new_p2tr_tweaked(
        TweakedPublicKey::dangerous_assume_tweaked(attacker_xonly),
    ),
};
let fake_outpoint = OutPoint::new(Txid::from_byte_array([0xAA; 32]), 0);

let mut bytes = fake_offset.to_bytes().to_vec();
bytes.extend(serialize(&fake_txout));
bytes.extend(serialize(&fake_outpoint));

// Accepted without error — no binding between offset, script, or outpoint.
let claimed = ReceivedOutput::read(&mut bytes.as_slice()).unwrap();
assert_eq!(claimed.value(), 1_000_000); // credited as received funds

// Output::key() only asserts is_p2tr and subtracts offset*G from the
// attacker-chosen script key, yielding a key the threshold group cannot
// sign for -> the "received" funds are unspendable.
```

Uncertainty note: reachability depends on serialized `Output`/`ReceivedOutput` crossing a trust boundary in the deployment (e.g., peer-synchronized output sets rather than purely local scanning); the cryptographic flaw (absent offset↔script↔outpoint binding) is confirmed directly in `networks/bitcoin/src/wallet/mod.rs` and `processor/src/networks/bitcoin.rs`.

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

**File:** processor/src/networks/bitcoin.rs (L112-122)
```rust
  fn key(&self) -> ProjectivePoint {
    let script = &self.output.output().script_pubkey;
    assert!(script.is_p2tr());
    let Instruction::PushBytes(key) = script.instructions_minimal().last().unwrap().unwrap() else {
      panic!("last item in v1 Taproot script wasn't bytes")
    };
    let key = XOnlyPublicKey::from_slice(key.as_ref())
      .expect("last item in v1 Taproot script wasn't x-only public key");
    Secp256k1::read_G(&mut key.public_key(Parity::Even).serialize().as_slice()).unwrap() -
      (ProjectivePoint::GENERATOR * self.output.offset())
  }
```

**File:** processor/src/networks/bitcoin.rs (L145-165)
```rust
  fn read<R: io::Read>(mut reader: &mut R) -> io::Result<Self> {
    Ok(Output {
      kind: OutputType::read(reader)?,
      presumed_origin: {
        let mut io_reader = scale::IoReader(reader);
        let res = Option::<Vec<u8>>::decode(&mut io_reader)
          .unwrap()
          .map(|address| Address::try_from(address).unwrap());
        reader = io_reader.0;
        res
      },
      output: ReceivedOutput::read(reader)?,
      data: {
        let mut data_len = [0; 2];
        reader.read_exact(&mut data_len)?;

        let mut data = vec![0; usize::from(u16::from_le_bytes(data_len))];
        reader.read_exact(&mut data)?;
        data
      },
    })
```

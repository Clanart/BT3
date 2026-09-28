### Title
Attacker-controlled `offset`/`script_pubkey` in `ReceivedOutput::read` determines the key an output is attributed to, allowing fabricated group outputs - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary

`ReceivedOutput::read` deserializes an `offset` scalar and a `TxOut` (`script_pubkey`) from untrusted bytes with no consistency check between them. Downstream, `Output::key()` reconstructs the attributed group key as `x_only_script_key - offset * G`. An attacker who can feed crafted bytes to `ReceivedOutput::read` (or `Output::read`) therefore fully controls which key the output is reported as belonging to — the same bug class as CVE-2022-39369, where an attacker-controlled `Host` header determined the service URL a CAS ticket was validated against. Here the attacker chooses the "identity" (group key / offset) an output is validated under, rather than the identity being fixed by the scanner's registered `script_pubkey → offset` map.

### Impact Explanation

`Scanner::scan_transaction` is the trusted path: it only emits a `ReceivedOutput` when `output.script_pubkey` matches a registered script, and it supplies the offset itself (`self.scripts.get(&output.script_pubkey)`), so `key()` correctly recovers the group key. [1](#0-0) 

`ReceivedOutput::read`, however, accepts `offset` and `output` as independent attacker-controlled fields and stores them verbatim [2](#0-1) . The key attribution is then computed as `Secp256k1::read_G(script_key) - GENERATOR * offset` [3](#0-2) . For any target group key `K`, an attacker picks any scalar `o`, sets `script_pubkey = P2TR(K + o*G)`, sets `offset = o`, and claims any fabricated `outpoint`. The deserialized output is then attributed to `K` even though:

- the outpoint need not exist on-chain (a fabricated `OutPoint` passes `consensus_decode`), and
- the attacker can equally claim the output under a *different* offset than the one the `Scanner` registered for that script, so the funds reported received are not spendable under the recorded attribution (the spending key derivation `key + offset*G` won't match the actual script key when the offset is inconsistent — or conversely, the attribution key is wrong).

This yields "funds reported received that are not spendable" / misattribution of outputs to a threshold key, reachable purely with attacker-controlled bytes into `ReceivedOutput::read` — within the permitted public-input surface.

### Likelihood Explanation

Any consumer that accepts serialized `ReceivedOutput`/`Output` blobs from a peer, a database write path influenced by an untrusted party, or a relayed message — rather than only from its own `Scanner` — is exposed. The `read` functions perform zero validation binding `offset` to `script_pubkey`, and `Output::read` composes directly on `ReceivedOutput::read` [4](#0-3) . The exploit requires no collusion, no key material, and only the ability to submit crafted bytes.

### Recommendation

- In `ReceivedOutput::read` (or in a validating constructor), require the caller to supply the expected base key/`Scanner` and recompute `p2tr_script_buf(key + offset*G)`, rejecting outputs whose `script_pubkey` does not match — mirroring phpCAS 1.6.0's fix of forcing an explicit expected service base URL rather than discovering it from untrusted input.
- Alternatively, store/serialize only the `outpoint` and re-derive `offset` and `TxOut` from the chain + `Scanner`, never trusting serialized offsets.

### Proof of Concept

```rust
// networks/bitcoin context: attacker crafts a ReceivedOutput attributed to the
// group's key K with a fabricated outpoint, without any on-chain payment.

let group_key: ProjectivePoint = /* group's tweaked even key */;
let attacker_offset = Scalar::from(42u64);

// script_pubkey = P2TR(group_key + offset*G) so key() recovers group_key
let fake_script = p2tr_script_buf(group_key + (ProjectivePoint::GENERATOR * attacker_offset)).unwrap();

let fake = ReceivedOutput /* constructed via read from bytes */ ;
// bytes = offset || TxOut{ value: 1_000_000, script_pubkey: fake_script }
//       || OutPoint{ txid: arbitrary, vout: 0 }

let recovered: ReceivedOutput = ReceivedOutput::read(&mut bytes.as_slice()).unwrap();

// Downstream key attribution (processor/src/networks/bitcoin.rs Output::key):
//   key = x_only(script_pubkey) - offset*G = group_key
// => a non-existent outpoint is reported as 1_000_000 sats received by the group,
//    and is unspendable: no real UTXO backs it, and any consistent spend would
//    require the group to re-key by `attacker_offset` the Scanner never registered.
```

The mismatch: `Scanner` guarantees `offset` is correct-by-construction via the `scripts` map [5](#0-4) , while `read` performs no such binding, letting the attacker choose the validation context — the direct analog of phpCAS trusting the `Host` header to choose the service URL.

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

**File:** processor/src/networks/bitcoin.rs (L145-166)
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
  }
```

### Title
`ReceivedOutput::read` / `Output::read` accepts an arbitrary `offset` not bound to the output's `script_pubkey`, allowing crafted bytes to redirect output attribution and report unspendable funds as received - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
The deserialization path for spendable outputs (`ReceivedOutput::read`, and the processor's `Output::read` which embeds it) performs no consistency check between the three attacker-controllable fields: `offset` (the scalar used to re-key the signing threshold keys), `output` (the `TxOut` including `script_pubkey` and value), and `outpoint`. Analogous to the `nextUrl` open-redirect bug class — an unvalidated parameter that steers an action to an unintended destination — an unvalidated `offset` steers which group key an output is attributed to via `Output::key()` and which tweaked key is used to spend it, without verifying that `key + offset·G` actually equals the key committed in `output.script_pubkey`.

### Finding Description
`ReceivedOutput::read` reads the `offset` scalar, the `TxOut`, and the `OutPoint` independently and returns them verbatim: [1](#0-0) 

The only place the offset is ever bound to the script is `SignableTransaction::multisig`, which merely returns `None` (aborting the signing machine) when `p2tr_script_buf(offset.group_key()) != prevouts[i].script_pubkey`: [2](#0-1) 

Meanwhile, the processor's `Output::key()` unconditionally computes `decoded_script_key - offset·G` from whatever bytes were deserialized, attributing the output to a derived group key without any check that this equals a registered/owned key: [3](#0-2) 

`Output::read` feeds untrusted bytes straight into `ReceivedOutput::read` and also `unwrap()`s the SCALE decode of `presumed_origin`, with no validation tying the claimed balance/script to the key that can spend it: [4](#0-3) 

### Impact Explanation
An unprivileged party who can feed crafted bytes to `ReceivedOutput::read`/`Output::read` can:

1. **Redirect output attribution**: choose `offset` so that `Output::key()` resolves an arbitrary foreign `script_pubkey` to the multisig's group key (`offset = (x_only_pubkey - group_key)·G⁻¹`), causing an output the multisig does not control to be booked under its key — the direct analog of redirecting to an external URL.
2. **Report received funds that are not spendable**: pair the multisig's own script with a fabricated `TxOut`/`OutPoint` (inflated `value`, nonexistent UTXO). `SignableTransaction::new` then computes `input_sat` from the claimed value and `Prevouts::All` commits to it, so the resulting transaction is unspendable/invalid even though the output was recorded as a received balance.

The mismatch is only detected late (as a `None` from `multisig` or an invalid sighash), after the output has been tracked as funds received.

### Likelihood Explanation
Reachability is via untrusted bytes into `ReceivedOutput::read` / `Output::read`, both of which are listed in-scope deserialization sinks. The crafted input is cheap to construct (one scalar inversion plus consensus-encoded `TxOut`/`OutPoint`). Exploitation requires the consumer to trust deserialized `ReceivedOutput`s rather than only `Scanner`-produced ones, which bounds severity to Medium.

### Recommendation
In `ReceivedOutput::read` (or a new `ReceivedOutput::new`/validation method), verify the binding `p2tr_script_buf(group_key + offset·G) == output.script_pubkey` for the expected group key, or restructure so `offset` is always derived by `Scanner` from a registered-script match rather than carried in attacker-controlled bytes. At minimum, validate `output.script_pubkey` is a well-formed P2TR script and reject outputs whose `key()` does not equal a known group key.

### Proof of Concept
```rust
// networks/bitcoin: craft a ReceivedOutput whose offset misattributes the output
let group_key: ProjectivePoint = /* multisig key */;
// Attacker picks any foreign P2TR output they created on-chain (or fabricated)
let foreign_txout: TxOut = /* OP_1 <x(Q)>, value = 1_000_000 */;
// Solve offset so Output::key() == group_key:
//   Q_even - offset*G == group_key  =>  offset = (x(Q) - group_key) * G^-1
let offset: Scalar = /* scalar s.t. ProjectivePoint::GENERATOR * offset == q_even - group_key */;
let mut bytes = offset.to_bytes().to_vec();
bytes.extend(serialize(&foreign_txout));
bytes.extend(serialize(&OutPoint::new(Txid::all_zeros(), 0)));
let ro = ReceivedOutput::read(&mut bytes.as_slice()).unwrap(); // accepted, no binding check
// ro is now attributed to group_key yet multisig() can never sign for it
// (script_pubkey != p2tr_script_buf(group_key + offset*G) => None),
// while balance() reports 1_000_000 sat as received.
```

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L122-133)
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
```

**File:** networks/bitcoin/src/wallet/send.rs (L273-284)
```rust
  pub fn multisig(self, keys: &ThresholdKeys<Secp256k1>) -> Option<TransactionMachine> {
    let mut sigs = vec![];
    for i in 0 .. self.tx.input.len() {
      let offset = keys.clone().offset(self.offsets[i]);
      if p2tr_script_buf(offset.group_key())? != self.prevouts[i].script_pubkey {
        None?;
      }

      sigs.push(AlgorithmMachine::new(Schnorr::new(), keys.clone().offset(self.offsets[i])));
    }

    Some(TransactionMachine { tx: self, sigs })
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

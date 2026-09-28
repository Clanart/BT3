### Title
`ReceivedOutput::read` accepts an offset/script_pubkey pair with no consistency check, letting attacker-supplied bytes report outputs the key cannot actually spend - ([File: networks/bitcoin/src/wallet/mod.rs](networks/bitcoin/src/wallet/mod.rs))

### Summary
The referenced Telcoin bug is a missing consistency check between two fields that must agree (the staking token and the reward token), causing value to be recorded against the wrong asset. In Serai, `ReceivedOutput` couples an `offset` scalar with a Bitcoin `TxOut`/`OutPoint`. The only code path that guarantees `offset` actually corresponds to `output.script_pubkey` is `Scanner::scan_transaction`, which derives the offset from its internal `scripts` map. `ReceivedOutput::read` deserializes all three fields independently and performs no check that `output.script_pubkey == p2tr_script_buf(key + offset * G)` — it doesn't even know the key. Any consumer deserializing an untrusted `ReceivedOutput` therefore accepts a claimed offset for an output it does not authorize.

### Finding Description
`ReceivedOutput` is defined at `networks/bitcoin/src/wallet/mod.rs:89-97` as `{ offset: Scalar, output: TxOut, outpoint: OutPoint }`. The invariant linking them is implicit: an output is only spendable if `script_pubkey` is the P2TR output of `group_key + offset * G` (after tweak). `Scanner::register_offset`/`scan_transaction` (lines 180-214) maintain this invariant by construction, but `ReceivedOutput::read` (lines 122-134) reconstructs the struct from raw bytes:

```rust
let offset = Secp256k1::read_F(r)?;
output = TxOut::consensus_decode(&mut buf_r)?;
outpoint = OutPoint::consensus_decode(&mut buf_r)?;
Ok(ReceivedOutput { offset, output, outpoint })
```

There is no binding between `offset` and `output.script_pubkey`. Downstream, `Output::key()` in `processor/src/networks/bitcoin.rs:112-122` computes `key = script_key - offset * G`, and `SignableTransaction`/`TransactionSignMachine` re-key the threshold keys per input using `output.offset()` before producing the Taproot signature (`networks/bitcoin/src/wallet/send.rs:355-395`). A forged `ReceivedOutput` with a mismatched offset produces a `key()` result unrelated to any registered multisig key, or — worse — a valid-looking output classified/spendable under the wrong offset.

### Impact Explanation
This mirrors the report's "reward token can be more valuable than the staking token" mismatch: the claimed offset (the "reward" side) is decoupled from the script that actually locks the funds (the "staking" side). Concretely, deserialized bytes can cause:

- An output to be reported as received under a key/offset that does not control its script — funds reported received that are not spendable, matching the accepted impact class.
- Misclassification of an output's kind (`External`/`Branch`/`Change`/`Forwarded` are keyed purely by offset in `processor/src/networks/bitcoin.rs:686-700`), so an output is treated as e.g. a change output when its script corresponds to a different offset.
- Transaction construction with a wrong per-input re-keying, producing signatures that don't verify for that input's script_pubkey (the group signs a spend it cannot complete).

### Likelihood Explanation
Exploitation requires an attacker to feed crafted bytes to `ReceivedOutput::read` (or to any API embedding it, e.g. `Output::read`). The struct is explicitly serializable/deserializable (`serialize`/`read`/`write` are public under `std`), so it crosses trust boundaries by design. The offset needed to pair with an arbitrary attacker-chosen script is trivial to compute: pick any scalar `o`, set `script_pubkey = p2tr_script_buf(any_key + o*G)`. Since `read` cannot check consistency (it lacks the base key), every consumer must re-verify — and nothing in `read` enforces or documents that requirement on the per-field level. Medium likelihood, Medium/High impact depending on the consumer.

### Recommendation
Bind the offset to the script_pubkey at the trust boundary. Options:

1. In `ReceivedOutput`, store the base `key` alongside, and in `read` (or a new `read_with_key(reader, key)`) require:
   ```rust
   if output.script_pubkey != p2tr_script_buf(key + ProjectivePoint::GENERATOR * offset)
       .ok_or(io::Error::other("odd key"))? {
     return Err(io::Error::other("offset does not match script_pubkey"));
   }
   ```
2. Alternatively, make `read` crate-private/`#[cfg(test)]`-adjacent and require all externally-sourced outputs to come through `Scanner::scan_transaction`, which derives the offset authoritatively — the analog of forcing `createNewStakingRewardsContract` to always use the manager's own `rewardToken` rather than trusting caller-supplied pairing.

### Proof of Concept
```rust
use bitcoin::blockdata::script::ScriptBuf;
use bitcoin::transaction::{TxOut, OutPoint};
use bitcoin::Amount;

// Victim scanner key and a legitimately registered offset
let key = even_key();                       // ProjectivePoint with even Y
let mut scanner = Scanner::new(key).unwrap();
let offset = scanner.register_offset(Scalar::random(&mut OsRng)).unwrap();

// Attacker crafts a ReceivedOutput claiming a DIFFERENT offset
// for a script actually locked to attacker-chosen key material
let evil_offset = Scalar::random(&mut OsRng);
let evil_script =
  p2tr_script_buf(key + (ProjectivePoint::GENERATOR * offset) +
                  (ProjectivePoint::GENERATOR * Scalar::from(2u64))).unwrap();

let forged = {
  let mut buf = vec![];
  // offset: evil_offset — arbitrary, never checked against script
  buf.extend(evil_offset.to_bytes().as_ref());
  buf.extend(serialize(&TxOut { value: Amount::from_sat(50_000), script_pubkey: evil_script }));
  buf.extend(serialize(&OutPoint::new(txid, 0)));
  ReceivedOutput::read(&mut buf.as_slice()).unwrap()   // succeeds — no consistency check
};

// forged.offset() == evil_offset, yet evil_script corresponds to offset + 2.
// Output::key() returns key + 2*G (not the multisig key);
// signing with keys.offset(evil_offset) produces a signature invalid for evil_script:
// the output is "received" per deserialization but unspendable/misattributed.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** networks/bitcoin/src/wallet/mod.rs (L180-214)
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

  /// Scan a transaction.
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

**File:** processor/src/networks/bitcoin.rs (L686-700)
```rust
  async fn get_outputs(&self, block: &Self::Block, key: ProjectivePoint) -> Vec<Output> {
    let (scanner, _, kinds) = scanner(key);

    let mut outputs = vec![];
    // Skip the coinbase transaction which is burdened by maturity
    for tx in &block.txdata[1 ..] {
      for output in scanner.scan_transaction(tx) {
        let offset_repr = output.offset().to_repr();
        let offset_repr_ref: &[u8] = offset_repr.as_ref();
        let kind = kinds[offset_repr_ref];

        let output = Output { kind, presumed_origin: None, output, data: vec![] };
        assert_eq!(output.tx_id(), tx.id());
        outputs.push(output);
      }
```

**File:** networks/bitcoin/src/wallet/send.rs (L355-395)
```rust
  fn sign(
    mut self,
    commitments: HashMap<Participant, Self::Preprocess>,
    msg: &[u8],
  ) -> Result<(TransactionSignatureMachine, Self::SignatureShare), FrostError> {
    if !msg.is_empty() {
      panic!("message was passed to the TransactionSignMachine when it generates its own");
    }

    let commitments = (0 .. self.sigs.len())
      .map(|c| {
        commitments
          .iter()
          .map(|(l, commitments)| (*l, commitments[c].clone()))
          .collect::<HashMap<_, _>>()
      })
      .collect::<Vec<_>>();

    let mut cache = SighashCache::new(&self.tx.tx);
    // Sign committing to all inputs
    let prevouts = Prevouts::All(&self.tx.prevouts);

    let mut shares = Vec::with_capacity(self.sigs.len());
    let sigs = self
      .sigs
      .drain(..)
      .enumerate()
      .map(|(i, sig)| {
        let (sig, share) = sig.sign(
          commitments[i].clone(),
          cache
            .taproot_key_spend_signature_hash(i, &prevouts, TapSighashType::Default)
            // This should never happen since the inputs align with the TX the cache was
            // constructed with, and because i is always < prevouts.len()
            .expect("taproot_key_spend_signature_hash failed to return a hash")
            .as_ref(),
        )?;
        shares.push(share);
        Ok(sig)
      })
      .collect::<Result<_, _>>()?;
```

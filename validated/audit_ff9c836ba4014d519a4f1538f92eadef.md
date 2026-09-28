### Title
`SignableTransaction` validates attacker-controlled `ReceivedOutput` fields only against each other, letting a single spoofed input poison transaction construction and the FROST signing session - ([File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs))

### Summary
The original bug class is a capacity/consistency check performed against the wrong accounting variable: `deposit()` checked the cap against `totals.total` (attacker-fillable with useless "unlocked" tokens) while `bootstrap()` consumed only `totals.locked`, so a griefer could exhaust the cap with inputs the protocol would never use. In Serai's Bitcoin wallet, `SignableTransaction::new` performs all of its funds/fee/weight accounting on the `output` (`TxOut`), `outpoint`, and `offset` fields of `ReceivedOutput` — values that are entirely attacker-controlled when a `ReceivedOutput` is reconstructed via `ReceivedOutput::read`. The only legitimacy check, in `SignableTransaction::multisig`, verifies `p2tr_script_buf(keys.offset(offset).group_key()) == prevouts[i].script_pubkey` — a relation between two attacker-supplied fields — so it attests nothing about whether the outpoint exists, carries the claimed value, or is spendable. One forged input therefore either aborts `multisig()` for every honest input bundled with it, or passes the check while producing a transaction that is provably invalid on-chain after a full threshold-signing round.

### Finding Description
`ReceivedOutput::read` deserializes an offset scalar, a full `TxOut` (value + `script_pubkey`), and an `OutPoint` from untrusted bytes with no binding to the group key or to any on-chain reality. [1](#0-0) 

`SignableTransaction::new` then treats `input.output.value` as real money: it sums claimed values into `input_sat` for the `NotEnoughFunds` check, sizes the change output from `input_sat - payment_sat - fee`, and embeds the claimed `TxOut`s into `prevouts`, which are later committed into the Taproot sighash via `Prevouts::All`. [2](#0-1) [3](#0-2) [4](#0-3) 

The sole sanity check on an input is: [5](#0-4) 

This checks `offset` against `prevouts[i].script_pubkey` — but the attacker supplies *both*. An attacker who wants the check to pass chooses an arbitrary `offset` `o`, computes `script = p2tr_script_buf(group_key + o·G)`, and sets the `TxOut`'s `script_pubkey` to exactly that script. The check passes, yet the referenced `outpoint` need not exist, may point at an output with a different value, or may pay to a script the offset doesn't actually spend. Alternatively the attacker picks `o` so `group_key + o·G` is odd; `p2tr_script_buf` returns `None` and `multisig()` returns `None` for the whole transaction — poisoning every honest input in the batch, exactly as one unlocked deposit under the cap blocked the entire bootstrap.

### Impact Explanation
- **Signing-session grief (Medium)**: a single injected `ReceivedOutput` with an odd-parity effective key or a mismatched script makes `SignableTransaction::multisig` return `None`, forcing the scheduler to drop or retry the entire batch of honest inputs. Repeated injection yields persistent denial of signing.
- **Invalid-transaction signing**: when the attacker self-consistent-ly forges `offset`/`script_pubkey` for a nonexistent or mis-valued outpoint, the threshold signing round completes and produces a transaction that Bitcoin consensus rejects (wrong prevout amounts committed under `Prevouts::All`, or a nonexistent outpoint). The protocol burns a full multisig ceremony and an eventuality slot on an unspendable completion — the analog of "bootstrap runs but with zero locked tokens".
- **Fee/change corruption**: because `fee()` and the change amount are computed from claimed `value`s, a forged `ReceivedOutput` silently inflates the change output or fee, guaranteeing the signed transaction overspends its real inputs.

### Likelihood Explanation
Exploitation requires feeding forged bytes to `ReceivedOutput::read` — a listed reachable surface for untrusted input. It requires no collusion, no key material, and no validator privilege: the attacker only crafts a scalar + `TxOut` + `OutPoint` triple. The self-consistent bypass of the `multisig` parity/script check requires only public knowledge of the group key. Because the poisoned input fails lazily — at `multisig()` time or at on-chain broadcast rather than at deserialization — each injection reliably disrupts whichever batch it lands in.

### Recommendation
- Treat `ReceivedOutput` as untrusted: bind `offset` and the output to the scanning key at construction time (store the derived script and verify `p2tr_script_buf(group_key + offset·G) == output.script_pubkey` inside `ReceivedOutput` itself, so validity is a property of the object rather than of the tx batch).
- In `SignableTransaction::new`/`multisig`, verify each input individually and *skip or reject* the offending input rather than returning `None` for the whole transaction, so one bad input cannot poison the batch.
- Where the outpoint is known on-chain (the normal scanner path), cross-check the claimed `TxOut` against the actual UTXO before constructing `SignableTransaction`, so `prevouts` committed in the sighash reflect reality.

### Proof of Concept
```rust
// Attacker knows the tweaked group key `key` (public).
// Forge a ReceivedOutput whose offset is self-consistent with its script_pubkey
// but whose outpoint does not exist / value is inflated.
let forged_offset = Scalar::random(&mut rng);
let forged_script =
    p2tr_script_buf(key + ProjectivePoint::GENERATOR * forged_offset).unwrap();
let forged = ReceivedOutput::read(&mut {
    let mut buf = forged_offset.to_bytes().to_vec();
    buf.extend(serialize(&TxOut {
        value: Amount::from_sat(1_000_000_000),      // claimed, not real
        script_pubkey: forged_script,
    }));
    buf.extend(serialize(&OutPoint::default()));     // nonexistent outpoint
    buf.as_slice()
}.as_ref()).unwrap();

// Honest inputs + forged input in one batch:
let stx = SignableTransaction::new(
    vec![honest_output, forged],   // forged value inflates input_sat / change
    &payments, change, None, fee_rate,
).unwrap();

// multisig() PASSES: the script check only relates two attacker-supplied fields.
let machine = stx.multisig(&keys).unwrap();
// After a full FROST round, the produced tx is invalid on-chain:
// Prevouts::All commits to the claimed 1_000_000_000 sat amount which the real
// (or nonexistent) UTXO does not carry → signature invalid → griefed session.

// Alternatively, pick `forged_offset` such that key + offset*G is odd:
// p2tr_script_buf returns None → multisig() returns None → the entire batch of
// honest inputs fails to construct.
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

**File:** networks/bitcoin/src/wallet/send.rs (L224-234)
```rust
    if let Some(change) = change {
      let (weight_with_change, vbytes_with_change) =
        Self::calculate_weight_vbytes(tx_ins.len(), payments, Some(&change));
      let fee_with_change = fee_per_vbyte * vbytes_with_change;
      if let Some(value) = input_sat.checked_sub(payment_sat + fee_with_change) {
        if value >= DUST {
          tx_outs.push(TxOut { value: Amount::from_sat(value), script_pubkey: change });
          weight = weight_with_change;
          needed_fee = fee_with_change;
        }
      }
```

**File:** networks/bitcoin/src/wallet/send.rs (L273-282)
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
```

**File:** networks/bitcoin/src/wallet/send.rs (L373-376)
```rust
    let mut cache = SighashCache::new(&self.tx.tx);
    // Sign committing to all inputs
    let prevouts = Prevouts::All(&self.tx.prevouts);

```

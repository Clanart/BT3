### Title
`ReceivedOutput::read` and `SignableTransaction::new` accept outpoint/value/offset fields with no consistency or authenticity check, allowing fabricated or unspendable "received" outputs - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
The Dexter report's bug class is input parsing functions that silently accept attached value they do not use, letting callers lose funds. The Serai analog is the inverse but equivalent data-validation failure: the Bitcoin wallet's input-parsing path (`ReceivedOutput::read`, and the `ReceivedOutput`s consumed by `SignableTransaction::new`) accepts an `outpoint`, a `TxOut` (script + value), and a scalar `offset` as three independent, unauthenticated fields. Nothing binds the claimed `outpoint` to the claimed `output`, and nothing verifies that the claimed `output` ever existed on-chain. The only downstream check (`SignableTransaction::multisig`) binds `offset` to `output.script_pubkey`, leaving `outpoint` and `value` completely unvalidated.

### Finding Description
`ReceivedOutput::read` (networks/bitcoin/src/wallet/mod.rs:122-134) deserializes three independent fields — `offset` via `Secp256k1::read_F`, then `output` and `outpoint` via `consensus_decode` — and returns them without any cross-field validation: [1](#0-0) 

The honest producer, `Scanner::scan_transaction` (mod.rs:199-214), always constructs a consistent triple: the `outpoint` is derived from the real `tx.compute_txid()` and `vout`, and the `offset` is the registered offset for that exact `script_pubkey`. But `read` accepts attacker-supplied bytes where:

1. `outpoint` references a non-existent or unrelated on-chain output, while `output` carries a valid Serai `script_pubkey` and an arbitrary inflated `value`.
2. `offset` is chosen to pass the only downstream check.

When these `ReceivedOutput`s reach `SignableTransaction::new` (send.rs:150-256), the code sums `input.output.value` (send.rs:175) as real spendable funds and builds payments/change/fee math against that fabricated `input_sat`. In `SignableTransaction::multisig` (send.rs:273-285), the sole integrity check is `p2tr_script_buf(offset.group_key())? == self.prevouts[i].script_pubkey` — it validates `offset ↔ script_pubkey` only, never `outpoint ↔ output` and never that the outpoint is a real, confirmed UTXO: [2](#0-1) 

The group will then run a full FROST signing round (send.rs:373-397, `Prevouts::All` over the fabricated prevouts) producing a transaction that is guaranteed invalid on-chain (wrong prevout hash/amount in the BIP-341 sighash or a nonexistent input). If the fabricated `ReceivedOutput` is recorded as a deposit before spend is attempted, funds are reported received that are not spendable.

### Impact Explanation
- **Fabricated deposits**: any path that feeds untrusted bytes through `ReceivedOutput::read` and then treats the parsed output as a received UTXO (value taken from `output.value`) credits the group with funds that do not exist, enabling payments/burns/mints scheduled against phantom inputs.
- **Trapped-funds analog of the Dexter bug**: like Dexter's functions accepting tezos they don't use, `SignableTransaction::new` accepts a claimed input amount it cannot actually spend; the discrepancy is only discovered as an invalid transaction after a threshold signing ceremony, burning a signing round and, where accounting has already recorded the deposit, crediting unbacked value.
- **Deterministic signing failure**: since the Schnorr signature is valid only for the sighash committing to the fabricated `prevouts` (send.rs:375, 386), the resulting transaction can never confirm — the worst case is mis-accounted funds, not theft of real UTXOs.

### Likelihood Explanation
Reachable whenever `ReceivedOutput` bytes cross a trust boundary (network messages, serialized output claims, or any consumer that re-parses rather than trusting `scan_transaction` output). Constructing the bytes requires no secret: the group's `p2tr_script_buf` is public, and `offset` = the registered offset (or `Scalar::ZERO` for the base key) is known. The attack is confined to integrity of received-output reporting — it cannot forge a spend of a real UTXO, since the fabricated prevout makes the on-chain transaction invalid — which keeps this at Medium rather than High.

### Recommendation
Mirror the report's short-term fix: validate what is accepted.
- In `ReceivedOutput::read` (or a new `verify`/`check` constructor), enforce self-consistency: recompute that `p2tr_script_buf(group_key + G*offset)` equals `output.script_pubkey`, matching the check duplicated in `SignableTransaction::multisig`.
- For provenance, callers that ingest externally-supplied `ReceivedOutput`s should confirm the `outpoint` resolves on-chain to a `TxOut` equal to `output` before treating it as received funds — the same check `Scanner::scan_transaction` gets for free from `tx.compute_txid()`.
- Document on `read` that it performs no binding between `outpoint`, `output`, and `offset`.

### Proof of Concept
```rust
// Attacker knows the group's base key `key` (even-Y) and its p2tr script.
use bitcoin::{OutPoint, TxOut, Amount, Txid, hashes::Hash};
use k256::Scalar;
use bitcoin_serai::wallet::{p2tr_script_buf, ReceivedOutput, SignableTransaction};

// Fabricate a "received" output: real script, fake value, fake outpoint.
let fake = ReceivedOutput::read::<&[u8]>(&mut {
    let mut buf = Scalar::ZERO.to_bytes().to_vec();          // offset 0 => base key
    buf.extend(bitcoin::consensus::encode::serialize(&TxOut {
        value: Amount::from_sat(100_000_000),                // claims 1 BTC
        script_pubkey: p2tr_script_buf(key).unwrap(),        // group's real script
    }));
    buf.extend(bitcoin::consensus::encode::serialize(&OutPoint {
        txid: Txid::all_zeros(),                             // nonexistent outpoint
        vout: 0,
    }));
    buf
}.as_slice()).unwrap();

// SignableTransaction::new treats `fake.value()` as real funds for
// payments/change/fee math and `multisig()` accepts it, because
// offset 0 + group key == script_pubkey. The resulting signed TX is
// unconfirmable; any accounting that credited the "deposit" is wrong.
let tx = SignableTransaction::new(vec![fake], &payments, change, None, fee)?;
assert!(tx.multisig(&keys).is_some()); // passes the only validation
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

**File:** networks/bitcoin/src/wallet/send.rs (L273-285)
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
  }
```

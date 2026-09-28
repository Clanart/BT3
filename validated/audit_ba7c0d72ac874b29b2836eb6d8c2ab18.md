### Title
Untrusted `ReceivedOutput::read` accepts arbitrary `TxOut`/`OutPoint`, enabling fabricated balances and permanently locked funds - (networks/bitcoin/src/wallet/mod.rs:122)

### Summary
The PercentFinance incident class — protocol accounting recording funds that can never be moved — maps directly onto Serai's Bitcoin wallet deserialization path. `ReceivedOutput::read` reconstructs a `ReceivedOutput` from raw bytes with no validation that the embedded `TxOut` is a Taproot output paying to the group's key, nor that the `OutPoint` exists on chain. Any bytes an unprivileged party feeds to this reader yield a `ReceivedOutput` indistinguishable from one produced by `Scanner::scan_transaction`, the only path that ties outputs to real on-chain data. The result is a balance the system records yet cannot ever spend: either the outpoint doesn't exist, or `SignableTransaction::multisig` refuses to sign because the prevout's `script_pubkey` does not equal `p2tr_script_buf(offset.group_key())`.

### Finding Description
`ReceivedOutput::read` performs only structural decoding:

```rust
// networks/bitcoin/src/wallet/mod.rs
pub fn read<R: Read>(r: &mut R) -> io::Result<ReceivedOutput> {
  let offset = Secp256k1::read_F(r)?;
  output = TxOut::consensus_decode(&mut buf_r)...;
  outpoint = OutPoint::consensus_decode(&mut buf_r)...;
  Ok(ReceivedOutput { offset, output, outpoint })
}
``` [1](#0-0) 

Three fields are fully attacker-controlled: the scalar `offset`, the `TxOut` (arbitrary `value` and arbitrary `script_pubkey` — no `is_p2tr()` check, no comparison to any expected key), and the `OutPoint` (arbitrary txid/vout). The legitimate producer, `Scanner::scan_transaction`, only builds `ReceivedOutput`s from confirmed chain data whose `script_pubkey` was found in the scanner's registered-script map: [2](#0-1) 
The deserialized path skips every one of these guarantees, yet returns the identical type, so any consumer crediting balances from deserialized `ReceivedOutput`s cannot distinguish forged inputs from real deposits.

When such an output is later fed to `SignableTransaction::new`/`multisig`, the only integrity check is:

```rust
// networks/bitcoin/src/wallet/send.rs
let offset = keys.clone().offset(self.offsets[i]);
if p2tr_script_buf(offset.group_key())? != self.prevouts[i].script_pubkey {
  None?;
}
``` [3](#0-2) 
This check catches mismatched scripts only by silently returning `None` — i.e., signing fails outright. For a forged `script_pubkey`, the "funds" are permanently locked: recorded as received, but no threshold signature can ever be produced for them. For a forged `OutPoint` with a correctly-formed script, `multisig` will sign a transaction spending a nonexistent input, which the Bitcoin network rejects — the credited balance is again unspendable forever.

### Impact Explanation
This reproduces the PercentFinance failure mode on Serai's own shape: assets enter protocol accounting that can never exit. Concretely:

1. **Fabricated deposit → real theft:** If deserialized outputs are used to credit an account (the read path exists precisely to transport scanned outputs across trust boundaries), an attacker supplies bytes encoding a multi-million-satoshi `TxOut` under a valid-looking script plus a bogus `OutPoint`. They are credited BTC that doesn't exist and can withdraw against the pool's real reserves — effective theft of all liquidity backing the fake balance.
2. **Permanent lock (PercentFinance analog):** Even absent crediting, a forged output whose `script_pubkey` fails the `multisig` check, or whose `OutPoint` doesn't resolve, becomes a permanently frozen entry — identical to PercentFinance's markets holding 446k USDC / 28 WBTC / 313 ETH that could never be withdrawn. The scheduler selecting such an input produces transactions that can never be signed or can never confirm, stalling the wallet's UTXO set.

### Likelihood Explanation
Reachability requires only that a party cause untrusted bytes to reach `ReceivedOutput::read` — one of the explicitly listed untrusted-byte sinks. Serialization round-trip (`write`/`serialize` ↔ `read`) is exercised in tests as the transport mechanism for outputs between scanning and signing contexts, confirming this read path is the deserialization boundary for output data: [4](#0-3) 
Crafting valid bytes is trivial — `consensus_encode` of any `TxOut`/`OutPoint` plus any scalar — requiring no key material, no collusion, and no privileged position. The only mitigating factor is that exploit value depends on the consumer trusting deserialized outputs without re-deriving them via `Scanner`; the type offers no marker distinguishing the two origins.

### Recommendation
- In `ReceivedOutput::read`, validate that `output.script_pubkey` is P2TR (`script_pubkey.is_p2tr()`) at minimum, mirroring the assertion already present in `OutputTrait::key` downstream.
- Prefer a design where the spendable key/scrtipt relationship is proven on read: recompute `p2tr_script_buf(scanner_key + G*offset)` against `output.script_pubkey`, or carry the Scanner context into deserialization so only registered scripts deserialize.
- Treat `OutPoint`s as claims: before crediting or scheduling spends, re-verify the outpoint resolves on chain to a `TxOut` equal to the stored one (hash-consistent lookup), rather than trusting serialized value/script bytes.
- Have `SignableTransaction::new` (not just `multisig`) reject inputs whose `script_pubkey` cannot match the signer's key set, failing loudly at construction instead of `None` at signing time.

### Proof of Concept
Conceptual byte-level forge (no chain interaction needed):

```rust
use bitcoin::{Amount, TxOut, OutPoint, Txid, ScriptBuf};
use bitcoin::consensus::serialize;
use k256::Scalar;
use bitcoin_serai::wallet::ReceivedOutput;

// Craft a ReceivedOutput claiming a 21 BTC deposit to a key-path-only
// P2TR script under an outpoint that was never created on chain.
let mut bytes = Vec::new();
bytes.extend(Scalar::ZERO.to_bytes());            // offset = 0
let fake_out = TxOut {
  value: Amount::from_sat(2_100_000_000),
  script_pubkey: ScriptBuf::new_p2tr_tweaked(/* any x-only key */),
};
bytes.extend(serialize(&fake_out));
let fake_point = OutPoint { txid: Txid::all_zeros(), vout: 0 };
bytes.extend(serialize(&fake_point));

let forged = ReceivedOutput::read(&mut bytes.as_slice()).unwrap();
assert_eq!(forged.value(), 2_100_000_000);
// `forged` is now indistinguishable from a scanner-produced output:
// - any consumer crediting balance() credits 21 nonexistent BTC
// - SignableTransaction::new(vec![forged], ...) accepts it as input
// - if script_pubkey matches the group key, multisig signs a spend of a
//   nonexistent outpoint -> tx rejected by Bitcoin, balance locked
// - if it doesn't, multisig returns None -> balance permanently frozen
```

Root cause: `ReceivedOutput::read` at `networks/bitcoin/src/wallet/mod.rs:122` performs unchecked consensus decoding of all three fields, with no binding to `Scanner`-registered scripts or on-chain existence — the same trust gap that turned PercentFinance's accounting entries into permanently immovable funds.

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

**File:** networks/bitcoin/src/wallet/mod.rs (L136-148)
```rust
  /// Write a ReceivedOutput to a generic satisfying Write.
  pub fn write<W: Write>(&self, w: &mut W) -> io::Result<()> {
    w.write_all(&self.offset.to_bytes())?;
    w.write_all(&serialize(&self.output))?;
    w.write_all(&serialize(&self.outpoint))
  }

  /// Serialize a ReceivedOutput to a `Vec<u8>`.
  pub fn serialize(&self) -> Vec<u8> {
    let mut res = Vec::new();
    self.write(&mut res).unwrap();
    res
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

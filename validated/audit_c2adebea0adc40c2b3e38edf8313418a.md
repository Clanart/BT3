### Title
BTC sent to a Serai multisig below the processor dust floor is permanently locked — ([File: processor/src/multisigs/scanner.rs](processor/src/multisigs/scanner.rs), [File: processor/src/networks/bitcoin.rs](processor/src/networks/bitcoin.rs))

### Summary
Analogous to the Gitcoin report — where the contract can receive ETH via `receive()` but `withdrawFunds()` can never move it once `tokenAddress` is set — the Serai Bitcoin processor permanently accepts-but-cannot-spend incoming outputs whose value is below `N::DUST`. The scanner's only "deposit reception" path silently drops such outputs, and there is no recovery mechanism: the multisig can never obtain a `ReceivedOutput` for them, so they can never be fed into `SignableTransaction`, signed, or swept. Any Bitcoin transaction an unprivileged sender broadcasts to the multisig's external/change/branch/forward P2TR address with a value under 10,000 sats is burned the moment it confirms.

### Finding Description
`Scanner::scan_transaction` in `networks/bitcoin/src/wallet/mod.rs` emits a `ReceivedOutput` for every output whose `script_pubkey` matches a registered key/offset script — with no minimum-value check. [1](#0-0) 

However, the processor-side scan loop filters the results:

```rust
// processor/src/multisigs/scanner.rs
for output in network.get_outputs(&block, key).await {
  assert_eq!(output.key(), key);
  if output.balance().amount.0 >= N::DUST {
    outputs.push(output);
  }
}
``` [2](#0-1) 

with `Bitcoin::DUST = 10_000` sats — far above the actual Bitcoin policy dust limit for a P2TR output (~330 sats, and even above the wallet's own conservative `DUST = 546`). [3](#0-2) [4](#0-3) 

A dropped output is not merely deprioritized: there is no other code path that can reconstruct it. Spending requires a `ReceivedOutput` (carrying the registered `offset`), which is only ever produced by scanning:

- `SignableTransaction::new` takes `inputs: Vec<ReceivedOutput>` and builds `offsets`/`prevouts` solely from them. [5](#0-4) 
- `SignableTransaction::multisig` refuses to sign unless `p2tr_script_buf(offset.group_key()) == prevout.script_pubkey`, so the prevout can only be consumed through the scanner-derived offset. [6](#0-5) 
- `get_outputs` re-derives outputs per block via `scanner(key)` — re-scanning the same block reproduces the same filter, so the output is invisible forever, including to any later aggregation/sweep logic. [7](#0-6) 

The filter is unconditional and there is no override, no operator-withdrawal equivalent, and no bookkeeping that would let the protocol revisit the outpoint — exactly the ETH-lock shape: receipt is accepted by the cryptography, but no spending path exists.

### Impact Explanation
Permanent loss of funds: any output below 10,000 sats sent to a Serai Bitcoin multisig address (external deposit address, or the deterministic `branch`/`change`/`forward` offset addresses derivable by anyone via `Secp256k1::hash_to_F(KEY_DST, ...)`) is rendered unspendable forever. [8](#0-7)  Unlike the ETH case, there is not even a privileged `withdrawFunds` that could be extended — the UTXO is cryptographically spendable but structurally unreachable because the signer refuses any prevout not produced through `Scanner`-registered offsets. Depositors lose the funds outright; the multisig's reported balance understates its true UTXO set.

### Likelihood Explanation
Reachable by any unprivileged party with public inputs: broadcasting a Bitcoin transaction paying e.g. 546–9,999 sats to the multisig's published P2TR address requires no permission, no validator collusion, and no malformed data — the output is a completely standard, consensus-valid P2TR output. It triggers on ordinary (if careless or malicious) deposits and on deliberately griefing dust-sized donations to the deterministic offset addresses.

### Recommendation
Do not silently discard sub-`DUST` outputs at scan time. Either:

- Emit them as a distinct `OutputType` (or record them without emitting a spend event) so the protocol can aggregate them later once economics allow, and/or
- Lower `Bitcoin::DUST` to the true P2TR dust threshold (~330 sats) so that any output the scanner detects is also economically and structurally spendable, and
- If dropping is retained, document it as a burn mechanism and consider having `get_outputs` only filter when constructing spends, not when observing receipts — mirroring the original report's recommendation of a recovery path (`withdrawFunds(withdrawToken)`) for non-primary assets.

### Proof of Concept
```rust
// Any sender crafts a standard tx paying 5_000 sats to the multisig's
// external address (script = p2tr_script_buf(tweaked group key)) — the same
// address Scanner::new registers with offset ZERO.
let dust_tx = Transaction {
    output: vec![TxOut {
        value: Amount::from_sat(5_000),                 // >= real dust (330), < N::DUST (10_000)
        script_pubkey: p2tr_script_buf(group_key).unwrap(),
    }],
    ..tx
};

// The wallet-level scanner DOES see it:
let found = Scanner::new(group_key).unwrap().scan_transaction(&dust_tx);
assert_eq!(found.len(), 1);                            // ReceivedOutput exists

// But the processor drops it before it is ever emitted/recorded:
//   scanner.rs:564  if output.balance().amount.0 >= N::DUST { push }  // 5_000 < 10_000 -> dropped
assert!(found[0].value() < Bitcoin::DUST);

// Consequence: no ReceivedOutput is ever persisted, so the outpoint can never be
// passed to SignableTransaction::new, and multisig() rejects any prevout whose
// script_pubkey doesn't match keys.offset(offset).group_key() — the BTC is locked.
```

### Citations

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

**File:** processor/src/multisigs/scanner.rs (L562-567)
```rust
          for output in network.get_outputs(&block, key).await {
            assert_eq!(output.key(), key);
            if output.balance().amount.0 >= N::DUST {
              outputs.push(output);
            }
          }
```

**File:** processor/src/networks/bitcoin.rs (L333-345)
```rust
  register(
    OutputType::Branch,
    *BRANCH_OFFSET.get_or_init(|| Secp256k1::hash_to_F(KEY_DST, b"branch")),
  );
  register(
    OutputType::Change,
    *CHANGE_OFFSET.get_or_init(|| Secp256k1::hash_to_F(KEY_DST, b"change")),
  );
  register(
    OutputType::Forwarded,
    *FORWARD_OFFSET.get_or_init(|| Secp256k1::hash_to_F(KEY_DST, b"forward")),
  );

```

**File:** processor/src/networks/bitcoin.rs (L638-646)
```rust
  const DUST: u64 = 10_000;

  // 2 inputs should be 2 * 230 = 460 weight units
  // The output should be ~36 bytes, or 144 weight units
  // The overhead should be ~20 bytes at most, or 80 weight units
  // 684 weight units, 171 vbytes, round up to 200
  // 200 vbytes at 1 sat/weight (our current minimum fee, 4 sat/vbyte) = 800 sat fee for the
  // aggregation TX
  const COST_TO_AGGREGATE: u64 = 800;
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

**File:** networks/bitcoin/src/wallet/send.rs (L27-32)
```rust
#[rustfmt::skip]
// https://github.com/bitcoin/bitcoin/blob/306ccd4927a2efe325c8d84be1bdb79edeb29b04/src/policy/policy.cpp#L26-L63
// As the above notes, a lower amount may not be considered dust if contained in a SegWit output
// This doesn't bother with delineation due to how marginal these values are, and because it isn't
// worth the complexity to implement differentation
pub const DUST: u64 = 546;
```

**File:** networks/bitcoin/src/wallet/send.rs (L150-185)
```rust
  pub fn new(
    mut inputs: Vec<ReceivedOutput>,
    payments: &[(ScriptBuf, u64)],
    change: Option<ScriptBuf>,
    data: Option<Vec<u8>>,
    fee_per_vbyte: u64,
  ) -> Result<SignableTransaction, TransactionError> {
    if inputs.is_empty() {
      Err(TransactionError::NoInputs)?;
    }

    if payments.is_empty() && change.is_none() && data.is_none() {
      Err(TransactionError::NoOutputs)?;
    }

    for (_, amount) in payments {
      if *amount < DUST {
        Err(TransactionError::DustPayment)?;
      }
    }

    if data.as_ref().map_or(0, Vec::len) > 80 {
      Err(TransactionError::TooMuchData)?;
    }

    let input_sat = inputs.iter().map(|input| input.output.value.to_sat()).sum::<u64>();
    let offsets = inputs.iter().map(|input| input.offset).collect();
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

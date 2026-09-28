### Title
Bitcoin deposits below `N::DUST` are silently ignored by the processor, permanently locking user funds at the multisig key - (File: processor/src/multisigs/scanner.rs)

### Summary
Analogous to `depositERC20()` accepting an `l2Gas` value sufficient to commit the deposit on L1 but insufficient to execute `finalizeDeposit()` on L2, Serai's Bitcoin integration accepts any Bitcoin transaction paying a multisig address, but `processor/src/multisigs/scanner.rs:564` silently drops any received output whose amount is below `Bitcoin::DUST` (10,000 sats). The deposit confirms on-chain — the coins are irrevocably at the threshold key — yet it is never reported to Serai, so the user is never credited and no refund path exists. Recovery would require out-of-band manual intervention by the processor set, paralleling the original report's "recoverable only by a proxy update" caveat.

### Finding Description
`Bitcoin::DUST` is defined as `10_000` satoshis in `processor/src/networks/bitcoin.rs:638`, an order-of-magnitude-above-economic-spendability value derived from a 5000 sat/kvB spendability analysis. `Bitcoin::get_outputs` (`processor/src/networks/bitcoin.rs:686-700`) returns every output the wallet `Scanner` recognizes — including sub-10,000-sat outputs, which `Scanner::scan_transaction` (`networks/bitcoin/src/wallet/mod.rs:199-214`) returns since it matches only `script_pubkey`. The processor's multisig scanner then filters:

```rust
if output.balance().amount.0 >= N::DUST {
  outputs.push(output);
}
```

(`processor/src/multisigs/scanner.rs:564`)

There is no error path, no minimum enforced at the address level, no refund instruction parsing for the dropped output, and no downstream mechanism that ever revisits it — the output is simply absent from the emitted `ScannerEvent`s, so the Serai chain never mints the corresponding asset and the scheduler never treats the UTXO as an input it must account for.

### Impact Explanation
A user who sends a valid, confirmed Bitcoin transaction paying ≥ the consensus/relay dust (P2TR dust ≈ 330–546 sats) but < 10,000 sats to a Serai deposit address loses those coins from the protocol's perspective: the deposit is final on Bitcoin yet never credited on Serai. Funds remain spendable only by the threshold multisig acting out-of-band (analogous to the L1ECOBridge proxy-upgrade recovery), which is not an exposed user flow. This is exactly the "deposit succeeds on the source chain, finalization never occurs, user funds lost" shape of the reference issue.

### Likelihood Explanation
Any unprivileged user can reach this path with only a public Bitcoin transaction — the exact class of public input in scope. No minimum deposit is enforced or communicated by the code at the point of deposit; `DUST` is an internal scheduler/fee constant, not a documented user-facing minimum. The original finding was rated Medium for the same reason (recoverable only via exceptional action, requiring user error in a parameter with no enforced floor).

### Recommendation
- Treat sub-`DUST` outputs as still-observed deposits: emit them and let the refund/`origin` path return them (minus fee), or aggregate them so they can be claimed later once economically viable, rather than dropping them silently.
- Alternatively/also, document and expose a minimum deposit amount (`Bitcoin::DUST` = 10,000 sats) to integrators/front-ends, paralleling the report's recommended minimum `l2Gas` limit.

### Proof of Concept
1. Obtain the current multisig deposit address (`p2tr_script_buf` of the group key, per `Scanner::new`).
2. Broadcast a standard Bitcoin transaction paying e.g. 2,000 sats to that script — valid, relayable, confirmable.
3. `Bitcoin::get_outputs` returns the `Output` (the wallet `Scanner` matches the script regardless of value).
4. At `processor/src/multisigs/scanner.rs:564`, `2000 < N::DUST` (10,000), so the output is never pushed and no `ScannerEvent::Block` credit is emitted.
5. The 2,000 sats sit at the threshold key with no crediting, scheduled spend, or refund — permanently lost absent manual multisig intervention. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** processor/src/multisigs/scanner.rs (L562-566)
```rust
          for output in network.get_outputs(&block, key).await {
            assert_eq!(output.key(), key);
            if output.balance().amount.0 >= N::DUST {
              outputs.push(output);
            }
```

**File:** processor/src/networks/bitcoin.rs (L626-638)
```rust
    Since these are solely relay rules, and may be raised, we require all outputs be spendable
    under a 5000 sat/kilo-vbyte fee rate.

    5000 sat/kilo-vbyte = 5 sat/vbyte
    5 * 57 = 285 sats/spent-output

    Even if an output took 100 bytes (it should be just ~29-43), taking 400 weight units, adding
    100 vbytes, tripling the transaction size, then the sats/tx would be < 1000.

    Increase by an order of magnitude, in order to ensure this is actually worth our time, and we
    get 10,000 satoshis.
  */
  const DUST: u64 = 10_000;
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

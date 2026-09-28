### Title
Change outputs between Bitcoin's relay dust and Serai's spendability threshold are created on-chain but never re-scanned, silently stranding funds - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
The external report describes a limit (`sqrtRatioLimit`) presented as slippage protection that does not revert and silently accepts a partial/lossy outcome. The Serai analog is the change-output dust check in `SignableTransaction::new`: the code decides whether leftover input value becomes a change output by comparing it against `DUST = 546` (Bitcoin's bare relay dust), while the rest of the protocol treats any output below `Bitcoin::DUST = 10_000` as non-existent. A change output in `[546, 10_000)` is therefore created on-chain, passes every "protection" check in `send.rs`, and is then unconditionally dropped by the processor's scanner — the bound checks the wrong constant, so the transaction "partially fills" the wallet's intent and the residual funds are stranded.

### Finding Description
In `networks/bitcoin/src/wallet/send.rs`, `SignableTransaction::new` computes leftover input value after payments and the with-change fee, and emits a change output whenever `value >= DUST`, where `DUST` is the module constant `546` [1](#0-0) [2](#0-1) .

The network definition deliberately sets a much higher spendability floor: `Bitcoin::DUST = 10_000`, justified in the comments by requiring outputs remain economically spendable under elevated fee rates [3](#0-2) .

The processor's scanner then hard-filters every received output with `output.balance().amount.0 >= N::DUST`, i.e. `< 10_000` sats are never registered as received [4](#0-3) .

So for any change amount `c` with `546 <= c < 10_000`:

1. `send.rs` creates a real P2TR output paying `c` to the change address (`key + G * offsets[OutputType::Change]`) [5](#0-4) .
2. The transaction is signed by the threshold group and broadcast.
3. `Scanner::scan_transaction` does match the script_pubkey and yields a `ReceivedOutput` [6](#0-5) , but the processor drops it at the `N::DUST` filter, so it never enters the scheduler's input set.
4. The `assert!((new_outputs + total_fee) <= original_outputs)` sanity check and `fee()` accounting both treat the output as legitimately created — nothing flags the discrepancy [7](#0-6) .

Like the UniV3 `sqrtRatioLimit`, the `value >= DUST` check looks like protection ("don't create an unspendable output") but enforces a bound lower than the system's real acceptance threshold, so the operation completes with a lossy outcome instead of folding the residual into the fee (which `change: None` would do) or erroring.

### Impact Explanation
Each affected transaction permanently strands up to 9,454 sats in a change output the protocol will never spend: the scheduler never sees it, so it can never be aggregated or used as an input, and it is not even counted in `operating_costs` (which only bumps when `on_chain_expected_change < Self::DUST` is estimated, a separate and approximate check) [8](#0-7) . The funds are recoverable only by out-of-band manual signing with the threshold key. This is a silent, non-reverting loss of user/protocol funds — the same impact class as the original report (partial fill instead of revert → residual tokens left behind).

### Likelihood Explanation
Reachable by an unprivileged party via public inputs: any user funding the multisig chooses the exact satoshi value of the outputs they send (the `ReceivedOutput`s that become inputs). Since `input_sat`, `payment_sat`, and `fee_with_change` are all visible quantities and deposits of arbitrary size are permissionless, an attacker can craft deposit amounts such that a scheduled transaction's change lands inside the `[546, 10_000)` window. Because the scheduler amortizes fees across payments and batches inputs, the attacker cannot deterministically hit the window on a specific transaction, but across many deposits the probability of some change output falling in the ~9,454-sat gap is realistic; even non-adversarial operation hits it occasionally since change is effectively uniform modulo the fee.

### Recommendation
In `SignableTransaction::new`, decide whether to emit the change output using the protocol's spendability threshold rather than the raw relay dust — i.e. expose the required minimum as a parameter (or raise `DUST` usage here to match `Network::DUST`) and only push the change `TxOut` when `value >= Bitcoin::DUST`-equivalent, folding the remainder into the fee otherwise, mirroring the already-documented "leftover becomes fee" path [9](#0-8) . Equivalently, after constructing the transaction, assert `fee() - needed_fee` does not silently absorb a spendable-denominator amount — check the actual outcome (change value created vs. what the scanner will accept), the same fix recommended for the UniV3 issue (compare the received amount against a minimum rather than trusting the price cap).

### Proof of Concept
Conceptual trace on regtest:

1. `Scanner::new(key)` → register change offset; note `change_script = p2tr_script_buf(key + G*offset_change)`.
2. Fund the key with a single `ReceivedOutput` of value `V` chosen so that `V - payment_sat - fee_with_change = 600` (i.e. `>= 546`, `< 10_000`). With `fee_per_vbyte = 1` and a ~200-vbyte transaction this is trivially satisfiable.
3. Call `SignableTransaction::new(inputs, &payments, Some(change_script), None, 1)`. The constructor succeeds and pushes `TxOut { value: 600, script_pubkey: change_script }` because `600 >= DUST` [10](#0-9) .
4. Sign and broadcast; then run the processor scan path on the confirming block. `Scanner::scan_transaction` returns the `ReceivedOutput` with `offset = change`, but `processor/src/multisigs/scanner.rs` drops it since `600 < Bitcoin::DUST` [11](#0-10) .
5. Result: 600 sats sit at the multisig's change key indefinitely — never indexed, never schedulable, never counted as an operating cost — while the transaction reported success. Repeating the pattern compounds the stranded amount.

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L30-32)
```rust
// This doesn't bother with delineation due to how marginal these values are, and because it isn't
// worth the complexity to implement differentation
pub const DUST: u64 = 546;
```

**File:** networks/bitcoin/src/wallet/send.rs (L143-148)
```rust
  /// Create a new SignableTransaction.
  ///
  /// If a change address is specified, any leftover funds will be sent to it if the leftover funds
  /// exceed the minimum output amount. If a change address isn't specified, all leftover funds
  /// will become part of the paid fee.
  ///
```

**File:** networks/bitcoin/src/wallet/send.rs (L223-235)
```rust
    // If there's a change address, check if there's change to give it
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
    }
```

**File:** processor/src/networks/bitcoin.rs (L622-638)
```rust
    Bitcoin defines multiple minimum feerate constants *per kilo-vbyte*. Currently, these are:
    - 1000 sat/kilo-vbyte for a transaction to be relayed
    - Each output's value must exceed the fee of the TX spending it at 3000 sat/kilo-vbyte
    The DUST constant needs to be determined by the latter.
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

**File:** processor/src/networks/bitcoin.rs (L666-669)
```rust
  fn change_address(key: ProjectivePoint) -> Option<Address> {
    let (_, offsets, _) = scanner(key);
    Some(address_from_key(key + (ProjectivePoint::GENERATOR * offsets[&OutputType::Change])))
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

**File:** networks/bitcoin/src/wallet/mod.rs (L199-213)
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
```

**File:** processor/src/networks/mod.rs (L532-534)
```rust
      // Sanity check the fee was successfully amortized
      let new_outputs = payments.iter().map(|payment| payment.balance.amount.0).sum::<u64>();
      assert!((new_outputs + total_fee) <= original_outputs);
```

**File:** processor/src/networks/mod.rs (L579-591)
```rust
    if change.is_some() {
      let on_chain_expected_change =
        inputs.iter().map(|input| input.balance().amount.0).sum::<u64>() -
          payments.iter().map(|payment| payment.balance.amount.0).sum::<u64>() -
          tx_fee;
      // If the change value is less than the dust threshold, it becomes an operating cost
      // This may be slightly inaccurate as dropping payments may reduce the fee, raising the
      // change above dust
      // That's fine since it'd have to be in a very precarious state AND then it's over-eager in
      // tabulating costs
      if on_chain_expected_change < Self::DUST {
        operating_costs += theoretical_change_amount;
      }
```

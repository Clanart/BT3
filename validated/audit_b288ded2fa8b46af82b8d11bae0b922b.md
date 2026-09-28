### Title
`Scanner` reports sub-dust / uneconomical outputs as spendable received funds without any value check - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
The OUSG report's bug class is an inadequate balance/limit check that conflates "balance exists" with "the operation can succeed," causing a redeem to fail even though the aggregate of available pools suffices. In Serai's Bitcoin wallet, `Scanner::scan_transaction` reports any transaction output paying to a registered script as a `ReceivedOutput` with no check on the output's value. An unprivileged third party can send dust (below `DUST = 546`, or simply below the marginal fee cost of an additional input) to Serai's deterministic `External`/`Branch`/`Change`/`Forwarded` addresses, causing the processor to account for and attempt to spend funds that are not economically spendable — the inverse shape of the same error: the wallet treats a resource as fully usable when only a partial/unusable balance exists.

### Finding Description
`Scanner::scan_transaction` matches outputs purely on `script_pubkey` and unconditionally pushes a `ReceivedOutput` for each match:

```rust
// networks/bitcoin/src/wallet/mod.rs
if let Some(offset) = self.scripts.get(&output.script_pubkey) {
  res.push(ReceivedOutput { offset: *offset, output: output.clone(), ... });
}
``` [1](#0-0) 

There is no `output.value` filter against `DUST` or against the marginal fee of adding an input. The processor's `get_outputs` forwards these directly into balance tracking and scheduling (`processor/src/networks/bitcoin.rs` [2](#0-1) ).

On the spend side, `SignableTransaction::new` checks only the aggregate: `input_sat < payment_sat + needed_fee` → `NotEnoughFunds` [3](#0-2) . Each dust input still adds its full vbytes to `needed_fee` while contributing less value than that fee increment, so including it strictly reduces the spendable amount — mirroring the OUSG case where the manager's combined BUIDL+USDC balance sufficed but the single-pool check failed the redeem. Here, a transaction that would succeed without the dust input fails `NotEnoughFunds` (or burns value into fees) once the scanner-fed scheduler includes it.

### Impact Explanation
- An attacker can grief any Serai multisig Bitcoin address by sending many tiny outputs to the publicly derivable `external`/`branch`/`change`/`forward` script_pubkeys. All offsets are deterministic (`Secp256k1::hash_to_F(KEY_DST, b"...")` in `processor/src/networks/bitcoin.rs` [4](#0-3) ), so the addresses are known to anyone.
- The processor reports these as received balance and may schedule transactions including them, causing `NotEnoughFunds` failures or silently converting attacker-provided "balance" into fees — funds reported received that are not spendable for their reported value.
- If the change leftover after accounting for the extra input weight drops below `DUST`, the change output is dropped and the remainder is burned as fee [5](#0-4) .

### Likelihood Explanation
Triggering requires only that someone sends a dust output to a known Serai deposit address — no validator compromise, no collusion, no leaked keys. Deposit addresses are long-lived and publicly advertised, so this is reachable by any unprivileged Bitcoin user at trivial cost (~546 sats per griefing UTXO). Whether it escalates beyond nuisance depends on the scheduler's input-selection policy (out of scope here), but the scanner layer unconditionally passes such outputs through.

### Recommendation
- In `Scanner::scan_transaction` (or the `get_outputs` wrapper), drop or flag outputs whose `value < DUST`, and preferably whose value is below the marginal fee cost of spending an input (`~57.5 vbytes * fee_rate` for a key-path input), rather than reporting them as freely spendable balance.
- Alternatively, tag `ReceivedOutput` with an economics-aware classification so the scheduler can exclude inputs whose fee cost exceeds their value, analogous to the recommended mitigation of pooling available balances rather than applying a single-pool check.

### Proof of Concept
Conceptual, following the pattern of `test_transaction_errors` in `networks/bitcoin/tests/wallet.rs`:

```rust
// Attacker sends a 300-sat (< DUST) output to the Serai external address.
// scanner.scan_transaction returns it as a normal ReceivedOutput.
let dust_output = /* output.value = 300, offset = Scalar::ZERO */;

// A spend that would succeed with only the honest input now fails or
// wastes funds because dust_input contributes 300 sats but adds ~58
// vbytes * fee_per_vbyte to needed_fee:
let inputs = vec![honest_output.clone(), dust_output.clone()];
// With honest_output.value() = payment + needed_fee(honest) + margin
// where margin < 58 * fee_per_vbyte - 300:
assert!(matches!(
  SignableTransaction::new(inputs, &payments, change, None, fee_per_vbyte),
  Err(TransactionError::NotEnoughFunds { .. })
));
// The same payment succeeds if the scanner had filtered the dust input:
assert!(SignableTransaction::new(vec![honest_output], &payments, change, None, fee_per_vbyte).is_ok());
```

The root cause is the unconditional `res.push(ReceivedOutput { ... })` in `scan_transaction` with no `output.value` sanity bound [6](#0-5) .

Uncertainty note: I did not inspect the out-of-scope scheduler (`N::Scheduler::schedule`) to confirm it naively includes all scanned inputs; if it already performs value-aware coin selection, the effective severity drops accordingly.

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

**File:** processor/src/networks/bitcoin.rs (L333-344)
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

**File:** networks/bitcoin/src/wallet/send.rs (L215-221)
```rust
    if input_sat < (payment_sat + needed_fee) {
      Err(TransactionError::NotEnoughFunds {
        inputs: input_sat,
        payments: payment_sat,
        fee: needed_fee,
      })?;
    }
```

**File:** networks/bitcoin/src/wallet/send.rs (L224-235)
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
    }
```

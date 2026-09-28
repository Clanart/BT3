### Title
Attacker can grief the Bitcoin wallet by sending economically-unspendable dust outputs that the scanner credits as balance - ([File: networks/bitcoin/src/wallet/send.rs])

### Summary
The reported bug class is: an unprivileged party directly transfers tokens to the protocol's address, inflating a `balanceOf`-style measurement and thereby DoS-ing a core flow. In Serai's Bitcoin stack, the analog is the `Scanner`/`SignableTransaction` pair: any output paying to a registered P2TR script is credited as a `ReceivedOutput`, with no check that the output's value exceeds the fee cost of spending it. An attacker who knows the (public) group key can send many minimally-above-dust outputs to the multisig's external/branch/change/forward addresses; these are reported as received funds even though each input costs more in fees to spend than it contributes, so balance is reported that is not economically spendable and transactions built over the inflated input set can fail with `NotEnoughFunds` / `TooLargeTransaction`.

### Finding Description
`Scanner::scan_transaction` accepts any output whose `script_pubkey` matches a registered script (`self.scripts.get(&output.script_pubkey)`), with no value-based filtering inside bitcoin-serai itself — the only gate is the caller-side `output.balance().amount.0 >= N::DUST` check in the processor. All of the relevant scripts are deterministic functions of the public group key (`p2tr_script_buf(key + G*offset)` for the fixed branch/change/forward offsets registered in `processor/src/networks/bitcoin.rs:324-344`), so any third party can compute them and fund them. [1](#0-0) [2](#0-1) 

On the spend side, `SignableTransaction::new` sums the nominal face value of every supplied input (`input_sat`) but charges a per-input weight-derived fee (`fee_per_vbyte * vbytes`, where vbytes grows ~58 vB per P2TR input). An output whose value is ≥ `DUST` (546 sats) but less than its marginal fee cost is counted as spendable balance yet reduces, rather than increases, what the transaction can pay out — directly analogous to counting a forced `balanceOf` donation as usable collateral. [3](#0-2) [4](#0-3) 

If the input set is flooded, the transaction either trips `NotEnoughFunds` (attacker dust consumes fee budget that real payments needed) or `TooLargeTransaction` (`weight > MAX_STANDARD_TX_WEIGHT`), both of which abort transaction construction before signing. [5](#0-4) 

### Impact Explanation
An attacker can permanently degrade the spendable balance of a Serai Bitcoin multisig: dust outputs are credited by the scanner and persisted as received outputs, yet each is a net-negative input at ordinary fee rates (a 546–~2000 sat output can cost more than its value to spend at moderate feerates). If consumers of `ReceivedOutput` feed the full scanned set into `SignableTransaction::new`, construction fails with `NotEnoughFunds` or `TooLargeTransaction`, a temporary DoS of withdrawals/forwarding until operators intervene — mirroring the reported "raise the ceiling" remediation. There is also no way to refuse or return these outputs; they are indistinguishable from legitimate deposits.

### Likelihood Explanation
Reachable by any unprivileged party: the group key is public and all four output scripts are derived from it deterministically. Cost is bounded only by the number of outputs the attacker is willing to fund at just above `DUST` (546 sats each) plus their own transaction fees — cheap griefing relative to the operational disruption, same asymmetry as in the Ubiquity report. Severity is Medium rather than High because the funds remain technically spendable and operators could, with out-of-band logic, exclude the dust inputs — but nothing in bitcoin-serai distinguishes attacker dust from legitimate outputs to enable that automatically.

### Recommendation
Track input economics instead of face value, mirroring the report's "internal accounting instead of `balanceOf`" fix:

- In `SignableTransaction::new` (networks/bitcoin/src/wallet/send.rs), charge each `ReceivedOutput` its marginal input weight (≈58 vB) and reject/skip inputs where `value < marginal_fee`, rather than crediting `input.output.value` unconditionally into `input_sat`.
- Alternatively/additionally, raise the effective acceptance threshold above the raw `DUST` constant so scanned outputs are only reported when `value >= DUST + cost_to_spend` at a configurable reference fee rate.
- Document that `Scanner`/`ReceivedOutput` make no economic-spendability guarantee, so callers must filter inputs before constructing transactions.

### Proof of Concept
1. Observe the multisig group key `K`; compute `p2tr_script_buf(K)` (external address) or the change/branch scripts via the fixed offsets (`hash_to_F("Serai Bitcoin Output Offset", b"change")` etc.).
2. Broadcast a transaction with many outputs of, e.g., 1000 sats each to those scripts, and get it confirmed.
3. `Bitcoin::get_outputs` / `Scanner::scan_transaction` returns each as a `ReceivedOutput`/`Output` with `balance() = 1000 sats`; they pass the `>= N::DUST` filter.
4. Constructing `SignableTransaction::new(all_scanned_outputs, payments, change, None, fee_per_vbyte)` either (a) silently overstates `input_sat` so the plan appears funded but each dust input nets negative value, or (b) once enough outputs accumulate, returns `Err(TooLargeTransaction)` / `Err(NotEnoughFunds)`, blocking the spend despite ample nominal balance — the same "donation DoS" shape as the Ubiquity ceiling-griefing issue.

### Citations

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

**File:** networks/bitcoin/src/wallet/send.rs (L27-32)
```rust
#[rustfmt::skip]
// https://github.com/bitcoin/bitcoin/blob/306ccd4927a2efe325c8d84be1bdb79edeb29b04/src/policy/policy.cpp#L26-L63
// As the above notes, a lower amount may not be considered dust if contained in a SegWit output
// This doesn't bother with delineation due to how marginal these values are, and because it isn't
// worth the complexity to implement differentation
pub const DUST: u64 = 546;
```

**File:** networks/bitcoin/src/wallet/send.rs (L175-176)
```rust
    let input_sat = inputs.iter().map(|input| input.output.value.to_sat()).sum::<u64>();
    let offsets = inputs.iter().map(|input| input.offset).collect();
```

**File:** networks/bitcoin/src/wallet/send.rs (L204-221)
```rust
    let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);

    let mut needed_fee = fee_per_vbyte * vbytes;
    // Technically, if there isn't change, this TX may still pay enough of a fee to pass the
    // minimum fee. Such edge cases aren't worth programming when they go against intent, as the
    // specified fee rate is too low to be valid
    // bitcoin::policy::DEFAULT_MIN_RELAY_TX_FEE is in sats/kilo-vbyte
    if needed_fee < ((u64::from(bitcoin::policy::DEFAULT_MIN_RELAY_TX_FEE) * vbytes) / 1000) {
      Err(TransactionError::TooLowFee)?;
    }

    if input_sat < (payment_sat + needed_fee) {
      Err(TransactionError::NotEnoughFunds {
        inputs: input_sat,
        payments: payment_sat,
        fee: needed_fee,
      })?;
    }
```

**File:** networks/bitcoin/src/wallet/send.rs (L241-243)
```rust
    if weight > u64::from(bitcoin::policy::MAX_STANDARD_TX_WEIGHT) {
      Err(TransactionError::TooLargeTransaction)?;
    }
```

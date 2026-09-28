### Title
Attacker can deposit economically unspendable outputs which the scanner credits, penalizing the honest validator set with fees — ([File: networks/bitcoin/src/wallet/send.rs])

### Summary
The M08 bug class is "an unprivileged party uses public inputs to make an honest operation partially fail while the honest party is still penalized." In Serai's bitcoin-serai crate the reachable analog is not gas starvation but **value starvation**: `Scanner::scan_transaction` credits any output paying to a registered script with no minimum-value check, and `SignableTransaction::new` never verifies that an input's value exceeds the marginal fee cost of spending it. An attacker can therefore flood a Serai deposit address with near-dust UTXOs that are reported as received yet cost more in fees to spend than they contribute — the honest set is penalized (fees burned / funds stranded) by an operation the attacker's public transaction forced upon it.

### Finding Description
`Scanner::scan_transaction` pushes a `ReceivedOutput` for every output whose `script_pubkey` is in `self.scripts`, with no check on `output.value` [1](#0-0) . The only dust check in the wallet applies to *outgoing* payments, not received inputs: `SignableTransaction::new` rejects `payments` under `DUST` but sums `inputs` unconditionally [2](#0-1) . The fee accounting then charges `fee_per_vbyte * vbytes` where vbytes grows linearly with `tx_ins.len()` (each Taproot input costs ~57.5 vB) [3](#0-2) . An input worth less than ~57.5 × fee_per_vbyte sats is net-negative: spending it destroys more value than it adds. Since a P2TR output only needs ~330 sats to be relay-valid (below the 546-sat `DUST` constant the wallet uses), an attacker can create confirmed outputs that (a) are reported by the scanner as received funds, and (b) either get swept into a transaction at a loss or remain permanently unspendable, stranding "received" balances.

### Impact Explanation
Fits the accepted impact "funds reported received that are not spendable." The attacker, with only a public Bitcoin transaction, inflates Serai's reported deposits with outputs that are uneconomical to ever spend. If the scheduler sweeps them, the validator set pays fees exceeding the input value (direct economic penalty mirroring M08's penalize-on-failure); if they are never spent, deposits are permanently stranded.

### Likelihood Explanation
Any Bitcoin user can send a ≥~330-sat output to a known Serai deposit script at negligible cost. No key material, validator status, or collusion is required — identical reachability to the mempool frontrunner in M08.

### Recommendation
Enforce a minimum economical value on received inputs: either filter outputs below a value floor in `Scanner::scan_transaction`, or in `SignableTransaction::new` drop (or reject transactions requiring) inputs whose `value` is less than their marginal weight cost (`fee_per_vbyte * per_input_vbytes`). Document that outputs below the floor are not credited.

### Proof of Concept
1. Attacker obtains a Serai deposit script (public, registered via `register_offset` / the base key).
2. Attacker broadcasts a confirmed transaction paying 400 sats (< 546 `DUST`, but relay-valid for P2TR) to that script.
3. `Scanner::scan_transaction` returns a `ReceivedOutput` for it — Serai reports the deposit as received.
4. At `fee_per_vbyte` ≈ 10, the input's marginal cost (~57.5 vB × 10 = 575 sats) exceeds its 400-sat value. Any `SignableTransaction::new` including it in `inputs` burns ~175 sats net, and `SignableTransaction::new` raises no error since inputs are never value-checked [4](#0-3) .

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

**File:** networks/bitcoin/src/wallet/send.rs (L165-185)
```rust
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

**File:** networks/bitcoin/src/wallet/send.rs (L204-220)
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
```

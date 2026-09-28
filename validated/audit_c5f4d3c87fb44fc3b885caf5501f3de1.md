### Title
Scanner accepts uneconomical dust outputs and `SignableTransaction::new` spends all inputs without checking value vs. fee cost, letting an unprivileged sender burn multisig funds in fees - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
The reported bug class is "a resource is checked for existence but not for the quality that makes using it safe" (a UniV3 pool existing but being illiquid/manipulable). The same shape exists in Serai's Bitcoin wallet: `Scanner::scan_transaction` accepts any output paying to a watched `script_pubkey` regardless of value, and `SignableTransaction::new` consumes every supplied `ReceivedOutput` as an input without ever checking whether the input's value exceeds the marginal fee cost of spending it. An unprivileged attacker can dust the multisig address, causing the wallet to spend inputs whose fee cost exceeds (or nearly equals) their value, draining legitimate funds into fees.

### Finding Description
`Scanner::scan_transaction` returns a `ReceivedOutput` for any transaction output whose `script_pubkey` is in `self.scripts`, with no minimum-value filter. `networks/bitcoin/src/wallet/mod.rs:199-214` [1](#0-0) 

`SignableTransaction::new` then:
- sums every input's value into `input_sat` and unconditionally turns every input into a `TxIn` (`networks/bitcoin/src/wallet/send.rs:175-185`),
- only rejects construction if the *aggregate* `input_sat < payment_sat + needed_fee` (`send.rs:215-221`).

Each Taproot key-spend input is priced by `calculate_weight_vbytes` using a fixed-size input with a 64-byte witness signature (`send.rs:68-84`), i.e. ~58 vbytes of marginal cost per input at `fee_per_vbyte`. There is no check that any individual input's `output.value` is above that marginal cost, or even above `DUST` (the `DUST` constant is only applied to *payments* and *change*, `send.rs:165-169` and `send.rs:228-233`). [2](#0-1) 

The result mirrors the oracle finding exactly: existence is verified (the script_pubkey matches a registered key), but the "liquidity" analog — whether the output is worth spending — is never checked. Because `Prevouts::All` commits each signature to all prevouts (`send.rs:373-390`), every input listed is spent atomically; there is no way to drop a dust input at signing time. [3](#0-2) 

### Impact Explanation
Any party who knows the multisig address (it is a public deposit address) can send it low-value outputs. On the next spend, the wallet includes these inputs and pays `fee_per_vbyte * ~58` vbytes each to spend them. When the fee rate is non-trivial (e.g. 50–200 sat/vB during congestion), spending a few-hundred- or few-thousand-sat output costs more than the output is worth; the deficit is paid out of the legitimate inputs' value, i.e. the multisig's funds are burned as miner fees. Repeated dusting forces this loss on every transaction the wallet builds, since all scanned outputs are used as inputs. This is "funds reported received that are not economically spendable" plus forced fee burn — a direct monetary loss reachable with only public inputs (a Bitcoin transaction the attacker sends).

### Likelihood Explanation
Dusting a known, fixed deposit address costs the attacker only the dust value and is fully permissionless — precisely like funding the PassThroughWallet with an illiquid token in the original report. The scanner must pick the output up (script matches) and the transaction builder has no code path to exclude it, so the loss triggers deterministically whenever `fee_per_vbyte` makes the marginal input cost exceed the dusted value. Likelihood is moderate: it requires the integrator to actually attempt spending the dust inputs and a fee rate above the dust-value break-even.

### Recommendation
In `SignableTransaction::new` (`networks/bitcoin/src/wallet/send.rs`), compute the marginal weight/vbytes per input and reject (or filter out) any `ReceivedOutput` whose `output.value` is below `fee_per_vbyte * marginal_vbytes` or below `DUST`. Optionally surface skipped inputs to the caller so dust is never swept. This is the direct analog of the report's "check the liquidity on the pool" recommendation — check that each claimed input carries enough value to make spending it sound before committing signatures to it via `Prevouts::All`.

### Proof of Concept
1. Let `K` be the multisig's scanned key; `addr = p2tr_script_buf(K)` is public.
2. Attacker broadcasts a transaction creating outputs to `addr` with values like 600 sat each — above `DUST` so it relays, but far below realistic spend cost. `Scanner::scan_transaction` returns each as a `ReceivedOutput` with `offset = Scalar::ZERO` (`mod.rs:205-211`).
3. The wallet later calls `SignableTransaction::new(inputs, payments, change, None, fee_per_vbyte)` with `fee_per_vbyte = 100`. Each dust input adds ~58 vbytes ⇒ ~5,800 sat of fee for 600 sat of value.
4. Construction succeeds as long as the honest inputs cover `payment_sat + needed_fee` (`send.rs:215-221`); `multisig()`/`sign()` then produce a valid signed transaction committing all prevouts (`send.rs:373-390`), paying the ~5,200-sat-per-dust-input deficit to miners out of the multisig's real balance.
5. Repeating the dust attack forces this loss on every spend, since the builder provides no mechanism to exclude uneconomical inputs.

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

**File:** networks/bitcoin/src/wallet/send.rs (L204-235)
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

**File:** networks/bitcoin/src/wallet/send.rs (L373-391)
```rust
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
```

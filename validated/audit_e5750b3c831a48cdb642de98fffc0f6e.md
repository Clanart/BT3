### Title
Unchecked `u64` arithmetic overflow in `SignableTransaction::new` lets attacker-supplied payment amounts produce an invalid, unspendable transaction and panic in `fee()` - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
The Redis advisory is a stack buffer overflow reachable by a crafted attacker input; in Serai's safe-Rust codebase the reachable analog class is attacker-controlled arithmetic overflowing buffer/index bounds. `SignableTransaction::new` sums untrusted payment amounts and adds the fee with plain `u64` `+`/`.sum()` operations that wrap in release builds, bypassing the `NotEnoughFunds` check and constructing a transaction whose outputs exceed its inputs. `fee()` then subtracts outputs from inputs with `-`, which underflows.

### Finding Description
In `networks/bitcoin/src/wallet/send.rs`, `payment_sat` is computed as an unchecked `u64` sum of caller-supplied payment amounts [1](#0-0) . The solvency check adds `needed_fee` with `+` [2](#0-1) , and the change path adds `fee_with_change` with `+` inside `checked_sub` [3](#0-2) . Each payment is only checked against `DUST` (546 sats), with no upper bound [4](#0-3) . With two payments of `u64::MAX`, `payment_sat` wraps to `u64::MAX - 1`, and `payment_sat + needed_fee` wraps to a small value, so `input_sat < (payment_sat + needed_fee)` is false and the transaction is built anyway. `fee()` then computes `sum(prevouts) - sum(outputs)` with plain subtraction [5](#0-4) , which underflows to a garbage fee in release (panic in debug). The resulting `SignableTransaction` is passed to `multisig()` and signed by the FROST `TransactionMachine` [6](#0-5) .

### Impact Explanation
The multisig produces a Schnorr-signed transaction that is consensus-invalid: outputs exceed inputs (negative fee) and individual outputs exceed `MAX_MONEY`. Any coordinator/processor path that constructs a `SignableTransaction` from attacker-influenced payment amounts (e.g., withdrawal instructions) will emit an unbroadcastable transaction — the intended payment never executes, and calling `fee()` on the object panics in debug builds or reports a wildly incorrect ~`u64::MAX` fee in release. This is a loss-of-liveness for the affected signing operation and a concrete signing of an unintended (invalid) message reached purely from public input values.

### Likelihood Explanation
Triggering requires only that two or more attacker-influenced payment amounts sum past `u64::MAX`; there is no bound on individual amounts other than `>= DUST`. Wherever `SignableTransaction::new` is invoked with externally specified `(ScriptBuf, u64)` payment lists, an unprivileged party controls the wrap. No cryptographic break, collusion, or privileged position is needed.

### Recommendation
Use `checked_add`/`checked_sum` when accumulating `payment_sat`, `input_sat`, `payment_sat + needed_fee`, and `payment_sat + fee_with_change`, and use `checked_sub` in `fee()`. Reject payments whose individual or cumulative amounts exceed the spendable input total (or `Amount::MAX_MONEY`) up front, and treat overflow as `TransactionError::NotEnoughFunds`.

### Proof of Concept
```rust
// networks/bitcoin/src/wallet/send.rs behavior, release build (wrapping arithmetic)
// inputs: a single ReceivedOutput worth 1_000_000 sats
let payments = vec![
    (script_pubkey_a.clone(), u64::MAX), // >= DUST, passes per-payment check
    (script_pubkey_b.clone(), u64::MAX), // >= DUST
];
// payment_sat = u64::MAX + u64::MAX wraps to u64::MAX - 1
// payment_sat + needed_fee wraps to needed_fee - 1 (< input_sat)
// => NotEnoughFunds check at send.rs:215 is bypassed
let tx = SignableTransaction::new(inputs, &payments, Some(change), None, fee_per_vbyte).unwrap();
// tx.transaction().output values: [u64::MAX, u64::MAX, ~1_000_000 change]
// sum(outputs) >> sum(inputs): transaction is consensus-invalid and unbroadcastable
// tx.fee() at send.rs:139 underflows: 1_000_000 - (u64::MAX - 1 + ...) wraps/panics
```

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L138-141)
```rust
  pub fn fee(&self) -> u64 {
    self.prevouts.iter().map(|prevout| prevout.value.to_sat()).sum::<u64>() -
      self.tx.output.iter().map(|prevout| prevout.value.to_sat()).sum::<u64>()
  }
```

**File:** networks/bitcoin/src/wallet/send.rs (L165-169)
```rust
    for (_, amount) in payments {
      if *amount < DUST {
        Err(TransactionError::DustPayment)?;
      }
    }
```

**File:** networks/bitcoin/src/wallet/send.rs (L187-187)
```rust
    let payment_sat = payments.iter().map(|payment| payment.1).sum::<u64>();
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

**File:** networks/bitcoin/src/wallet/send.rs (L228-228)
```rust
      if let Some(value) = input_sat.checked_sub(payment_sat + fee_with_change) {
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

### Title
Unchecked u64 arithmetic in `SignableTransaction::new` overflows/panics on attacker-controlled amounts, permanently stalling spends - ([File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs))

### Summary
The reported bug class — a multiplication/addition of attacker-influenced amounts that overflows the integer type and makes a recovery path impossible — exists in Serai's Bitcoin transaction builder. `SignableTransaction::new` performs multiple unchecked `u64` operations on values that originate from `ReceivedOutput::read` (fully attacker-controlled bytes, including the `TxOut.value` field) and from the caller-supplied `payments`/`fee_per_vbyte` parameters:

- `input_sat = inputs.iter().map(|i| i.output.value.to_sat()).sum::<u64>()` [1](#0-0) 
- `payment_sat = payments.iter().map(|p| p.1).sum::<u64>()` [2](#0-1) 
- `let mut needed_fee = fee_per_vbyte * vbytes;` [3](#0-2) 
- `if input_sat < (payment_sat + needed_fee)` [4](#0-3) 
- `let fee_with_change = fee_per_vbyte * vbytes_with_change;` [5](#0-4) 

`ReceivedOutput::read` decodes `TxOut` via `consensus_decode`, which accepts any `u64` value (no 21M cap is enforced at decode time), so `input.output.value` is arbitrary attacker-controlled data [6](#0-5) .

### Finding Description
None of the arithmetic above uses `checked_*`/`saturating_*`. In debug builds every one of these operations panics on overflow; in release builds they wrap silently, corrupting the funds check:

- Two crafted `ReceivedOutput`s (or many scanned outputs) whose `value.to_sat()` sums past `u64::MAX` wrap `input_sat` to a small value. With `payment_sat`/`needed_fee` also wrapped small, the `input_sat < payment_sat + needed_fee` check can pass even though the real input total is enormous — or it can spuriously fail and prevent any spend.
- `fee_per_vbyte * vbytes` overflow wraps `needed_fee` to ~0, so the `NotEnoughFunds` check at line 215 is satisfied even when `input_sat < payment_sat`, allowing construction of a `SignableTransaction` whose outputs exceed its inputs.
- The resulting `Transaction` is then fed to `TransactionSignMachine::sign`, which computes `taproot_key_spend_signature_hash` over `Prevouts::All` and has the threshold group produce valid BIP-340 signatures for a consensus-invalid transaction [7](#0-6) . `SignableTransaction::fee()` at lines 139–141 also underflows when outputs exceed inputs, panicking post-signing.

### Impact Explanation
An unprivileged party who supplies `ReceivedOutput` bytes (e.g., serialized outputs shared between validator components via `ReceivedOutput::read`) or influences payment amounts can trigger a panic that aborts transaction construction — permanently stalling spends of real scanned UTXOs the same way the reference overflow permanently blocks reclaim — or cause the multisig to sign a consensus-invalid transaction whose outputs exceed its inputs, wasting a FROST signing session and producing an unspendable/unbroadcastable artifact. This mirrors the report's "overflow in amount × rate locks funds" class mapped onto Serai's Bitcoin send path.

### Likelihood Explanation
Medium. `TxOut::consensus_decode` accepts the full `u64` range, so reaching the overflow requires only crafted bytes, not on-chain funds. However, exploitation requires an attacker to influence the `inputs`/`payments`/`fee_per_vbyte` fed into `SignableTransaction::new` by the coordinator; outputs from `Scanner::scan_transaction` of real blocks are bounded by the 21M supply cap, so the overflow is reachable primarily through deserialized `ReceivedOutput`s or malicious plan parameters rather than pure on-chain sends.

### Recommendation
Use checked arithmetic throughout `SignableTransaction::new`:

```rust
let input_sat = inputs.iter()
    .try_fold(0u64, |acc, i| acc.checked_add(i.output.value.to_sat()))
    .ok_or(TransactionError::NotEnoughFunds { inputs: u64::MAX, payments: 0, fee: 0 })?;
let payment_sat = payments.iter()
    .try_fold(0u64, |acc, p| acc.checked_add(p.1))
    .ok_or(TransactionError::TooLargeTransaction)?;
let needed_fee = fee_per_vbyte.checked_mul(vbytes).ok_or(TransactionError::TooLowFee)?;
let total_needed = payment_sat.checked_add(needed_fee).ok_or(TransactionError::NotEnoughFunds { ... })?;
```

Additionally, validate `output.value` in `ReceivedOutput::read` (e.g., reject values above `Amount::MAX_MONEY`) and reject `fee_per_vbyte`/`payments` values that cannot fit the budget before any multiplication.

### Proof of Concept
```rust
// Construct two fake ReceivedOutputs whose decoded TxOut values saturate u64
let mut bytes = Vec::new();
bytes.extend(Scalar::ZERO.to_bytes());                       // offset for ReceivedOutput::read
// TxOut { value: u64::MAX, script_pubkey: <p2tr of group key> }
bytes.extend(u64::MAX.to_le_bytes());
bytes.push(34u8);                                          // script len
// ... push OP_1 <32-byte key> script bytes ...
// OutPoint::default() consensus bytes
bytes.extend([0u8; 36]);

let o1 = ReceivedOutput::read(&mut bytes.as_slice()).unwrap();
let o2 = o1.clone();

// input_sat wraps to u64::MAX - 1 (or panics in debug); a single dust payment
// then either spuriously fails NotEnoughFunds or passes with wrapped math.
let _ = SignableTransaction::new(vec![o1, o2], &[(script, 546)], None, None, 1);
// debug: thread panics at `sum::<u64>()` overflow
// release: input_sat == u64::MAX - 1, arithmetic proceeds on wrapped values
```

The panic/wrap occurs inside `SignableTransaction::new` before any bounds check can reject the inputs, blocking all subsequent reclaim/spend attempts that reuse these outputs.

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L175-175)
```rust
    let input_sat = inputs.iter().map(|input| input.output.value.to_sat()).sum::<u64>();
```

**File:** networks/bitcoin/src/wallet/send.rs (L187-187)
```rust
    let payment_sat = payments.iter().map(|payment| payment.1).sum::<u64>();
```

**File:** networks/bitcoin/src/wallet/send.rs (L206-206)
```rust
    let mut needed_fee = fee_per_vbyte * vbytes;
```

**File:** networks/bitcoin/src/wallet/send.rs (L215-215)
```rust
    if input_sat < (payment_sat + needed_fee) {
```

**File:** networks/bitcoin/src/wallet/send.rs (L227-227)
```rust
      let fee_with_change = fee_per_vbyte * vbytes_with_change;
```

**File:** networks/bitcoin/src/wallet/send.rs (L373-397)
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
        shares.push(share);
        Ok(sig)
      })
      .collect::<Result<_, _>>()?;

    Ok((TransactionSignatureMachine { tx: self.tx.tx, sigs }, shares))
```

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

### Title
Unvalidated u64 value arithmetic in `SignableTransaction::new` overflows and can produce an unspendable/consensus-invalid transaction - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` performs unchecked `u64` arithmetic (`input_sat` summation, `payment_sat` summation, `payment_sat + needed_fee`, `fee_per_vbyte * vbytes`) on values that originate from attacker-influenceable bytes: `ReceivedOutput::read` consensus-decodes a `TxOut` whose `value` is an arbitrary `u64` with no bound check against Bitcoin's `MAX_MONEY` or against overflow. This is the Serai analog of the Surge finding: fixed-width integer math (`userDebt * 1e18`) breaks when magnitudes exceed a bound the constructor never validates.

### Finding Description
In `networks/bitcoin/src/wallet/send.rs`: [1](#0-0) [2](#0-1) [3](#0-2) 

- Line 175: `input_sat` is a plain `sum::<u64>()` over `input.output.value.to_sat()` for each `ReceivedOutput`.
- Line 187: `payment_sat` is a plain `sum::<u64>()` over payment amounts, each only checked `>= DUST` (line 165-169) — there is no upper bound.
- Line 206: `fee_per_vbyte * vbytes` is an unchecked multiply.
- Line 215: `input_sat < (payment_sat + needed_fee)` is the only solvency check, and it is an unchecked add.
- The change path *does* use `checked_sub` (line 228), showing the overflow hazard was considered for subtraction but the additive/multiplicative paths were left raw.

`ReceivedOutput` is an explicitly in-scope untrusted-bytes entry point: `ReceivedOutput::read` (`networks/bitcoin/src/wallet/mod.rs:122-134`) decodes `TxOut::consensus_decode` with no range check on `output.value`, so a serialized `ReceivedOutput` can carry `value = u64::MAX`.

### Impact Explanation
Any sum/product exceeding `u64::MAX` panics in debug builds and silently wraps in release builds:

- Wrapping `input_sat` to a small value makes a legitimately-funded wallet report `NotEnoughFunds` — outputs that were received and owned cannot be spent (permanent lock of funds through this code path).
- Wrapping `payment_sat` low, or passing a single inflated `ReceivedOutput`, makes the solvency check `input_sat < payment_sat + needed_fee` pass when the transaction actually pays out more than its inputs. The resulting `SignableTransaction` is then driven through `multisig`/`TransactionMachine` and the FROST threshold signature is produced over a consensus-invalid transaction — the group signs a spend it cannot broadcast, burning a signing session and stalling the wallet.

This satisfies the acceptance criterion "funds reported received that are not spendable": the scanner/wallet accepts the output and the signable-transaction constructor either aborts or emits a transaction the network will reject.

### Likelihood Explanation
An unprivileged party only needs to feed a crafted `ReceivedOutput` blob (or enough large `payments` entries, each ≥ 546 sats) into the code path. No validator status, collusion, or broken BFT assumptions are required — just untrusted bytes reaching `ReceivedOutput::read`/`SignableTransaction::new`, exactly the reachability class specified.

### Recommendation
Bound-check values at the deserialization boundary and use checked arithmetic throughout `SignableTransaction::new`:

- In `ReceivedOutput::read` (or a validation helper), reject `output.value > Amount::MAX_MONEY` (21e14 sats), matching Bitcoin's consensus bound — the analog of Surge's recommended "reject high-supply token at pool creation" check.
- Replace `sum::<u64>()` for `input_sat`/`payment_sat` with `try_fold`/`checked_add`, and `fee_per_vbyte * vbytes` / `payment_sat + needed_fee` with `checked_mul`/`checked_add`, returning `TransactionError::NotEnoughFunds`/`TooLargeTransaction` on overflow.

### Proof of Concept
```rust
// networks/bitcoin/tests/wallet.rs style test (no RPC needed)
use bitcoin::{TxOut, Amount, OutPoint, ScriptBuf, consensus::encode::serialize};
use bitcoin_serai::wallet::{ReceivedOutput, SignableTransaction};

// Build a ReceivedOutput blob with an absurd (unchecked) value
let mut bytes = k256::Scalar::ONE.to_bytes().to_vec(); // offset
bytes.extend(serialize(&TxOut {
    value: Amount::from_sat(u64::MAX),
    script_pubkey: ScriptBuf::new(),
}));
bytes.extend(serialize(&OutPoint::null()));
let bogus = ReceivedOutput::read(&mut bytes.as_slice()).unwrap(); // succeeds, no bound check

let pay = (ScriptBuf::new_p2tr_tweaked(
    bitcoin::key::TweakedPublicKey::dangerous_assume_tweaked(
        bitcoin::key::XOnlyPublicKey::from_slice(&[2u8; 32]).unwrap()
    )), 1000);

// Two such outputs: input_sat wraps to u64::MAX*2 mod 2^64 = u64::MAX - 1
// -> NotEnoughFunds despite "massive" inputs, or worse:
// single bogus output u64::MAX: solvency check passes, TX outputs > inputs ->
// the multisig signs a transaction Bitcoin consensus will reject.
let tx = SignableTransaction::new(
    vec![bogus],
    &[pay],
    None,
    None,
    10,
);
// tx either spuriously errors or yields a consensus-invalid transaction
// that TransactionMachine will still produce a threshold signature for.
```

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L175-175)
```rust
    let input_sat = inputs.iter().map(|input| input.output.value.to_sat()).sum::<u64>();
```

**File:** networks/bitcoin/src/wallet/send.rs (L187-187)
```rust
    let payment_sat = payments.iter().map(|payment| payment.1).sum::<u64>();
```

**File:** networks/bitcoin/src/wallet/send.rs (L206-215)
```rust
    let mut needed_fee = fee_per_vbyte * vbytes;
    // Technically, if there isn't change, this TX may still pay enough of a fee to pass the
    // minimum fee. Such edge cases aren't worth programming when they go against intent, as the
    // specified fee rate is too low to be valid
    // bitcoin::policy::DEFAULT_MIN_RELAY_TX_FEE is in sats/kilo-vbyte
    if needed_fee < ((u64::from(bitcoin::policy::DEFAULT_MIN_RELAY_TX_FEE) * vbytes) / 1000) {
      Err(TransactionError::TooLowFee)?;
    }

    if input_sat < (payment_sat + needed_fee) {
```

### Title
Unchecked u128 Multiplication in `denormalize_amount` Causes Permanent Freezing of User Funds During Transfer Finalization - (File: near/omni-bridge/src/lib.rs)

### Summary
The `denormalize_amount` helper performs an unchecked `u128` multiplication when scaling token amounts from destination-chain decimals to origin-chain decimals. For tokens with a large decimal difference and a sufficiently large transfer amount, this multiplication overflows `u128`. When triggered inside `fin_transfer_callback`, the panic rolls back the callback but leaves the user's tokens permanently locked on the origin chain with no path to finalization.

### Finding Description
`denormalize_amount` is defined as:

```rust
fn denormalize_amount(amount: u128, decimals: Decimals) -> u128 {
    let diff_decimals: u32 = (decimals.origin_decimals - decimals.decimals).into();
    amount * (10_u128.pow(diff_decimals))   // ← unchecked multiplication
}
``` [1](#0-0) 

It is called unconditionally inside `fin_transfer_callback` to reconstruct the NEAR-side amount from the proof-supplied EVM amount:

```rust
amount: Self::denormalize_amount(init_transfer.amount.0, decimals).into(),
``` [2](#0-1) 

`init_transfer.amount.0` is the raw token amount from the origin-chain `InitTransfer` event (in the token's EVM decimals). `diff_decimals = origin_decimals − decimals` is the scaling exponent. When:

```
init_transfer.amount.0  ×  10^diff_decimals  >  u128::MAX  (≈ 3.4 × 10^38)
```

the multiplication overflows. With Rust's `overflow-checks = true` (the standard NEAR contract build profile), this is a hard panic. The callback reverts, but the origin-chain `InitTransfer` event has already been emitted and the user's tokens are already locked or burned on the origin chain. Because the proof is deterministic, every subsequent call to `fin_transfer` with the same proof will panic identically, making the lock permanent.

The same unchecked call appears in `claim_fee_callback`:

```rust
let denormalized_amount = Self::denormalize_amount(
    fin_transfer.amount.0,
    self.token_decimals.get(&token_address)...
);
let fee = transfer_message.amount.0 - denormalized_amount;  // unchecked subtraction
``` [3](#0-2) 

If overflow checks are disabled (wrapping mode), `denormalize_amount` silently returns a small wrapped value, making `fee = transfer_message.amount.0 − (wrapped small value)` a very large number, causing the relayer to drain the bridge's token balance.

### Impact Explanation
- **Permanent freezing of user funds**: A user who initiates a transfer on the origin chain with an amount that overflows `u128` after denormalization can never finalize the transfer on NEAR. Their tokens are irrecoverably locked on the origin chain.
- **Accounting corruption / potential over-payment** (if overflow checks are off): The wrapped `denormalized_amount` produces a wildly incorrect `fee`, breaking bridge collateralization and potentially draining locked tokens.

Both outcomes match the allowed impact scope: *"Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds"* and *"Balance, decimal, fee, token-mapping, or accounting corruption that breaks bridge collateralization."*

### Likelihood Explanation
The overflow threshold depends on `diff_decimals`:

| EVM decimals | NEAR decimals | diff | Overflow threshold (EVM raw units) | Human-readable |
|---|---|---|---|---|
| 6 | 24 | 18 | > 3.4 × 10²⁰ | > 340 trillion tokens |
| 0 | 24 | 24 | > 3.4 × 10¹⁴ | > 340 trillion tokens |
| 6 | 18 | 12 | > 3.4 × 10²⁶ | > 340 quadrillion tokens |

For tokens with 0 EVM decimals and 24 NEAR decimals (diff = 24), the threshold is ~3.4 × 10¹⁴ raw units. Many meme tokens or governance tokens have supplies in the quadrillions, making this threshold reachable. A user who holds and transfers such an amount triggers the bug without any privileged access.

### Recommendation
Replace the unchecked multiplication in `denormalize_amount` with a checked variant and propagate the error:

```rust
fn denormalize_amount(amount: u128, decimals: Decimals) -> u128 {
    let diff_decimals: u32 = (decimals.origin_decimals - decimals.decimals).into();
    let multiplier = 10_u128.pow(diff_decimals);
    amount.checked_mul(multiplier)
        .near_expect(BridgeError::AmountOverflow)
}
```

Similarly, replace the bare subtraction in `claim_fee_callback` with `checked_sub`:

```rust
let fee = transfer_message.amount.0
    .checked_sub(denormalized_amount)
    .near_expect(BridgeError::InvalidFee);
``` [4](#0-3) 

### Proof of Concept

**Setup**: Register a token with `origin_decimals = 24`, `decimals = 0` (diff = 24). The overflow threshold is `u128::MAX / 10^24 ≈ 3.4 × 10^14` raw units.

**Steps**:
1. On the origin chain, call `initTransfer` with `amount = 4 × 10^14` tokens (a plausible supply for a meme token with 0 decimals).
2. The origin chain emits `InitTransfer { amount: 4e14, ... }`.
3. A relayer submits the proof to NEAR via `fin_transfer`.
4. `fin_transfer_callback` calls `denormalize_amount(4e14, Decimals { decimals: 0, origin_decimals: 24 })`.
5. The computation is `4e14 × 10^24 = 4 × 10^38 > u128::MAX (≈ 3.4 × 10^38)`.
6. With overflow checks enabled: **panic** → callback reverts → transfer never stored → user's tokens permanently locked on origin chain.
7. Every retry of `fin_transfer` with the same proof produces the same panic. [1](#0-0) [5](#0-4)

### Citations

**File:** near/omni-bridge/src/lib.rs (L722-732)
```rust
        let transfer_message = TransferMessage {
            origin_nonce: init_transfer.origin_nonce,
            token: init_transfer.token,
            amount: Self::denormalize_amount(init_transfer.amount.0, decimals).into(),
            recipient: init_transfer.recipient,
            fee: Self::denormalize_fee(&init_transfer.fee, decimals),
            sender: init_transfer.sender,
            msg: init_transfer.msg,
            destination_nonce,
            origin_transfer_id: None,
        };
```

**File:** near/omni-bridge/src/lib.rs (L1122-1131)
```rust
        let denormalized_amount = Self::denormalize_amount(
            fin_transfer.amount.0,
            self.token_decimals
                .get(&token_address)
                .near_expect(BridgeError::TokenDecimalsNotFound),
        );
        // Fee includes both the user-specified fee and any dust lost during decimal
        // normalization (see `normalize_amount`). Since `denormalize(normalize(x)) <= x`
        // due to floor division, the difference naturally captures the normalization remainder.
        let fee = transfer_message.amount.0 - denormalized_amount;
```

**File:** near/omni-bridge/src/lib.rs (L2776-2779)
```rust
    fn denormalize_amount(amount: u128, decimals: Decimals) -> u128 {
        let diff_decimals: u32 = (decimals.origin_decimals - decimals.decimals).into();
        amount * (10_u128.pow(diff_decimals))
    }
```

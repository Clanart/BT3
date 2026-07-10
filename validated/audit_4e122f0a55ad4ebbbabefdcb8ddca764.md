### Title
Unchecked Multiplication Overflow in `denormalize_amount()` Can Permanently Lock User Funds - (File: `near/omni-bridge/src/lib.rs`)

### Summary
`denormalize_amount()` performs an unguarded `amount * 10_u128.pow(diff_decimals)` multiplication. Because NEAR compiles with `overflow-checks = true`, any overflow panics and reverts the `fin_transfer_callback` transaction. A user who initiates a transfer on the origin chain with an amount exceeding `u128::MAX / 10^diff_decimals` will find their funds permanently locked on the origin chain with no recovery path.

### Finding Description

`denormalize_amount` in `near/omni-bridge/src/lib.rs` is:

```rust
fn denormalize_amount(amount: u128, decimals: Decimals) -> u128 {
    let diff_decimals: u32 = (decimals.origin_decimals - decimals.decimals).into();
    amount * (10_u128.pow(diff_decimals))
}
``` [1](#0-0) 

The `Decimals` struct stores two `u8` fields:

```rust
pub struct Decimals {
    pub decimals: u8,
    pub origin_decimals: u8,
}
``` [2](#0-1) 

`denormalize_amount` is called in `fin_transfer_callback` — the NEAR-side handler that finalizes an inbound cross-chain transfer — using the amount directly from the prover result (i.e., the amount the user specified on the origin chain):

```rust
amount: Self::denormalize_amount(init_transfer.amount.0, decimals).into(),
``` [3](#0-2) 

It is also called in `fast_fin_transfer` and `denormalize_fee`: [4](#0-3) [5](#0-4) 

The `near/Cargo.toml` enables `overflow-checks = true`, so any integer overflow panics (reverts) rather than wrapping silently.

The overflow condition is:

```
amount * 10^diff_decimals > u128::MAX  (≈ 3.4 × 10^38)
```

For a token registered with `origin_decimals = 24` (NEAR native) and `decimals = 18` (EVM representation), `diff_decimals = 6`. The overflow threshold is `u128::MAX / 10^6 ≈ 3.4 × 10^32` in 18-decimal units — astronomically large for typical supply. However, for tokens with larger decimal gaps (e.g., `origin_decimals = 36, decimals = 0`, giving `diff_decimals = 36`), the threshold drops to `u128::MAX / 10^36 ≈ 3400` in 0-decimal units — a completely ordinary transfer amount. Furthermore, if `diff_decimals >= 39`, the expression `10_u128.pow(diff_decimals)` itself overflows, causing every single finalization of that token to panic regardless of amount.

There is a secondary underflow risk in the same line: `decimals.origin_decimals - decimals.decimals` is a `u8` subtraction. If `origin_decimals < decimals` (possible through misconfiguration), this also panics with `overflow-checks = true`.

### Impact Explanation

When `fin_transfer_callback` panics, NEAR reverts all state changes from that callback. However, the user's funds on the origin chain have already been locked or burned by the `initTransfer` call. There is no automatic refund or retry mechanism. The funds are permanently irrecoverable — matching the **Critical: permanent freezing of user funds** impact class.

### Likelihood Explanation

- For tokens with small `diff_decimals` (0–6, covering most EVM↔NEAR pairs), the overflow threshold is so high that it is practically unreachable.
- For tokens with `diff_decimals` in the range 18–38, the threshold falls to amounts that are large but not impossible (e.g., a whale or protocol treasury transfer).
- For tokens with `diff_decimals >= 39`, **every** finalization panics unconditionally, permanently bricking the token's inbound bridge flow.
- The `origin_decimals` and `decimals` values are set by the DAO/admin at token registration time, so the worst cases require either a misconfigured registration or a token with an unusually large decimal gap. The user-controlled variable is `amount`, which is bounded only by `u128::MAX` on the EVM side.

### Recommendation

Replace the bare multiplication with a checked variant and return an error (or panic with a clear message) rather than allowing an uncontrolled overflow panic:

```rust
fn denormalize_amount(amount: u128, decimals: Decimals) -> u128 {
    let diff_decimals: u32 = decimals.origin_decimals
        .checked_sub(decimals.decimals)
        .expect(BridgeError::InvalidDecimals.as_ref())
        .into();
    let multiplier = 10_u128.checked_pow(diff_decimals)
        .expect(BridgeError::DecimalOverflow.as_ref());
    amount.checked_mul(multiplier)
        .expect(BridgeError::AmountOverflow.as_ref())
}
```

This surfaces a clear, auditable error rather than an opaque panic, and allows callers to handle the failure gracefully (e.g., by rejecting the transfer before funds are committed on the origin chain).

### Proof of Concept

1. Admin registers a token with `origin_decimals = 42`, `decimals = 0` → `diff_decimals = 42`.
2. `10_u128.pow(42)` = 10^42 > u128::MAX (≈ 3.4 × 10^38) → the `pow` call itself panics.
3. User calls `initTransfer` on EVM with any non-zero amount; funds are locked in the EVM bridge contract.
4. Relayer submits proof to NEAR; `fin_transfer_callback` is invoked.
5. `denormalize_amount` panics at `10_u128.pow(42)`.
6. NEAR reverts the callback; the EVM funds remain locked forever with no recovery path.

For a less extreme but still realistic case with `diff_decimals = 18`:
- Overflow threshold = `u128::MAX / 10^18 ≈ 3.4 × 10^20` in origin-chain units.
- A user transferring `3.4 × 10^20 + 1` base units (e.g., 340 whole tokens of an 18-decimal token) triggers the panic and permanently loses their funds. [1](#0-0) [6](#0-5)

### Citations

**File:** near/omni-bridge/src/lib.rs (L698-727)
```rust
    #[private]
    #[payable]
    pub fn fin_transfer_callback(
        &mut self,
        #[serializer(borsh)] storage_deposit_actions: &Vec<StorageDepositAction>,
        #[serializer(borsh)] predecessor_account_id: AccountId,
    ) -> PromiseOrValue<Nonce> {
        let Ok(ProverResult::InitTransfer(init_transfer)) = Self::decode_prover_result(0) else {
            env::panic_str(BridgeError::InvalidProofMessage.to_string().as_str())
        };
        require!(
            self.factories
                .get(&init_transfer.emitter_address.get_chain())
                == Some(init_transfer.emitter_address),
            BridgeError::UnknownFactory.as_ref()
        );

        let decimals = self
            .token_decimals
            .get(&init_transfer.token)
            .near_expect(BridgeError::TokenDecimalsNotFound);

        let destination_nonce =
            self.get_next_destination_nonce(init_transfer.recipient.get_chain());
        let transfer_message = TransferMessage {
            origin_nonce: init_transfer.origin_nonce,
            token: init_transfer.token,
            amount: Self::denormalize_amount(init_transfer.amount.0, decimals).into(),
            recipient: init_transfer.recipient,
            fee: Self::denormalize_fee(&init_transfer.fee, decimals),
```

**File:** near/omni-bridge/src/lib.rs (L770-772)
```rust
        let denormalized_amount =
            Self::denormalize_amount(fast_fin_transfer_msg.amount.0, decimals);
        let denormalized_fee = Self::denormalize_fee(&fast_fin_transfer_msg.fee, decimals);
```

**File:** near/omni-bridge/src/lib.rs (L2776-2779)
```rust
    fn denormalize_amount(amount: u128, decimals: Decimals) -> u128 {
        let diff_decimals: u32 = (decimals.origin_decimals - decimals.decimals).into();
        amount * (10_u128.pow(diff_decimals))
    }
```

**File:** near/omni-bridge/src/lib.rs (L2790-2794)
```rust
    fn denormalize_fee(fee: &Fee, decimals: Decimals) -> Fee {
        Fee {
            fee: U128(Self::denormalize_amount(fee.fee.0, decimals)),
            native_fee: fee.native_fee,
        }
```

**File:** near/omni-bridge/src/storage.rs (L132-136)
```rust
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Decimals {
    pub decimals: u8,
    pub origin_decimals: u8,
}
```

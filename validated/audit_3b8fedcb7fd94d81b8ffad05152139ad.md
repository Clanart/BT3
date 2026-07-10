### Title
Unchecked u128 Overflow in `denormalize_amount` Permanently Locks User Funds - (File: near/omni-bridge/src/lib.rs)

### Summary

`denormalize_amount` performs an unchecked `u128` multiplication when scaling token amounts from normalized (NEAR) units back to origin-chain units. For tokens whose `origin_decimals` exceeds `decimals` by a large margin — a configuration that arises naturally from the EVM bridge's `_normalizeDecimals` cap at 18 — the multiplication overflows, either panicking and permanently locking the user's EVM-side tokens, or silently wrapping to a corrupt smaller value that breaks bridge collateralization.

### Finding Description

`denormalize_amount` is defined as:

```rust
// near/omni-bridge/src/lib.rs:2776-2778
fn denormalize_amount(amount: u128, decimals: Decimals) -> u128 {
    let diff_decimals: u32 = (decimals.origin_decimals - decimals.decimals).into();
    amount * (10_u128.pow(diff_decimals))   // ← no overflow guard
}
```

`Decimals` stores two `u8` fields:

```rust
// near/omni-bridge/src/storage.rs:133-136
pub struct Decimals {
    pub decimals: u8,
    pub origin_decimals: u8,
}
```

On the EVM side, `_normalizeDecimals` hard-caps the NEAR-side decimal count at 18:

```solidity
// evm/src/omni-bridge/contracts/OmniBridge.sol:586-592
function _normalizeDecimals(uint8 decimals) internal pure returns (uint8) {
    uint8 maxAllowedDecimals = 18;
    if (decimals > maxAllowedDecimals) { return maxAllowedDecimals; }
    return decimals;
}
```

When a token with `origin_decimals = D > 18` is registered, `decimals = 18` and `diff_decimals = D − 18`. The `DeployToken` event emits both values; `bind_token_callback` stores them verbatim via `add_token`:

```rust
// near/omni-bridge/src/lib.rs:1262-1267
self.add_token(
    &deploy_token.token,
    &deploy_token.token_address,
    deploy_token.decimals,
    deploy_token.origin_decimals,
);
```

`fin_transfer_callback` then calls `denormalize_amount` with the raw EVM-event amount:

```rust
// near/omni-bridge/src/lib.rs:725
amount: Self::denormalize_amount(init_transfer.amount.0, decimals).into(),
```

`u128::MAX ≈ 3.4 × 10^38`. For `diff_decimals = 6` (e.g., a 24-decimal token), the safe ceiling is `u128::MAX / 10^6 ≈ 3.4 × 10^32` EVM units. Any amount above that overflows. For `diff_decimals = 18` (e.g., a 36-decimal token), the ceiling is `≈ 3.4 × 10^20` EVM units — for a 36-decimal token that is `≈ 3.4 × 10^−16` human-readable tokens, meaning **every non-trivial transfer overflows**.

The same unchecked multiplication is present in `denormalize_fee` (called on the same code path) and in `fast_fin_transfer`:

```rust
// near/omni-bridge/src/lib.rs:770-771
let denormalized_amount =
    Self::denormalize_amount(fast_fin_transfer_msg.amount.0, decimals);
```

### Impact Explanation

**If Rust overflow checks are enabled (panic on overflow):** `fin_transfer_callback` aborts. The transfer is never finalized on NEAR. The user's tokens are already locked or burned on EVM with no on-chain refund path — **permanent, irrecoverable loss of user funds**.

**If overflow checks are disabled (wrapping arithmetic, the Rust release-mode default for `wasm32-unknown-unknown` without explicit `overflow-checks = true`):** The stored `transfer_message.amount` is a silently corrupted, much smaller value. The user receives far fewer tokens on NEAR than they locked on EVM. The surplus remains permanently locked in the EVM vault — **bridge collateralization is broken and value is misdirected**.

Both outcomes fall within the allowed impact scope: Critical permanent freezing of user funds, or High balance/accounting corruption that breaks bridge collateralization.

### Likelihood Explanation

The `logMetadata` function on EVM is **permissionless**:

```solidity
// evm/src/omni-bridge/contracts/OmniBridge.sol:224
function logMetadata(address tokenAddress) external payable {
```

Any caller can trigger metadata logging for any ERC20 token. If that token reports `decimals() > 18`, the MPC will sign the metadata, and `deployToken` / `bind_token` will register it with `origin_decimals > 18`. Tokens with 24 decimals exist in production (e.g., certain DeFi tokens). Tokens with 36 decimals are less common but deployable by anyone. Once such a token is registered, any user bridging a sufficiently large amount (or, for very high `diff_decimals`, any amount at all) triggers the overflow.

### Recommendation

Replace the bare multiplication with a checked variant and revert on overflow:

```rust
fn denormalize_amount(amount: u128, decimals: Decimals) -> u128 {
    let diff_decimals: u32 = (decimals.origin_decimals - decimals.decimals).into();
    let multiplier = 10_u128
        .checked_pow(diff_decimals)
        .unwrap_or_else(|| env::panic_str("ERR_DECIMAL_OVERFLOW"));
    amount
        .checked_mul(multiplier)
        .unwrap_or_else(|| env::panic_str("ERR_AMOUNT_OVERFLOW"))
}
```

Additionally, enforce `origin_decimals >= decimals` at token registration time in `add_token`, and consider capping `diff_decimals` to a safe maximum (e.g., 18) to bound the multiplier.

### Proof of Concept

1. Deploy an ERC20 token `T` on EVM with `decimals() = 36`.
2. Call `logMetadata(T)` on the EVM `OmniBridge`. The MPC signs the metadata with `decimals = 36`.
3. Call `deployToken` (or `bind_token` on NEAR with the proof). NEAR stores `Decimals { decimals: 18, origin_decimals: 36 }` → `diff_decimals = 18`.
4. Mint `10^20` units of `T` (= `10^-16` human-readable tokens) and call `initTransfer` on EVM. Tokens are locked.
5. Relayer submits proof to NEAR `fin_transfer`. `fin_transfer_callback` calls:
   ```
   denormalize_amount(10^20, {decimals:18, origin_decimals:36})
   = 10^20 * 10^18 = 10^38 < u128::MAX  // just under — succeeds
   ```
6. Repeat with `amount = 4 × 10^20`:
   ```
   4×10^20 * 10^18 = 4×10^38 > u128::MAX (~3.4×10^38)  // overflows
   ```
   With overflow checks enabled: `fin_transfer_callback` panics; user's `4×10^20` EVM units are permanently locked with no NEAR-side claim possible.
   With wrapping: stored amount = `(4×10^38) mod 2^128 ≈ 6.5×10^37`; user receives `6.5×10^37 / 10^18 = 6.5×10^19` NEAR-side units instead of `4×10^20`; the difference is permanently locked in the EVM vault. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** near/omni-bridge/src/lib.rs (L722-727)
```rust
        let transfer_message = TransferMessage {
            origin_nonce: init_transfer.origin_nonce,
            token: init_transfer.token,
            amount: Self::denormalize_amount(init_transfer.amount.0, decimals).into(),
            recipient: init_transfer.recipient,
            fee: Self::denormalize_fee(&init_transfer.fee, decimals),
```

**File:** near/omni-bridge/src/lib.rs (L770-771)
```rust
        let denormalized_amount =
            Self::denormalize_amount(fast_fin_transfer_msg.amount.0, decimals);
```

**File:** near/omni-bridge/src/lib.rs (L1262-1267)
```rust
        self.add_token(
            &deploy_token.token,
            &deploy_token.token_address,
            deploy_token.decimals,
            deploy_token.origin_decimals,
        );
```

**File:** near/omni-bridge/src/lib.rs (L2776-2778)
```rust
    fn denormalize_amount(amount: u128, decimals: Decimals) -> u128 {
        let diff_decimals: u32 = (decimals.origin_decimals - decimals.decimals).into();
        amount * (10_u128.pow(diff_decimals))
```

**File:** near/omni-bridge/src/storage.rs (L132-136)
```rust
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Decimals {
    pub decimals: u8,
    pub origin_decimals: u8,
}
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L224-232)
```text
    function logMetadata(address tokenAddress) external payable {
        string memory name = IERC20Metadata(tokenAddress).name();
        string memory symbol = IERC20Metadata(tokenAddress).symbol();
        uint8 decimals = IERC20Metadata(tokenAddress).decimals();

        logMetadataExtension(tokenAddress, name, symbol, decimals);

        emit BridgeTypes.LogMetadata(tokenAddress, name, symbol, decimals);
    }
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L586-592)
```text
    function _normalizeDecimals(uint8 decimals) internal pure returns (uint8) {
        uint8 maxAllowedDecimals = 18;
        if (decimals > maxAllowedDecimals) {
            return maxAllowedDecimals;
        }
        return decimals;
    }
```

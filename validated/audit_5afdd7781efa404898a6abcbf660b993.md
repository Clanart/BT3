### Title
`init_transfer` accepts sub-unit net amounts that `sign_transfer` permanently rejects, irrecoverably locking user funds — (File: `near/omni-bridge/src/lib.rs`)

---

### Summary

`init_transfer` validates the fee using raw NEAR-side token units (`fee.fee < amount`), while `sign_transfer` validates the net transfer amount using destination-chain normalized units (`normalize_amount(amount - fee) > 0`). For tokens whose NEAR-side precision exceeds the destination chain's precision (e.g., 24 vs 18 decimals), a user can initiate a transfer where `amount - fee` is positive in raw units but truncates to zero after normalization. The transfer is accepted and tokens are locked, but every subsequent call to `sign_transfer` permanently reverts with `ERR_INVALID_AMOUNT_TO_TRANSFER`. No cancel or recovery path exists.

---

### Finding Description

**`init_transfer` fee check (raw units):**

```rust
require!(
    transfer_message.fee.fee < transfer_message.amount,
    BridgeError::InvalidFee.as_ref()
);
``` [1](#0-0) 

This check only requires `fee < amount` in raw NEAR-side token units. It does not verify that the net amount (`amount - fee`) is representable on the destination chain.

**`sign_transfer` check (normalized units):**

```rust
let amount_to_transfer = Self::normalize_amount(
    transfer_message.amount_without_fee().near_expect(BridgeError::InvalidFee),
    decimals,
);
require!(
    amount_to_transfer > 0,
    BridgeError::InvalidAmountToTransfer.as_ref()
);
``` [2](#0-1) 

**`normalize_amount` uses floor division:**

```rust
fn normalize_amount(amount: u128, decimals: Decimals) -> u128 {
    let diff_decimals: u32 = (decimals.origin_decimals - decimals.decimals).into();
    amount / (10_u128.pow(diff_decimals))
}
``` [3](#0-2) 

**`amount_without_fee` is a simple subtraction:**

```rust
pub fn amount_without_fee(&self) -> Option<u128> {
    self.amount.0.checked_sub(self.fee.fee.0)
}
``` [4](#0-3) 

**Tokens are locked immediately in `init_transfer_internal`:**

```rust
self.burn_tokens_if_needed(token_id.clone(), transfer_message.amount);
self.lock_tokens_if_needed(
    transfer_message.get_destination_chain(),
    &token_id,
    transfer_message.amount.0,
);
``` [5](#0-4) 

**`update_transfer_fee` cannot rescue the transfer** — it only allows increasing the fee (not decreasing it), and its own check also uses raw units:

```rust
require!(
    fee.fee >= current_fee.fee && fee.fee < transfer.message.amount,
    BridgeError::InvalidFee.as_ref()
);
``` [6](#0-5) 

Increasing the fee makes `amount - fee` even smaller, which still normalizes to zero. There is no public cancel or refund function for pending transfers.

---

### Impact Explanation

**Impact: Critical — Permanent, irrecoverable lock of user funds.**

A user who initiates a transfer where `amount - fee < 10^(origin_decimals - destination_decimals)` will have their tokens locked in the bridge with no recovery path:

- `init_transfer` accepts the transfer and locks/burns the tokens.
- Every call to `sign_transfer` reverts with `ERR_INVALID_AMOUNT_TO_TRANSFER`.
- `update_transfer_fee` can only increase the fee, making the net amount smaller.
- No cancel/refund function exists for pending transfers.
- The tokens are permanently frozen in the bridge contract.

---

### Likelihood Explanation

**Likelihood: Medium.**

This affects any token pair where `origin_decimals > decimals` (e.g., a NEAR-native token with 24 decimals bridged to EVM with 18 decimals, giving a divisor of 10^6). The user must send a transfer where the net amount (`amount - fee`) is between 1 and `10^(origin_decimals - decimals) - 1` raw units. This is reachable by any unprivileged user calling `ft_transfer_call` → `ft_on_transfer` → `init_transfer`. It can happen accidentally (small transfer with a high fee) or be triggered by a griefing actor who convinces a user to set a specific fee.

---

### Recommendation

Add a normalized-amount check inside `init_transfer` (or `init_transfer_internal`) before locking tokens:

```rust
let token_address = self.get_token_address(
    init_transfer_msg.get_destination_chain(),
    self.get_token_id(&OmniAddress::Near(token_id.clone())),
);
if let Some(addr) = token_address {
    if let Some(decimals) = self.token_decimals.get(&addr) {
        let net = transfer_message.amount_without_fee()
            .near_expect(BridgeError::InvalidFee);
        require!(
            Self::normalize_amount(net, decimals) > 0,
            BridgeError::InvalidAmountToTransfer.as_ref()
        );
    }
}
```

This mirrors the check already present in `sign_transfer` and ensures that any transfer accepted by `init_transfer` can always be finalized.

---

### Proof of Concept

**Setup**: Token with `origin_decimals = 24`, `decimals = 18` (divisor = 10^6). User has 10 raw units of the token.

1. User calls `ft_transfer_call` with `amount = 5`, `fee = 4`, `recipient = <EVM address>`.
2. `init_transfer` checks `4 < 5` → passes.
3. `init_transfer_internal` locks 5 raw units in the bridge.
4. Relayer calls `sign_transfer` for this transfer.
5. `normalize_amount(5 - 4) = normalize_amount(1) = 1 / 10^6 = 0`.
6. `require!(0 > 0, ...)` → panics with `ERR_INVALID_AMOUNT_TO_TRANSFER`.
7. User tries `update_transfer_fee` to set `fee = 3` → rejected with `ERR_INVALID_FEE` (fee cannot decrease).
8. User tries `update_transfer_fee` to set `fee = 4` (same) → `normalize_amount(1) = 0` still fails in `sign_transfer`.
9. The 5 raw units are permanently locked. No recovery path exists. [1](#0-0) [2](#0-1) [3](#0-2) [5](#0-4) [6](#0-5)

### Citations

**File:** near/omni-bridge/src/lib.rs (L399-402)
```rust
                require!(
                    fee.fee >= current_fee.fee && fee.fee < transfer.message.amount,
                    BridgeError::InvalidFee.as_ref()
                );
```

**File:** near/omni-bridge/src/lib.rs (L475-485)
```rust
        let amount_to_transfer = Self::normalize_amount(
            transfer_message
                .amount_without_fee()
                .near_expect(BridgeError::InvalidFee),
            decimals,
        );

        require!(
            amount_to_transfer > 0,
            BridgeError::InvalidAmountToTransfer.as_ref()
        );
```

**File:** near/omni-bridge/src/lib.rs (L554-557)
```rust
        require!(
            transfer_message.fee.fee < transfer_message.amount,
            BridgeError::InvalidFee.as_ref()
        );
```

**File:** near/omni-bridge/src/lib.rs (L1851-1857)
```rust
            self.burn_tokens_if_needed(token_id.clone(), transfer_message.amount);

            self.lock_tokens_if_needed(
                transfer_message.get_destination_chain(),
                &token_id,
                transfer_message.amount.0,
            );
```

**File:** near/omni-bridge/src/lib.rs (L2784-2787)
```rust
    fn normalize_amount(amount: u128, decimals: Decimals) -> u128 {
        let diff_decimals: u32 = (decimals.origin_decimals - decimals.decimals).into();
        amount / (10_u128.pow(diff_decimals))
    }
```

**File:** near/omni-types/src/lib.rs (L593-595)
```rust
    pub fn amount_without_fee(&self) -> Option<u128> {
        self.amount.0.checked_sub(self.fee.fee.0)
    }
```

### Title
`u128`-to-`u64` Silent Overflow in Solana `finalize_transfer` Permanently Locks Funds for Large NEAR-to-Solana Transfers — (`solana/programs/bridge_token_factory/src/instructions/user/finalize_transfer.rs`)

---

### Summary

The Solana bridge program stores the incoming transfer amount as `u128` in `FinalizeTransferPayload`, but SPL token operations (`transfer_checked` / `mint_to`) require a `u64`. The cast `data.amount.try_into()` fails with `AmountOverflow` if the NEAR-normalized amount exceeds `u64::MAX`. Because the NEAR side never validates that the normalized amount fits in `u64` before locking or burning the user's tokens, a transfer whose normalized amount exceeds `u64::MAX` is permanently irrecoverable.

---

### Finding Description

**NEAR side — no upper-bound check before locking funds**

`init_transfer` on NEAR accepts any `U128` amount and stores it in `pending_transfers`. The normalization step happens later, inside `sign_transfer`:

```rust
// near/omni-bridge/src/lib.rs
let amount_to_transfer = Self::normalize_amount(
    transfer_message.amount_without_fee()...,
    decimals,
);
``` [1](#0-0) 

`normalize_amount` divides by `10^(origin_decimals − decimals)` and returns a `u128`:

```rust
fn normalize_amount(amount: u128, decimals: Decimals) -> u128 {
    let diff_decimals: u32 = (decimals.origin_decimals - decimals.decimals).into();
    amount / (10_u128.pow(diff_decimals))
}
``` [2](#0-1) 

The result is placed in `TransferMessagePayload.amount: U128` and signed. No check ensures the result fits in `u64`.

**Solana side — hard `u64` cast on the normalized amount**

`FinalizeTransferPayload.amount` is `u128`: [3](#0-2) 

Both `finalize_transfer` and `finalize_transfer_sol` cast it to `u64`:

```rust
// finalize_transfer.rs  line 114
data.amount.try_into().map_err(|_| error!(ErrorCode::AmountOverflow))?
// finalize_transfer_sol.rs  line 88
data.amount.try_into().map_err(|_| error!(ErrorCode::AmountOverflow))?
``` [4](#0-3) [5](#0-4) 

If `data.amount > u64::MAX`, the instruction reverts. The Wormhole acknowledgement message is never posted, so the NEAR bridge never learns of the failure. The funds remain locked in `pending_transfers` on NEAR with no refund path.

---

### Impact Explanation

**Critical — Permanent, irrecoverable fund lock.**

A NEAR-native token with 24 decimals bridged to a Solana token with 9 decimals has a normalization factor of 10^15. `u64::MAX ≈ 1.844 × 10^19`. Any single transfer whose normalized amount exceeds this threshold causes:

- For NEAR-native tokens: the user's tokens are locked in the NEAR bridge vault forever.
- For Solana-native tokens (bridged to NEAR and back): the tokens are burned on NEAR and can never be minted on Solana.

**Concrete example:**

| Parameter | Value |
|---|---|
| NEAR token decimals | 24 |
| Solana token decimals | 9 |
| Normalization factor | 10^15 |
| Transfer amount | 20 billion tokens = 2 × 10^34 smallest units |
| Normalized amount | 2 × 10^19 |
| `u64::MAX` | ≈ 1.844 × 10^19 |
| Result | `AmountOverflow` → permanent lock |

20 billion tokens is a realistic supply for many fungible tokens (meme tokens, governance tokens, etc.).

---

### Likelihood Explanation

**Medium.** The condition requires:
1. A token whose NEAR-side decimal precision (e.g., 24) is significantly higher than its Solana-side precision (e.g., 9).
2. A single transfer whose normalized amount exceeds `u64::MAX` (~18.4 billion in 9-decimal units).

Both conditions are realistic. NEAR's native precision is 24 decimals. Tokens with billions-of-units supplies are common. No privileged role is required — any unprivileged token holder can trigger this by initiating a large transfer.

---

### Recommendation

1. **On the NEAR side**, inside `sign_transfer`, after computing `amount_to_transfer`, validate that it fits in `u64` when the destination chain is Solana:
   ```rust
   if destination_chain == ChainKind::Sol {
       require!(
           u64::try_from(amount_to_transfer).is_ok(),
           BridgeError::AmountExceedsDestinationCapacity
       );
   }
   ```
   This prevents the transfer from being signed and allows the user to reclaim their funds before they are irrecoverably locked.

2. Alternatively, enforce the `u64` upper bound at `init_transfer` time when the destination is Solana, so the user's tokens are never locked in the first place.

---

### Proof of Concept

1. Deploy a NEAR-native token with 24 decimals and a supply of 10^35 smallest units (100 trillion tokens).
2. Register the token in the NEAR bridge with `origin_decimals = 24`, `decimals = 9` for the Solana mapping.
3. Call `ft_transfer_call` on NEAR with `amount = 2 × 10^34` (20 billion tokens), recipient on Solana.
4. NEAR bridge stores the transfer; `pending_transfers` now holds `amount = U128(2 × 10^34)`.
5. Relayer calls `sign_transfer`; `normalize_amount(2e34, {24, 9}) = 2e19`; MPC signs payload with `amount = U128(2e19)`.
6. Relayer submits signed payload to Solana `finalize_transfer`.
7. `(2e19u128).try_into::<u64>()` fails → `ErrorCode::AmountOverflow` → instruction reverts.
8. No Wormhole message is posted; NEAR bridge never receives a `FinTransfer` confirmation.
9. The 20 billion tokens remain locked in the NEAR bridge vault with no refund mechanism.

### Citations

**File:** near/omni-bridge/src/lib.rs (L475-480)
```rust
        let amount_to_transfer = Self::normalize_amount(
            transfer_message
                .amount_without_fee()
                .near_expect(BridgeError::InvalidFee),
            decimals,
        );
```

**File:** near/omni-bridge/src/lib.rs (L2784-2787)
```rust
    fn normalize_amount(amount: u128, decimals: Decimals) -> u128 {
        let diff_decimals: u32 = (decimals.origin_decimals - decimals.decimals).into();
        amount / (10_u128.pow(diff_decimals))
    }
```

**File:** solana/programs/bridge_token_factory/src/state/message/finalize_transfer.rs (L11-16)
```rust
pub struct FinalizeTransferPayload {
    pub destination_nonce: u64,
    pub transfer_id: TransferId,
    pub amount: u128,
    pub fee_recipient: Option<String>,
}
```

**File:** solana/programs/bridge_token_factory/src/instructions/user/finalize_transfer.rs (L114-114)
```rust
                data.amount.try_into().map_err(|_| error!(ErrorCode::AmountOverflow))?,
```

**File:** solana/programs/bridge_token_factory/src/instructions/user/finalize_transfer_sol.rs (L88-88)
```rust
            data.amount.try_into().map_err(|_| error!(ErrorCode::AmountOverflow))?,
```

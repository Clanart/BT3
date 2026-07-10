### Title
Unchecked u128 Multiplication Overflow in `denormalize_amount` Causes Permanent Fund Freeze or Silent Accounting Corruption on `fin_transfer` - (File: near/omni-bridge/src/lib.rs)

### Summary

`denormalize_amount` performs a bare `u128` multiplication without overflow protection. When `origin_decimals − decimals ≥ 39`, the factor `10_u128.pow(diff_decimals)` exceeds `u128::MAX`. With `overflow-checks = true` (debug / explicit profile flag) the NEAR runtime panics and every `fin_transfer_callback` for that token reverts, permanently freezing user funds locked on the source chain. With wrapping arithmetic (release default) the stored amount silently wraps to a tiny value, minting far fewer tokens than owed — an accounting corruption that breaks bridge collateralization.

### Finding Description

```rust
// near/omni-bridge/src/lib.rs  line 2776-2779
fn denormalize_amount(amount: u128, decimals: Decimals) -> u128 {
    let diff_decimals: u32 = (decimals.origin_decimals - decimals.decimals).into();
    amount * (10_u128.pow(diff_decimals))   // ← unchecked multiplication
}
```

`Decimals` is stored per token address at registration time:

```rust
// line 2724-2735
self.token_decimals.insert(
    token_address,
    &Decimals { decimals, origin_decimals },
)
```

`denormalize_amount` is called unconditionally in three critical paths:

1. **`fin_transfer_callback`** (line 725) — converts the proof amount to NEAR-native units before minting/transferring.
2. **`fast_fin_transfer`** (line 771) — same conversion for fast-path transfers.
3. **`claim_fee_callback`** (line 1122) — converts the settled amount to compute the fee.

`u128::MAX ≈ 3.4 × 10^38`, so `10_u128.pow(39)` already overflows. Any token registered with `origin_decimals − decimals ≥ 39` (e.g., NEAR-side decimals = 57, EVM-side decimals = 18 → diff = 39) triggers the overflow on every finalization attempt.

### Impact Explanation

**Overflow-checks enabled (panic path):**  
`fin_transfer_callback` panics → the NEAR transaction reverts → the transfer is never finalized → the user's tokens remain locked on the source chain with no recovery path. This is a permanent, irrecoverable freeze of user funds.

**Wrapping arithmetic (release default):**  
`amount * 10^39` wraps to a small value. The bridge mints or transfers a negligible amount to the recipient while the full source-chain balance remains locked. This silently breaks bridge collateralization and misdirects value.

### Likelihood Explanation

The attack path is fully public:

1. **Deploy a NEAR NEP-141 token with `decimals = 57`** — any account can do this.
2. **Call NEAR `log_metadata`** (public) — the MPC signs the metadata including `decimals = 57`.
3. **Call EVM `deployToken`** (public, `whenNotPaused`) with the MPC signature — the EVM bridge deploys a bridge token capped at 18 decimals and emits `DeployToken(addr, token, name, symbol, 18, 57)`.
4. **Call NEAR `bind_token`** (public, `#[pause(except(roles(Role::DAO)))]`) with a proof of the EVM event — NEAR stores `Decimals { decimals: 18, origin_decimals: 57 }`.
5. **Any transfer of this token from EVM to NEAR** causes a trusted relayer's `fin_transfer
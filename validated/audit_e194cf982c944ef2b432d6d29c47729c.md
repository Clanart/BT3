### Title
Unsafe `u128`→`u64` Downcast in Solana `finalize_transfer` Permanently Locks NEAR-Side Funds - (File: `solana/programs/bridge_token_factory/src/instructions/user/finalize_transfer.rs`)

### Summary
When finalizing a NEAR→Solana transfer, the signed payload amount (`u128`) is cast to `u64` for the SPL token operation. If the normalized amount exceeds `u64::MAX` (~18.4 × 10¹⁸), the cast fails with `AmountOverflow`. Due to Solana's atomic transaction model the destination nonce is never consumed, so the relayer can retry indefinitely — but the funds were already burned/locked on NEAR with no on-chain refund path, permanently freezing them.

### Finding Description
In `FinalizeTransfer::process`, the nonce is marked used first, then the amount is downcast:

```rust
// finalize_transfer.rs lines 91-135
UsedNonces::use_nonce(data.destination_nonce, ...)?;   // nonce consumed in same tx

transfer_checked(
    ...,
    data.amount.try_into().map_err(|_| error!(ErrorCode::AmountOverflow))?,  // line 114
    self.mint.decimals,
)?;
```

`data.amount` is typed `u128` in `FinalizeTransferPayload` (matching NEAR's native amount width), but SPL token operations require `u64`. The `try_into()` call returns `ErrorCode::AmountOverflow` if `data.amount > u64::MAX`.

Because Solana transactions are atomic, the `UsedNonces::use_nonce` write is also rolled back on failure, so the nonce is not consumed and the relayer can retry. However, the source-side funds were already burned or locked on NEAR inside `ft_on_transfer → init_transfer → init_transfer_internal`, and the NEAR contract stores the pending transfer in `pending_transfers` with no `cancel_transfer` or user-callable refund function. Every retry of `finalize_transfer` will hit the same overflow and revert, leaving the funds irrecoverably locked.

The analogous pattern in `init_transfer.rs` (line 100) and `finalize_transfer_sol.rs` (line 88) also cast `u128` amounts to `u64`, but those paths operate on Solana-side funds that are rolled back on failure — only the NEAR→Solana finalization path creates the permanent lock.

The NEAR bridge's `normalize_amount` function (called in `sign_transfer`) converts the NEAR-side amount into the destination chain's decimal precision and stores the result as `U128` in the signed `TransferMessagePayload`. If the destination Solana token has equal or greater decimal precision than the NEAR token, or if the raw transfer amount is large, the normalized value can exceed `u64::MAX`.

### Impact Explanation
A user who initiates a NEAR→Solana transfer where the normalized amount exceeds `u64::MAX` will have their tokens permanently locked in the NEAR bridge contract. There is no `cancel_transfer`, `refund`, or DAO-callable recovery function visible in the contract. The `pending_transfers` entry persists indefinitely. This matches **Critical — Permanent freezing, irrecoverable lock of user funds in bridge flows**.

### Likelihood Explanation
The overflow threshold is `u64::MAX ≈ 1.84 × 10¹⁹` in the smallest token unit. For a Solana token with 9 decimals this corresponds to ~18.4 billion whole tokens — a large but not impossible amount for high-supply tokens. For tokens where the NEAR-side representation uses more decimal places than the Solana-side (e.g., NEAR token with 18 decimals mapped to a Solana token also with 18 decimals), a transfer of as few as ~19 whole tokens would overflow. The bridge supports arbitrary token pairs via `bind_token`/`deploy_token`, so a token deployer or the protocol itself can register a pairing where this condition is reachable by ordinary users.

### Recommendation
1. In `normalize_amount` (NEAR side), add an explicit check that the result fits in `u64` before the MPC signs the payload, and reject the `sign_transfer` call with a clear error if it does not.
2. Alternatively, add a DAO-callable `cancel_pending_transfer` function on NEAR that refunds the sender when a transfer has been pending beyond a timeout, providing a recovery path for stuck transfers.

### Proof of Concept
1. Register a NEAR↔Solana token pair where the Solana token has 18 decimals.
2. User calls `ft_on_transfer` on NEAR with `InitTransfer` for 20 whole tokens (20 × 10¹⁸ = 2 × 10¹⁹ in smallest units). Funds are locked on NEAR.
3. `sign_transfer` is called; `normalize_amount` produces `2 × 10¹⁹` (same decimals on both sides). MPC signs the payload with `amount = 2 × 10¹⁹`.
4. Relayer calls Solana `finalize_transfer` with the signed payload.
5. `data.amount.try_into::<u64>()` fails because `2 × 10¹⁹ > u64::MAX (≈ 1.84 × 10¹⁹)`.
6. Instruction reverts; nonce not consumed; relayer retries — always fails.
7. Funds remain locked in NEAR `pending_transfers` with no recovery path. [1](#0-0) [2](#0-1) 
<cite repo="Annirich/omni

### Citations

**File:** solana/programs/bridge_token_factory/src/instructions/user/finalize_transfer.rs (L91-116)
```rust
        UsedNonces::use_nonce(
            data.destination_nonce,
            &self.used_nonces,
            &mut self.config,
            self.authority.to_account_info(),
            self.common.payer.to_account_info(),
            &Rent::get()?,
            self.system_program.to_account_info(),
        )?;

        if let Some(vault) = &self.vault {
            // Native version. We have a proof of token registration by vault existence
            transfer_checked(
                CpiContext::new_with_signer(
                    self.token_program.to_account_info(),
                    TransferChecked {
                        from: vault.to_account_info(),
                        to: self.token_account.to_account_info(),
                        authority: self.authority.to_account_info(),
                        mint: self.mint.to_account_info(),
                    },
                    &[&[AUTHORITY_SEED, &[self.config.bumps.authority]]],
                ),
                data.amount.try_into().map_err(|_| error!(ErrorCode::AmountOverflow))?,
                self.mint.decimals,
            )?;
```

**File:** solana/programs/bridge_token_factory/src/instructions/user/finalize_transfer.rs (L124-135)
```rust
            mint_to(
                CpiContext::new_with_signer(
                    self.token_program.to_account_info(),
                    MintTo {
                        mint: self.mint.to_account_info(),
                        to: self.token_account.to_account_info(),
                        authority: self.authority.to_account_info(),
                    },
                    &[&[AUTHORITY_SEED, &[self.config.bumps.authority]]],
                ),
                data.amount.try_into().map_err(|_| error!(ErrorCode::AmountOverflow))?,
            )?;
```

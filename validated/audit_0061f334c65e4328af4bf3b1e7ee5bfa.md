### Title
Token-2022 `TransferFeeConfig` Applied to Vault `transfer_checked` But Not to `burn`/`mint_to`, Causing Undercollateralization on Solana → NEAR Transfers - (File: `solana/programs/bridge_token_factory/src/instructions/user/init_transfer.rs`)

---

### Summary

The Solana bridge program supports both native tokens (locked in a vault via `transfer_checked`) and bridged tokens (burned/minted directly). When a native Solana token has the Token-2022 `TransferFeeConfig` extension enabled, calling `transfer_checked` silently deducts a protocol-level transfer fee from the amount received by the vault. However, the Wormhole message posted to NEAR always contains the full user-supplied `payload.amount` — not the post-fee amount actually deposited. NEAR then credits the user with more tokens than are physically locked in the vault, permanently breaking bridge collateralization. The `burn` path (bridged tokens) is unaffected because `burn` does not apply transfer fees.

---

### Finding Description

In `init_transfer.rs`, the `process` function handles two cases based on whether a vault PDA exists:

**Native token path (vault exists):** [1](#0-0) 

`transfer_checked` is a Token-2022-aware CPI. When the mint has a `TransferFeeConfig` extension, the SPL Token-2022 runtime deducts a fee from the transferred amount. If the fee rate is `r` basis points, the vault receives `payload.amount - floor(payload.amount * r / 10000)` tokens, not `payload.amount`.

**Bridged token path (no vault):** [2](#0-1) 

`burn` does not apply `TransferFeeConfig` fees. The full `payload.amount` is burned.

**Wormhole message posted in both cases:** [3](#0-2) 

The message serialized for NEAR always encodes `payload.amount` — the pre-fee value — regardless of which path was taken. For the native vault path, this overstates the amount actually locked.

The same asymmetry exists in `finalize_transfer.rs` in the reverse direction: `transfer_checked` from vault to recipient deducts a fee, but the `FinalizeTransferResponse` posted back to NEAR encodes the full `data.amount`: [4](#0-3) [5](#0-4) 

---

### Impact Explanation

**Solana → NEAR direction (`init_transfer`):**

- User calls `init_transfer` with `payload.amount = X` for a native Token-2022 token with a 1% transfer fee.
- Vault receives `X * 0.99` tokens (fee deducted by Token-2022 runtime).
- Wormhole message says `amount = X`.
- NEAR credits the user with `X` tokens.
- Bridge vault is undercollateralized by `X * 0.01` per transfer.
- Accumulated over many transfers, the vault cannot cover all redemptions. Users who bridge back last receive fewer tokens than expected, or the vault is fully drained before all users are served.

This is a **balance/accounting corruption that breaks bridge collateralization** — a direct match to the allowed High impact class.

**NEAR → Solana direction (`finalize_transfer`):**

- NEAR burns `X` tokens and sends a signed payload with `amount = X`.
- `transfer_checked` from vault deducts fee; recipient receives `X * 0.99`.
- Confirmation message to NEAR says `amount = X`.
- NEAR records `X` as settled, but recipient only got `X * 0.99`.
- Vault is drained by `X` per finalization but only `X * 0.99` reaches the user; the fee goes to the Token-2022 fee collector, not the bridge.

---

### Likelihood Explanation

- The Solana bridge explicitly supports Token-2022 via `token_interface` and `token_2022` imports throughout the codebase.
- `SECURITY.md` acknowledges that "Token-2022 tokens with transfer hooks are not supported" but makes no mention of `TransferFeeConfig` — meaning tokens with transfer fees are not excluded by design.
- Any unprivileged user can register a native Solana token with `TransferFeeConfig` via `log_metadata` (which is permissionless per `SECURITY.md`).
- Once registered, any bridge user calling `init_transfer` with such a token triggers the accounting discrepancy.
- No privileged access, key compromise, or colluding validators are required.

---

### Recommendation

In `init_transfer.rs`, after calling `transfer_checked` for the native vault path, read back the actual amount received by the vault (i.e., compute the post-fee amount using the mint's `TransferFeeConfig` extension) and use that post-fee amount in the Wormhole message, not `payload.amount`. Specifically:

1. Before posting the Wormhole message, check whether the mint has a `TransferFeeConfig` extension.
2. If it does, compute `actual_amount = payload.amount - transfer_fee` (using `calculate_epoch_fee` from the extension).
3. Serialize `actual_amount` into the NEAR-bound message instead of `payload.amount`.
4. Apply the same correction in `finalize_transfer.rs` for the vault unlock path: compute the post-fee amount and use it in `FinalizeTransferResponse`.

Alternatively, reject registration of Token-2022 tokens with `TransferFeeConfig` in `log_metadata` to prevent such tokens from entering the bridge.

---

### Proof of Concept

1. Deploy a Token-2022 mint on Solana with `TransferFeeConfig` set to 100 basis points (1%).
2. Call `log_metadata` to register the token with the bridge (permissionless).
3. Call `init_transfer` with `payload.amount = 1_000_000` (1M tokens).
4. Token-2022 runtime deducts 10,000 tokens as fee; vault receives 990,000 tokens.
5. Wormhole message encodes `amount = 1_000_000`.
6. NEAR processes the VAA and credits the user with 1,000,000 tokens on NEAR.
7. Bridge vault holds only 990,000 tokens but NEAR has issued 1,000,000 — a 10,000-token deficit per transfer.
8. Repeat 100 times: vault holds 99,000,000 tokens but NEAR has issued 100,000,000 — the last ~1% of users cannot redeem.

### Citations

**File:** solana/programs/bridge_token_factory/src/instructions/user/init_transfer.rs (L88-102)
```rust
        if let Some(vault) = &self.vault {
            // Native version. We have a proof of token registration by vault existence
            transfer_checked(
                CpiContext::new(
                    self.token_program.to_account_info(),
                    TransferChecked {
                        from: self.from.to_account_info(),
                        to: vault.to_account_info(),
                        authority: self.user.to_account_info(),
                        mint: self.mint.to_account_info(),
                    },
                ),
                payload.amount.try_into().map_err(|_| error!(ErrorCode::InvalidArgs))?,
                self.mint.decimals,
            )?;
```

**File:** solana/programs/bridge_token_factory/src/instructions/user/init_transfer.rs (L103-121)
```rust
        } else {
            // Bridged version. May be a fake token with our authority set but it will be ignored on the near side
            require!(
                self.mint.mint_authority.contains(self.authority.key),
                ErrorCode::InvalidBridgedToken
            );

            burn(
                CpiContext::new(
                    self.token_program.to_account_info(),
                    Burn {
                        mint: self.mint.to_account_info(),
                        from: self.from.to_account_info(),
                        authority: self.user.to_account_info(),
                    },
                ),
                payload.amount.try_into().map_err(|_| error!(ErrorCode::InvalidArgs))?,
            )?;
        }
```

**File:** solana/programs/bridge_token_factory/src/instructions/user/init_transfer.rs (L123-128)
```rust
        self.common.post_message(payload.serialize_for_near((
            self.common.sequence.sequence,
            self.user.key(),
            self.mint.key(),
        ))?)?;

```

**File:** solana/programs/bridge_token_factory/src/instructions/user/finalize_transfer.rs (L101-116)
```rust
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

**File:** solana/programs/bridge_token_factory/src/instructions/user/finalize_transfer.rs (L138-144)
```rust
        let payload = FinalizeTransferResponse {
            token: self.mint.key(),
            amount: data.amount,
            fee_recipient: data.fee_recipient.unwrap_or_default(),
            transfer_id: data.transfer_id,
        }
        .serialize_for_near(())?;
```

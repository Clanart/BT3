### Title
Token-2022 Transfer Fee Extension Causes Vault Undercollateralization in `init_transfer` — (`solana/programs/bridge_token_factory/src/instructions/user/init_transfer.rs`)

### Summary
The Solana bridge's `init_transfer` instruction uses `transfer_checked` to lock native Token-2022 tokens into the vault, then posts `payload.amount` verbatim in the Wormhole message to NEAR. When the token mint has the Token-2022 **transfer fee extension** enabled, `transfer_checked` withholds a fee from the destination, so the vault receives `amount − withheld_fee` while the Wormhole message (and therefore the NEAR side) records the full `amount`. The vault is permanently undercollateralized by the withheld fee for every such deposit, and subsequent `finalize_transfer` unlocks for the full credited amount will fail or drain tokens belonging to other depositors.

### Finding Description

The Solana bridge explicitly supports Token-2022 (`anchor_spl::token_2022`, `TokenInterface`) and the `log_metadata` instruction is permissionless — anyone can register any SPL or Token-2022 mint.

In `init_transfer.rs`, when a vault exists (native token path), the code calls:

```rust
transfer_checked(
    CpiContext::new(..., TransferChecked {
        from: self.from.to_account_info(),
        to: vault.to_account_info(),
        authority: self.user.to_account_info(),
        mint: self.mint.to_account_info(),
    }),
    payload.amount.try_into()...,
    self.mint.decimals,
)?;
``` [1](#0-0) 

Immediately after, the full `payload.amount` is serialized into the Wormhole message without any adjustment:

```rust
self.common.post_message(payload.serialize_for_near((
    self.common.sequence.sequence,
    self.user.key(),
    self.mint.key(),
))?)?;
``` [2](#0-1) 

The `InitTransferPayload` serialized to NEAR contains `self.amount` — the caller-supplied value, not the vault's actual received balance: [3](#0-2) [4](#0-3) 

For a Token-2022 mint with the **transfer fee extension**, `transfer_checked(X)` credits the destination with `X − withheld_fee` and stores `withheld_fee` in the vault account's `withheld_amount` field (inaccessible to normal transfers; only harvestable by the fee authority). The vault's spendable balance is therefore `X − withheld_fee`, while NEAR is told `X` was locked.

The Solana SECURITY.md acknowledges that **transfer hooks** are unsupported, but says nothing about the transfer fee extension, and the bridge is documented as supporting Token-2022: [5](#0-4) [6](#0-5) 

The EVM SECURITY.md's "fee-on-transfer tokens not supported" note applies only to EVM and is not replicated for the Solana side: [7](#0-6) 

### Impact Explanation

Every `init_transfer` call for a Token-2022 token with transfer fee extension creates a deficit: the vault holds `amount − fee` but NEAR credits `amount`. Over time (or in a single large transfer) the vault becomes undercollateralized. When users later bridge back (NEAR → Solana `finalize_transfer`), the unlock CPI from the vault will either:

1. **Fail outright** if the vault balance is insufficient for the requested unlock amount, permanently freezing the user's funds on NEAR with no recourse.
2. **Drain tokens belonging to other depositors** if the vault still has a residual balance from other users, breaking collateralization for the entire pool of that token.

This matches the allowed impact: **High — balance/accounting corruption that breaks bridge collateralization**.

### Likelihood Explanation

- `log_metadata` is permissionless; any Token-2022 mint (including one with transfer fee extension) can be registered.
- The attacker path requires only calling `init_transfer` as a normal user with a registered Token-2022 fee-bearing token — no privileged role needed.
- Token-2022 transfer fee extension is a standard, widely-used feature (e.g., many DeFi tokens on Solana use it).
- The deficit accumulates with every deposit, so even small fees compound across many users.

### Recommendation

After `transfer_checked`, read back the vault's actual received amount by comparing pre- and post-transfer vault balances (or by reading the Token-2022 transfer fee extension state), and use that actual received amount in `payload.serialize_for_near(...)` instead of `payload.amount`. Alternatively, explicitly reject Token-2022 mints that have the transfer fee extension enabled (check for `TransferFeeConfig` extension in the mint account before proceeding), consistent with how transfer hooks are rejected.

### Proof of Concept

1. Deploy a Token-2022 mint with `TransferFeeConfig` set to 5% fee.
2. Call `log_metadata` for that mint → vault PDA is created.
3. Call `init_transfer` with `payload.amount = 1_000_000`.
4. `transfer_checked(1_000_000)` executes: vault receives `950_000`; `50_000` is withheld.
5. Wormhole message records `amount = 1_000_000`.
6. NEAR `fin_transfer_callback` credits the recipient with `1_000_000` tokens (minted or unlocked on NEAR).
7. Recipient bridges `1_000_000` back to Solana via `finalize_transfer`.
8. `finalize_transfer` calls `transfer_checked(1_000_000)` from vault → vault only has `950_000` spendable → CPI fails → user's funds are permanently frozen on NEAR. [8](#0-7)

### Citations

**File:** solana/programs/bridge_token_factory/src/instructions/user/init_transfer.rs (L88-130)
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

        self.common.post_message(payload.serialize_for_near((
            self.common.sequence.sequence,
            self.user.key(),
            self.mint.key(),
        ))?)?;

        Ok(())
    }
```

**File:** solana/programs/bridge_token_factory/src/state/message/init_transfer.rs (L8-14)
```rust
pub struct InitTransferPayload {
    pub amount: u128,
    pub recipient: String,
    pub fee: u128,
    pub native_fee: u64,
    pub message: String,
}
```

**File:** solana/programs/bridge_token_factory/src/state/message/init_transfer.rs (L31-33)
```rust
        // 4. amount
        self.amount.serialize(&mut writer)?;
        // 5. fee
```

**File:** solana/SECURITY.md (L11-19)
```markdown
- **Wrapped tokens are always classic SPL Token, not Token-2022** — Intentional design decision. Bridged mints don't need Token-2022 extensions.

## Known Issues

Low-severity items acknowledged but not yet addressed:

- **No validation of `recipient` string in `InitTransferPayload`** — An invalid recipient causes the transfer to fail on the NEAR side after tokens are locked/burned on Solana. Manual intervention would be needed.
- **No validation of `fee_recipient` length in `FinalizeTransferPayload`** — Excessively large strings increase Wormhole message size. Bounded by Solana tx size limits in practice.
- **Token-2022 tokens with transfer hooks are not supported** — Transfer hook extra account metas are not included in instruction account sets. Affected tokens will fail at runtime (denial, not fund loss).
```

**File:** solana/CLAUDE.md (L13-13)
```markdown
- **`bridge_token_factory`**: Single Anchor program implementing a factory pattern for cross-chain token bridging between Solana and NEAR, supports both SPL Token and Token-2022
```

**File:** evm/SECURITY.md (L7-7)
```markdown
- **Fee-on-transfer tokens not supported**: `initTransfer` emits the requested `amount`, not the actual received balance. Fee-on-transfer and rebasing tokens are intentionally unsupported
```

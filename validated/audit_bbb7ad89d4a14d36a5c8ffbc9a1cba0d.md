### Title
`u128`-to-`u64` Amount Truncation in Solana `finalize_transfer` Permanently Locks User Funds on NEAR - (`solana/programs/bridge_token_factory/src/instructions/user/finalize_transfer.rs`)

---

### Summary

The Solana bridge's `finalize_transfer` and `finalize_transfer_sol` instructions receive a `u128` amount from NEAR (via Wormhole message) but must convert it to `u64` to call Solana's SPL token program. If the bridged amount exceeds `u64::MAX` (~18.4 × 10¹⁸ smallest units), the conversion fails with `AmountOverflow`, the transaction reverts, and the user's tokens remain permanently locked or burned on NEAR with no refund path.

---

### Finding Description

`FinalizeTransferPayload.amount` is typed as `u128`, matching the NEAR side's `U128` representation: [1](#0-0) 

When the instruction executes, both the native-token path (`transfer_checked`) and the bridged-token path (`mint_to`) cast this `u128` to `u64`: [2](#0-1) 

The same cast exists in the SOL-native path: [3](#0-2) 

On the NEAR side, `TransferMessage.amount` is `U128` and the bridge imposes no upper-bound check against `u64::MAX` before locking or burning tokens for a Solana-destined transfer: [4](#0-3) 

The NEAR bridge locks/burns tokens at `init_transfer` time and emits an event. The Solana bridge is expected to finalize. If finalization always reverts (because `amount > u64::MAX`), the nonce is never marked used, but the NEAR-side lock is never reversed — there is no on-chain refund callback from Solana back to NEAR.

---

### Impact Explanation

A user bridging a token with 18 decimals from NEAR to Solana needs only **more than ~18.44 whole tokens** (i.e., `> u64::MAX` in the smallest unit, e.g., 20 × 10¹⁸) to trigger this. The tokens are locked/burned on NEAR, every relayer attempt to finalize on Solana reverts with `AmountOverflow`, and the funds are irrecoverably frozen. This matches the **Critical — permanent freezing / irrecoverable lock of user funds** impact class.

---

### Likelihood Explanation

18-decimal tokens are the dominant standard (USDC, USDT variants, WETH, etc.). A user bridging even a modest amount — e.g., 20 USDC-equivalent tokens denominated with 18 decimals — would exceed `u64::MAX` in raw units. The NEAR bridge accepts any `U128` amount with no Solana-specific ceiling check, so any ordinary bridge user can trigger this unintentionally.

---

### Recommendation

1. **On the NEAR side**: Before locking/burning tokens for a Solana-destined transfer, validate that `amount.0 <= u64::MAX`. Reject the transfer early with a clear error if it exceeds the Solana limit.
2. **On the Solana side**: The `AmountOverflow` guard is correct but insufficient alone — the NEAR side must prevent the lock from ever occurring for out-of-range amounts.
3. **Alternatively**: Implement a cross-chain refund/cancel flow so that a permanently-unfinalizeable transfer can be unwound on NEAR.

---

### Proof of Concept

1. Token `FOO` has 18 decimals and is registered on both NEAR and Solana via the bridge.
2. Alice holds 20 FOO on NEAR (raw amount: `20_000_000_000_000_000_000`, i.e., `2 × 10¹⁹ > u64::MAX ≈ 1.84 × 10¹⁹`).
3. Alice calls `ft_transfer_call` on NEAR targeting the bridge with recipient `sol:<alice_solana_address>` and amount `20_000_000_000_000_000_000`. The NEAR bridge locks/burns her tokens and emits an `InitTransfer` event.
4. A relayer picks up the Wormhole VAA and calls `finalize_transfer` on Solana with `FinalizeTransferPayload { amount: 20_000_000_000_000_000_000_u128, … }`.
5. At line 114 of `finalize_transfer.rs`, `data.amount.try_into::<u64>()` returns `Err` → `error!(ErrorCode::AmountOverflow)` → transaction reverts.
6. The destination nonce is never marked used; the Solana-side state is unchanged.
7. Alice's 20 FOO are permanently locked on NEAR with no recovery path. [5](#0-4) [6](#0-5)

### Citations

**File:** solana/programs/bridge_token_factory/src/state/message/finalize_transfer.rs (L10-16)
```rust
#[derive(AnchorSerialize, AnchorDeserialize, Debug)]
pub struct FinalizeTransferPayload {
    pub destination_nonce: u64,
    pub transfer_id: TransferId,
    pub amount: u128,
    pub fee_recipient: Option<String>,
}
```

**File:** solana/programs/bridge_token_factory/src/instructions/user/finalize_transfer.rs (L103-135)
```rust
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
        } else {
            // Bridged version. May be a fake token with our authority set but it will be ignored on the near side
            require!(
                self.mint.mint_authority.contains(self.authority.key),
                ErrorCode::InvalidBridgedToken
            );

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

**File:** solana/programs/bridge_token_factory/src/instructions/user/finalize_transfer_sol.rs (L79-89)
```rust
        transfer(
            CpiContext::new_with_signer(
                self.common.system_program.to_account_info(),
                Transfer {
                    from: self.sol_vault.to_account_info(),
                    to: self.recipient.to_account_info(),
                },
                &[&[SOL_VAULT_SEED, &[self.config.bumps.sol_vault]]],
            ),
            data.amount.try_into().map_err(|_| error!(ErrorCode::AmountOverflow))?,
        )?;
```

**File:** near/omni-types/src/lib.rs (L559-571)
```rust
#[near(serializers=[borsh, json])]
#[derive(Debug, Clone)]
pub struct TransferMessage {
    pub origin_nonce: Nonce,
    pub token: OmniAddress,
    pub amount: U128,
    pub recipient: OmniAddress,
    pub fee: Fee,
    pub sender: OmniAddress,
    pub msg: String,
    pub destination_nonce: Nonce,
    pub origin_transfer_id: Option<UnifiedTransferId>,
}
```

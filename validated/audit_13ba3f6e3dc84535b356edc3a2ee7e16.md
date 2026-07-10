### Title
Permanent Lamport Lock in `sol_vault` via Unguarded `native_fee` in `init_transfer_sol` — (`solana/programs/bridge_token_factory/src/instructions/user/init_transfer_sol.rs`)

### Summary

`init_transfer_sol` accepts a caller-supplied `native_fee` and deposits `amount + native_fee` lamports into `sol_vault`, but `finalize_transfer_sol` only ever releases `amount` lamports. NEAR compensates the fee recipient by **minting** wrapped-SOL tokens on NEAR rather than releasing actual SOL from `sol_vault`. The `native_fee` lamports are therefore permanently stranded in `sol_vault` with no on-chain recovery path.

---

### Finding Description

**Step 1 — No guard on `native_fee` in `init_transfer_sol`**

`init_transfer_sol.process` enforces `payload.fee == 0` but places **no constraint on `payload.native_fee`**. Any caller can supply an arbitrary non-zero value. [1](#0-0) 

The transfer to `sol_vault` is `native_fee + amount`, not just `amount`.

**Step 2 — Wormhole message carries both fields**

`serialize_for_near` encodes `amount` and `native_fee` as separate fields in the outgoing Wormhole VAA. [2](#0-1) 

**Step 3 — NEAR mints wrapped SOL instead of releasing actual SOL**

When NEAR processes the fee via `send_fee_internal`, for a Solana-origin transfer it calls `ext_token::mint` on the wrapped-SOL token contract — it does **not** instruct Solana to release lamports from `sol_vault`. [3](#0-2) 

**Step 4 — `finalize_transfer_sol` releases only `data.amount`**

`FinalizeTransferPayload` has no `native_fee` field. The only lamport transfer out of `sol_vault` is `data.amount`. [4](#0-3) [5](#0-4) 

**Step 5 — No admin recovery path**

There is no `withdraw`, `rescue`, or admin-drain instruction for `sol_vault` anywhere in the program.



---

### Impact Explanation

Every lamport paid as `native_fee` in `init_transfer_sol` is permanently locked in `sol_vault`. The fee recipient receives minted wrapped-SOL tokens on NEAR (unbacked by the locked lamports), while the actual SOL is irrecoverable. This breaks the bridge collateralization invariant: `sol_vault` accumulates lamports that can never be released, and the wrapped-SOL supply on NEAR grows without a corresponding reduction in `sol_vault`. This is a **Critical** impact — permanent, irrecoverable lock of user funds in the bridge vault.

---

### Likelihood Explanation

The `native_fee` field is a standard, documented part of `InitTransferPayload` and is the intended mechanism for users to pay relayers. Any user who sets `native_fee > 0` when calling `init_transfer_sol` triggers the lock. No special privilege or exploit knowledge is required — it is reachable by any unprivileged bridge user through the public `init_transfer_sol` instruction.

---

### Recommendation

Add a guard in `init_transfer_sol.process` rejecting any non-zero `native_fee`:

```rust
require!(payload.native_fee == 0, ErrorCode::InvalidFee);
```

Alternatively, if `native_fee` is intended to be supported for SOL transfers, `finalize_transfer_sol` must be extended to also transfer `native_fee` lamports to the `fee_recipient` account, and `FinalizeTransferPayload` must carry the `native_fee` amount so NEAR can sign over the correct total.

---

### Proof of Concept

```
1. Call init_transfer_sol(amount=1_000_000_000, native_fee=100_000_000, fee=0, recipient=<NEAR addr>)
   → sol_vault.lamports increases by 1_100_000_000

2. NEAR fin_transfer processes the VAA:
   - Stores transfer: amount=1e9, fee={fee:0, native_fee:1e8}

3. NEAR sign_transfer creates FinalizeTransferPayload{amount=1e9, ...}
   (amount_without_fee = 1e9 - 0 = 1e9; native_fee is not included)

4. Relayer calls finalize_transfer_sol with the signed payload
   → sol_vault.lamports decreases by 1_000_000_000 (only amount)
   → recipient receives 1_000_000_000 lamports

5. NEAR claim_fee → send_fee_internal mints 1e8 wrapped-SOL tokens to fee_recipient on NEAR
   (no SOL is released from sol_vault)

6. Assert: sol_vault.lamports stranded += 100_000_000
   Assert: no on-chain instruction exists to recover these lamports
```

### Citations

**File:** solana/programs/bridge_token_factory/src/instructions/user/init_transfer_sol.rs (L35-53)
```rust
    pub fn process(&self, payload: &InitTransferPayload) -> Result<()> {
        require!(payload.fee == 0, ErrorCode::InvalidFee);
        require!(payload.amount > 0, ErrorCode::InvalidArgs);

        transfer(
            CpiContext::new(
                self.common.system_program.to_account_info(),
                Transfer {
                    from: self.user.to_account_info(),
                    to: self.sol_vault.to_account_info(),
                },
            ),
            payload
                .native_fee
                .checked_add(
                    payload.amount.try_into().map_err(|_| error!(ErrorCode::InvalidArgs))?,
                )
                .ok_or_else(|| error!(ErrorCode::InvalidArgs))?,
        )?;
```

**File:** solana/programs/bridge_token_factory/src/state/message/init_transfer.rs (L32-36)
```rust
        self.amount.serialize(&mut writer)?;
        // 5. fee
        self.fee.serialize(&mut writer)?;
        // 6. native_fee
        u128::from(self.native_fee).serialize(&mut writer)?;
```

**File:** near/omni-bridge/src/lib.rs (L2668-2673)
```rust
            } else {
                ext_token::ext(self.get_native_token_id(origin_chain))
                    .with_static_gas(MINT_TOKEN_GAS)
                    .mint(fee_recipient.clone(), transfer_message.fee.native_fee, None)
                    .detach();
            }
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

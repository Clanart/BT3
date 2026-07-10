### Title
`native_fee` Paid in Native SOL Is Permanently Locked in `sol_vault` and Never Distributed to Fee Recipients — (File: `solana/programs/bridge_token_factory/src/instructions/user/init_transfer_sol.rs`)

---

### Summary

When a user initiates a native SOL transfer from Solana to NEAR via `init_transfer_sol`, they pay a `native_fee` denominated in native SOL. This fee is deposited into the `sol_vault` PDA. However, there is no on-chain instruction on Solana to release `native_fee` lamports from `sol_vault` to the fee recipient. Simultaneously, `init_transfer_sol` enforces `fee == 0` (the token-denominated fee), so the fee recipient also receives nothing on the NEAR side when the transfer is finalized. The `native_fee` is permanently misdirected into `sol_vault` — effectively subsidizing outgoing NEAR→Solana native SOL payouts — while the relayer who facilitated the transfer is left uncompensated.

---

### Finding Description

`init_transfer_sol` enforces that the token-denominated fee is zero and collects both the transfer amount and the `native_fee` into `sol_vault`:

```rust
// init_transfer_sol.rs lines 36-53
require!(payload.fee == 0, ErrorCode::InvalidFee);
require!(payload.amount > 0, ErrorCode::InvalidArgs);

transfer(
    CpiContext::new(...Transfer { from: self.user, to: self.sol_vault }),
    payload.native_fee
        .checked_add(payload.amount.try_into()...)
        .ok_or_else(|| error!(ErrorCode::InvalidArgs))?,
)?;
``` [1](#0-0) 

The `sol_vault` PDA is a system account seeded by `SOL_VAULT_SEED`. Its only outflow path in the entire program is `finalize_transfer_sol`, which releases SOL to a recipient of a NEAR→Solana transfer:

```rust
// finalize_transfer_sol.rs lines 79-89
transfer(
    CpiContext::new_with_signer(
        ...Transfer { from: self.sol_vault, to: self.recipient },
        &[&[SOL_VAULT_SEED, &[self.config.bumps.sol_vault]]],
    ),
    data.amount...,
)?;
``` [2](#0-1) 

There is no `claim_native_fee`, `withdraw_native_fee`, or any other Solana-side instruction that routes `native_fee` lamports to a fee recipient. The full list of user instructions confirms this:



On the NEAR side, `claim_fee_callback` computes the relayer's reward as the difference between the stored transfer amount and the denormalized on-chain amount — i.e., the token-denominated fee only:

```rust
// near/omni-bridge/src/lib.rs lines 1131-1133
let fee = transfer_message.amount.0 - denormalized_amount;
self.send_fee_internal(&transfer_message, fee_recipient, fee)
``` [3](#0-2) 

Because `init_transfer_sol` mandates `fee == 0`, the token-denominated fee stored in the transfer message is zero. `claim_fee_callback` therefore pays the fee recipient zero tokens. The `native_fee` in SOL, which is the only compensation the user offered, sits in `sol_vault` and is never forwarded.

The same partial issue exists for `init_transfer` (SPL token path): `native_fee` is also deposited into `sol_vault` and is equally unreachable by the fee recipient, even though the token fee is non-zero and claimable on NEAR.

```rust
// init_transfer.rs lines 75-86
if payload.native_fee > 0 {
    transfer(
        CpiContext::new(...Transfer { from: self.user, to: self.sol_vault }),
        payload.native_fee,
    )?;
}
``` [4](#0-3) 

---

### Impact Explanation

This is a fee-accounting corruption that permanently misdirects value. Every `native_fee` paid by a user for a Solana→NEAR transfer accumulates in `sol_vault` and is consumed by unrelated NEAR→Solana outgoing payouts rather than reaching the intended fee recipient. For native SOL transfers specifically, the fee recipient receives **zero compensation** on both chains despite the user having paid a non-zero `native_fee`. This falls squarely under:

> **High. Balance, decimal, fee, token-mapping, or accounting corruption that breaks bridge collateralization or misdirects value.**

---

### Likelihood Explanation

Any unprivileged user who calls `init_transfer_sol` with `native_fee > 0` triggers the misdirection. No special role or key is required. The `native_fee` field is a standard user-supplied parameter in `InitTransferPayload`. Relayers who discover they are uncompensated for native SOL transfers will stop servicing them, potentially making the native SOL bridge path economically non-functional. [5](#0-4) 

---

### Recommendation

Add a Solana-side instruction (e.g., `claim_native_fee`) that allows the designated fee recipient — identified from the corresponding `FinalizeTransferResponse` or a separate on-chain record — to withdraw their owed `native_fee` lamports from `sol_vault`. Alternatively, route the `native_fee` directly to the relayer's account at `init_transfer_sol` time (if the relayer is known at that point), mirroring how the token fee is handled on NEAR. The NEAR-side `claim_fee_callback` should also be extended to account for `native_fee` in its fee computation.

---

### Proof of Concept

1. User calls `init_transfer_sol` with `amount = 1 SOL`, `native_fee = 0.01 SOL`, `fee = 0` (enforced).
2. `sol_vault` receives `1.01 SOL`. [6](#0-5) 
3. Wormhole message is posted; NEAR relayer observes it and calls `fin_transfer` on NEAR.
4. NEAR `fin_transfer_callback` creates a `TransferMessage` with `fee.fee = 0`, `fee.native_fee = 0.01 SOL` (in lamports). [7](#0-6) 
5. Relayer later calls `claim_fee` on NEAR. `claim_fee_callback` computes `fee = amount - denormalized_amount = 0`. `send_fee_internal` pays the relayer **0 tokens**. [3](#0-2) 
6. The `0.01 SOL` `native_fee` remains in `sol_vault` indefinitely, eventually consumed by the next `finalize_transfer_sol` payout to an unrelated recipient. [2](#0-1) 
7. The relayer receives no compensation for the native SOL bridge operation.

### Citations

**File:** solana/programs/bridge_token_factory/src/instructions/user/init_transfer_sol.rs (L36-53)
```rust
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

**File:** near/omni-bridge/src/lib.rs (L722-732)
```rust
        let transfer_message = TransferMessage {
            origin_nonce: init_transfer.origin_nonce,
            token: init_transfer.token,
            amount: Self::denormalize_amount(init_transfer.amount.0, decimals).into(),
            recipient: init_transfer.recipient,
            fee: Self::denormalize_fee(&init_transfer.fee, decimals),
            sender: init_transfer.sender,
            msg: init_transfer.msg,
            destination_nonce,
            origin_transfer_id: None,
        };
```

**File:** near/omni-bridge/src/lib.rs (L1131-1133)
```rust
        let fee = transfer_message.amount.0 - denormalized_amount;

        self.send_fee_internal(&transfer_message, fee_recipient, fee)
```

**File:** solana/programs/bridge_token_factory/src/instructions/user/init_transfer.rs (L75-86)
```rust
        if payload.native_fee > 0 {
            transfer(
                CpiContext::new(
                    self.common.system_program.to_account_info(),
                    Transfer {
                        from: self.user.to_account_info(),
                        to: self.sol_vault.to_account_info(),
                    },
                ),
                payload.native_fee,
            )?;
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

### Title
Missing Recipient Account Validation in `FinalizeTransferSol` Enables Native SOL Theft — (File: solana/programs/bridge_token_factory/src/instructions/user/finalize_transfer_sol.rs)

---

### Summary

The `FinalizeTransferSol` instruction transfers native SOL from the bridge vault to the `recipient` account supplied in the instruction accounts, but never validates that this account matches the `recipient` field inside the MPC-signed `FinalizeTransferPayload`. An unprivileged attacker can submit a valid signed payload while substituting their own account as `recipient`, stealing the SOL and permanently consuming the nonce so the legitimate recipient can never claim their funds.

---

### Finding Description

In `FinalizeTransfer` (the SPL-token variant), the recipient is implicitly enforced because the `token_account` PDA is derived from `data.payload.recipient`:

```rust
seeds = [TOKEN_ACCOUNT_SEED, data.payload.recipient.as_ref(), mint.key().as_ref()],
```

Anchor's seed-derivation check makes it impossible to pass a token account that does not belong to the payload's recipient — any mismatch causes the instruction to fail at account validation.

In `FinalizeTransferSol` (the native SOL variant), no equivalent constraint exists:

```rust
/// CHECK: this can be any type of account
#[account(mut)]
pub recipient: UncheckedAccount<'info>,
```

The `process` function then transfers SOL directly to `self.recipient` — the caller-supplied account — without ever comparing it to `data.recipient` (the address embedded in the MPC-signed payload):

```rust
transfer(
    CpiContext::new_with_signer(
        self.common.system_program.to_account_info(),
        Transfer {
            from: self.sol_vault.to_account_info(),
            to: self.recipient.to_account_info(),   // ← caller-controlled, unvalidated
        },
        &[&[SOL_VAULT_SEED, &[self.config.bumps.sol_vault]]],
    ),
    data.amount.try_into().map_err(|_| error!(ErrorCode::AmountOverflow))?,
)?;
```

The nonce is consumed by `UsedNonces::use_nonce` before the transfer, so after a successful (attacker-redirected) call the destination nonce is permanently spent and the legitimate recipient has no recourse.

Both files import the same `FinalizeTransferPayload` struct, confirming the `recipient` field is present and accessible in `FinalizeTransferSol` but simply never checked.

---

### Impact Explanation

**Critical — Direct theft of native SOL and permanent freezing of victim funds.**

- The attacker receives the full SOL amount intended for the victim.
- The destination nonce is marked used; the victim can never re-submit the transfer.
- No privileged role is required; the instruction has no caller restriction.

---

### Likelihood Explanation

**High.** The signed payload is broadcast on-chain (via Wormhole message or NEAR event) before the relayer submits it to Solana. Any observer can extract the payload and race to submit `finalize_transfer_sol` with their own account as `recipient`. The attack requires no special access, no key material, and no collusion — only the ability to submit a Solana transaction before the legitimate relayer.

---

### Recommendation

Add an explicit constraint in the `FinalizeTransferSol` accounts struct that ties the instruction's `recipient` account to the signed payload's recipient field, mirroring the implicit enforcement already present in `FinalizeTransfer`:

```rust
#[account(
    mut,
    constraint = recipient.key() == &data.payload.recipient
        @ ErrorCode::InvalidRecipient,
)]
pub recipient: SystemAccount<'info>,
```

---

### Proof of Concept

1. NEAR MPC signs a `FinalizeTransferPayload`: `{ destination_nonce: N, amount: X, recipient: victim_pubkey, … }`.
2. The signed payload is observable on-chain (NEAR event / Wormhole VAA).
3. Attacker constructs a `finalize_transfer_sol` instruction using the authentic signed payload but substitutes `recipient = attacker_pubkey` in the accounts list.
4. Anchor validates the signature over the payload — passes, because the payload bytes are unmodified.
5. `UsedNonces::use_nonce(N, …)` marks nonce N as spent.
6. `transfer(sol_vault → attacker_pubkey, X lamports)` executes successfully.
7. Victim's nonce N is permanently consumed; they receive nothing. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** solana/programs/bridge_token_factory/src/instructions/user/finalize_transfer_sol.rs (L52-54)
```rust
    /// CHECK: this can be any type of account
    #[account(mut)]
    pub recipient: UncheckedAccount<'info>,
```

**File:** solana/programs/bridge_token_factory/src/instructions/user/finalize_transfer_sol.rs (L67-77)
```rust
impl FinalizeTransferSol<'_> {
    pub fn process(&mut self, data: FinalizeTransferPayload) -> Result<()> {
        UsedNonces::use_nonce(
            data.destination_nonce,
            &self.used_nonces,
            &mut self.config,
            self.authority.to_account_info(),
            self.common.payer.to_account_info(),
            &Rent::get()?,
            self.system_program.to_account_info(),
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

**File:** solana/programs/bridge_token_factory/src/instructions/user/finalize_transfer.rs (L89-99)
```rust
impl FinalizeTransfer<'_> {
    pub fn process(&mut self, data: FinalizeTransferPayload) -> Result<()> {
        UsedNonces::use_nonce(
            data.destination_nonce,
            &self.used_nonces,
            &mut self.config,
            self.authority.to_account_info(),
            self.common.payer.to_account_info(),
            &Rent::get()?,
            self.system_program.to_account_info(),
        )?;
```

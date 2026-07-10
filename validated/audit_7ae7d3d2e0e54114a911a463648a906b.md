### Title
Solana `FinalizeTransfer` `mint` Account Not Validated Against Signed Payload Token Address — (`solana/programs/bridge_token_factory/src/instructions/user/finalize_transfer.rs`)

---

### Summary

The Solana `FinalizeTransfer` instruction accepts a caller-supplied `mint` account that is **not constrained to match the token address in the MPC-signed `FinalizeTransferPayload`**. An attacker holding any valid MPC-signed transfer payload can substitute a different (higher-value) mint account, causing the bridge to mint or release the wrong token. This is the direct analog of the BribeVault M-10 finding: distribution parameters (here, the mint) are accepted blindly without being validated against the actual signed/locked asset.

---

### Finding Description

In `FinalizeTransfer::process`, the Anchor account struct declares the `mint` account with only a `mint::token_program = token_program` constraint:

```rust
#[account(
    mut,
    mint::token_program = token_program,
)]
pub mint: Box<InterfaceAccount<'info, Mint>>,
``` [1](#0-0) 

There is **no constraint** of the form `constraint = mint.key() == data.payload.token_address`. The `#[instruction(data: SignedPayload<FinalizeTransferPayload>)]` attribute makes the signed payload available for use in account constraints, but it is never used to bind the `mint` key. [2](#0-1) 

The `vault` PDA is derived from the caller-supplied `mint`:

```rust
seeds = [VAULT_SEED, mint.key().as_ref()],
``` [3](#0-2) 

And the `token_account` (recipient ATA) is also derived from the caller-supplied `mint`:

```rust
associated_token::mint = mint,
associated_token::authority = recipient,
``` [4](#0-3) 

Inside `process`, the actual transfer or mint uses `data.amount` against whichever `mint` was passed in:

```rust
transfer_checked(..., data.amount, self.mint.decimals)?;
// or
mint_to(..., data.amount)?;
``` [5](#0-4) 

Critically, the Wormhole response posted back to NEAR uses `self.mint.key()` — the **caller-supplied** mint — as the authoritative token identifier, not any field from the signed payload:

```rust
let payload = FinalizeTransferResponse {
    token: self.mint.key(),   // ← caller-controlled, not from signed data
    amount: data.amount,
    fee_recipient: data.fee_recipient.unwrap_or_default(),
    transfer_id: data.transfer_id,
}.serialize_for_near(())?;
``` [6](#0-5) 

Compare this with the EVM side, where `finTransfer` uses `payload.tokenAddress` directly from the MPC-verified signature and never accepts a separate caller-supplied token account: [7](#0-6) 

On NEAR, `sign_transfer` constructs a `TransferMessagePayload` that explicitly includes `token_address` in the MPC-signed data:

```rust
let transfer_payload = TransferMessagePayload {
    ...
    token_address,
    amount: U128(amount_to_transfer),
    ...
};
``` [8](#0-7) 

The Solana instruction receives this signed payload but never enforces that the `mint` account key equals the `token_address` field inside it.

---

### Impact Explanation

**Critical — unauthorized mint of bridged assets.**

Two attack paths exist:

1. **Bridged token path (no vault):** The bridge mints tokens when `self.mint.mint_authority.contains(self.authority.key)`. Any bridge-deployed token satisfies this. An attacker with a valid MPC signature for a low-value token (e.g., 1 USDC) can pass the mint of a high-value bridge token (e.g., WBTC) and receive minted WBTC instead.

2. **Native token path (vault exists):** If the attacker passes a mint whose vault holds locked native tokens, `transfer_checked` releases those tokens to the attacker's ATA, draining the vault of a completely different asset than what was signed.

In both cases the Wormhole message back to NEAR reports the substituted mint as the token, corrupting the NEAR-side accounting and preventing legitimate fee claims or collateralization checks from detecting the discrepancy.

---

### Likelihood Explanation

**High.** Any user who can initiate a cross-chain transfer from NEAR to Solana (an unprivileged, public action via `ft_on_transfer` / `init_transfer`) will eventually receive a valid MPC-signed `FinalizeTransferPayload`. That payload's `destination_nonce` is the only per-use guard; the `mint` account is entirely free. No privileged role, leaked key, or colluding MPC threshold is required — the attacker simply substitutes the `mint` account when submitting the instruction.

---

### Recommendation

Add an explicit account constraint that binds the `mint` key to the token address in the signed payload:

```rust
#[account(
    mut,
    mint::token_program = token_program,
    constraint = mint.key() == data.payload.token_address
        @ ErrorCode::InvalidMint,
)]
pub mint: Box<InterfaceAccount<'info, Mint>>,
```

This mirrors the EVM approach where `payload.tokenAddress` from the verified signature is used directly, with no separate caller-supplied token parameter.

---

### Proof of Concept

1. Alice initiates a transfer of 1 USDC (low-value bridge token) from NEAR to Solana via `ft_on_transfer` → `init_transfer`. The NEAR bridge stores the transfer and eventually calls `sign_transfer`, producing an MPC-signed `FinalizeTransferPayload` with `token_address = USDC_MINT`, `amount = 1_000_000`, `destination_nonce = N`.

2. Alice intercepts the signed payload before submitting it to Solana.

3. Alice calls `finalize_transfer` on Solana with:
   - `data` = the valid MPC-signed payload (for USDC, nonce N)
   - `mint` = the Solana mint address of WBTC (a high-value bridge-deployed token whose `mint_authority` is the bridge `authority` PDA)
   - `vault` = `None` (no vault for WBTC, so the mint path is taken)
   - `token_account` = Alice's WBTC ATA

4. `UsedNonces::use_nonce(N, ...)` marks nonce N as used (replay is prevented for this nonce).

5. The `mint_authority` check passes because WBTC is a bridge-deployed token.

6. `mint_to` mints `1_000_000` WBTC (in WBTC's native decimals) to Alice's ATA.

7. The Wormhole message reports `token = WBTC_MINT`, `amount = 1_000_000`, `transfer_id = (NEAR, origin_nonce)` back to NEAR.

8. Alice has received WBTC worth orders of magnitude more than the 1 USDC she locked on NEAR. The bridge's WBTC supply is now unbacked.

### Citations

**File:** solana/programs/bridge_token_factory/src/instructions/user/finalize_transfer.rs (L23-25)
```rust
#[derive(Accounts)]
#[instruction(data: SignedPayload<FinalizeTransferPayload>)]
pub struct FinalizeTransfer<'info> {
```

**File:** solana/programs/bridge_token_factory/src/instructions/user/finalize_transfer.rs (L53-57)
```rust
    #[account(
        mut,
        mint::token_program = token_program,
    )]
    pub mint: Box<InterfaceAccount<'info, Mint>>,
```

**File:** solana/programs/bridge_token_factory/src/instructions/user/finalize_transfer.rs (L63-70)
```rust
        token::authority = authority,
        seeds = [
            VAULT_SEED,
            mint.key().as_ref(),
        ],
        bump,
        token::token_program = token_program,
    )]
```

**File:** solana/programs/bridge_token_factory/src/instructions/user/finalize_transfer.rs (L73-80)
```rust
    #[account(
        init_if_needed,
        payer = common.payer,
        associated_token::mint = mint,
        associated_token::authority = recipient,
        token::token_program = token_program,
    )]
    pub token_account: Box<InterfaceAccount<'info, TokenAccount>>,
```

**File:** solana/programs/bridge_token_factory/src/instructions/user/finalize_transfer.rs (L101-136)
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
        }
```

**File:** solana/programs/bridge_token_factory/src/instructions/user/finalize_transfer.rs (L138-147)
```rust
        let payload = FinalizeTransferResponse {
            token: self.mint.key(),
            amount: data.amount,
            fee_recipient: data.fee_recipient.unwrap_or_default(),
            transfer_id: data.transfer_id,
        }
        .serialize_for_near(())?;

        self.common.post_message(payload)?;

```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L279-312)
```text
    function finTransfer(
        bytes calldata signatureData,
        BridgeTypes.TransferMessagePayload calldata payload
    ) external payable whenNotPaused(PAUSED_FIN_TRANSFER) {
        if (completedTransfers[payload.destinationNonce]) {
            revert NonceAlreadyUsed(payload.destinationNonce);
        }

        completedTransfers[payload.destinationNonce] = true;

        bytes memory borshEncoded = bytes.concat(
            bytes1(uint8(BridgeTypes.PayloadType.TransferMessage)),
            Borsh.encodeUint64(payload.destinationNonce),
            bytes1(payload.originChain),
            Borsh.encodeUint64(payload.originNonce),
            bytes1(omniBridgeChainId),
            Borsh.encodeAddress(payload.tokenAddress),
            Borsh.encodeUint128(payload.amount),
            bytes1(omniBridgeChainId),
            Borsh.encodeAddress(payload.recipient),
            bytes(payload.feeRecipient).length == 0 // None or Some(String) in rust
                ? bytes("\x00")
                : bytes.concat(
                    bytes("\x01"),
                    Borsh.encodeString(payload.feeRecipient)
                ),
            bytes(payload.message).length == 0
                ? bytes("")
                : Borsh.encodeBytes(payload.message)
        );
        bytes32 hashed = keccak256(borshEncoded);

        if (ECDSA.recover(hashed, signatureData) != nearBridgeDerivedAddress) {
            revert InvalidSignature();
```

**File:** near/omni-bridge/src/lib.rs (L491-500)
```rust
        let transfer_payload = TransferMessagePayload {
            prefix: PayloadType::TransferMessage,
            destination_nonce: transfer_message.destination_nonce,
            transfer_id,
            token_address,
            amount: U128(amount_to_transfer),
            recipient: transfer_message.recipient,
            fee_recipient,
            message,
        };
```

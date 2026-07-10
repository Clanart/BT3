### Title
`FinalizeTransfer` Does Not Validate `mint` Account Against Payload's `token_address` — (File: `solana/programs/bridge_token_factory/src/instructions/user/finalize_transfer.rs`)

### Summary
The Solana `FinalizeTransfer` instruction accepts a caller-supplied `mint` account that is never validated against the `token_address` field in the MPC-signed `FinalizeTransferPayload`. A malicious relayer can substitute any mint whose authority is the bridge PDA, causing the wrong token to be minted to the recipient while consuming the destination nonce and permanently locking the user's funds on NEAR.

### Finding Description

The NEAR side's `sign_transfer` function constructs and MPC-signs a `TransferMessagePayload` that explicitly includes the destination-chain token address: [1](#0-0) 

This signed payload is deserialized on Solana as `SignedPayload<FinalizeTransferPayload>` and its signature is verified by the Wormhole CPI. The payload therefore carries a trusted `token_address` (the expected Solana mint pubkey).

However, the `FinalizeTransfer` accounts struct constrains the `mint` account only to a matching `token_program`: [2](#0-1) 

There is no Anchor constraint of the form `address = data.payload.token_address` on the `mint` account. Compare this with `DeployToken`, where the mint is deterministically derived from the payload's token field via PDA seeds: [3](#0-2) 

In `FinalizeTransfer`, the bridged-token path only checks that the bridge authority is the mint authority: [4](#0-3) 

Because the bridge authority PDA (`[AUTHORITY_SEED]`) is a publicly known address, any party can create a worthless SPL token and set the bridge authority as its mint authority, satisfying this check with an arbitrary mint.

The `FinalizeTransferResponse` reports back `self.mint.key()` — whatever mint was passed in — as the token: [5](#0-4) 

On the NEAR side, `FinTransferMessage` carries no `token` field: [6](#0-5) 

So NEAR's `claim_fee_callback` never cross-checks the token reported by Solana against the token in the stored pending transfer: [7](#0-6) 

The mismatch is invisible to both chains.

### Impact Explanation

**Critical.** A malicious relayer executes the following:

1. User initiates a NEAR → Solana transfer of 1 000 USDC. NEAR locks the USDC and the MPC signs a payload with `token_address = USDC_MINT`, `destination_nonce = N`.
2. Attacker creates a worthless SPL token `W` with the bridge authority PDA as its mint authority.
3. Attacker calls `finalize_transfer` passing `mint = W_MINT` instead of `USDC_MINT`.
4. The program mints 1 000 `W` tokens to the recipient and marks nonce `N` as used.
5. The legitimate finalization with `USDC_MINT` can never execute (nonce already consumed).
6. The recipient receives worthless tokens; the user's 1 000 USDC is permanently locked on NEAR.

This constitutes both **direct theft of bridged assets** and **permanent freezing of user funds**.

### Likelihood Explanation

**Medium.** The attack requires the ability to submit a `finalize_transfer` transaction before the legitimate relayer. Any registered relayer, or any party who can front-run the relayer on Solana, can execute this. No privileged key or colluding MPC signers are needed — only a valid signed payload (which is broadcast publicly by the relayer infrastructure) and a pre-created worthless mint.

### Recommendation

Add an explicit address constraint on the `mint` account in the `FinalizeTransfer` accounts struct, tying it to the `token_address` field in the payload:

```rust
#[account(
    mut,
    address = Pubkey::try_from(data.payload.token_address.as_ref())
                  .map_err(|_| error!(ErrorCode::InvalidTokenAddress))?,
    mint::token_program = token_program,
)]
pub mint: Box<InterfaceAccount<'info, Mint>>,
```

Alternatively, derive the mint via PDA seeds from the payload's token identifier (as `DeployToken` already does), making the correct mint the only account that can satisfy the constraint.

### Proof of Concept

```
1. Deploy worthless SPL token W:
   - mint_authority = bridge_authority PDA (seeds: [AUTHORITY_SEED])
   - This satisfies the only check: `self.mint.mint_authority.contains(self.authority.key)`

2. Obtain a valid MPC-signed FinalizeTransferPayload for USDC:
   - token_address = USDC_MINT
   - destination_nonce = N
   - amount = 1_000_000_000 (1000 USDC, 6 decimals)
   - recipient = victim_wallet

3. Call finalize_transfer with:
   - data = (valid signed payload above)
   - mint = W_MINT  ← substituted
   - vault = None   ← bridged path taken
   - token_account = victim_wallet's ATA for W

4. Outcome:
   - UsedNonces marks nonce N as consumed
   - 1_000_000_000 W tokens minted to victim_wallet
   - FinalizeTransferResponse { token: W_MINT, amount: 1_000_000_000, ... } sent to NEAR
   - NEAR claim_fee_callback removes the pending transfer by transfer_id only
   - Victim's 1000 USDC locked on NEAR permanently; victim holds worthless W tokens
```

### Citations

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

**File:** near/omni-bridge/src/lib.rs (L1094-1100)
```rust
        let transfer_message = self.remove_transfer_message(fin_transfer.transfer_id);

        if let Some(origin_transfer_id) = transfer_message.origin_transfer_id.clone() {
            let mut fast_transfer = FastTransfer::from_transfer(
                transfer_message.clone(),
                self.get_token_id(&transfer_message.token),
            );
```

**File:** solana/programs/bridge_token_factory/src/instructions/user/finalize_transfer.rs (L53-57)
```rust
    #[account(
        mut,
        mint::token_program = token_program,
    )]
    pub mint: Box<InterfaceAccount<'info, Mint>>,
```

**File:** solana/programs/bridge_token_factory/src/instructions/user/finalize_transfer.rs (L119-135)
```rust
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

**File:** solana/programs/bridge_token_factory/src/instructions/user/deploy_token.rs (L45-53)
```rust
    #[account(
        init,
        payer = common.payer,
        seeds = [WRAPPED_MINT_SEED, data.payload.token.to_hashed_bytes().as_ref()],
        bump,
        mint::decimals = std::cmp::min(MAX_ALLOWED_DECIMALS, data.payload.decimals),
        mint::authority = authority,
    )]
    pub mint: Box<Account<'info, Mint>>,
```

**File:** near/omni-types/src/prover_result.rs (L20-27)
```rust
#[near(serializers=[borsh, json])]
#[derive(Debug, Clone)]
pub struct FinTransferMessage {
    pub transfer_id: TransferId,
    pub fee_recipient: Option<AccountId>,
    pub amount: U128,
    pub emitter_address: OmniAddress,
}
```

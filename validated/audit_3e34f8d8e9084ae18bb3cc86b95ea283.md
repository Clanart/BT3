### Title
Mint PDA Frontrunning Permanently Blocks Token Deployment on Solana — (`solana/programs/bridge_token_factory/src/instructions/user/deploy_token.rs`)

### Summary

The Solana `deploy_token` instruction initializes a mint account using Anchor's `init` constraint at a deterministic PDA address derived from the token string. Because the PDA address is publicly computable and Anchor's `init` calls `create_account` (which fails if the target account already has lamports), any attacker can permanently block deployment of a specific token by transferring lamports to the PDA address before the relayer submits the instruction.

### Finding Description

In `deploy_token.rs`, the mint account is declared with Anchor's `init` constraint and PDA seeds `[WRAPPED_MINT_SEED, data.payload.token.to_hashed_bytes()]`: [1](#0-0) 

Anchor's `init` constraint internally calls the Solana System Program's `create_account` instruction. `create_account` requires the target account to have exactly 0 lamports; if the account already has any lamports (even 1), the instruction fails and the entire transaction reverts.

Because the PDA address is deterministic and publicly computable from the token identifier, an attacker can:

1. Observe a `LogMetadata` event on NEAR (or any source chain) for a token that has not yet been deployed on Solana.
2. Compute the Solana mint PDA: `PDA([WRAPPED_MINT_SEED, token.to_hashed_bytes()])`.
3. Issue a System Program `transfer` to send lamports to that PDA address. This creates a system-owned account at the address with non-zero lamports.
4. When the relayer subsequently submits `deploy_token`, Anchor's `init` constraint calls `create_account`, which fails because the account already has lamports.

There is no `init_if_needed`, no try/catch, and no fallback path in the instruction. The transaction reverts unconditionally. Because the attacker-funded account persists (it is rent-exempt if enough lamports are sent), every future `deploy_token` attempt for that token will also fail. The token is permanently undeployable on Solana under the current code. [2](#0-1) 

The `initialize_token_metadata` call that follows (creating Metaplex metadata) is never reached: [3](#0-2) 

### Impact Explanation

Any token targeted by the attacker can never be deployed on Solana. Users who have initiated or will initiate cross-chain transfers to Solana for that token will find the bridge permanently unable to finalize those transfers, resulting in irrecoverable lock of their funds in the source-chain bridge contract. This matches **Critical — Permanent freezing / irrecoverable lock of user funds in bridge flows**.

### Likelihood Explanation

- The PDA address is deterministic and computable by anyone given the token identifier, which is public from on-chain events.
- The attacker does not need to frontrun in the traditional mempool sense; they only need to act at any point before the relayer submits `deploy_token` to Solana.
- The cost to the attacker is the lamports sent (a few thousand lamports, well under $1), and those lamports are effectively burned (locked in an account the bridge can never reclaim).
- A single attacker transaction permanently blocks the token for all future users.

Likelihood: **High**.

### Recommendation

Replace the `init` constraint with `init_if_needed` (Anchor feature `init-if-needed`) and add a discriminator/owner check to verify the account is not already a valid mint owned by the Token Program. Alternatively, after a failed `create_account`, detect the pre-existing lamports and use `transfer` + `allocate` + `assign` to take ownership of the account before initializing it as a mint. A simpler mitigation is to verify at the start of the instruction that the account has 0 lamports and is uninitialized, and if it already is a correctly initialized mint (owned by Token Program with the right authority), skip creation and proceed directly to metadata initialization.

### Proof of Concept

```
1. Token "eth:0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48" (USDC) emits LogMetadata on NEAR.

2. Attacker computes Solana PDA:
   seed = sha256("eth:0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48")  // to_hashed_bytes()
   mint_pda = find_program_address([WRAPPED_MINT_SEED, seed], PROGRAM_ID)

3. Attacker sends 2_039_280 lamports (rent-exempt minimum for a Mint account)
   to mint_pda via System Program transfer.

4. Relayer submits deploy_token with valid MPC signature.
   Anchor's `init` calls create_account(payer, mint_pda, lamports, 82, TOKEN_PROGRAM_ID).
   System Program returns error: "account already has lamports" → transaction fails.

5. Every subsequent deploy_token attempt for this token fails identically.
   USDC is permanently undeployable on Solana via this bridge.
```

### Citations

**File:** solana/programs/bridge_token_factory/src/instructions/user/deploy_token.rs (L37-71)
```rust
#[derive(Accounts)]
#[instruction(data: SignedPayload<DeployTokenPayload>)]
pub struct DeployToken<'info> {
    #[account(
        seeds = [AUTHORITY_SEED],
        bump = common.config.bumps.authority,
    )]
    pub authority: SystemAccount<'info>,
    #[account(
        init,
        payer = common.payer,
        seeds = [WRAPPED_MINT_SEED, data.payload.token.to_hashed_bytes().as_ref()],
        bump,
        mint::decimals = std::cmp::min(MAX_ALLOWED_DECIMALS, data.payload.decimals),
        mint::authority = authority,
    )]
    pub mint: Box<Account<'info, Mint>>,
    #[account(
        mut,
        seeds = [
            METADATA_SEED,
            MetaplexID.as_ref(),
            &mint.key().to_bytes(),
        ],
        bump,
        seeds::program = MetaplexID,
    )]
    pub metadata: SystemAccount<'info>,

    pub common: WormholeCPI<'info>,

    pub system_program: Program<'info, System>,
    pub token_program: Program<'info, Token>,
    pub token_metadata_program: Program<'info, Metaplex>,
}
```

**File:** solana/programs/bridge_token_factory/src/instructions/user/deploy_token.rs (L73-121)
```rust
impl DeployToken<'_> {
    pub fn initialize_token_metadata(&self, mut metadata: DeployTokenPayload) -> Result<()> {
        let bump = &[self.common.config.bumps.authority];
        let signer_seeds = &[&[AUTHORITY_SEED, bump][..]];
        let origin_decimals = metadata.decimals;
        metadata.decimals = std::cmp::min(MAX_ALLOWED_DECIMALS, metadata.decimals);

        let cpi_accounts = CreateMetadataAccountsV3 {
            payer: self.common.payer.to_account_info(),
            update_authority: self.authority.to_account_info(),
            mint: self.mint.to_account_info(),
            metadata: self.metadata.to_account_info(),
            mint_authority: self.authority.to_account_info(),
            system_program: self.system_program.to_account_info(),
            rent: self.common.rent.to_account_info(),
        };
        let cpi_ctx = CpiContext::new_with_signer(
            self.token_metadata_program.to_account_info(),
            cpi_accounts,
            signer_seeds,
        );
        create_metadata_accounts_v3(
            cpi_ctx,
            DataV2 {
                name: metadata.name,
                symbol: metadata.symbol,
                uri: String::new(),
                seller_fee_basis_points: 0,
                creators: None,
                collection: None,
                uses: None,
            },
            true, // TODO: Maybe better to make it immutable
            true,
            None,
        )?;

        let payload = DeployTokenResponse {
            token: metadata.token,
            solana_mint: self.mint.key(),
            decimals: metadata.decimals,
            origin_decimals,
        }
        .serialize_for_near(())?;

        self.common.post_message(payload)?;

        Ok(())
    }
```

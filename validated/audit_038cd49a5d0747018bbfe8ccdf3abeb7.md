### Title
Permanent DoS on NEAR Token Deployment via Lamport Griefing of `WRAPPED_MINT_SEED` PDA — (`solana/programs/bridge_token_factory/src/instructions/user/deploy_token.rs`)

---

### Summary

The `deploy_token` instruction uses Anchor's `init` constraint to create the wrapped mint at a deterministic PDA. An unprivileged attacker can permanently block deployment of any NEAR token by sending a trivial amount of lamports to the PDA address before the legitimate call, causing Solana's System Program to reject the subsequent `create_account` CPI with "account already in use."

---

### Finding Description

The `DeployToken` account struct constrains the mint with `init`: [1](#0-0) 

The PDA address is fully deterministic and publicly computable:

```
find_program_address(
    [b"wrapped_mint", token.to_hashed_bytes()],
    bridge_program_id
)
``` [2](#0-1) [3](#0-2) 

The `token` string is embedded in the signed payload and is visible on-chain (or derivable from NEAR state) before `deploy_token` is executed.

**Attack path:**

1. Attacker observes the `token` string (e.g., from a pending transaction or NEAR contract state).
2. Attacker computes the PDA offline.
3. Attacker calls `SystemProgram::transfer` to send ≥1 lamport to the PDA address. The System Program does not require the recipient to sign a transfer, so this works for any address including off-curve PDAs.
4. The PDA now exists as a System-Program-owned account with lamports but zero data.
5. When the legitimate `deploy_token` is submitted, Anchor's `init` calls `create_account` via CPI. Solana's System Program rejects this with "account already in use" because the account already has lamports.
6. Every subsequent retry of `deploy_token` for that token string fails identically — the block is permanent.

**Why there is no recovery:** The bridge program has no instruction that signs for the PDA (via `invoke_signed`) to drain its lamports back. Since the account is owned by the System Program (not the bridge program), no external party can close it either. A program upgrade would be required to add a recovery path. [4](#0-3) 

The question's framing of "pre-initializing with a different mint authority" is slightly imprecise — an attacker cannot create a full SPL mint at a PDA address because they cannot sign for it. The simpler lamport-transfer attack is sufficient and achieves the same permanent block.

---

### Impact Explanation

Any NEAR token can be permanently prevented from ever being deployed on Solana. Once blocked, no wrapped mint exists for that token, so `finalize_transfer` (which requires a valid mint) can never succeed for it either. Users who have locked assets on NEAR expecting a Solana-side deployment have no recourse without a program upgrade. This is an irrecoverable loss of bridge functionality for the targeted token(s).

---

### Likelihood Explanation

- **Cost to attacker:** ~0.002 SOL (rent-exempt minimum for a zero-data account) per token blocked.
- **Skill required:** None beyond basic Solana transaction construction.
- **Detection window:** The token string is public; the attacker can act any time before the first `deploy_token` call, including by monitoring the mempool.
- **Scalability:** The attacker can block every future NEAR token deployment in a single sweep for negligible cost.

---

### Recommendation

Replace the bare `init` constraint with logic that handles a pre-funded-but-uninitialized account. The standard Solana pattern is:

1. Use `init_if_needed` only if the account is truly uninitialized (no discriminator), combined with an explicit check that the mint authority matches the bridge authority if the account already exists.
2. Alternatively, add an admin instruction that uses `invoke_signed` with the PDA seeds to drain lamports from a griefed PDA back to the admin, then retry deployment.
3. The most robust fix is to add a pre-flight check: if the account has lamports but no data and is owned by System Program, use `invoke_signed` to call `SystemProgram::assign` + `SystemProgram::allocate` to take ownership before `create_account`.

---

### Proof of Concept

```rust
// 1. Compute the PDA
let token = "usdt.tether-token.near";
let hash = token_to_hashed_bytes(token); // mirrors StringExt::to_hashed_bytes
let (pda, _bump) = Pubkey::find_program_address(
    &[b"wrapped_mint", &hash],
    &bridge_program_id,
);

// 2. Send 1 lamport to the PDA (no signature from PDA required)
let ix = system_instruction::transfer(&attacker_pubkey, &pda, 1);
send_transaction(&[ix], &[&attacker_keypair]);

// 3. Submit legitimate deploy_token — will fail with AccountAlreadyInUse
let result = deploy_token(signed_payload_for(token));
assert!(matches!(result, Err(e) if e contains "already in use"));

// 4. Retry indefinitely — same error every time
```

### Citations

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

**File:** solana/programs/bridge_token_factory/src/constants.rs (L19-19)
```rust
pub const WRAPPED_MINT_SEED: &[u8] = b"wrapped_mint";
```

**File:** solana/programs/bridge_token_factory/src/lib.rs (L66-76)
```rust
    pub fn deploy_token(
        ctx: Context<DeployToken>,
        data: SignedPayload<DeployTokenPayload>,
    ) -> Result<()> {
        msg!("Deploying token");

        data.verify_signature((), &ctx.accounts.common.config.derived_near_bridge_address)?;
        ctx.accounts.initialize_token_metadata(data.payload)?;

        Ok(())
    }
```

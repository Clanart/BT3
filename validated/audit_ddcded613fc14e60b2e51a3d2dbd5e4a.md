### Title
Pending NEAR→EVM Transfers for Deployed Tokens Become Permanently Unresolvable After `migrate_deployed_token` — (File: near/omni-bridge/src/lib.rs)

### Summary
When a user initiates a NEAR→EVM transfer of a deployed (bridged) token, the tokens are burned immediately and a pending transfer entry is stored. If the DAO subsequently calls `migrate_deployed_token` before the transfer is signed, the `sign_transfer` call permanently panics because the old token's address mapping is removed. The burned tokens are irrecoverable and no cancellation path exists.

### Finding Description

**Transfer initiation flow (NEAR → EVM for a deployed token):**

In `init_transfer_internal`, when the token is a deployed (bridged) token, it is burned immediately via `burn_tokens_if_needed`, and a pending transfer entry is stored in `pending_transfers` with `token = OmniAddress::Near(old_token)`: [1](#0-0) 

**`migrate_deployed_token` removes the old mapping:**

`migrate_deployed_token` removes `token_id_to_address[(origin_chain, old_token)]` and replaces it with `token_id_to_address[(origin_chain, new_token)]`: [2](#0-1) 

**`sign_transfer` panics for the pending transfer:**

`sign_transfer` resolves the destination token address by calling `get_token_address(destination_chain, old_token)`, which reads `token_id_to_address[(destination_chain, old_token)]`. After migration this entry no longer exists, so the call panics unconditionally: [3](#0-2) 

`get_token_address` is a direct lookup with no fallback to `migrated_tokens`: [4](#0-3) 

The pending transfer entry in `pending_transfers` still holds `OmniAddress::Near(old_token)` and is never updated. There is no cancel-transfer or refund path for the source-side pending transfer. `swap_migrated_token` only helps users who still hold old tokens in their wallet — it cannot recover already-burned tokens: [5](#0-4) 

### Impact Explanation

**Critical — permanent freezing / irrecoverable lock of user funds.**

For every deployed (bridged) token transfer that is in-flight (burned on NEAR, pending `sign_transfer`) at the moment `migrate_deployed_token` is executed:

- The tokens are already burned and cannot be re-minted.
- `sign_transfer` panics permanently for those transfer IDs.
- No built-in cancellation or refund mechanism exists for source-side pending transfers.
- The user loses the full transferred amount with no recovery path.

### Likelihood Explanation

**Low.** The scenario requires:
1. A user to have an in-flight NEAR→EVM transfer for a deployed token.
2. The DAO to call `migrate_deployed_token` for that token before the transfer is signed.

Token migrations are planned, infrequent operations. However, because `migrate_deployed_token` does not check for or drain pending transfers before removing the old mapping, any overlap — even a brief one — causes permanent fund loss. The DAO action is legitimate and expected; the vulnerability is a design gap in the migration procedure.

### Recommendation

Before removing the old token mapping in `migrate_deployed_token`, either:

1. **Reject migration if pending transfers exist** — add a check that `pending_transfers` contains no entries referencing `old_token` before proceeding.
2. **Rewrite pending transfer entries** — iterate over pending transfers for `old_token` and update their `token` field to `OmniAddress::Near(new_token)` atomically with the mapping swap.
3. **Add a fallback in `sign_transfer`** — after `get_token_address` returns `None`, check `migrated_tokens` to see if `old_token` has been migrated and resolve via `new_token`.

Option 3 is the least invasive and mirrors the fix pattern from the external report (wrapping the failing call in a try/catch and redirecting to a recovery path).

### Proof of Concept

1. Deploy a bridged ERC-20 token on EVM; deploy its NEAR counterpart via `deploy_token`. Call it `old.factory.bridge.near`.
2. User calls `ft_transfer_call` on `old.factory.bridge.near` with destination `ChainKind::Eth`. `init_transfer_internal` burns the tokens and stores a pending transfer with `token = OmniAddress::Near("old.factory.bridge.near")`.
3. DAO calls `migrate_deployed_token(ChainKind::Eth, "old.factory.bridge.near", "new.factory.bridge.near")`. `token_id_to_address[(Eth, "old.factory.bridge.near")]` is deleted.
4. Relayer calls `sign_transfer` for the pending transfer ID. Execution reaches:
   ```rust
   self.get_token_address(ChainKind::Eth, "old.factory.bridge.near")
   // → token_id_to_address.get(&(Eth, "old.factory.bridge.near")) → None
   // → env::panic_str("ERR_FAILED_TO_GET_TOKEN_ADDRESS")
   ```
5. `sign_transfer` panics. The pending transfer can never be signed. The burned tokens are permanently lost.

### Citations

**File:** near/omni-bridge/src/lib.rs (L462-469)
```rust
        let token_address = self
            .get_token_address(
                transfer_message.get_destination_chain(),
                self.get_token_id(&transfer_message.token),
            )
            .unwrap_or_else(|| {
                env::panic_str(BridgeError::FailedToGetTokenAddress.to_string().as_str())
            });
```

**File:** near/omni-bridge/src/lib.rs (L1360-1366)
```rust
    pub fn get_token_address(
        &self,
        chain_kind: ChainKind,
        token: AccountId,
    ) -> Option<OmniAddress> {
        self.token_id_to_address.get(&(chain_kind, token))
    }
```

**File:** near/omni-bridge/src/lib.rs (L1628-1642)
```rust
        let origin_address = self
            .token_id_to_address
            .remove(&(origin_chain, old_token.clone()))
            .near_expect(BridgeError::FailedToGetTokenAddress);

        require!(
            self.token_id_to_address
                .insert(&(origin_chain, new_token.clone()), &origin_address)
                .is_none(),
            BridgeError::TokenExists.as_ref()
        );

        self.token_address_to_id
            .insert(&origin_address, &new_token)
            .near_expect(BridgeError::ExpectedToOverwriteTokenAddress);
```

**File:** near/omni-bridge/src/lib.rs (L1850-1857)
```rust
        if let OmniAddress::Near(token_id) = transfer_message.token.clone() {
            self.burn_tokens_if_needed(token_id.clone(), transfer_message.amount);

            self.lock_tokens_if_needed(
                transfer_message.get_destination_chain(),
                &token_id,
                transfer_message.amount.0,
            );
```

**File:** near/omni-bridge/src/lib.rs (L2738-2753)
```rust
    fn swap_migrated_token(
        &mut self,
        sender_id: AccountId,
        old_token: AccountId,
        amount: U128,
    ) -> Promise {
        let new_token = self
            .migrated_tokens
            .get(&old_token)
            .near_expect(BridgeError::TokenNotMigrated);

        let burn = ext_token::ext(old_token).burn(amount);
        let mint = ext_token::ext(new_token).mint(sender_id, amount, None);

        burn.and(mint)
    }
```

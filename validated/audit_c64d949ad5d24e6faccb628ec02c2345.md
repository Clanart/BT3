### Title
Silent Burn Failure in `resolve_fast_transfer` Causes Double-Mint of Deployed Bridge Tokens - (File: `near/omni-bridge/src/lib.rs`)

### Summary

In the fast-transfer-to-NEAR flow for deployed bridge tokens, `resolve_fast_transfer` attempts to burn `amount_without_fee` from the bridge contract's own balance to cancel the mint that was just issued to the recipient. However, the bridge contract holds zero deployed tokens after minting directly to the recipient, so the burn panics internally but is silently swallowed by `.detach()`. When the canonical `fin_transfer` proof later arrives, the bridge mints `amount_without_fee` a second time to the relayer, producing an unbacked token supply inflation equal to the full transfer amount.

### Finding Description

**Step 1 — Fast transfer mints tokens to recipient.**

In `fast_fin_transfer_to_near_callback`, `send_tokens` is called for a deployed bridge token with an empty message:

```rust
self.send_tokens(
    fast_transfer.token_id.clone(),
    recipient,
    amount_without_fee,
    &fast_transfer.msg,   // empty → msg = None
)
``` [1](#0-0) 

Inside `send_tokens`, for a deployed token with `msg = None`, `OmniToken::mint` deposits directly into the recipient's account:

```rust
self.token.internal_deposit(&account_id, amount.into());
``` [2](#0-1) 

After this call the bridge contract holds **zero** units of the deployed token.

**Step 2 — `resolve_fast_transfer` tries to burn from the bridge — silently fails.**

Immediately after `send_tokens`, `resolve_fast_transfer` is chained:

```rust
// Burn the tokens to ensure the locked tokens are not double-minted
self.burn_tokens_if_needed(token_id.clone(), amount);
``` [3](#0-2) 

`burn_tokens_if_needed` issues a fire-and-forget cross-contract call:

```rust
ext_token::ext(token)
    .with_static_gas(BURN_TOKEN_GAS)
    .burn(amount)
    .detach();          // ← failure is silently discarded
``` [4](#0-3) 

`OmniToken::burn` withdraws from `env::predecessor_account_id()` — the bridge contract:

```rust
self.token
    .internal_withdraw(&env::predecessor_account_id(), amount.into());
``` [5](#0-4) 

Because the bridge holds zero tokens, `internal_withdraw` panics. The `.detach()` call means this panic is never observed by the bridge; the fast-transfer record remains in `fast_transfers` as if the burn succeeded.

**Step 3 — Canonical `fin_transfer` mints tokens a second time to the relayer.**

When the on-chain proof for the same transfer is submitted via `fin_transfer`, `process_fin_transfer_to_near` detects the existing fast-transfer record and redirects the recipient to the relayer:

```rust
Some(status) => {
    self.remove_fast_transfer(&fast_transfer.id());
    (status.relayer.clone(), String::new(), status.relayer)
}
``` [6](#0-5) 

`send_tokens` is then called again with the relayer as recipient, minting `amount_without_fee` a second time:

```rust
self.send_tokens(
    token.clone(),
    recipient,          // = relayer
    U128(transfer_message.amount_without_fee()...),
    &msg,               // empty
)
``` [7](#0-6) 

**Net result:** `amount_without_fee` tokens are minted to the recipient (fast transfer) and `amount_without_fee` tokens are minted to the relayer (fin transfer), for a total supply inflation of `2 × amount_without_fee` against a single cross-chain transfer that locked only `amount` on the origin chain.

### Impact Explanation

This is an unauthorized mint of deployed bridge tokens. Every fast-transfer-to-NEAR that involves a deployed (bridge-issued) token inflates the token supply by `amount_without_fee`. The relayer receives that amount for free, and the bridge's collateralization invariant (tokens on NEAR ≤ tokens locked on origin chain) is permanently broken by the same amount. This maps directly to the **Critical** impact class: *Direct theft or unauthorized mint of bridged assets*.

### Likelihood Explanation

The fast-transfer path is a core, publicly accessible relayer feature. Any active relayer processing transfers of deployed bridge tokens (the common case for assets originating on EVM chains) will trigger this path on every fast transfer. No special permissions or unusual conditions are required beyond being a trusted relayer, which is a role that multiple parties hold.

### Recommendation

The burn-to-cancel-mint design is structurally unsound for deployed tokens because the bridge never holds the minted tokens. Two correct alternatives:

1. **Remove the burn from `resolve_fast_transfer`** and instead track that the fast transfer was funded by a mint. In `process_fin_transfer_to_near`, when a fast-transfer record exists, skip `send_tokens` entirely (the relayer was already reimbursed by the initial mint) and only handle fee distribution.

2. **Change `send_tokens` for the fast-transfer path** so that for deployed tokens the bridge mints to itself first, then transfers to the recipient. On success the bridge holds zero tokens (correct). On failure the bridge holds the tokens and the burn in `resolve_fast_transfer` succeeds. This matches the `msg`-non-empty branch that already works correctly.

### Proof of Concept

```
1. Relayer (trusted) calls ft_on_transfer → fast_fin_transfer for a
   deployed bridge token T, amount = 1000, fee = 10, recipient = alice.near.

2. fast_fin_transfer_to_near_callback fires:
   - send_tokens(T, alice, 990, "") → OmniToken::mint(alice, 990, None)
     → alice.balance[T] += 990; bridge.balance[T] = 0
   - resolve_fast_transfer(T, id, 990, false):
     - burn_tokens_if_needed(T, 990) → ext T.burn(990).detach()
       → OmniToken::burn: internal_withdraw(bridge, 990) → PANIC (bridge has 0)
       → panic silently discarded by .detach()
     - fast_transfer record remains in storage

3. Relayer submits fin_transfer proof for the same origin transfer.

4. fin_transfer_callback → process_fin_transfer_to_near:
   - fast_transfer record found → recipient = relayer
   - send_tokens(T, relayer, 990, "") → OmniToken::mint(relayer, 990, None)
     → relayer.balance[T] += 990

5. Final state:
   - alice.balance[T]  = 990  (correct recipient)
   - relayer.balance[T] = 990  (free tokens)
   - T.totalSupply     = 1980  (should be 990)
   - Origin chain locked = 1000 (unchanged)
   - Bridge is under-collateralized by 990 T tokens.
```

### Citations

**File:** near/omni-bridge/src/lib.rs (L877-892)
```rust
        self.send_tokens(
            fast_transfer.token_id.clone(),
            recipient,
            amount_without_fee,
            &fast_transfer.msg,
        )
        .then(
            Self::ext(env::current_account_id())
                .with_static_gas(RESOLVE_FAST_TRANSFER_GAS)
                .resolve_fast_transfer(
                    &fast_transfer.token_id,
                    &fast_transfer.id(),
                    amount_without_fee,
                    !fast_transfer.msg.is_empty(),
                ),
        )
```

**File:** near/omni-bridge/src/lib.rs (L903-904)
```rust
        // Burn the tokens to ensure the locked tokens are not double-minted
        self.burn_tokens_if_needed(token_id.clone(), amount);
```

**File:** near/omni-bridge/src/lib.rs (L1806-1812)
```rust
    fn burn_tokens_if_needed(&self, token: AccountId, amount: U128) {
        if self.is_deployed_token(&token) {
            ext_token::ext(token)
                .with_static_gas(BURN_TOKEN_GAS)
                .burn(amount)
                .detach();
        }
```

**File:** near/omni-bridge/src/lib.rs (L1888-1895)
```rust
        let (recipient, msg, fee_recipient) = match fast_transfer_status {
            Some(status) => {
                require!(
                    !status.finalised,
                    BridgeError::FastTransferAlreadyFinalised.as_ref()
                );
                self.remove_fast_transfer(&fast_transfer.id());
                (status.relayer.clone(), String::new(), status.relayer)
```

**File:** near/omni-bridge/src/lib.rs (L1957-1966)
```rust
        self.send_tokens(
            token.clone(),
            recipient,
            U128(
                transfer_message
                    .amount_without_fee()
                    .near_expect(BridgeError::InvalidFee),
            ),
            &msg,
        )
```

**File:** near/omni-token/src/lib.rs (L140-143)
```rust
        } else {
            self.token.internal_deposit(&account_id, amount.into());
            PromiseOrValue::Value(amount)
        }
```

**File:** near/omni-token/src/lib.rs (L149-150)
```rust
        self.token
            .internal_withdraw(&env::predecessor_account_id(), amount.into());
```

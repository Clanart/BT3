### Title
Unchecked Detached `burn` Promise in `burn_tokens_if_needed` Enables Unbacked Supply Minting — (`near/omni-bridge/src/lib.rs`)

---

### Summary

`burn_tokens_if_needed` fires a cross-contract `burn` call with `.detach()`, meaning its result is never observed. If the burn promise fails (e.g., due to gas exhaustion with only 3 TGas allocated), the bridge still emits `InitTransferEvent` and stores the transfer message, allowing relayers to mint tokens on the destination chain while the source tokens remain unburned in the bridge's balance — creating unbacked supply.

---

### Finding Description

`burn_tokens_if_needed` is defined as: [1](#0-0) 

```rust
fn burn_tokens_if_needed(&self, token: AccountId, amount: U128) {
    if self.is_deployed_token(&token) {
        ext_token::ext(token)
            .with_static_gas(BURN_TOKEN_GAS)
            .burn(amount)
            .detach();   // ← result never checked
    }
}
```

`BURN_TOKEN_GAS` is only **3 TGas**: [2](#0-1) 

The `burn` function on the token contract performs `assert_controller` (storage read) and `internal_withdraw` (storage read + write): [3](#0-2) 

3 TGas is extremely tight for a cross-contract call that includes storage operations. The NEAR protocol's base cross-contract call overhead alone consumes ~2.4 TGas, leaving almost nothing for the actual `burn` execution.

`burn_tokens_if_needed` is called in three critical paths, all before the transfer is committed:

**1. `init_transfer_internal` — normal bridge-out flow:** [4](#0-3) 

The burn is fired at line 1851, but `InitTransferEvent` is emitted unconditionally at line 1863 and the function returns `U128(0)` (keep tokens) regardless of whether the burn succeeded.

**2. `fast_fin_transfer_to_other_chain` — fast transfer relayer path:** [5](#0-4) 

**3. `resolve_fast_transfer` — fast transfer resolution:** [6](#0-5) 

In all three cases, if the detached burn promise fails, the bridge proceeds as if the burn succeeded.

---

### Impact Explanation

For deployed tokens (tokens deployed by the bridge on NEAR), the bridge is the controller and the only authorized caller of `burn`. The flow via `ft_transfer_call` transfers tokens into the bridge's balance on the token contract, then the bridge is supposed to burn them before emitting the cross-chain transfer event.

If the burn fails silently:
- The tokens remain in the bridge's balance on the NEAR token contract (not destroyed).
- The `InitTransferEvent` is already emitted and the transfer message is stored.
- A relayer submits the proof to the destination chain, which mints the full amount for the recipient.
- Result: tokens exist both on NEAR (in bridge's balance) **and** on the destination chain — unbacked supply / unauthorized mint.

This matches the **Critical** impact class: *Direct unauthorized mint of bridged assets*.

---

### Likelihood Explanation

The burn call is allocated only 3 TGas. The NEAR cross-contract call base cost is ~2.4 TGas, leaving ~0.6 TGas for `assert_controller` (storage read) and `internal_withdraw` (storage read + write). Under normal conditions this may succeed, but:

- Any increase in storage cost, contract complexity after an upgrade, or gas pricing change can push the burn over the limit.
- Since the promise is detached, **any** failure — gas exhaustion, panic in `internal_withdraw` (e.g., balance underflow due to a race condition), or a future token contract upgrade — is silently ignored with no revert and no alert.
- The CLAUDE.md itself flags this pattern: *"Check .detach() usage: Detached promises should only be used for non-critical operations"* — yet `burn_tokens_if_needed` is used in the most critical accounting path. [7](#0-6) 

---

### Recommendation

Replace the detached burn with a chained promise that checks the result before emitting `InitTransferEvent`. Specifically:

1. In `init_transfer_internal`, do **not** emit `InitTransferEvent` inline. Instead, return a `Promise` that chains the burn → callback, and only emit the event (and return `U128(0)` to keep tokens) inside the callback after confirming the burn succeeded.
2. If the burn fails in the callback, revert by returning the full `transfer_message.amount` (refund the user) and removing the transfer message.
3. Apply the same fix to `fast_fin_transfer_to_other_chain` and `resolve_fast_transfer`.
4. Increase `BURN_TOKEN_GAS` to a safe margin (e.g., 5–10 TGas) to account for storage operation costs.

---

### Proof of Concept

1. Deploy a bridge-deployed token (present in `deployed_tokens`).
2. Call `ft_transfer_call` on the token contract, sending `N` tokens to the bridge with an `InitTransfer` message targeting an EVM recipient.
3. Inside `ft_on_transfer` → `init_transfer_internal`:
   - `burn_tokens_if_needed` fires a detached burn with 3 TGas.
   - If the burn runs out of gas (e.g., by crafting a scenario where the token contract's storage operations consume the full 3 TGas), the burn promise fails silently.
   - `InitTransferEvent` is emitted with the full amount.
   - `ft_on_transfer` returns `U128(0)` — tokens stay in bridge's balance.
4. A relayer picks up `InitTransferEvent` and submits proof to the EVM bridge.
5. EVM bridge mints `N` tokens for the recipient.
6. The bridge now holds `N` tokens on NEAR **and** `N` tokens have been minted on EVM — unbacked supply created. [8](#0-7) [9](#0-8) [2](#0-1)

### Citations

**File:** near/omni-bridge/src/lib.rs (L72-72)
```rust
const BURN_TOKEN_GAS: Gas = Gas::from_tgas(3);
```

**File:** near/omni-bridge/src/lib.rs (L903-904)
```rust
        // Burn the tokens to ensure the locked tokens are not double-minted
        self.burn_tokens_if_needed(token_id.clone(), amount);
```

**File:** near/omni-bridge/src/lib.rs (L932-933)
```rust
        self.burn_tokens_if_needed(fast_transfer.token_id.clone(), amount_without_fee.into());

```

**File:** near/omni-bridge/src/lib.rs (L1806-1813)
```rust
    fn burn_tokens_if_needed(&self, token: AccountId, amount: U128) {
        if self.is_deployed_token(&token) {
            ext_token::ext(token)
                .with_static_gas(BURN_TOKEN_GAS)
                .burn(amount)
                .detach();
        }
    }
```

**File:** near/omni-bridge/src/lib.rs (L1850-1864)
```rust
        if let OmniAddress::Near(token_id) = transfer_message.token.clone() {
            self.burn_tokens_if_needed(token_id.clone(), transfer_message.amount);

            self.lock_tokens_if_needed(
                transfer_message.get_destination_chain(),
                &token_id,
                transfer_message.amount.0,
            );
        } else {
            self.remove_transfer_message_without_refund(transfer_message.get_transfer_id());
            return transfer_message.amount;
        }

        env::log_str(&OmniBridgeEvent::InitTransferEvent { transfer_message }.to_log_string());
        U128(0)
```

**File:** near/omni-token/src/lib.rs (L146-151)
```rust
    fn burn(&mut self, amount: U128) {
        self.assert_controller();

        self.token
            .internal_withdraw(&env::predecessor_account_id(), amount.into());
    }
```

**File:** near/CLAUDE.md (L228-228)
```markdown
4. **Check .detach() usage**: Detached promises should only be used for non-critical operations
```

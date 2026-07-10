### Title
Silent Failure of Token Burn via `.detach()` Breaks Bridge Collateralization — (`near/omni-bridge/src/lib.rs`)

---

### Summary

`burn_tokens_if_needed` fires the token burn promise with `.detach()`, discarding the result. If the burn fails for any reason, the failure is silently swallowed, the transfer is still committed and logged, and the destination chain relayer finalizes the transfer — minting tokens there — while the source tokens remain unburned in the bridge contract. This breaks the 1:1 collateralization invariant of the bridge.

---

### Finding Description

In `near/omni-bridge/src/lib.rs`, the helper `burn_tokens_if_needed` is:

```rust
fn burn_tokens_if_needed(&self, token: AccountId, amount: U128) {
    if self.is_deployed_token(&token) {
        ext_token::ext(token)
            .with_static_gas(BURN_TOKEN_GAS)
            .burn(amount)
            .detach();   // ← result is never observed
    }
}
``` [1](#0-0) 

`.detach()` is the NEAR equivalent of an unchecked low-level `.call{value:...}("")` in Solidity: the promise is scheduled but its success or failure is never inspected. If the `burn` cross-contract call fails (e.g., due to insufficient static gas allocation `BURN_TOKEN_GAS`, a transient token-contract error, or any other revert), NEAR does **not** roll back the state changes already made in the current call frame.

`burn_tokens_if_needed` is called unconditionally inside `init_transfer_internal`:

```rust
if let OmniAddress::Near(token_id) = transfer_message.token.clone() {
    self.burn_tokens_if_needed(token_id.clone(), transfer_message.amount);
    self.lock_tokens_if_needed(
        transfer_message.get_destination_chain(),
        &token_id,
        transfer_message.amount.0,
    );
}
// ...
env::log_str(&OmniBridgeEvent::InitTransferEvent { transfer_message }.to_log_string());
U128(0)
``` [2](#0-1) 

`init_transfer_internal` returns `U128(0)` (meaning "all tokens consumed, no refund") regardless of whether the burn succeeded. The `InitTransferEvent` is emitted, the `locked_tokens` counter for the destination chain is incremented, and the transfer is fully committed to state — all before the burn promise resolves.

The entry path for an unprivileged user is through `ft_on_transfer` (the NEP-141 receiver callback), which any user can trigger by calling `ft_transfer_call` on a registered bridge-deployed token and directing it to the bridge contract.

---

### Impact Explanation

When the burn fails silently:

1. The user's bridge-deployed NEAR tokens are transferred to the bridge contract (via `ft_transfer_call`) — the user loses them from their wallet.
2. The `InitTransferEvent` is emitted on-chain; a relayer picks it up and finalizes the transfer on the destination chain, **minting** the corresponding tokens there.
3. The NEAR-side tokens are **not** burned — they remain held by the bridge contract.
4. The bridge's `locked_tokens` accounting is incremented for the destination chain, but the source tokens are not destroyed.

Result: tokens exist simultaneously on both chains. The bridge's 1:1 collateralization guarantee is broken. The excess NEAR-side tokens sitting in the bridge contract could be re-claimed through a subsequent finalization flow, enabling effective double-spend of the bridged asset.

This matches the allowed impact: **"Balance, decimal, fee, token-mapping, or accounting corruption that breaks bridge collateralization or misdirects value."**

---

### Likelihood Explanation

The burn can fail due to:

- **Insufficient static gas**: `BURN_TOKEN_GAS` is a fixed constant. If the token contract's `burn` implementation consumes more gas than allocated (e.g., after an upgrade), the call fails silently.
- **Token contract transient error**: Any panic or revert inside the deployed token's `burn` function (e.g., arithmetic overflow, access-control check, or storage issue) causes the promise to fail, which `.detach()` ignores.

The trigger is reachable by any holder of a bridge-deployed token with no privileged role required. The gas-exhaustion scenario in particular is realistic after any token contract upgrade that increases `burn` complexity.

---

### Recommendation

Replace the fire-and-forget `.detach()` with a proper callback that verifies the burn succeeded before committing the transfer state. The pattern used elsewhere in the codebase (e.g., `send_tokens(...).then(Self::ext(...).fin_transfer_send_tokens_callback(...))`) should be applied here:

```rust
fn burn_tokens_if_needed(&self, token: AccountId, amount: U128) -> Option<Promise> {
    if self.is_deployed_token(&token) {
        Some(
            ext_token::ext(token)
                .with_static_gas(BURN_TOKEN_GAS)
                .burn(amount)
        )
    } else {
        None
    }
}
```

`init_transfer_internal` should chain a callback on the returned promise and only emit `InitTransferEvent` and update `locked_tokens` after confirming the burn succeeded. If the burn fails, the callback should return the full amount so `ft_on_transfer` refunds the user.

---

### Proof of Concept

1. User holds bridge-deployed tokens on NEAR (e.g., a NEAR-side representation of an ERC-20 that originated on Ethereum).
2. User calls `ft_transfer_call(bridge_contract, amount, msg)` on the token contract.
3. Bridge's `ft_on_transfer` is invoked → `init_transfer_internal` is called.
4. `burn_tokens_if_needed` schedules `burn(amount).detach()`.
5. The burn promise fails (e.g., `BURN_TOKEN_GAS` is exhausted by the token contract).
6. NEAR does **not** revert `init_transfer_internal`'s state changes; `InitTransferEvent` is emitted and `locked_tokens` is incremented.
7. `ft_on_transfer` returns `U128(0)` → no refund to the user.
8. A relayer observes `InitTransferEvent` and calls `finTransfer` on the destination chain, minting tokens for the user there.
9. The NEAR-side tokens remain unburned in the bridge contract.
10. Both the destination-chain minted tokens and the unburned NEAR-side tokens now exist simultaneously — bridge collateralization is broken.

### Citations

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

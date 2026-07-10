### Title
Detached Burn Promise Result Not Verified in `init_transfer_internal` Allows Unbacked Cross-Chain Token Release - (File: near/omni-bridge/src/lib.rs)

### Summary
In `near/omni-bridge/src/lib.rs`, the `burn_tokens_if_needed` helper fires a cross-contract `burn` call with `.detach()`, meaning its success or failure is never observed. The `InitTransferEvent` is emitted in the same synchronous transaction regardless of whether the burn actually succeeds. If the burn fails silently, relayers process the event and release tokens on the destination chain while the NEAR-side tokens remain unburned in the bridge contract, creating unbacked supply.

### Finding Description

`burn_tokens_if_needed` is defined as:

```rust
// near/omni-bridge/src/lib.rs ~L1806-1813
fn burn_tokens_if_needed(&self, token: AccountId, amount: U128) {
    if self.is_deployed_token(&token) {
        ext_token::ext(token)
            .with_static_gas(BURN_TOKEN_GAS)
            .burn(amount)
            .detach();   // ← promise result is permanently discarded
    }
}
```

It is called from `init_transfer_internal`:

```rust
// near/omni-bridge/src/lib.rs ~L1850-1863
if let OmniAddress::Near(token_id) = transfer_message.token.clone() {
    self.burn_tokens_if_needed(token_id.clone(), transfer_message.amount);
    self.lock_tokens_if_needed(...);
} else { ... }

env::log_str(&OmniBridgeEvent::InitTransferEvent { transfer_message }.to_log_string());
U128(0)
```

Because NEAR cross-contract calls are asynchronous and `.detach()` severs the callback chain entirely, the `InitTransferEvent` log is written in the same receipt as the burn dispatch — before the burn promise ever resolves. There is no callback that checks `env::promise_result(0)` for the burn outcome. If the burn promise fails (e.g., insufficient `BURN_TOKEN_GAS`, token contract panic, or any other reason), the failure is silently swallowed and the event is already on-chain.

### Impact Explanation

This matches the **Critical** impact class: *Direct unauthorized mint of bridged assets / balance/accounting corruption that breaks bridge collateralization.*

Scenario:
1. A user calls `ft_transfer_call` on a deployed (bridged) token, sending `N` tokens to the bridge.
2. The bridge's `ft_on_transfer` triggers `init_transfer_internal`, which fires the detached `burn(N)` and immediately emits `InitTransferEvent`.
3. The burn promise fails (e.g., gas exhaustion under `BURN_TOKEN_GAS`).
4. Relayers observe the `InitTransferEvent` and finalize the transfer on the destination chain, minting/releasing `N` tokens there.
5. The `N` NEAR-side tokens were never burned — they remain locked in the bridge contract.
6. The destination chain now has `N` unbacked tokens in circulation; the bridge's collateral accounting is permanently broken.

### Likelihood Explanation

The `BURN_TOKEN_GAS` constant is a fixed static gas allocation. Any token contract whose `burn` implementation consumes more gas than this constant (e.g., due to storage operations, hooks, or future upgrades) will cause a silent failure. Additionally, any panic inside the token's `burn` function (e.g., access-control check, arithmetic overflow) produces a failed promise that is never observed. This is reachable by any unprivileged user who initiates a transfer of a deployed bridged token.

### Recommendation

Replace the fire-and-forget `.detach()` pattern with a chained callback that verifies the burn succeeded before the transfer is considered initiated:

```rust
fn burn_tokens_if_needed(&self, token: AccountId, amount: U128) -> Option<Promise> {
    if self.is_deployed_token(&token) {
        Some(
            ext_token::ext(token)
                .with_static_gas(BURN_TOKEN_GAS)
                .burn(amount),
        )
    } else {
        None
    }
}
```

Then in `init_transfer_internal`, chain a callback on the returned promise that checks `env::promise_result(0)` and panics (reverting the entire receipt) if the burn failed. The `InitTransferEvent` should only be emitted inside that callback after confirming success.

### Proof of Concept

1. Deploy a bridged token whose `burn` function consumes slightly more gas than `BURN_TOKEN_GAS`.
2. Call `ft_transfer_call` on that token, transferring `N` tokens to the bridge with a valid cross-chain transfer message.
3. The bridge emits `InitTransferEvent` in the same receipt.
4. The detached burn promise fails in a subsequent receipt — no callback observes this.
5. A relayer submits the `InitTransferEvent` proof to the destination chain and receives `N` tokens there.
6. The bridge contract still holds the original `N` tokens (unburned), while `N` new tokens exist on the destination chain — unbacked supply created. [1](#0-0) [2](#0-1)

### Citations

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

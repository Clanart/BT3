### Title
Detached Burn Promise in `init_transfer_internal` Allows Unbacked Cross-Chain Token Release — (File: `near/omni-bridge/src/lib.rs`)

### Summary
`burn_tokens_if_needed` fires the cross-contract `burn()` call with `.detach()`, meaning the bridge never checks whether the burn succeeded. Because the `InitTransferEvent` is emitted in the same synchronous transaction — before the burn executes — the MPC will sign and finalize the cross-chain transfer even if the burn later fails. This breaks the 1:1 collateral invariant: bridged tokens remain in the bridge's balance on NEAR while the origin chain releases the corresponding native tokens.

### Finding Description

In `init_transfer_internal`, the bridge:

1. Stores the transfer message.
2. Calls `burn_tokens_if_needed` — which schedules a detached (fire-and-forget) `burn()` cross-contract call.
3. Immediately emits `InitTransferEvent` in the same transaction.
4. Returns `U128(0)` to the calling token contract, confirming the transfer. [1](#0-0) 

```rust
fn burn_tokens_if_needed(&self, token: AccountId, amount: U128) {
    if self.is_deployed_token(&token) {
        ext_token::ext(token)
            .with_static_gas(BURN_TOKEN_GAS)  // only 3 TGas
            .burn(amount)
            .detach();                         // result never checked
    }
}
``` [2](#0-1) 

The burn promise is scheduled to execute in a **subsequent** NEAR transaction. The `InitTransferEvent` is already committed to the chain log in the **current** transaction. The MPC/relayer observes this event and begins processing the cross-chain transfer immediately, with no dependency on the burn outcome. [3](#0-2) 

The gas budget allocated for the burn is `BURN_TOKEN_GAS = Gas::from_tgas(3)` — 3 TGas. [4](#0-3) 

A cross-contract call in NEAR carries a base overhead that, combined with the token contract's execution, can exceed 3 TGas. If the burn runs out of gas or panics for any reason, NEAR silently drops the failed detached promise; the bridge contract is never notified.

The project's own security checklist acknowledges this risk: [5](#0-4) 

> "Check .detach() usage: Detached promises should only be used for non-critical operations"

The burn of bridged tokens during `init_transfer_internal` is a **critical** operation, not a non-critical one.

### Impact Explanation

When the burn fails silently:

- The bridged token (e.g., bridged USDC) is **not destroyed** on NEAR; it remains in the bridge contract's balance.
- The `InitTransferEvent` is already on-chain; the MPC signs the payload and the origin chain (EVM/Solana/StarkNet) **releases the native tokens** to the recipient.
- Result: 100 bridged USDC still exist on NEAR (held by bridge, not burned) while 100 native USDC are released on the origin chain.
- The bridge's collateral backing is broken: the total outstanding bridged supply on NEAR exceeds the locked/available supply on the origin chain.
- Repeated occurrences inflate the unbacked bridged supply, eventually making the bridge insolvent.

This matches the allowed impact: **"Balance, decimal, fee, token-mapping, or accounting corruption that breaks bridge collateralization or misdirects value."**

### Likelihood Explanation

The entry path is fully unprivileged: any holder of a bridge-deployed token calls `ft_transfer_call` on the token contract, which triggers `ft_on_transfer` → `init_transfer` → `init_transfer_internal` → `burn_tokens_if_needed`. [6](#0-5) 

The burn fails silently if:

1. **Gas exhaustion**: `BURN_TOKEN_GAS = 3 TGas` is below the typical cost of a cross-contract call plus NEP-141 burn execution. Any token contract whose `burn` function performs even minimal storage operations will exceed this budget.
2. **Token contract panic**: If the deployed token contract panics during burn (e.g., due to a bug or upgrade), the detached promise fails with no effect on bridge state.

Condition (1) is structurally present for every bridged token whose burn function costs more than 3 TGas, making this a systematic rather than edge-case risk.

### Recommendation

Replace the detached burn with a chained promise that checks the result before emitting `InitTransferEvent`. The event emission and transfer record must be gated on burn success:

```rust
fn burn_tokens_if_needed_checked(&self, token: AccountId, amount: U128) -> Option<Promise> {
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

In `init_transfer_internal`, chain the event emission and state finalization as a callback on the burn promise, reverting the transfer message if the burn fails. Alternatively, increase `BURN_TOKEN_GAS` to a safe margin (≥ 10 TGas) and add a callback that reverts the transfer on burn failure.

### Proof of Concept

1. Attacker holds 100 units of a bridge-deployed token (e.g., `bridged-usdc.bridge.near`) on NEAR.
2. Attacker calls `ft_transfer_call` on `bridged-usdc.bridge.near` with `amount=100`, `msg=<InitTransfer to Ethereum>`.
3. Token contract transfers 100 tokens to bridge, calls `ft_on_transfer` on bridge.
4. Bridge executes `init_transfer_internal`:
   - Stores transfer message.
   - Schedules `burn(100)` as a detached promise with 3 TGas.
   - Emits `InitTransferEvent{amount=100, recipient=<attacker_eth_address>}`.
   - Returns `U128(0)` — tokens stay in bridge.
5. The detached burn executes in the next NEAR block and fails (gas exhaustion or panic). Bridge is not notified.
6. MPC observes `InitTransferEvent`, signs the transfer payload.
7. Attacker (or relayer) submits the MPC signature to the EVM bridge; `finTransfer` releases 100 USDC to attacker's Ethereum address.
8. Result: 100 bridged USDC remain in bridge's NEAR balance (not burned); 100 native USDC released on Ethereum. Bridge collateral is short by 100 USDC. [7](#0-6) [8](#0-7) [4](#0-3)

### Citations

**File:** near/omni-bridge/src/lib.rs (L72-72)
```rust
const BURN_TOKEN_GAS: Gas = Gas::from_tgas(3);
```

**File:** near/omni-bridge/src/lib.rs (L252-263)
```rust
    #[pause(except(roles(Role::DAO, Role::UnrestrictedDeposit)))]
    pub fn ft_on_transfer(&mut self, sender_id: AccountId, amount: U128, msg: String) {
        let token_id = env::predecessor_account_id();
        let parsed_msg: BridgeOnTransferMsg = serde_json::from_str(&msg)
            .or_else(|_| serde_json::from_str(&msg).map(BridgeOnTransferMsg::InitTransfer))
            .near_expect(BridgeError::ParseMsg);

        // We can't trust sender_id to pay for storage as it can be spoofed.
        let signer_id = env::signer_account_id();
        let promise_or_promise_index_or_value = match parsed_msg {
            BridgeOnTransferMsg::InitTransfer(init_transfer_msg) => {
                self.init_transfer(sender_id, signer_id, token_id, amount, init_transfer_msg)
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

**File:** near/omni-bridge/src/lib.rs (L1850-1865)
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
    }
```

**File:** near/CLAUDE.md (L228-228)
```markdown
4. **Check .detach() usage**: Detached promises should only be used for non-critical operations
```

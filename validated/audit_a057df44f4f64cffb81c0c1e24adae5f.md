### Title
Unchecked Fire-and-Forget Token Transfer to Relayer Permanently Freezes Funds — (`File: near/omni-bridge/src/lib.rs`)

---

### Summary

In two locations within `near/omni-bridge/src/lib.rs`, the bridge calls `send_tokens()` to repay a fast-transfer relayer and immediately calls `.detach()` on the returned promise, discarding the result. State mutations (marking the fast transfer as finalised and decrementing locked-token accounting) are committed **before** the detached send. If the token transfer fails for any reason, the relayer's repayment tokens remain permanently locked inside the bridge contract with no recovery path.

---

### Finding Description

**Instance 1 — `process_fin_transfer_to_other_chain`**

When `fin_transfer_callback` processes a cross-chain proof for a transfer that had a prior fast transfer, the bridge repays the relayer who fronted the funds:

```rust
// near/omni-bridge/src/lib.rs  lines 1997-2040
self.unlock_tokens_if_needed(          // ← accounting decremented
    transfer_message.get_origin_chain(),
    &token,
    transfer_message.amount.0,
);
...
if let Some(relayer) = recipient {
    self.send_tokens(token, relayer, U128(...), "")
        .detach();                     // ← result silently discarded
    self.mark_fast_transfer_as_finalised(&fast_transfer.id()); // ← state committed
}
``` [1](#0-0) 

The locked-token counter is decremented and the fast transfer is marked finalised **before** the send result is known. If `send_tokens` fails (e.g., the relayer's account lacks storage registration for the token, the token contract panics, or gas is exhausted), the tokens remain in the bridge contract. Because the fast transfer is already finalised, no retry is possible and no admin recovery function exists.

**Instance 2 — `utxo_fin_transfer_fast`**

The same pattern appears in the UTXO fast-transfer settlement path:

```rust
// near/omni-bridge/src/lib.rs  lines 2529-2548
let amount = if fast_transfer.get_destination_chain() == ChainKind::Near {
    self.remove_fast_transfer(&fast_transfer.id());   // ← state committed
    ...
} else {
    self.mark_fast_transfer_as_finalised(&fast_transfer.id()); // ← state committed
    ...
};

self.send_tokens(fast_transfer.token_id.clone(), fast_transfer_status.relayer, amount, "")
    .detach();   // ← result silently discarded
``` [2](#0-1) 

The fast transfer record is removed or finalised before the detached send. A failed send leaves the tokens permanently stuck.

**Contrast with the correctly handled path**

The `process_fin_transfer_to_near` path (the recipient-to-NEAR flow) correctly chains a callback:

```rust
self.send_tokens(token, recipient, amount, &msg)
    .then(Self::ext(...).fin_transfer_send_tokens_callback(...))
``` [3](#0-2) 

That callback checks `is_refund_required`, reverts lock actions, and removes the fin-transfer record on failure. The relayer-repayment paths lack any equivalent error handling.

**`send_tokens` failure modes for non-deployed tokens**

`send_tokens` dispatches `ft_transfer` for non-deployed (native) tokens:

```rust
ext_token::ext(token)
    .with_attached_deposit(ONE_YOCTO)
    .with_static_gas(FT_TRANSFER_GAS)
    .ft_transfer(recipient, amount, None)
``` [4](#0-3) 

`ft_transfer` panics if the recipient has no storage registration for the token. For deployed tokens, `mint()` is called — which can also fail if the token contract is paused, upgraded, or otherwise unavailable. In all failure cases, `.detach()` means the bridge never learns of the failure.

---

### Impact Explanation

**Permanent freezing / irrecoverable lock of relayer funds in the bridge.**

A relayer who fronted tokens to a user via the fast-transfer path is entitled to repayment when the canonical proof is submitted. If the repayment promise fails silently:

- The locked-token accounting has already been decremented (tokens "released" on paper).
- The fast transfer is marked finalised, blocking any retry.
- The actual tokens remain in the bridge contract with no admin recovery function.
- The relayer permanently loses the full principal they fronted.

This matches the allowed impact: *"Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows."*

---

### Likelihood Explanation

The failure condition is realistic:

1. **Storage not registered**: NEP-141 `ft_transfer` panics if the recipient (`relayer`) has no storage registered for the specific token. A relayer who performed the fast transfer using a different token path, or whose storage was later withdrawn, would trigger this.
2. **Token contract unavailability**: If the token contract is paused, migrated, or temporarily unavailable at the moment of proof submission, the `ft_transfer` or `mint` call fails.
3. **Gas miscalculation**: `send_tokens` subtracts `SEND_TOKENS_CALLBACK_GAS` from available gas even when called with `.detach()` (no callback exists), as noted by the inline `TODO` comment at line 2065. This can leave insufficient gas for the actual transfer. [5](#0-4) 

---

### Recommendation

Replace `.detach()` with a proper callback that checks the promise result and, on failure, reverts the state mutations:

1. **Do not mark the fast transfer as finalised before confirming the send succeeds.** Pass the fast-transfer ID and lock-action data into a callback.
2. **In the callback**, if the send failed: un-finalise (or re-insert) the fast transfer record and restore the locked-token accounting so the operation can be retried.
3. Apply the same pattern used in `process_fin_transfer_to_near` / `fin_transfer_send_tokens_callback`, which already handles the refund/revert case correctly.

---

### Proof of Concept

1. Relayer R calls `ft_transfer_call` on a native token contract, triggering `fast_fin_transfer` in the bridge. The bridge records the fast transfer with R as the relayer.
2. R's storage registration for the token is later withdrawn (or R uses a fresh account that never registered storage for this specific token).
3. Any trusted relayer submits `fin_transfer` with the canonical proof for the same transfer.
4. `fin_transfer_callback` → `process_fin_transfer_to_other_chain` is reached. The bridge calls `unlock_tokens_if_needed` (decrementing locked balance), then `send_tokens(token, R, amount, "").detach()`, then `mark_fast_transfer_as_finalised`.
5. The `ft_transfer` to R panics because R has no storage. The panic is silently swallowed by `.detach()`.
6. The fast transfer is now finalised — no retry is possible. The tokens remain in the bridge contract. R has permanently lost the fronted amount. [6](#0-5) [7](#0-6)

### Citations

**File:** near/omni-bridge/src/lib.rs (L1957-1977)
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
        .then(
            Self::ext(env::current_account_id())
                .with_static_gas(SEND_TOKENS_CALLBACK_GAS)
                .fin_transfer_send_tokens_callback(
                    transfer_message,
                    &fee_recipient,
                    !msg.is_empty(),
                    predecessor_account_id,
                    lock_actions,
                ),
        )
```

**File:** near/omni-bridge/src/lib.rs (L1997-2040)
```rust
        self.unlock_tokens_if_needed(
            transfer_message.get_origin_chain(),
            &token,
            transfer_message.amount.0,
        );
        self.lock_tokens_if_needed(
            transfer_message.get_destination_chain(),
            &token,
            transfer_message.fee.fee.into(),
        );

        let fast_transfer = FastTransfer::from_transfer(transfer_message.clone(), token.clone());
        let recipient = if let Some(status) = self.get_fast_transfer_status(&fast_transfer.id()) {
            require!(
                !status.finalised,
                BridgeError::FastTransferAlreadyFinalised.as_ref()
            );
            Some(status.relayer)
        } else {
            self.lock_tokens_if_needed(
                transfer_message.get_destination_chain(),
                &token,
                transfer_message
                    .amount_without_fee()
                    .near_expect(BridgeError::InvalidFee),
            );

            None
        };

        // If fast transfer happened, send tokens to the relayer that executed fast transfer
        if let Some(relayer) = recipient {
            self.send_tokens(
                token,
                relayer,
                U128(
                    transfer_message
                        .amount_without_fee()
                        .near_expect(BridgeError::InvalidFee),
                ),
                "",
            )
            .detach();
            self.mark_fast_transfer_as_finalised(&fast_transfer.id());
```

**File:** near/omni-bridge/src/lib.rs (L2063-2067)
```rust
        let ft_transfer_call_gas = env::prepaid_gas()
            .saturating_sub(env::used_gas())
            .saturating_sub(SEND_TOKENS_CALLBACK_GAS) // TODO: not all send_tokens callbacks has the same gas.
            .saturating_sub(MINT_TOKEN_GAS)
            .min(FT_TRANSFER_CALL_GAS);
```

**File:** near/omni-bridge/src/lib.rs (L2102-2106)
```rust
        } else if msg.is_empty() {
            ext_token::ext(token)
                .with_attached_deposit(ONE_YOCTO)
                .with_static_gas(FT_TRANSFER_GAS)
                .ft_transfer(recipient, amount, None)
```

**File:** near/omni-bridge/src/lib.rs (L2529-2548)
```rust
        let amount = if fast_transfer.get_destination_chain() == ChainKind::Near {
            self.remove_fast_transfer(&fast_transfer.id());
            fast_transfer.amount
        } else {
            self.mark_fast_transfer_as_finalised(&fast_transfer.id());
            // With transfers to other chain the fee will be claimed after finalization on the destination chain
            U128(
                fast_transfer
                    .amount_without_fee()
                    .near_expect(BridgeError::InvalidFee),
            )
        };

        self.send_tokens(
            fast_transfer.token_id.clone(),
            fast_transfer_status.relayer,
            amount,
            "",
        )
        .detach();
```

### Title
Missing Destination-Chain Validation in `init_transfer` Allows Permanent Fund Loss or Freeze — (`File: near/omni-bridge/src/lib.rs`)

### Summary
`init_transfer` accepts and finalizes a user's token transfer to any `ChainKind` value without verifying that the destination chain is actually configured in the bridge (i.e., has a registered factory and token-address mapping). Tokens are burned or locked before any such check occurs. The transfer is then permanently stuck because `sign_transfer` panics when it later discovers the token is not registered for that chain. There is no user-accessible cancellation path.

### Finding Description
In `near/omni-bridge/src/lib.rs`, the `init_transfer` function performs only one chain-level guard:

```rust
require!(
    init_transfer_msg.recipient.get_chain() != ChainKind::Near,
    BridgeError::InvalidRecipientChain.as_ref()
);
``` [1](#0-0) 

It does **not** check whether `self.factories.get(&destination_chain)` is `Some(...)`, nor whether `self.token_id_to_address.get(&(destination_chain, token_id))` exists. After this single guard, the function immediately increments the global nonce, stores the `TransferMessage` in `pending_transfers`, and then calls `init_transfer_internal`: [2](#0-1) 

Inside `init_transfer_internal`, for a deployed (bridge) token, `burn_tokens_if_needed` is called unconditionally, permanently destroying the user's tokens: [3](#0-2) 

For a native (non-deployed) token, the tokens are held by the bridge contract. `lock_tokens_if_needed` is called, but if the `(destination_chain, token_id)` key is absent from `locked_tokens`, it silently returns `LockAction::Unchanged` without updating any accounting: [4](#0-3) 

The transfer is now stored in `pending_transfers`. When a relayer later calls `sign_transfer`, it attempts to resolve the token address for the destination chain:

```rust
let token_address = self
    .get_token_address(
        transfer_message.get_destination_chain(),
        self.get_token_id(&transfer_message.token),
    )
    .unwrap_or_else(|| {
        env::panic_str(BridgeError::FailedToGetTokenAddress.to_string().as_str())
    });
``` [5](#0-4) 

This panics because no token address was ever registered for the unsupported destination chain. The transfer is permanently stuck in `pending_transfers` with no user-accessible cancellation function.

### Impact Explanation
- **Deployed (bridge) tokens**: Tokens are burned in `burn_tokens_if_needed` before the chain-support check ever occurs. The burn is irrecoverable; the user permanently loses their assets.
- **Native (non-deployed) tokens**: Tokens are held by the bridge contract. The transfer is stuck in `pending_transfers` indefinitely. Only DAO intervention via `transfer_token_as_dao` can recover them; the user has no self-service recovery path.

Both outcomes match the allowed impact: **Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows.**

### Likelihood Explanation
`ChainKind` is a fixed enum covering all chains the protocol intends to support (Eth, Sol, Strk, Base, Arb, Bnb, Pol, HyperEvm, Abs, Fogo, Btc, Near). During protocol expansion — when a new chain kind is added to the enum before its factory and token mappings are configured — any user who sends tokens targeting that chain kind (via a direct contract call or a misconfigured frontend) will trigger this path. The entry point is the fully public `ft_transfer_call` → `ft_on_transfer` → `init_transfer` chain, requiring no privileged role.

### Recommendation
Add an explicit check in `init_transfer` (before incrementing the nonce or storing the message) that the destination chain has a registered factory and that the token is registered for that chain:

```rust
require!(
    self.factories.contains_key(&init_transfer_msg.get_destination_chain()),
    BridgeError::UnknownFactory.as_ref()
);
require!(
    self.get_token_address(
        init_transfer_msg.get_destination_chain(),
        token_id.clone(),
    ).is_some(),
    BridgeError::FailedToGetTokenAddress.as_ref()
);
```

This mirrors the pattern already used in `fin_transfer_callback` and `bind_token_callback`, which both validate `self.factories` before proceeding. [6](#0-5) 

### Proof of Concept
1. Assume `ChainKind::Strk` is present in the enum but no factory or token mapping has been registered for it yet (e.g., during a phased rollout).
2. User calls `ft_transfer_call` on a deployed bridge token contract, passing `msg = InitTransfer { recipient: OmniAddress::Strk(<some_address>), fee: 0, ... }`.
3. `ft_on_transfer` → `init_transfer` passes the only guard (`get_chain() != Near`).
4. `current_origin_nonce` is incremented; the `TransferMessage` is stored in `pending_transfers`.
5. `burn_tokens_if_needed` fires and burns the user's tokens (detached promise, no rollback).
6. `lock_tokens_if_needed(ChainKind::Strk, ...)` returns `Unchanged` because the key is absent.
7. A relayer calls `sign_transfer` for this transfer ID.
8. `get_token_address(ChainKind::Strk, token_id)` returns `None` → `env::panic_str(BridgeError::FailedToGetTokenAddress)`.
9. The transfer remains in `pending_transfers` forever; the user's tokens are permanently burned. [7](#0-6) [8](#0-7) [9](#0-8)

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

**File:** near/omni-bridge/src/lib.rs (L523-534)
```rust
    fn init_transfer(
        &mut self,
        sender_id: AccountId,
        signer_id: AccountId,
        token_id: AccountId,
        amount: U128,
        init_transfer_msg: InitTransferMsg,
    ) -> PromiseOrPromiseIndexOrValue<U128> {
        require!(
            init_transfer_msg.recipient.get_chain() != ChainKind::Near,
            BridgeError::InvalidRecipientChain.as_ref()
        );
```

**File:** near/omni-bridge/src/lib.rs (L536-557)
```rust
        self.current_origin_nonce += 1;
        let destination_nonce =
            self.get_next_destination_nonce(init_transfer_msg.get_destination_chain());

        let transfer_message = TransferMessage {
            origin_nonce: self.current_origin_nonce,
            token: OmniAddress::Near(token_id),
            amount,
            recipient: init_transfer_msg.recipient,
            fee: Fee {
                fee: init_transfer_msg.fee,
                native_fee: init_transfer_msg.native_token_fee,
            },
            sender: OmniAddress::Near(sender_id),
            msg: init_transfer_msg.msg.map(String::from).unwrap_or_default(),
            destination_nonce,
            origin_transfer_id: None,
        };
        require!(
            transfer_message.fee.fee < transfer_message.amount,
            BridgeError::InvalidFee.as_ref()
        );
```

**File:** near/omni-bridge/src/lib.rs (L708-713)
```rust
        require!(
            self.factories
                .get(&init_transfer.emitter_address.get_chain())
                == Some(init_transfer.emitter_address),
            BridgeError::UnknownFactory.as_ref()
        );
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

**File:** near/omni-bridge/src/token_lock.rs (L48-57)
```rust
    fn lock_tokens(
        &mut self,
        chain_kind: ChainKind,
        token_id: &AccountId,
        amount: u128,
    ) -> LockAction {
        let key = (chain_kind, token_id.clone());
        let Some(current_amount) = self.locked_tokens.get(&key) else {
            return LockAction::Unchanged;
        };
```

**File:** near/omni-bridge/src/token_lock.rs (L96-107)
```rust
    pub(crate) fn lock_tokens_if_needed(
        &mut self,
        chain_kind: ChainKind,
        token_id: &AccountId,
        amount: u128,
    ) -> LockAction {
        if self.get_token_origin_chain(token_id) == chain_kind || amount == 0 {
            return LockAction::Unchanged;
        }

        self.lock_tokens(chain_kind, token_id, amount)
    }
```

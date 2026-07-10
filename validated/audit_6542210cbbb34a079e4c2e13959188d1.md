### Title
No Recovery Mechanism for Pending Transfers When Token Address Is Unregistered on Destination Chain - (File: near/omni-bridge/src/lib.rs)

### Summary
`init_transfer_internal` burns or locks user tokens and records the transfer in `pending_transfers` **before** verifying that the token has a registered address on the destination chain. If the token address is absent, every subsequent `sign_transfer` call panics unconditionally. Because the contract has no user-initiated cancel or refund path for pending transfers, the user's funds are permanently irrecoverable.

### Finding Description
In `init_transfer_internal`, tokens are burned (for deployed/bridged tokens) or locked, and the transfer is inserted into `pending_transfers`, with no prior check that the token is registered on the destination chain:

```rust
// near/omni-bridge/src/lib.rs ~1850
if let OmniAddress::Near(token_id) = transfer_message.token.clone() {
    self.burn_tokens_if_needed(token_id.clone(), transfer_message.amount);
    self.lock_tokens_if_needed(
        transfer_message.get_destination_chain(),
        &token_id,
        transfer_message.amount.0,
    );
}
```

Later, when a relayer calls `sign_transfer`, the contract looks up the token address for the destination chain and panics if it is absent:

```rust
// near/omni-bridge/src/lib.rs ~462
let token_address = self
    .get_token_address(
        transfer_message.get_destination_chain(),
        self.get_token_id(&transfer_message.token),
    )
    .unwrap_or_else(|| {
        env::panic_str(BridgeError::FailedToGetTokenAddress.to_string().as_str())
    });
```

The only code paths that remove an entry from `pending_transfers` are:

1. `claim_fee_callback` — requires a successful on-chain finalization proof from the destination chain.
2. `sign_transfer_callback` with `fee.is_zero()` — requires a successful MPC signature.

```rust
// near/omni-bridge/src/lib.rs ~655
if let Ok(signature) = call_result {
    if fee.is_zero() {
        self.remove_transfer_message(message_payload.transfer_id);
    }
    // ...
}
```

Neither path is reachable when `sign_transfer` always panics. There is no `cancel_transfer`, no user-initiated refund, and no DAO function to return tokens to the original sender. The contract is structurally identical to the MerkleLockup pattern: assets are committed before the protocol can verify the configuration is valid, and there is no escape hatch.

### Impact Explanation
**Critical — Permanent irrecoverable lock of user funds.**

For deployed (bridged) tokens: `burn_tokens_if_needed` fires a detached burn call. The tokens are destroyed on NEAR. If the destination chain token address is missing, the transfer can never be signed, the burned tokens can never be re-minted, and the user suffers a total loss.

For native tokens: the tokens are held by the bridge contract. They are stuck indefinitely because no function allows the user to reclaim them from `pending_transfers`.

### Likelihood Explanation
**Medium.** The `init_transfer` entry point accepts any `OmniAddress` recipient on any supported chain. It performs no pre-flight check that the token is registered on the destination chain. A user who transfers a bridged token (e.g., a Solana-origin token) to a chain where that token has not yet been deployed triggers this condition with a normal, unprivileged `ft_transfer_call`. No special role or key is required.

### Recommendation
1. **Fail early**: Add a check in `init_transfer` (before burning/locking) that `get_token_address(destination_chain, token_id)` returns `Some`. Revert and refund the user if the token is not registered.
2. **Add a user-initiated cancel path**: Introduce a `cancel_transfer(transfer_id)` function callable by the original sender that removes the entry from `pending_transfers` and returns the tokens (re-mints for deployed tokens, transfers back for native tokens). This is the direct analog of the Sablier grace-period clawback fix.

### Proof of Concept
1. `bridged-sol-usdc.near` is a deployed token (bridged from Solana), registered on `ChainKind::Sol` but **not** on `ChainKind::Eth`.
2. User calls `ft_transfer_call` on `bridged-sol-usdc.near` with `msg = InitTransferMsg { recipient: OmniAddress::Eth(...), fee: U128(0), ... }`.
3. `ft_on_transfer` → `init_transfer` → `init_transfer_internal`:
   - `burn_tokens_if_needed` burns the user's tokens (detached, irreversible). [1](#0-0) 
   - Transfer is inserted into `pending_transfers`. [2](#0-1) 
4. Relayer calls `sign_transfer` for this `transfer_id`.
5. `get_token_address(ChainKind::Eth, token_id)` returns `None` → `env::panic_str(FailedToGetTokenAddress)`. [3](#0-2) 
6. `sign_transfer` panics; no state is changed; the transfer remains in `pending_transfers` indefinitely.
7. `sign_transfer_callback` is never reached, so `remove_transfer_message` is never called. [4](#0-3) 
8. `claim_fee_callback` is unreachable because no finalization proof can be produced for a transfer that was never signed.
9. The user's tokens are permanently burned with no recovery path. There is no `cancel_transfer` or equivalent function anywhere in the contract. [5](#0-4)

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

**File:** near/omni-bridge/src/lib.rs (L655-658)
```rust
        if let Ok(signature) = call_result {
            if fee.is_zero() {
                self.remove_transfer_message(message_payload.transfer_id);
            }
```

**File:** near/omni-bridge/src/lib.rs (L1829-1865)
```rust
    fn init_transfer_internal(
        &mut self,
        transfer_message: TransferMessage,
        storage_owner: AccountId,
    ) -> U128 {
        let required_storage_balance = self
            .add_transfer_message(transfer_message.clone(), storage_owner.clone())
            .saturating_add(NearToken::from_yoctonear(transfer_message.fee.native_fee.0));

        if self
            .try_update_storage_balance(
                storage_owner,
                required_storage_balance,
                NearToken::from_yoctonear(0),
            )
            .is_err()
        {
            self.remove_transfer_message_without_refund(transfer_message.get_transfer_id());
            return transfer_message.amount;
        }

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

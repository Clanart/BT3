### Title
No User-Callable Cancellation Mechanism for Pending Transfers Causes Permanent Fund Loss - (File: `near/omni-bridge/src/lib.rs`)

### Summary
When a user initiates a cross-chain transfer via `ft_on_transfer` → `init_transfer`, their tokens are immediately and irrevocably burned (for deployed/bridged tokens) or locked in the bridge contract (for native tokens) before any validation that the transfer can actually be completed on the destination chain. If the transfer cannot be finalized — because the token is not registered for the destination chain, the normalized amount rounds to zero, or the MPC signer is unavailable — there is no user-callable function to cancel the pending transfer and recover funds. The transfer remains in `pending_transfers` indefinitely with no recovery path.

### Finding Description

**Step 1 — Tokens burned/locked at initiation, before destination-chain validation.**

`init_transfer_internal` unconditionally burns deployed tokens and stores the transfer in `pending_transfers` without checking whether the token is registered for the destination chain:

```rust
// near/omni-bridge/src/lib.rs  ~line 1850
if let OmniAddress::Near(token_id) = transfer_message.token.clone() {
    self.burn_tokens_if_needed(token_id.clone(), transfer_message.amount);
    self.lock_tokens_if_needed(
        transfer_message.get_destination_chain(),
        &token_id,
        transfer_message.amount.0,
    );
}
```

`burn_tokens_if_needed` is a fire-and-forget detached call — once the outer function returns `U128(0)`, the NEP-141 token contract treats the tokens as consumed and does not refund them.

**Step 2 — `init_transfer` accepts any non-NEAR destination chain without checking token registration.**

`init_transfer` only validates that the recipient chain is not NEAR and that `fee < amount`. It does not verify that the token is registered for the destination chain:

```rust
// near/omni-bridge/src/lib.rs  ~line 531
require!(
    init_transfer_msg.recipient.get_chain() != ChainKind::Near,
    BridgeError::InvalidRecipientChain.as_ref()
);
// No check: is the token registered for the destination chain?
```

**Step 3 — `sign_transfer` panics if the token is not registered for the destination chain.**

When the relayer later calls `sign_transfer`, it panics with `FailedToGetTokenAddress` if the token has no mapping for the destination chain:

```rust
// near/omni-bridge/src/lib.rs  ~line 462
let token_address = self
    .get_token_address(
        transfer_message.get_destination_chain(),
        self.get_token_id(&transfer_message.token),
    )
    .unwrap_or_else(|| {
        env::panic_str(BridgeError::FailedToGetTokenAddress.to_string().as_str())
    });
```

Similarly, if the normalized amount rounds to zero due to decimal precision differences, `sign_transfer` panics with `InvalidAmountToTransfer`:

```rust
// near/omni-bridge/src/lib.rs  ~line 482
require!(
    amount_to_transfer > 0,
    BridgeError::InvalidAmountToTransfer.as_ref()
);
```

**Step 4 — `sign_transfer_callback` does nothing on failure; transfer remains in `pending_transfers`.**

```rust
// near/omni-bridge/src/lib.rs  ~line 649
pub fn sign_transfer_callback(
    &mut self,
    #[callback_result] call_result: Result<SignatureResponse, PromiseError>,
    ...
) {
    if let Ok(signature) = call_result {
        if fee.is_zero() {
            self.remove_transfer_message(message_payload.transfer_id);
        }
        // emit event
    }
    // On Err: nothing happens — transfer stays in pending_transfers
}
```

**Step 5 — No user-callable cancellation function exists.**

A complete audit of all public functions in `near/omni-bridge/src/lib.rs` and `near/omni-bridge/src/btc.rs` reveals no `cancel_transfer`, `withdraw_pending_transfer`, or equivalent function callable by an unprivileged user. The only user-callable functions are storage management (`storage_deposit`, `storage_withdraw`, `storage_unregister`). `storage_unregister` with `force=true` removes the storage balance entry but does not cancel pending transfers or return tokens.

The only functions that remove a pending transfer are:
- `sign_transfer_callback` (only when fee is zero and MPC signing succeeds)
- `submit_transfer_to_utxo_chain_connector` (relayer-only, UTXO chains only)
- `claim_fee_callback` (relayer-only)
- `init_transfer_internal` failure path (only on storage check failure, before burn)

None of these are callable by the user to recover from a stuck transfer.

### Impact Explanation

For a user who initiates a transfer of a deployed (bridged) token to a destination chain where the token is not registered:

1. The tokens are burned via `burn_tokens_if_needed` (detached, irreversible once the outer call returns `U128(0)`)
2. The transfer is stored in `pending_transfers`
3. `sign_transfer` panics every time the relayer attempts it
4. The transfer is permanently stuck; the user's tokens are permanently destroyed

For native NEAR tokens (not deployed by bridge), the tokens are locked in the bridge contract with no user-callable recovery. DAO intervention via `transfer_token_as_dao` would be required, but this is not guaranteed and requires privileged access.

This matches the allowed impact: **Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows.**

### Likelihood Explanation

The bridge supports many destination chains (`ChainKind::Eth`, `Base`, `Arb`, `Bnb`, `Pol`, `HyperEvm`, `Abs`, `Sol`, `Fogo`, `Strk`, `Btc`, `Zcash`). A given token is typically registered only for a subset of these chains. A user who specifies a destination chain where their token is not registered — either by mistake or because the token was de-registered after transfer initiation — will permanently lose their funds. The `init_transfer` entry point accepts any non-NEAR destination chain without validating token registration, making this a realistic user-triggered scenario requiring no privileged access.

### Recommendation

1. **Add a pre-burn validation in `init_transfer`**: Before burning/locking tokens, verify that the token is registered for the destination chain via `get_token_address`. If not registered, return the full token amount as a refund.

2. **Add a user-callable cancellation function**: Implement a `cancel_transfer(transfer_id: TransferId)` function that allows the original sender (verified via `transfer.owner`) to cancel a pending transfer. For native tokens, refund via `ft_transfer`. For deployed tokens, mint the equivalent amount back to the sender. This is the direct analog of the retry/refund mechanism implemented by the Atlas Protocol team in the referenced remediation.

### Proof of Concept

1. User holds 1000 units of `eth.token.near` (a deployed bridged token registered only for `ChainKind::Eth`).
2. User calls `ft_transfer_call` on `eth.token.near` with `amount=1000` and `msg` containing `InitTransferMsg { recipient: OmniAddress::Sol("..."), fee: 0, ... }` (Solana destination).
3. `ft_on_transfer` → `init_transfer` → `init_transfer_internal` executes:
   - `add_transfer_message` stores the transfer in `pending_transfers`
   - `burn_tokens_if_needed` fires a detached burn of 1000 tokens
   - Returns `U128(0)` — tokens consumed
4. Relayer calls `sign_transfer(transfer_id, ...)`:
   - `get_token_address(ChainKind::Sol, eth_token_near)` returns `None`
   - Panics: `BridgeError::FailedToGetTokenAddress`
5. Transfer remains in `pending_transfers` indefinitely.
6. User's 1000 `eth.token.near` tokens are permanently burned. No user-callable function exists to cancel the transfer or recover the tokens. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** near/omni-bridge/src/lib.rs (L462-485)
```rust
        let token_address = self
            .get_token_address(
                transfer_message.get_destination_chain(),
                self.get_token_id(&transfer_message.token),
            )
            .unwrap_or_else(|| {
                env::panic_str(BridgeError::FailedToGetTokenAddress.to_string().as_str())
            });

        let decimals = self
            .token_decimals
            .get(&token_address)
            .near_expect(BridgeError::TokenDecimalsNotFound);
        let amount_to_transfer = Self::normalize_amount(
            transfer_message
                .amount_without_fee()
                .near_expect(BridgeError::InvalidFee),
            decimals,
        );

        require!(
            amount_to_transfer > 0,
            BridgeError::InvalidAmountToTransfer.as_ref()
        );
```

**File:** near/omni-bridge/src/lib.rs (L531-557)
```rust
        require!(
            init_transfer_msg.recipient.get_chain() != ChainKind::Near,
            BridgeError::InvalidRecipientChain.as_ref()
        );

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

**File:** near/omni-bridge/src/lib.rs (L648-668)
```rust
    #[private]
    pub fn sign_transfer_callback(
        &mut self,
        #[callback_result] call_result: Result<SignatureResponse, PromiseError>,
        #[serializer(borsh)] message_payload: TransferMessagePayload,
        #[serializer(borsh)] fee: &Fee,
    ) {
        if let Ok(signature) = call_result {
            if fee.is_zero() {
                self.remove_transfer_message(message_payload.transfer_id);
            }

            env::log_str(
                &OmniBridgeEvent::SignTransferEvent {
                    signature,
                    message_payload,
                }
                .to_log_string(),
            );
        }
    }
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

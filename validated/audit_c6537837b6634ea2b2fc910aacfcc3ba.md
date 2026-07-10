### Title
Unregistered Token Deposits Permanently Lock User Funds in NEAR Bridge — (`near/omni-bridge/src/lib.rs`)

### Summary

The NEAR bridge's `ft_on_transfer` → `init_transfer` → `init_transfer_internal` flow accepts any NEP-141 token via `ft_transfer_call` without validating that the token is registered in the bridge's token registry for the requested destination chain. Tokens from unregistered tokens are consumed (not refunded) and permanently locked in the bridge contract, with no user-accessible recovery path.

### Finding Description

`ft_on_transfer` (line 253) is the public entry point for NEAR-side bridge transfers. It dispatches to `init_transfer` (line 523) for `InitTransfer` messages. `init_transfer` constructs a `TransferMessage` using the caller-supplied `token_id` (the NEP-141 predecessor account) and immediately proceeds without checking whether that token is registered in `token_id_to_address` for the destination chain.

`init_transfer_internal` (line 1829) then:
1. Stores the transfer message in `pending_transfers` (consuming storage balance).
2. Calls `burn_tokens_if_needed` — silently skips for unregistered tokens since `is_deployed_token` returns `false`.
3. Calls `lock_tokens_if_needed` — silently returns `LockAction::Unchanged` for unregistered tokens because `locked_tokens.get(&key)` returns `None`.
4. Returns `U128(0)` — signalling to the NEP-141 standard that **zero tokens are refunded**, so the full amount stays in the bridge contract.

Later, when `sign_transfer` (line 447) is called for this pending transfer, it calls `get_token_address` for the destination chain:

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

For an unregistered token, `token_id_to_address.get(&(chain_kind, token_id))` returns `None`, causing `sign_transfer` to always panic. The transfer can never be signed, and there is no user-accessible cancel or withdraw function to recover the locked tokens.

### Impact Explanation

User funds are permanently and irrecoverably locked in the bridge contract. The tokens are held by the bridge (returned `U128(0)` to the NEP-141 token contract), the pending transfer record exists in `pending_transfers`, but `sign_transfer` will always revert for that transfer ID. There is no `cancel_transfer`, `withdraw`, or equivalent public function that allows the user to reclaim their tokens. This matches the **Critical** allowed impact: *Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge flows*.

### Likelihood Explanation

`ft_on_transfer` is a public, permissionless entry point — any NEAR account can call `ft_transfer_call` on any NEP-141 token contract pointing to the bridge as receiver with an `InitTransfer` message. No role, whitelist, or token-registration check gates this path. A user who mistakenly (or experimentally) bridges a token that has not yet been registered via `bind_token`/`deploy_token` will lose their funds with no recourse.

### Recommendation

Add a registration check at the start of `init_transfer` before consuming the token:

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

+   // Ensure the token is registered for the destination chain before accepting it
+   require!(
+       self.get_token_address(
+           init_transfer_msg.get_destination_chain(),
+           token_id.clone(),
+       ).is_some(),
+       BridgeError::TokenNotRegistered.as_ref()
+   );
    ...
}
```

Returning the full `amount` from `ft_on_transfer` (i.e., refunding) when the token is unregistered would also be acceptable, but the check above prevents the nonce increment and storage consumption as well.

### Proof of Concept

1. Alice holds 1000 units of `unregistered-token.near`, a valid NEP-141 token not yet registered in the bridge.
2. Alice calls `ft_transfer_call` on `unregistered-token.near` with `receiver_id = bridge.near`, `amount = 1000`, and `msg = {"InitTransfer": {"recipient": "0xAlice...", "fee": "0", ...}}`.
3. `ft_on_transfer` dispatches to `init_transfer` → `init_transfer_internal`. No registration check occurs. `init_transfer_internal` returns `U128(0)` — tokens stay in the bridge.
4. Alice's balance is now 0. The bridge holds 1000 tokens. A pending transfer record exists.
5. Any relayer calls `sign_transfer` for Alice's transfer ID → panics with `ERR_FAILED_TO_GET_TOKEN_ADDRESS`.
6. Alice has no `cancel_transfer` or `withdraw` function to call. Funds are permanently locked.

**Relevant code references:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

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

**File:** near/omni-bridge/src/lib.rs (L523-557)
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

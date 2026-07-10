### Title
No User-Callable Escape Hatch for Pending Transfers Causes Permanent Fund Lock - (File: `near/omni-bridge/src/lib.rs`)

### Summary

When a NEAR user initiates a bridge transfer, their tokens are burned/locked and a `TransferMessage` is stored in `pending_transfers`. Completing or cancelling this transfer requires a trusted relayer to call `sign_transfer` followed by `claim_fee`. There is no user-callable cancellation function. If the bridge is paused or the trusted relayer set becomes empty, user funds are permanently and irrecoverably locked in `pending_transfers` with no escape hatch.

### Finding Description

The NEAR bridge transfer flow proceeds as follows:

1. User calls `ft_transfer_call` on a token contract, which triggers `ft_on_transfer` on the bridge.
2. `ft_on_transfer` calls `init_transfer`, which calls `init_transfer_internal`.
3. Inside `init_transfer_internal`, the user's tokens are **burned** (for deployed/bridged tokens) or **locked** (for native tokens), and a `TransferMessage` is inserted into `pending_transfers`. [1](#0-0) 

Once this succeeds, the only code paths that remove the `pending_transfers` entry are:

- `sign_transfer_callback` (only when fee is zero, called after `sign_transfer`)
- `remove_transfer_message` called from `claim_fee_callback`

Both `sign_transfer` and `claim_fee` are gated by `#[trusted_relayer]` and `#[pause(except(roles(Role::DAO)))]`: [2](#0-1) [3](#0-2) 

There is **no `cancel_transfer`, `withdraw_transfer`, or any other user-callable function** that removes a `pending_transfers` entry and returns tokens to the user. A grep for `cancel_transfer`, `withdraw_transfer`, `cancel_pending`, and `refund_transfer` across `near/omni-bridge/src/` returns zero matches.

The `remove_transfer_message` and `remove_transfer_message_without_refund` internal helpers are only reachable through the relayer-gated paths: [4](#0-3) 

### Impact Explanation

**Critical — Permanent freezing / irrecoverable lock of user funds in bridge flows.**

Once `init_transfer_internal` succeeds:
- For **deployed (bridged) tokens**: the tokens are burned. They cannot be recovered without a new mint, which requires the full bridge finalization path.
- For **native NEAR tokens**: the tokens are held by the bridge contract and tracked in `locked_tokens`. They cannot be withdrawn without relayer action.

In either case, if no trusted relayer processes the transfer (via `sign_transfer` → destination chain finalization → `claim_fee`), the user's funds are permanently frozen. There is no timeout, no expiry, and no user-callable exit.

### Likelihood Explanation

Two realistic trigger conditions exist, both reachable without any privileged attacker:

1. **Bridge pause**: The `PauseManager` role can pause the bridge. `sign_transfer` and `claim_fee` are both paused by this action (they only bypass for `Role::DAO`, not for users). A user who initiated a transfer just before a pause has no recourse.

2. **Empty relayer set**: Trusted relayers can voluntarily withdraw their stake at any time. If all relayers exit (e.g., due to economic conditions, a protocol upgrade, or a coordinated exit), no one can call `sign_transfer` or `claim_fee`, and all pending transfers are permanently frozen. The `#[trusted_relayer]` bypass roles (`Role::DAO`, `Role::UnrestrictedRelayer`) require privileged access that the user does not have. [5](#0-4) 

### Recommendation

Add a user-callable `cancel_transfer` function that:
1. Verifies the caller is the original `sender` of the pending transfer (stored in `TransferMessage.sender`).
2. Optionally enforces a minimum age (e.g., transfer must be older than N blocks) to prevent griefing of relayers who are actively processing the transfer.
3. Reverses the token burn (mint back) or unlocks the native tokens and returns them to the sender.
4. Removes the entry from `pending_transfers` and reverts any `locked_tokens` accounting.

This is the direct analog of the "force repay" flag recommended in the Gearbox report: give the user an escape hatch so they are not permanently dependent on a third party to recover their own funds.

### Proof of Concept

**Step-by-step:**

1. User Alice calls `ft_transfer_call` on a bridged token contract with `receiver_id = bridge`, `amount = 1000`, and `msg = InitTransfer{recipient: EVM_ADDRESS, fee: 0, ...}`.
2. `ft_on_transfer` → `init_transfer` → `init_transfer_internal` executes:
   - `add_transfer_message` inserts `TransferMessage` into `pending_transfers` with `transfer_id = {origin_chain: Near, origin_nonce: N}`.
   - `burn_tokens_if_needed` burns Alice's 1000 tokens from the bridge's balance.
   - `lock_tokens_if_needed` increments `locked_tokens[(Eth, token_id)]` by 1000.
3. The bridge is paused by the `PauseManager` (or all relayers withdraw stake).
4. Alice attempts to recover her funds. She has no function to call:
   - `sign_transfer` → reverts: caller is not a trusted relayer.
   - `claim_fee` → reverts: caller is not a trusted relayer.
   - No `cancel_transfer` exists.
5. Alice's 1000 tokens are permanently burned with no corresponding mint on the destination chain. The `pending_transfers` entry remains indefinitely. [6](#0-5) [7](#0-6)

### Citations

**File:** near/omni-bridge/src/lib.rs (L245-249)
```rust
#[trusted_relayer(
    bypass_roles(Role::DAO, Role::UnrestrictedRelayer),
    manager_roles(Role::DAO, Role::RelayerManager),
    config_roles(Role::DAO)
)]
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

**File:** near/omni-bridge/src/lib.rs (L444-452)
```rust
    #[payable]
    #[trusted_relayer]
    #[pause(except(roles(Role::DAO)))]
    pub fn sign_transfer(
        &mut self,
        transfer_id: TransferId,
        fee_recipient: Option<AccountId>,
        fee: &Option<Fee>,
    ) -> Promise {
```

**File:** near/omni-bridge/src/lib.rs (L1054-1057)
```rust
    #[payable]
    #[trusted_relayer]
    #[pause(except(roles(Role::DAO)))]
    pub fn claim_fee(&mut self, #[serializer(borsh)] args: ClaimFeeArgs) -> Promise {
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

**File:** near/omni-bridge/src/lib.rs (L2194-2211)
```rust
    fn remove_transfer_message(&mut self, transfer_id: TransferId) -> TransferMessage {
        let storage_usage = env::storage_usage();
        let transfer = self
            .pending_transfers
            .remove(&transfer_id)
            .map(storage::TransferMessageStorage::into_main)
            .near_expect(BridgeError::TransferNotExist);

        let refund =
            env::storage_byte_cost().saturating_mul((storage_usage - env::storage_usage()).into());

        if let Some(mut storage) = self.accounts_balances.get(&transfer.owner) {
            storage.available = storage.available.saturating_add(refund);
            self.accounts_balances.insert(&transfer.owner, &storage);
        }

        transfer.message
    }
```

### Title
`migrate_deployed_token` Leaves Stale `pending_transfers` Entries, Permanently Locking User Funds - (File: near/omni-bridge/src/lib.rs)

### Summary
The `migrate_deployed_token` function atomically re-keys the token-mapping tables (`token_id_to_address`, `token_address_to_id`, `deployed_tokens`, `deployed_tokens_v2`) from `old_token` to `new_token`, but leaves every existing `pending_transfers` entry that encodes `token: OmniAddress::Near(old_token)` completely untouched. After the migration, `sign_transfer` permanently panics for every such pending transfer because the `old_token` → destination-chain address lookup is gone, and there is no cancel/refund path. User funds are irrecoverably locked.

### Finding Description

**State written at transfer creation (`init_transfer_internal`):**

Every outgoing NEAR → foreign-chain transfer stores a `TransferMessage` in `pending_transfers` with:
```
token: OmniAddress::Near(token_id)   // e.g. OmniAddress::Near("eth-usdc.factory.bridge.near")
``` [1](#0-0) 

**State destroyed by `migrate_deployed_token`:**

The DAO-callable migration removes the `(origin_chain, old_token)` key from `token_id_to_address` and overwrites `token_address_to_id[origin_address]` with `new_token`: [2](#0-1) 

It does **not** scan or update `pending_transfers`.

**Permanent panic in `sign_transfer`:**

When a relayer later calls `sign_transfer` for the stale pending transfer, the code resolves the token ID from the stored `OmniAddress::Near(old_token)` (returning `old_token` directly, line 1369–1370), then looks up the destination-chain address:

```rust
let token_address = self
    .get_token_address(
        transfer_message.get_destination_chain(),
        self.get_token_id(&transfer_message.token),  // → old_token
    )
    .unwrap_or_else(|| {
        env::panic_str(BridgeError::FailedToGetTokenAddress.to_string().as_str())
    });
``` [3](#0-2) 

`get_token_address(destination_chain, old_token)` calls `token_id_to_address.get(&(destination_chain, old_token))`, which now returns `None` because `migrate_deployed_token` removed that key. The call panics unconditionally. [4](#0-3) 

**No recovery path exists.** The only functions that remove entries from `pending_transfers` are `remove_fin_transfer` (requires a successful `ft_transfer_call` callback after `sign_transfer` succeeds) and `remove_transfer_message_without_refund` (requires a proof from the destination chain that the transfer was finalised). Neither can be reached because `sign_transfer` never completes. There is no `cancel_transfer` or user-initiated refund function. [5](#0-4) 

**`locked_tokens` accounting is also broken.** `migrate_deployed_token` does not migrate the `locked_tokens` entry from `(origin_chain, old_token)` to `(origin_chain, new_token)`, so the locked-token counter for the new token ID starts at zero while the old counter retains the locked amount, permanently corrupting bridge collateralisation accounting. [6](#0-5) 

### Impact Explanation
Any user who initiated a NEAR → foreign-chain transfer for `old_token` before the DAO called `migrate_deployed_token` has their bridged tokens permanently frozen in `pending_transfers`. The tokens were already burned/locked on the NEAR side at `ft_on_transfer` time; they cannot be recovered because `sign_transfer` always panics and no refund path exists. This matches the allowed impact: **Critical — permanent freezing / irrecoverable lock of user funds in bridge flows**.

### Likelihood Explanation
`migrate_deployed_token` is a DAO-only function intended for legitimate token-contract upgrades (e.g., migrating from a legacy factory token to a new omni-token). The DAO has no on-chain mechanism to enumerate or drain `pending_transfers` before calling it. Any window between a user's `ft_on_transfer` and the relayer's `sign_transfer` — which can span multiple blocks — is sufficient to trigger the lock. The DAO acting in good faith (not maliciously) is the trigger; the vulnerability is in the function's design, not in operator intent.

### Recommendation
1. **Guard against in-flight transfers:** Before executing the migration, require that no `pending_transfers` entries reference `old_token` (e.g., maintain a per-token pending-transfer counter), or provide a DAO-callable function to rewrite stale `pending_transfers` entries from `old_token` to `new_token`.
2. **Migrate `locked_tokens`:** Copy the `locked_tokens` balance from `(origin_chain, old_token)` to `(origin_chain, new_token)` and remove the old entry inside `migrate_deployed_token`.
3. **Add a user-callable cancel/refund path:** Allow the original sender to reclaim tokens from a pending transfer that has been stuck for longer than a timeout, analogous to the fix applied to the VoterV3 `_reset` path in the referenced report.

### Proof of Concept

```
1. Token "eth-usdc.factory.bridge.near" (old_token) is deployed on NEAR,
   origin chain = Eth, origin_address = 0xA0b8...

2. Alice calls ft_transfer_call on old_token → bridge.ft_on_transfer →
   init_transfer(recipient = OmniAddress::Eth(alice_eth), amount = 1000).
   State written:
     pending_transfers[{Near, nonce=42}] = TransferMessage {
         token: OmniAddress::Near("eth-usdc.factory.bridge.near"),
         recipient: OmniAddress::Eth(alice_eth),
         amount: 1000, ...
     }
     locked_tokens[(Eth, "eth-usdc.factory.bridge.near")] += 1000

3. DAO calls migrate_deployed_token(
       origin_chain = Eth,
       old_token    = "eth-usdc.factory.bridge.near",
       new_token    = "eth-usdc.omni.bridge.near"
   ).
   State after:
     token_id_to_address[(Eth, "eth-usdc.factory.bridge.near")] → REMOVED
     token_id_to_address[(Eth, "eth-usdc.omni.bridge.near")]    → 0xA0b8...
     token_address_to_id[0xA0b8...]                             → "eth-usdc.omni.bridge.near"
     pending_transfers[{Near, nonce=42}]                        → UNCHANGED (stale)

4. Relayer calls sign_transfer({Near, nonce=42}).
   get_token_id(OmniAddress::Near("eth-usdc.factory.bridge.near"))
     → "eth-usdc.factory.bridge.near"
   get_token_address(Eth, "eth-usdc.factory.bridge.near")
     → token_id_to_address.get(&(Eth, "eth-usdc.factory.bridge.near"))
     → None
   → env::panic_str("ERR_FAILED_TO_GET_TOKEN_ADDRESS")

5. Alice's 1000 tokens are permanently locked. sign_transfer will panic
   on every retry. No cancel/refund function exists.
``` [7](#0-6) [6](#0-5) [8](#0-7)

### Citations

**File:** near/omni-bridge/src/lib.rs (L447-469)
```rust
    pub fn sign_transfer(
        &mut self,
        transfer_id: TransferId,
        fee_recipient: Option<AccountId>,
        fee: &Option<Fee>,
    ) -> Promise {
        let transfer_message = self.get_transfer_message(transfer_id);

        if let Some(fee) = &fee {
            require!(
                &transfer_message.fee == fee,
                BridgeError::InvalidFee.as_ref()
            );
        }

        let token_address = self
            .get_token_address(
                transfer_message.get_destination_chain(),
                self.get_token_id(&transfer_message.token),
            )
            .unwrap_or_else(|| {
                env::panic_str(BridgeError::FailedToGetTokenAddress.to_string().as_str())
            });
```

**File:** near/omni-bridge/src/lib.rs (L1360-1366)
```rust
    pub fn get_token_address(
        &self,
        chain_kind: ChainKind,
        token: AccountId,
    ) -> Option<OmniAddress> {
        self.token_id_to_address.get(&(chain_kind, token))
    }
```

**File:** near/omni-bridge/src/lib.rs (L1368-1376)
```rust
    pub fn get_token_id(&self, address: &OmniAddress) -> AccountId {
        if let OmniAddress::Near(token_account_id) = address {
            token_account_id.clone()
        } else {
            self.token_address_to_id
                .get(address)
                .near_expect(BridgeError::TokenNotRegistered)
        }
    }
```

**File:** near/omni-bridge/src/lib.rs (L1604-1664)
```rust
    #[access_control_any(roles(Role::DAO))]
    #[payable]
    pub fn migrate_deployed_token(
        &mut self,
        origin_chain: ChainKind,
        old_token: AccountId,
        new_token: AccountId,
    ) {
        require!(
            env::attached_deposit() >= NEP141_DEPOSIT,
            BridgeError::NotEnoughAttachedDeposit.as_ref()
        );

        require!(
            self.deployed_tokens.remove(&old_token),
            BridgeError::OldTokenNotDeployed.as_ref(),
        );
        require!(
            self.deployed_tokens.insert(&new_token),
            BridgeError::TokenExists.as_ref()
        );
        self.deployed_tokens_v2.remove(&old_token);
        self.deployed_tokens_v2.insert(&new_token, &origin_chain);

        let origin_address = self
            .token_id_to_address
            .remove(&(origin_chain, old_token.clone()))
            .near_expect(BridgeError::FailedToGetTokenAddress);

        require!(
            self.token_id_to_address
                .insert(&(origin_chain, new_token.clone()), &origin_address)
                .is_none(),
            BridgeError::TokenExists.as_ref()
        );

        self.token_address_to_id
            .insert(&origin_address, &new_token)
            .near_expect(BridgeError::ExpectedToOverwriteTokenAddress);

        require!(
            self.migrated_tokens
                .insert(&old_token, &new_token)
                .is_none(),
            BridgeError::TokenAlreadyMigrated.as_ref()
        );

        ext_token::ext(new_token.clone())
            .with_static_gas(STORAGE_DEPOSIT_GAS)
            .with_attached_deposit(NEP141_DEPOSIT)
            .storage_deposit(&env::current_account_id(), Some(true))
            .detach();

        env::log_str(
            &OmniBridgeEvent::MigrateTokenEvent {
                old_token_id: old_token,
                new_token_id: new_token,
            }
            .to_log_string(),
        );
    }
```

**File:** near/omni-bridge/src/lib.rs (L2180-2192)
```rust
    fn add_transfer_message(
        &mut self,
        transfer_message: TransferMessage,
        message_owner: AccountId,
    ) -> NearToken {
        let storage_usage = env::storage_usage();
        require!(
            self.insert_raw_transfer(transfer_message, message_owner,)
                .is_none(),
            BridgeError::KeyExists.as_ref()
        );
        env::storage_byte_cost().saturating_mul((env::storage_usage() - storage_usage).into())
    }
```

**File:** near/omni-bridge/src/lib.rs (L2194-2224)
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

    fn remove_transfer_message_without_refund(
        &mut self,
        transfer_id: TransferId,
    ) -> TransferMessage {
        let transfer = self
            .pending_transfers
            .remove(&transfer_id)
            .map(storage::TransferMessageStorage::into_main)
            .near_expect(BridgeError::TransferNotExist);

        transfer.message
    }
```

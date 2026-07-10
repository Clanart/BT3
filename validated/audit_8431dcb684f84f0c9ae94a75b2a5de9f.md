### Title
Fee Tokens Permanently Frozen When `fee_recipient` Is a Smart Contract Unable to Call `claim_fee` - (File: `near/omni-bridge/src/lib.rs`)

### Summary

`claim_fee` enforces that only the `fee_recipient` account itself can invoke it. If the `fee_recipient` is a NEAR smart contract (e.g., a DAO-controlled or multisig relayer) that has no method to call `claim_fee`, the fee tokens and the associated pending transfer record are permanently locked in the bridge with no admin escape hatch.

### Finding Description

In `claim_fee_callback`, the contract enforces a strict caller-identity check:

```rust
require!(
    fee_recipient == *predecessor_account_id,
    BridgeError::OnlyFeeRecipientCanClaim.as_ref()
);
``` [1](#0-0) 

The `fee_recipient` is set by the trusted relayer when it calls `sign_transfer`, which embeds the value into the signed payload that is later verified on the destination chain and returned as a `FinTransferMessage` proof:

```rust
let transfer_payload = TransferMessagePayload {
    ...
    fee_recipient,
    ...
};
``` [2](#0-1) 

The `FinTransferMessage` proof carries `fee_recipient: Option<AccountId>`. If it is `None` or set to a contract that cannot call `claim_fee`, the callback panics or the check fails permanently:

```rust
let fee_recipient = fin_transfer.fee_recipient.unwrap_or_else(|| {
    env::panic_str(BridgeError::FeeRecipientNotSetOrEmpty.to_string().as_str());
});
``` [3](#0-2) 

Additionally, `claim_fee` itself is gated by `#[trusted_relayer]`, meaning the caller must be a registered trusted relayer AND must equal the `fee_recipient`: [4](#0-3) 

The only function that removes a pending transfer is `remove_transfer_message`, called exclusively from `claim_fee_callback` (for fee-bearing transfers): [5](#0-4) 

There is no admin function, timeout, or DAO-callable escape hatch to remove a pending transfer or recover its fee tokens. The `set_locked_tokens` admin function only adjusts accounting, not the actual token custody or the `pending_transfers` map: [6](#0-5) 

### Impact Explanation

When `process_fin_transfer_to_other_chain` stores a transfer in `pending_transfers`, the fee portion is locked in the bridge contract via `lock_tokens_if_needed`:

```rust
self.lock_tokens_if_needed(
    transfer_message.get_destination_chain(),
    &token,
    transfer_message.fee.fee.into(),
);
``` [7](#0-6) 

If `claim_fee` is never successfully called, those fee tokens remain in the bridge contract's custody indefinitely with no recovery path. The pending transfer record also remains in `pending_transfers` forever, permanently consuming storage and corrupting the bridge's accounting of locked tokens. This matches **Critical: Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows.**

### Likelihood Explanation

Relayers in production bridge deployments are commonly implemented as smart contracts (DAO-controlled relayers, multisig relayers, or automated keeper contracts). If such a contract is deployed as the trusted relayer and sets itself as `fee_recipient` in `sign_transfer`, but does not implement a method to call `claim_fee` on the bridge (e.g., it is an immutable contract, or its governance cannot authorize such a call), the fee is permanently frozen. This is a realistic operational scenario, not a theoretical one.

### Recommendation

Remove the `fee_recipient == predecessor_account_id` restriction from `claim_fee_callback`. Instead, allow any caller (or at minimum any trusted relayer) to invoke `claim_fee` for a given transfer, while still sending the fee to the `fee_recipient` recorded in the proof. The `fee_recipient` identity is already cryptographically bound in the signed `FinTransferMessage` proof, so removing the caller restriction does not weaken security — it only removes the unnecessary liveness dependency on the `fee_recipient` being able to initiate the call.

### Proof of Concept

1. A DAO-controlled NEAR smart contract `dao-relayer.near` is registered as a trusted relayer.
2. A user initiates a NEAR→EVM transfer with `fee = 100`.
3. `dao-relayer.near` calls `sign_transfer(transfer_id, fee_recipient = Some("dao-relayer.near"), fee = Some(...))`.
4. `dao-relayer.near` finalizes the transfer on EVM; the EVM emits a `FinTransfer` event with `fee_recipient = "dao-relayer.near"`.
5. `dao-relayer.near` attempts to call `claim_fee` on NEAR, but its governance contract has no method to dispatch a `claim_fee` call to the bridge (it is an immutable contract deployed before `claim_fee` existed, or its ABI does not include this call).
6. No other account can call `claim_fee` because `fee_recipient == predecessor_account_id` is enforced.
7. The 100-token fee remains locked in the bridge contract forever. The pending transfer record is never removed from `pending_transfers`. No admin function exists to recover the tokens. [8](#0-7)

### Citations

**File:** near/omni-bridge/src/lib.rs (L491-500)
```rust
        let transfer_payload = TransferMessagePayload {
            prefix: PayloadType::TransferMessage,
            destination_nonce: transfer_message.destination_nonce,
            transfer_id,
            token_address,
            amount: U128(amount_to_transfer),
            recipient: transfer_message.recipient,
            fee_recipient,
            message,
        };
```

**File:** near/omni-bridge/src/lib.rs (L1054-1064)
```rust
    #[payable]
    #[trusted_relayer]
    #[pause(except(roles(Role::DAO)))]
    pub fn claim_fee(&mut self, #[serializer(borsh)] args: ClaimFeeArgs) -> Promise {
        self.verify_proof(args.chain_kind, args.prover_args).then(
            Self::ext(env::current_account_id())
                .with_attached_deposit(env::attached_deposit())
                .with_static_gas(CLAIM_FEE_CALLBACK_GAS)
                .claim_fee_callback(&env::predecessor_account_id()),
        )
    }
```

**File:** near/omni-bridge/src/lib.rs (L1066-1134)
```rust
    #[private]
    #[payable]
    pub fn claim_fee_callback(
        &mut self,
        #[serializer(borsh)] predecessor_account_id: &AccountId,
        #[callback_result]
        #[serializer(borsh)]
        call_result: Result<ProverResult, PromiseError>,
    ) -> PromiseOrValue<()> {
        let Ok(ProverResult::FinTransfer(fin_transfer)) = call_result else {
            env::panic_str(BridgeError::InvalidProofMessage.to_string().as_str())
        };

        let fee_recipient = fin_transfer.fee_recipient.unwrap_or_else(|| {
            env::panic_str(BridgeError::FeeRecipientNotSetOrEmpty.to_string().as_str());
        });

        require!(
            fee_recipient == *predecessor_account_id,
            BridgeError::OnlyFeeRecipientCanClaim.as_ref()
        );
        require!(
            self.factories
                .get(&fin_transfer.emitter_address.get_chain())
                == Some(fin_transfer.emitter_address),
            BridgeError::UnknownFactory.as_ref()
        );

        let transfer_message = self.remove_transfer_message(fin_transfer.transfer_id);

        if let Some(origin_transfer_id) = transfer_message.origin_transfer_id.clone() {
            let mut fast_transfer = FastTransfer::from_transfer(
                transfer_message.clone(),
                self.get_token_id(&transfer_message.token),
            );
            fast_transfer.transfer_id = origin_transfer_id;

            if let Some(fast_transfer_status) = self.get_fast_transfer_status(&fast_transfer.id()) {
                // For fast transfers we need to wait for finalization of the first leg (Origin chain -> Near) before allowing fee claim.
                // This confirms that fast transfer was executed with correct parameters.
                // Othewise malicious relayer can create a fast transfer with arbitrary high fee and claim it here.
                if fast_transfer_status.finalised {
                    self.remove_fast_transfer(&fast_transfer.id());
                } else {
                    env::panic_str(BridgeError::FastTransferNotFinalised.to_string().as_str());
                }
            }
        }

        let token = self.get_token_id(&transfer_message.token);
        let token_address = self
            .get_token_address(transfer_message.get_destination_chain(), token.clone())
            .unwrap_or_else(|| {
                env::panic_str(BridgeError::FailedToGetTokenAddress.to_string().as_str())
            });

        let denormalized_amount = Self::denormalize_amount(
            fin_transfer.amount.0,
            self.token_decimals
                .get(&token_address)
                .near_expect(BridgeError::TokenDecimalsNotFound),
        );
        // Fee includes both the user-specified fee and any dust lost during decimal
        // normalization (see `normalize_amount`). Since `denormalize(normalize(x)) <= x`
        // due to floor division, the difference naturally captures the normalization remainder.
        let fee = transfer_message.amount.0 - denormalized_amount;

        self.send_fee_internal(&transfer_message, fee_recipient, fee)
    }
```

**File:** near/omni-bridge/src/lib.rs (L2002-2006)
```rust
        self.lock_tokens_if_needed(
            transfer_message.get_destination_chain(),
            &token,
            transfer_message.fee.fee.into(),
        );
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

**File:** near/omni-bridge/src/token_lock.rs (L38-44)
```rust
    #[access_control_any(roles(Role::DAO, Role::TokenLockController))]
    pub fn set_locked_tokens(&mut self, args: Vec<SetLockedTokenArgs>) {
        for arg in args {
            self.locked_tokens
                .insert(&(arg.chain_kind, arg.token_id), &arg.amount.0);
        }
    }
```

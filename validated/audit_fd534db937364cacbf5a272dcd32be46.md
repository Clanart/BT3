### Title
Unchecked `relayer` Field in `FastFinTransferMsg` Enables Fee Theft via Front-Running — (File: near/omni-bridge/src/lib.rs)

---

### Summary

In `fast_fin_transfer`, the `signer_id` (the actual NEAR account executing the fast transfer) is verified to be a trusted relayer, but the `relayer` field inside `FastFinTransferMsg` — which determines who receives the fee at finalization — is accepted without any check that it equals `signer_id`. Because `FastTransfer::id()` is computed from fields that do **not** include `relayer`, two competing trusted relayers can submit the same fast transfer with different `relayer` values. The first to land wins the fee; the second is refunded their tokens but earns nothing.

---

### Finding Description

`FastFinTransferMsg` carries a `relayer: AccountId` field that is caller-supplied:

```rust
pub struct FastFinTransferMsg {
    pub transfer_id: UnifiedTransferId,
    pub recipient: OmniAddress,
    pub fee: Fee,
    pub msg: String,
    pub amount: U128,
    pub storage_deposit_amount: Option<U128>,
    pub relayer: AccountId,   // ← arbitrary, not validated
}
``` [1](#0-0) 

Inside `fast_fin_transfer`, the only identity check is that `signer_id` is a trusted relayer. The `relayer` field from the message is passed directly to `add_fast_transfer` without any equality check against `signer_id`:

```rust
require!(self.is_trusted_relayer(&signer_id), "Relayer is not active");
// ...
self.fast_fin_transfer_to_other_chain(
    &fast_transfer,
    signer_id,
    fast_fin_transfer_msg.relayer,   // ← unchecked, can be any account
);
``` [2](#0-1) [3](#0-2) 

`FastTransfer::id()` is a SHA-256 hash of the Borsh-serialized `FastTransfer` struct, which contains `transfer_id`, `token_id`, `amount`, `fee`, `recipient`, and `msg` — but **not** `relayer`:

```rust
pub struct FastTransfer {
    pub transfer_id: UnifiedTransferId,
    pub token_id: AccountId,
    pub amount: U128,
    pub fee: Fee,
    pub recipient: OmniAddress,
    pub msg: String,
    // relayer is NOT here
}

pub fn id(&self) -> FastTransferId {
    FastTransferId(utils::sha256(&borsh::to_vec(self).unwrap()))
}
``` [4](#0-3) 

`add_fast_transfer` stores the `relayer` from the message as the fee recipient in `FastTransferStatus`:

```rust
fn add_fast_transfer(
    &mut self,
    fast_transfer: &FastTransfer,
    relayer: AccountId,
    storage_owner: AccountId,
) -> NearToken {
    // ...
    self.fast_transfers.insert(
        &fast_transfer.id(),
        &FastTransferStatusStorage::V0(FastTransferStatus {
            relayer,          // ← stored as fee recipient
            storage_owner,
            finalised: false,
        }),
    )
``` [5](#0-4) 

At finalization (`fin_transfer_callback` / `process_fin_transfer_to_other_chain`), the stored `status.relayer` is used as the fee recipient — not the account that calls `fin_transfer`:

```rust
let (recipient, msg, fee_recipient) = match fast_transfer_status {
    Some(status) => {
        // ...
        (status.relayer.clone(), String::new(), status.relayer)
    }
``` [6](#0-5) 

---

### Impact Explanation

A trusted relayer (Relayer B) can observe Relayer A's pending `FastFinTransfer` transaction in the NEAR mempool and submit an identical fast transfer for the same `transfer_id` but with `relayer: B`. Because `FastTransfer::id()` does not include the `relayer` field, both transactions hash to the same key. Whichever lands first wins: B's `FastTransferStatus { relayer: B }` is stored, A's transaction fails with `ERR_FAST_TRANSFER_ALREADY_PERFORMED` and A's tokens are refunded. When `fin_transfer` is later called, the fee is paid to B, not A.

Additionally, any trusted relayer can unconditionally redirect their earned fee to any arbitrary account by setting `relayer` to that account — bypassing any intended fee-distribution policy.

This is fee misdirection / accounting corruption that misdirects bridge value, matching the **High** allowed impact: *"Balance, decimal, fee, token-mapping, or accounting corruption that breaks bridge collateralization or misdirects value."*

---

### Likelihood Explanation

The attack requires the adversary to be a registered trusted relayer and to have sufficient token balance to front-run the fast transfer. Both conditions are realistic in a competitive relayer market. NEAR's mempool is observable before block inclusion, making the front-running window real. The `relayer` field being unchecked is also exploitable without front-running: any trusted relayer can simply set `relayer` to an arbitrary account at will.

---

### Recommendation

Enforce that `fast_fin_transfer_msg.relayer == signer_id` before proceeding, or remove the `relayer` field entirely and use `signer_id` directly as the fee recipient stored in `FastTransferStatus`. For example, in `fast_fin_transfer`:

```rust
require!(
    fast_fin_transfer_msg.relayer == signer_id,
    "Relayer field must match the transaction signer"
);
```

This binds the fee recipient to the actual executor, eliminating both the front-running vector and the arbitrary fee-redirection vector.

---

### Proof of Concept

1. Trusted relayer A calls `ft_transfer_call` on the token contract with `msg: FastFinTransfer { transfer_id: X, amount: N, fee: F, recipient: R, relayer: A }`.
2. Trusted relayer B observes A's pending transaction and immediately calls `ft_transfer_call` with identical parameters except `relayer: B`, submitting with higher gas priority.
3. B's transaction is included first. `add_fast_transfer` stores `FastTransferStatus { relayer: B, finalised: false }` under `fast_transfer.id()`.
4. A's transaction is included next. `add_fast_transfer` finds the key already occupied and panics with `ERR_FAST_TRANSFER_ALREADY_PERFORMED`. A's tokens are refunded via the `ft_transfer_call` return value.
5. When any relayer later calls `fin_transfer` with proof of the origin-chain `InitTransfer` for nonce X, `process_fin_transfer_to_near` (or `process_fin_transfer_to_other_chain`) reads `status.relayer = B` and sends the fee to B.
6. A spent gas and lost the fee opportunity; B earned the fee without having been the legitimate executor. [7](#0-6) [1](#0-0) [5](#0-4) [8](#0-7)

### Citations

**File:** near/omni-types/src/lib.rs (L504-513)
```rust
#[derive(Serialize, Deserialize, BorshSerialize, BorshDeserialize, Debug, Clone)]
pub struct FastFinTransferMsg {
    pub transfer_id: UnifiedTransferId,
    pub recipient: OmniAddress,
    pub fee: Fee,
    pub msg: String,
    pub amount: U128,
    pub storage_deposit_amount: Option<U128>,
    pub relayer: AccountId,
}
```

**File:** near/omni-types/src/lib.rs (L844-858)
```rust
#[derive(Debug, Clone)]
pub struct FastTransfer {
    pub transfer_id: UnifiedTransferId,
    pub token_id: AccountId,
    pub amount: U128,
    pub fee: Fee,
    pub recipient: OmniAddress,
    pub msg: String,
}

impl FastTransfer {
    #[allow(clippy::missing_panics_doc)]
    pub fn id(&self) -> FastTransferId {
        FastTransferId(utils::sha256(&borsh::to_vec(self).unwrap()))
    }
```

**File:** near/omni-bridge/src/lib.rs (L748-836)
```rust
    #[allow(clippy::needless_pass_by_value)]
    fn fast_fin_transfer(
        &mut self,
        token_id: AccountId,
        amount: U128,
        signer_id: AccountId,
        fast_fin_transfer_msg: FastFinTransferMsg,
    ) -> PromiseOrPromiseIndexOrValue<U128> {
        require!(self.is_trusted_relayer(&signer_id), "Relayer is not active");

        let origin_token = self
            .get_token_address(
                fast_fin_transfer_msg.transfer_id.origin_chain,
                token_id.clone(),
            )
            .near_expect(BridgeError::TokenNotFound);

        let decimals = self
            .token_decimals
            .get(&origin_token)
            .near_expect(BridgeError::TokenDecimalsNotFound);

        let denormalized_amount =
            Self::denormalize_amount(fast_fin_transfer_msg.amount.0, decimals);
        let denormalized_fee = Self::denormalize_fee(&fast_fin_transfer_msg.fee, decimals);
        require!(
            denormalized_amount == amount.0 + denormalized_fee.fee.0,
            BridgeError::InvalidFastTransferAmount.as_ref()
        );

        if self.is_unified_transfer_finalised(&fast_fin_transfer_msg.transfer_id) {
            env::panic_str(BridgeError::TransferAlreadyFinalised.to_string().as_str());
        }

        let fast_transfer = FastTransfer {
            token_id: token_id.clone(),
            recipient: fast_fin_transfer_msg.recipient.clone(),
            amount: U128(denormalized_amount),
            fee: denormalized_fee,
            transfer_id: fast_fin_transfer_msg.transfer_id,
            msg: fast_fin_transfer_msg.msg,
        };

        if let OmniAddress::Near(recipient) = fast_fin_transfer_msg.recipient {
            let storage_deposit_amount = fast_fin_transfer_msg
                .storage_deposit_amount
                .map(|amount| amount.0)
                .unwrap_or_default();
            if storage_deposit_amount > 0 {
                self.update_storage_balance(
                    signer_id.clone(),
                    NearToken::from_yoctonear(storage_deposit_amount),
                    NearToken::from_yoctonear(0),
                );
            }

            let deposit_action = StorageDepositAction {
                account_id: recipient,
                token_id,
                storage_deposit_amount: fast_fin_transfer_msg
                    .storage_deposit_amount
                    .map(|amount| amount.0),
            };

            Self::check_or_pay_ft_storage(
                &deposit_action,
                &mut NearToken::from_yoctonear(storage_deposit_amount),
            )
            .then(
                Self::ext(env::current_account_id())
                    .with_static_gas(
                        FAST_TRANSFER_CALLBACK_GAS.saturating_add(FT_TRANSFER_CALL_GAS),
                    )
                    .fast_fin_transfer_to_near_callback(
                        &fast_transfer,
                        signer_id,
                        fast_fin_transfer_msg.relayer,
                    ),
            )
            .into()
        } else {
            self.fast_fin_transfer_to_other_chain(
                &fast_transfer,
                signer_id,
                fast_fin_transfer_msg.relayer,
            );
            PromiseOrPromiseIndexOrValue::Value(U128(0))
        }
    }
```

**File:** near/omni-bridge/src/lib.rs (L1887-1895)
```rust
        // If fast transfer happened, change recipient and fee recipient to the relayer that executed fast transfer
        let (recipient, msg, fee_recipient) = match fast_transfer_status {
            Some(status) => {
                require!(
                    !status.finalised,
                    BridgeError::FastTransferAlreadyFinalised.as_ref()
                );
                self.remove_fast_transfer(&fast_transfer.id());
                (status.relayer.clone(), String::new(), status.relayer)
```

**File:** near/omni-bridge/src/lib.rs (L2246-2268)
```rust
    fn add_fast_transfer(
        &mut self,
        fast_transfer: &FastTransfer,
        relayer: AccountId,
        storage_owner: AccountId,
    ) -> NearToken {
        let storage_usage = env::storage_usage();
        require!(
            self.fast_transfers
                .insert(
                    &fast_transfer.id(),
                    &FastTransferStatusStorage::V0(FastTransferStatus {
                        relayer,
                        storage_owner,
                        finalised: false,
                    }),
                )
                .is_none(),
            BridgeError::FastTransferAlreadyPerformed.as_ref()
        );
        env::storage_byte_cost()
            .saturating_mul((env::storage_usage().saturating_sub(storage_usage)).into())
    }
```

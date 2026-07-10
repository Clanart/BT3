### Title
Sender Can Call `update_transfer_fee` After Fast Transfer Is Performed, Breaking Fast Transfer Matching and Causing Relayer Fund Loss — (`near/omni-bridge/src/lib.rs`)

---

### Summary

`update_transfer_fee` does not check whether a fast transfer has already been performed for a pending transfer. Because `FastTransferId` is computed as a hash of the entire `FastTransfer` struct (including the fee), a sender who raises the fee after a relayer has fronted funds causes the fast transfer lookup at finalization to silently miss, redirecting the full payout to the original recipient instead of the relayer and leaving the relayer uncompensated.

---

### Finding Description

**Fast transfer ID is fee-sensitive.**

`FastTransfer::id()` hashes the entire `FastTransfer` struct via Borsh:

```rust
pub fn id(&self) -> FastTransferId {
    FastTransferId(utils::sha256(&borsh::to_vec(self).unwrap()))
}
``` [1](#0-0) 

`FastTransfer` includes `fee` as a field:

```rust
pub struct FastTransfer {
    pub transfer_id: UnifiedTransferId,
    pub token_id: AccountId,
    pub amount: U128,
    pub fee: Fee,
    pub recipient: OmniAddress,
    pub msg: String,
}
``` [2](#0-1) 

**At finalization, the fast transfer is reconstructed from the current (possibly mutated) `TransferMessage`.**

`process_fin_transfer_to_near` calls `FastTransfer::from_transfer` using the live `transfer_message`, which carries whatever fee is currently stored in `pending_transfers`:

```rust
let fast_transfer = FastTransfer::from_transfer(transfer_message.clone(), token.clone());
let fast_transfer_status = self.get_fast_transfer_status(&fast_transfer.id());
``` [3](#0-2) 

`from_transfer` copies the fee directly from the transfer message:

```rust
pub fn from_transfer(transfer: TransferMessage, token_id: AccountId) -> Self {
    Self {
        ...
        fee: transfer.fee,
        ...
    }
}
``` [4](#0-3) 

**`update_transfer_fee` has no guard against an already-performed fast transfer.**

The only guard in `update_transfer_fee` is that `origin_transfer_id` must be `None` (i.e., the transfer is not itself the second leg of a fast transfer). There is no check that a fast transfer has already been recorded for this `transfer_id`:

```rust
require!(
    transfer.message.origin_transfer_id.is_none(),
    BridgeError::UpdateFeeNotAllowedForTransfer.as_ref()
);
``` [5](#0-4) 

The sender is then allowed to raise the token fee:

```rust
require!(
    fee.fee >= current_fee.fee && fee.fee < transfer.message.amount,
    BridgeError::InvalidFee.as_ref()
);
require!(
    fee.fee == current_fee.fee
        || OmniAddress::Near(env::predecessor_account_id())
            == transfer.message.sender,
    BridgeError::SenderCanUpdateTokenFeeOnly.as_ref()
);
``` [6](#0-5) 

---

### Impact Explanation

**Attack flow:**

1. Sender initiates a transfer with `fee = X`. A `TransferMessage` with `fee = X` is stored in `pending_transfers`.
2. A trusted relayer performs a fast transfer via `fast_fin_transfer`, fronting `amount − X` tokens to the recipient. A `FastTransfer` with `fee = X` is stored in `fast_transfers` under key `sha256(borsh(FastTransfer{..., fee: X, ...}))`.
3. Before the original proof is submitted, the sender calls `update_transfer_fee` and raises the fee to `Y > X`. The `TransferMessage` in `pending_transfers` is updated to `fee = Y`. No check prevents this.
4. A relayer submits the proof and calls `fin_transfer` → `fin_transfer_callback` → `process_fin_transfer_to_near`. The bridge reconstructs `FastTransfer` from the updated message (fee = Y) and computes `sha256(borsh(FastTransfer{..., fee: Y, ...}))`. This key does **not** exist in `fast_transfers`.
5. `get_fast_transfer_status` returns `None`. The bridge falls into the `None` branch and sends `amount − Y` tokens to the **original recipient** (not the relayer).
6. The relayer's `FastTransferStatus` entry remains in `fast_transfers` permanently (never removed, never finalized), and the relayer is never reimbursed for the `amount − X` tokens they already sent.

**Concrete losses:**
- The relayer loses `amount − X` tokens (direct theft of relayer funds by the sender).
- The recipient receives tokens twice: once from the relayer's fast transfer and once from the finalization payout.
- The orphaned `FastTransferStatus` entry can never be claimed or cleaned up.

This matches the allowed impact: **balance/accounting corruption that breaks bridge collateralization and misdirects value**.

---

### Likelihood Explanation

The sender is an unprivileged bridge user who controls the timing of `update_transfer_fee`. The window between a relayer's fast transfer and the submission of the finalization proof is non-trivial (cross-chain proof generation takes time). The sender has a clear financial incentive: they get their recipient paid twice while the relayer bears the loss. The call is cheap and requires no special access.

---

### Recommendation

In `update_transfer_fee`, add a guard that rejects the update if a fast transfer has already been recorded for the given `transfer_id`:

```rust
// Reject fee update if a fast transfer has already been performed
let fast_transfer = FastTransfer::from_transfer(transfer.message.clone(), token_id);
require!(
    self.get_fast_transfer_status(&fast_transfer.id()).is_none(),
    BridgeError::FastTransferAlreadyPerformed.as_ref()
);
```

Alternatively, store the fast transfer lookup key independently of the fee (e.g., keyed only by `UnifiedTransferId`), so that a fee change cannot cause a lookup miss at finalization.

---

### Proof of Concept

```
1. Sender calls ft_transfer_call → init_transfer
   → pending_transfers[nonce=1] = TransferMessage { fee: 100, amount: 10_000, recipient: Alice }

2. Relayer calls ft_transfer_call (FastFinTransfer)
   → fast_fin_transfer stores:
     fast_transfers[sha256(FastTransfer{fee:100,...})] = FastTransferStatus { relayer: Relayer, finalised: false }
   → Relayer sends 9_900 tokens to Alice

3. Sender calls update_transfer_fee(nonce=1, fee=9_000)
   → pending_transfers[nonce=1].fee = 9_000
   → No error; no fast-transfer guard exists

4. Relayer submits proof → fin_transfer_callback → process_fin_transfer_to_near
   → fast_transfer = FastTransfer::from_transfer(msg{fee:9_000}, ...)
   → fast_transfer.id() = sha256(FastTransfer{fee:9_000,...})  ← different key
   → get_fast_transfer_status returns None
   → Bridge sends 1_000 tokens to Alice (amount - new_fee)
   → Relayer's entry in fast_transfers is never removed

Result:
  Alice received 9_900 (from relayer) + 1_000 (from finalization) = 10_900 tokens
  Relayer lost 9_900 tokens, never reimbursed
  fast_transfers entry for Relayer is permanently orphaned
```

### Citations

**File:** near/omni-types/src/lib.rs (L843-852)
```rust
#[near(serializers=[borsh, json])]
#[derive(Debug, Clone)]
pub struct FastTransfer {
    pub transfer_id: UnifiedTransferId,
    pub token_id: AccountId,
    pub amount: U128,
    pub fee: Fee,
    pub recipient: OmniAddress,
    pub msg: String,
}
```

**File:** near/omni-types/src/lib.rs (L854-858)
```rust
impl FastTransfer {
    #[allow(clippy::missing_panics_doc)]
    pub fn id(&self) -> FastTransferId {
        FastTransferId(utils::sha256(&borsh::to_vec(self).unwrap()))
    }
```

**File:** near/omni-types/src/lib.rs (L869-882)
```rust
impl FastTransfer {
    pub fn from_transfer(transfer: TransferMessage, token_id: AccountId) -> Self {
        Self {
            transfer_id: UnifiedTransferId {
                origin_chain: transfer.get_origin_chain(),
                kind: TransferIdKind::Nonce(transfer.origin_nonce),
            },
            token_id,
            amount: transfer.amount,
            fee: transfer.fee,
            recipient: transfer.recipient,
            msg: transfer.msg,
        }
    }
```

**File:** near/omni-bridge/src/lib.rs (L393-396)
```rust
                require!(
                    transfer.message.origin_transfer_id.is_none(),
                    BridgeError::UpdateFeeNotAllowedForTransfer.as_ref()
                );
```

**File:** near/omni-bridge/src/lib.rs (L399-409)
```rust
                require!(
                    fee.fee >= current_fee.fee && fee.fee < transfer.message.amount,
                    BridgeError::InvalidFee.as_ref()
                );

                require!(
                    fee.fee == current_fee.fee
                        || OmniAddress::Near(env::predecessor_account_id())
                            == transfer.message.sender,
                    BridgeError::SenderCanUpdateTokenFeeOnly.as_ref()
                );
```

**File:** near/omni-bridge/src/lib.rs (L1878-1879)
```rust
        let fast_transfer = FastTransfer::from_transfer(transfer_message.clone(), token.clone());
        let fast_transfer_status = self.get_fast_transfer_status(&fast_transfer.id());
```

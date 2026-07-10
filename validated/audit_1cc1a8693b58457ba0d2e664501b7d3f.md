### Title
Missing Zero-Address Validation in `init_transfer` Allows Permanent Irrecoverable Fund Lock — (`near/omni-bridge/src/lib.rs`)

---

### Summary

The NEAR bridge's `init_transfer` function accepts any `OmniAddress` as the cross-chain recipient without checking whether it is a zero address. A user can initiate a transfer to `OmniAddress::Eth(H160::ZERO)` (or any EVM-chain zero address). Tokens are immediately burned or locked on NEAR, the MPC signs the payload, but every subsequent attempt to finalize the transfer on the EVM side permanently reverts because OpenZeppelin ERC20 `mint` and `transfer` both revert on `address(0)`. No cancel or refund path exists, so the funds are irrecoverably lost.

---

### Finding Description

`init_transfer` in `near/omni-bridge/src/lib.rs` validates only that the recipient chain is not NEAR:

```rust
require!(
    init_transfer_msg.recipient.get_chain() != ChainKind::Near,
    BridgeError::InvalidRecipientChain.as_ref()
);
``` [1](#0-0) 

There is no call to `OmniAddress::is_zero()`, which is already implemented in the type:

```rust
pub fn is_zero(&self) -> bool {
    match self {
        Self::Eth(address) | Self::Arb(address) | ... => address.is_zero(),
        ...
    }
}
``` [2](#0-1) 

After `init_transfer_internal` succeeds, the token is burned or locked on NEAR and a `TransferMessage` is stored in `pending_transfers`: [3](#0-2) 

The relayer then calls `sign_transfer`, which constructs a `TransferMessagePayload` containing the zero-address recipient and requests an MPC signature: [4](#0-3) 

The signed payload is submitted to the EVM `finTransfer`. For bridge tokens the contract calls:

```solidity
IBridgeToken(payload.tokenAddress).mint(
    payload.recipient,   // address(0)
    payload.amount
);
``` [5](#0-4) 

OpenZeppelin's `_mint` unconditionally reverts when `account == address(0)`. The same applies to the `safeTransfer` path for non-bridge ERC20 tokens: [6](#0-5) 

Because the EVM transaction reverts entirely, `completedTransfers[nonce]` is also rolled back, so the relayer can retry — but every retry reverts for the same reason. The NEAR-side `pending_transfers` entry can only be removed via `claim_fee_callback`, which requires a proof of successful EVM finalization that can never be produced: [7](#0-6) 

There is no cancel, timeout, or admin-rescue path for stuck `pending_transfers`.

---

### Impact Explanation

**Critical — Permanent irrecoverable lock of user funds in the bridge flow.**

Any tokens sent via `init_transfer` to a zero EVM address are burned or locked on NEAR and can never be released. The EVM finalization step will revert on every attempt for all standard ERC20 and bridge tokens (OpenZeppelin reverts on `mint`/`transfer` to `address(0)`). No recovery mechanism exists in the contract.

---

### Likelihood Explanation

**Medium.** The scenario is reachable by any unprivileged user via `ft_transfer_call` → `BridgeOnTransferMsg::InitTransfer`. It can occur accidentally (user pastes a zero address) or deliberately (a user wishing to permanently destroy bridged tokens). The `OmniAddress` type accepts `H160::ZERO` as a structurally valid value, so no special encoding is required.

---

### Recommendation

Add a zero-address guard in `init_transfer` immediately after the chain-kind check:

```rust
require!(
    init_transfer_msg.recipient.get_chain() != ChainKind::Near,
    BridgeError::InvalidRecipientChain.as_ref()
);
require!(
    !init_transfer_msg.recipient.is_zero(),
    BridgeError::InvalidRecipientAddress.as_ref()
);
```

The `is_zero()` method and the `InvalidRecipientAddress` error variant are already present in the codebase: [2](#0-1) [8](#0-7) 

Apply the same guard in the EVM `initTransfer` for the `recipient` string (reject empty strings) and in the StarkNet `init_transfer` for consistency.

---

### Proof of Concept

1. User holds a bridged ERC20 token on NEAR (e.g., `weth.bridge.near`).
2. User calls `ft_transfer_call` on the token contract with:
   ```json
   {
     "receiver_id": "omni-bridge.near",
     "amount": "1000000000000000000",
     "msg": "{\"InitTransfer\":{\"recipient\":\"eth:0x0000000000000000000000000000000000000000\",\"fee\":\"0\",\"native_token_fee\":\"0\"}}"
   }
   ```
3. `init_transfer` passes the chain-kind check (`Eth != Near`), burns the token on NEAR, stores the `TransferMessage`, and returns `U128(0)` (success).
4. Relayer calls `sign_transfer`; MPC signs a payload with `recipient = 0x0000000000000000000000000000000000000000`.
5. Relayer submits the signed payload to EVM `finTransfer`.
6. EVM executes `IBridgeToken(tokenAddress).mint(address(0), amount)` → reverts with `"ERC20: mint to the zero address"`.
7. The entire EVM transaction reverts; `completedTransfers[nonce]` is not set.
8. Every subsequent relay attempt reverts identically.
9. The user's tokens are permanently locked in the NEAR bridge with no recovery path.

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

**File:** near/omni-bridge/src/lib.rs (L531-534)
```rust
        require!(
            init_transfer_msg.recipient.get_chain() != ChainKind::Near,
            BridgeError::InvalidRecipientChain.as_ref()
        );
```

**File:** near/omni-bridge/src/lib.rs (L1094-1094)
```rust
        let transfer_message = self.remove_transfer_message(fin_transfer.transfer_id);
```

**File:** near/omni-bridge/src/lib.rs (L1829-1864)
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
```

**File:** near/omni-types/src/lib.rs (L299-313)
```rust
    pub fn is_zero(&self) -> bool {
        match self {
            Self::Eth(address)
            | Self::Arb(address)
            | Self::Base(address)
            | Self::Bnb(address)
            | Self::Pol(address)
            | Self::HyperEvm(address)
            | Self::Abs(address) => address.is_zero(),
            Self::Near(address) => *address == ZERO_ACCOUNT_ID,
            Self::Sol(address) | Self::Fogo(address) => address.is_zero(),
            Self::Btc(address) | Self::Zcash(address) => address.is_empty(),
            Self::Strk(address) => address.is_zero(),
        }
    }
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L337-355)
```text
        } else if (isBridgeToken[payload.tokenAddress]) {
            if (payload.message.length == 0) {
                IBridgeToken(payload.tokenAddress).mint(
                    payload.recipient,
                    payload.amount
                );
            } else {
                IBridgeToken(payload.tokenAddress).mint(
                    payload.recipient,
                    payload.amount,
                    payload.message
                );
            }
        } else {
            IERC20(payload.tokenAddress).safeTransfer(
                payload.recipient,
                payload.amount
            );
        }
```

**File:** near/omni-types/src/errors.rs (L31-31)
```rust
    InvalidRecipientAddress,
```

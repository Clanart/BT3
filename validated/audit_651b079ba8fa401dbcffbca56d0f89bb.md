### Title
Fee-on-Transfer Token Accounting Mismatch in `initTransfer` Breaks Bridge Collateralization — (File: evm/src/omni-bridge/contracts/OmniBridge.sol)

---

### Summary

The EVM `OmniBridge.initTransfer` function records the caller-supplied `amount` parameter directly in the emitted `InitTransfer` event without verifying that the actual tokens received by the contract equal `amount`. For fee-on-transfer (deflationary/rebasable) ERC20 tokens, `safeTransferFrom` delivers fewer tokens than `amount` to the bridge, but the event records the full `amount`. The NEAR bridge uses this event as a proof to release or mint tokens on the destination chain, causing it to release more tokens than the EVM bridge actually holds. This breaks bridge collateralization.

---

### Finding Description

In `OmniBridge.initTransfer`, for non-bridge, non-custom-minter ERC20 tokens, the contract executes:

```solidity
IERC20(tokenAddress).safeTransferFrom(
    msg.sender,
    address(this),
    amount          // requested amount, not verified as received
);
```

and then immediately emits:

```solidity
emit BridgeTypes.InitTransfer(
    msg.sender,
    tokenAddress,
    currentOriginNonce,
    amount,         // caller-supplied, not actual received balance
    fee,
    nativeFee,
    recipient,
    message
);
``` [1](#0-0) 

There is no balance-before/balance-after check. For a fee-on-transfer token, `safeTransferFrom(sender, bridge, 1000)` may deliver only 990 tokens to the bridge (10 taken as a transfer fee by the token contract), yet the event records `amount = 1000`.

The NEAR bridge's `fin_transfer_callback` decodes the proof of this event and uses `init_transfer.amount` directly to compute how many tokens to release or mint to the recipient:

```rust
let transfer_message = TransferMessage {
    ...
    amount: Self::denormalize_amount(init_transfer.amount.0, decimals).into(),
    ...
};
``` [2](#0-1) 

So the NEAR bridge releases `1000` tokens (after decimal normalization) while the EVM bridge only holds `990`. The same pattern exists in the StarkNet bridge:

```cairo
let success = IERC20Dispatcher { contract_address: token_address }
    .transfer_from(caller, get_contract_address(), amount.into());
assert(success, 'ERR_TRANSFER_FROM_FAILED');
// ... then emits `amount` in the event
``` [3](#0-2) 

---

### Impact Explanation

Every `initTransfer` call with a fee-on-transfer token creates a deficit: the EVM bridge holds `amount - fee` tokens but the NEAR side releases `amount` tokens. Repeated over many transfers, the EVM bridge vault becomes progressively undercollateralized. When users attempt to bridge back (NEAR → EVM), the EVM bridge will eventually be unable to fulfill `safeTransfer` calls because its actual token balance is less than the sum of all recorded obligations. This constitutes **balance/accounting corruption that breaks bridge collateralization** — a High-severity impact per the allowed scope.

---

### Likelihood Explanation

The `initTransfer` function accepts any ERC20 `tokenAddress` that is not in `isBridgeToken` or `customMinters`. There is no token whitelist. Fee-on-transfer tokens (reflection tokens, tokens with built-in transfer taxes) are common in the wild. Any unprivileged user can call `initTransfer` with such a token. No special role or leaked key is required. The attacker simply needs to hold a fee-on-transfer ERC20 and call `initTransfer` on the bridge.

---

### Recommendation

Record the actual received balance rather than the caller-supplied `amount`. Use a balance-before/balance-after pattern:

```solidity
uint256 balanceBefore = IERC20(tokenAddress).balanceOf(address(this));
IERC20(tokenAddress).safeTransferFrom(msg.sender, address(this), amount);
uint256 actualReceived = IERC20(tokenAddress).balanceOf(address(this)) - balanceBefore;
require(actualReceived == amount, "Fee-on-transfer tokens not supported");
```

Alternatively, explicitly document and enforce that fee-on-transfer tokens are not supported, and add a check (e.g., a token allowlist or a pre-transfer balance check that reverts on mismatch) to prevent such tokens from being bridged.

---

### Proof of Concept

1. Deploy or use an existing fee-on-transfer ERC20 token `T` that deducts 1% on every transfer.
2. Approve the `OmniBridge` for `1000` units of `T`.
3. Call `OmniBridge.initTransfer(T, 1000, 0, 0, "recipient.near", "")`.
4. Inside `initTransfer`, `safeTransferFrom(msg.sender, bridge, 1000)` is called. The token contract deducts 10 tokens as a fee; the bridge receives 990.
5. The `InitTransfer` event is emitted with `amount = 1000`.
6. A relayer submits this event as proof to the NEAR bridge via `fin_transfer`.
7. The NEAR bridge's `fin_transfer_callback` reads `init_transfer.amount = 1000` from the proof and releases 1000 tokens (after decimal normalization) to the recipient on NEAR.
8. The EVM bridge is now short 10 tokens. Repeating this 100 times creates a 1000-token deficit, eventually making the EVM bridge unable to honor withdrawals from NEAR. [4](#0-3) [5](#0-4)

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L373-437)
```text
    function initTransfer(
        address tokenAddress,
        uint128 amount,
        uint128 fee,
        uint128 nativeFee,
        string calldata recipient,
        string calldata message
    ) external payable whenNotPaused(PAUSED_INIT_TRANSFER) {
        currentOriginNonce += 1;
        if (fee >= amount) {
            revert InvalidFee();
        }

        uint256 extensionValue;
        if (tokenAddress == address(0)) {
            if (fee != 0) {
                revert InvalidFee();
            }
            extensionValue = msg.value - amount - nativeFee;
        } else {
            extensionValue = msg.value - nativeFee;
            if (customMinters[tokenAddress] != address(0)) {
                IERC20(tokenAddress).safeTransferFrom(
                    msg.sender,
                    customMinters[tokenAddress],
                    amount
                );
                ICustomMinter(customMinters[tokenAddress]).burn(
                    tokenAddress,
                    amount
                );
            } else if (isBridgeToken[tokenAddress]) {
                BridgeToken(tokenAddress).burn(msg.sender, amount);
            } else {
                IERC20(tokenAddress).safeTransferFrom(
                    msg.sender,
                    address(this),
                    amount
                );
            }
        }

        initTransferExtension(
            msg.sender,
            tokenAddress,
            currentOriginNonce,
            amount,
            fee,
            nativeFee,
            recipient,
            message,
            extensionValue
        );

        emit BridgeTypes.InitTransfer(
            msg.sender,
            tokenAddress,
            currentOriginNonce,
            amount,
            fee,
            nativeFee,
            recipient,
            message
        );
    }
```

**File:** near/omni-bridge/src/lib.rs (L698-746)
```rust
    #[private]
    #[payable]
    pub fn fin_transfer_callback(
        &mut self,
        #[serializer(borsh)] storage_deposit_actions: &Vec<StorageDepositAction>,
        #[serializer(borsh)] predecessor_account_id: AccountId,
    ) -> PromiseOrValue<Nonce> {
        let Ok(ProverResult::InitTransfer(init_transfer)) = Self::decode_prover_result(0) else {
            env::panic_str(BridgeError::InvalidProofMessage.to_string().as_str())
        };
        require!(
            self.factories
                .get(&init_transfer.emitter_address.get_chain())
                == Some(init_transfer.emitter_address),
            BridgeError::UnknownFactory.as_ref()
        );

        let decimals = self
            .token_decimals
            .get(&init_transfer.token)
            .near_expect(BridgeError::TokenDecimalsNotFound);

        let destination_nonce =
            self.get_next_destination_nonce(init_transfer.recipient.get_chain());
        let transfer_message = TransferMessage {
            origin_nonce: init_transfer.origin_nonce,
            token: init_transfer.token,
            amount: Self::denormalize_amount(init_transfer.amount.0, decimals).into(),
            recipient: init_transfer.recipient,
            fee: Self::denormalize_fee(&init_transfer.fee, decimals),
            sender: init_transfer.sender,
            msg: init_transfer.msg,
            destination_nonce,
            origin_transfer_id: None,
        };

        if let OmniAddress::Near(recipient) = transfer_message.recipient.clone() {
            self.process_fin_transfer_to_near(
                recipient,
                &predecessor_account_id,
                transfer_message,
                storage_deposit_actions,
            )
            .into()
        } else {
            self.process_fin_transfer_to_other_chain(predecessor_account_id, transfer_message);
            PromiseOrValue::Value(destination_nonce)
        }
    }
```

**File:** starknet/src/omni_bridge.cairo (L303-329)
```text
            } else {
                let success = IERC20Dispatcher { contract_address: token_address }
                    .transfer_from(caller, get_contract_address(), amount.into());
                assert(success, 'ERR_TRANSFER_FROM_FAILED');
            }

            if native_fee > 0 {
                let native_token = self.strk_token_address.read();
                let success = IERC20Dispatcher { contract_address: native_token }
                    .transfer_from(caller, get_contract_address(), native_fee.into());
                assert(success, 'ERR_FEE_TRANSFER_FAILED');
            }

            self
                .emit(
                    Event::InitTransfer(
                        InitTransfer {
                            sender: caller,
                            token_address,
                            origin_nonce,
                            amount,
                            fee,
                            native_fee,
                            recipient,
                            message,
                        },
                    ),
```

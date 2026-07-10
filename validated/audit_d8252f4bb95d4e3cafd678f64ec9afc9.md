### Title
Fee-on-Transfer Token Accounting Discrepancy in `initTransfer` Creates Unbacked Bridged Supply — (`evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary

`OmniBridge.sol::initTransfer` and `omni_bridge.cairo::init_transfer` both record the caller-supplied `amount` parameter in the cross-chain event/message rather than the actual tokens received by the contract. For fee-on-transfer ERC20 tokens, the contract receives `amount − transfer_fee` tokens while the event announces `amount`. The NEAR bridge processes the announced value and mints or releases the full `amount` on the destination chain, creating an unbacked supply and eventually making the EVM vault insolvent.

---

### Finding Description

In `OmniBridge.sol::initTransfer`, the native-ERC20 branch calls:

```solidity
IERC20(tokenAddress).safeTransferFrom(
    msg.sender,
    address(this),
    amount          // ← user-supplied parameter
);
```

and then immediately emits:

```solidity
emit BridgeTypes.InitTransfer(
    msg.sender,
    tokenAddress,
    currentOriginNonce,
    amount,         // ← same user-supplied parameter, not actual received
    fee,
    nativeFee,
    recipient,
    message
);
``` [1](#0-0) [2](#0-1) 

For a fee-on-transfer ERC20 token (e.g., a token that deducts 1% on every transfer), `safeTransferFrom` causes the contract to receive `amount * 0.99`, but the `InitTransfer` event records `amount`. The NEAR bridge reads this event and mints or releases `amount` bridged tokens on the destination chain.

The identical pattern exists in StarkNet's `init_transfer`:

```cairo
let success = IERC20Dispatcher { contract_address: token_address }
    .transfer_from(caller, get_contract_address(), amount.into());
assert(success, 'ERR_TRANSFER_FROM_FAILED');
// ...
self.emit(Event::InitTransfer(InitTransfer { ..., amount, ... }))
``` [3](#0-2) [4](#0-3) 

There is no balance-before/balance-after check anywhere in either `initTransfer` implementation to measure the actual tokens received. The `amount` parameter is trusted verbatim. [5](#0-4) 

---

### Impact Explanation

Each `initTransfer` with a fee-on-transfer token creates a deficit of `transfer_fee` tokens in the EVM vault relative to the bridged supply on the destination chain. Over time (or in a single large transfer), the vault becomes undercollateralized. When users bridge tokens back from the destination chain to EVM, `finTransfer` calls `IERC20(payload.tokenAddress).safeTransfer(payload.recipient, payload.amount)` using the full announced amount: [6](#0-5) 

The vault cannot cover the full outstanding supply. The last users to withdraw receive nothing or the transaction reverts — **permanent, irrecoverable lock of user funds** and **bridge collateralization corruption**. An attacker who controls a fee-on-transfer token registered with the bridge can deliberately drain the vault by repeatedly bridging and back-bridging, extracting the fee-inflated difference each round trip.

---

### Likelihood Explanation

- Fee-on-transfer ERC20 tokens are a well-known token class (e.g., tokens with protocol fees, deflationary tokens, PAXG). Any such token that is registered with the bridge (via `deployToken` or `addCustomToken`) or used directly in the open `else` branch of `initTransfer` triggers the bug.
- No privileged access is required. Any unprivileged user can call `initTransfer` with any ERC20 address; the native-ERC20 branch has no whitelist check.
- The NEAR bridge's `locked_tokens` accounting tracks the announced `amount`, not the actual vault balance, so the discrepancy compounds silently. [7](#0-6) 

---

### Recommendation

Measure the actual tokens received using a balance-before/balance-after pattern:

```solidity
uint256 balanceBefore = IERC20(tokenAddress).balanceOf(address(this));
IERC20(tokenAddress).safeTransferFrom(msg.sender, address(this), amount);
uint256 actualReceived = IERC20(tokenAddress).balanceOf(address(this)) - balanceBefore;
require(actualReceived == amount, "FeeOnTransferNotSupported");
// or: use actualReceived in the emitted event instead of amount
```

Apply the same fix to `starknet/src/omni_bridge.cairo::init_transfer`. Alternatively, explicitly document and enforce that fee-on-transfer tokens are not supported, and add a registry check that rejects any token exhibiting this behavior.

---

### Proof of Concept

1. Deploy a fee-on-transfer ERC20 token `FeeToken` (1% fee on every transfer) on EVM. Register it with the bridge so NEAR recognizes it.
2. Call `OmniBridge.initTransfer(FeeToken, 1_000_000, 0, 0, nearRecipient, "")`.
   - `safeTransferFrom` moves `1_000_000` from caller; contract receives `990_000` (1% fee deducted).
   - `InitTransfer` event emits `amount = 1_000_000`.
3. NEAR bridge processes the event, mints `1_000_000` bridged `FeeToken` to `nearRecipient`.
4. `nearRecipient` bridges back `1_000_000` bridged tokens to EVM.
5. NEAR burns `1_000_000` bridged tokens and sends a `FinTransfer` message to EVM with `amount = 1_000_000`.
6. `OmniBridge.finTransfer` calls `safeTransfer(recipient, 1_000_000)` — but the vault only holds `990_000`.
7. The transaction reverts. The `1_000_000` bridged tokens are already burned on NEAR. Funds are permanently lost. [8](#0-7)

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L279-367)
```text
    function finTransfer(
        bytes calldata signatureData,
        BridgeTypes.TransferMessagePayload calldata payload
    ) external payable whenNotPaused(PAUSED_FIN_TRANSFER) {
        if (completedTransfers[payload.destinationNonce]) {
            revert NonceAlreadyUsed(payload.destinationNonce);
        }

        completedTransfers[payload.destinationNonce] = true;

        bytes memory borshEncoded = bytes.concat(
            bytes1(uint8(BridgeTypes.PayloadType.TransferMessage)),
            Borsh.encodeUint64(payload.destinationNonce),
            bytes1(payload.originChain),
            Borsh.encodeUint64(payload.originNonce),
            bytes1(omniBridgeChainId),
            Borsh.encodeAddress(payload.tokenAddress),
            Borsh.encodeUint128(payload.amount),
            bytes1(omniBridgeChainId),
            Borsh.encodeAddress(payload.recipient),
            bytes(payload.feeRecipient).length == 0 // None or Some(String) in rust
                ? bytes("\x00")
                : bytes.concat(
                    bytes("\x01"),
                    Borsh.encodeString(payload.feeRecipient)
                ),
            bytes(payload.message).length == 0
                ? bytes("")
                : Borsh.encodeBytes(payload.message)
        );
        bytes32 hashed = keccak256(borshEncoded);

        if (ECDSA.recover(hashed, signatureData) != nearBridgeDerivedAddress) {
            revert InvalidSignature();
        }

        MultiTokenInfo memory multiToken = multiTokens[payload.tokenAddress];

        if (payload.tokenAddress == address(0)) {
            // slither-disable-next-line arbitrary-send-eth
            (bool success, ) = payload.recipient.call{value: payload.amount}(
                ""
            );
            if (!success) revert FailedToSendEther();
        } else if (multiToken.tokenAddress != address(0)) {
            IERC1155(multiToken.tokenAddress).safeTransferFrom(
                address(this),
                payload.recipient,
                multiToken.tokenId,
                payload.amount,
                ""
            );
        } else if (customMinters[payload.tokenAddress] != address(0)) {
            ICustomMinter(customMinters[payload.tokenAddress]).mint(
                payload.tokenAddress,
                payload.recipient,
                payload.amount
            );
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

        finTransferExtension(payload);

        emit BridgeTypes.FinTransfer(
            payload.originChain,
            payload.originNonce,
            payload.tokenAddress,
            payload.amount,
            payload.recipient,
            payload.feeRecipient
        );
    }
```

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

**File:** starknet/src/omni_bridge.cairo (L304-306)
```text
                let success = IERC20Dispatcher { contract_address: token_address }
                    .transfer_from(caller, get_contract_address(), amount.into());
                assert(success, 'ERR_TRANSFER_FROM_FAILED');
```

**File:** starknet/src/omni_bridge.cairo (L316-330)
```text
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
                )
```

**File:** near/omni-bridge/src/token_lock.rs (L48-69)
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
        let new_amount = current_amount
            .checked_add(amount)
            .near_expect(TokenLockError::LockedTokensOverflow);

        self.locked_tokens.insert(&key, &new_amount);

        LockAction::Locked {
            chain_kind,
            token_id: token_id.clone(),
            amount,
        }
    }
```

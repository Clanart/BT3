### Title
Reentrancy via ERC777 `tokensToSend` Hook in `initTransfer` Causes Nonce Reuse and Permanent Fund Lock — (File: `evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary

`OmniBridge.initTransfer` increments `currentOriginNonce` at the top of the function but **never saves it to a local variable**. Both `initTransferExtension` and the `InitTransfer` event read `currentOriginNonce` directly from storage at the time they execute. Because the function has no `nonReentrant` guard, an ERC777 token's `tokensToSend` hook can reenter `initTransfer` during the `safeTransferFrom` call, increment `currentOriginNonce` a second time, and cause the outer call to publish a Wormhole message and emit an event with the **same** `originNonce` as the inner call. On NEAR, only one of the two transfers with that nonce can ever be finalized; the other's tokens are permanently locked in the EVM bridge with no recovery path.

---

### Finding Description

In `OmniBridge.sol`, `initTransfer` executes in this order:

1. **Line 381** — `currentOriginNonce += 1;` (storage write, e.g. becomes N+1)
2. **Lines 407–411** — `IERC20(tokenAddress).safeTransferFrom(msg.sender, address(this), amount)` — external call; for ERC777 tokens this fires the `tokensToSend` hook on `msg.sender`
3. **Lines 415–425** — `initTransferExtension(..., currentOriginNonce, ...)` — reads `currentOriginNonce` **from storage again**
4. **Lines 427–436** — `emit BridgeTypes.InitTransfer(..., currentOriginNonce, ...)` — reads `currentOriginNonce` **from storage again** [1](#0-0) 

Because `currentOriginNonce` is never captured into a stack-local variable before the external call, any reentrant call that increments it will corrupt the value seen by steps 3 and 4 of the outer call.

`initTransferExtension` in `OmniBridgeWormhole.sol` encodes `originNonce` directly into the Wormhole payload: [2](#0-1) 

Neither `initTransfer` nor `initTransfer1155` carries a `nonReentrant` modifier: [3](#0-2) [4](#0-3) 

---

### Impact Explanation

Two distinct `InitTransfer` EVM events and two distinct Wormhole messages are published carrying the **same** `originNonce` (N+2) for two different transfers. On NEAR, `fin_transfer_callback` stores each transfer keyed by `(origin_chain, origin_nonce)`: [5](#0-4) 

The first `fin_transfer` call for nonce N+2 succeeds and records the transfer. The second call for the same nonce either panics (transfer already exists) or overwrites the first record. Either way, the tokens locked in the EVM bridge for the "losing" transfer are **irrecoverable** — there is no refund or rescue path in the EVM contract. This satisfies the **Critical — permanent freezing / irrecoverable lock of user funds** impact class.

---

### Likelihood Explanation

- ERC777 tokens are ERC20-compatible; the bridge's `else` branch in `initTransfer` accepts **any** ERC20 token address without a whitelist check.
- The attacker only needs to register a `tokensToSend` hook implementer via the ERC1820 registry — a standard, permissionless on-chain operation.
- No privileged role, leaked key, or colluding party is required.
- The attack is fully self-contained in a single transaction.

---

### Recommendation

1. **Add `nonReentrant`** (OpenZeppelin `ReentrancyGuardUpgradeable`) to both `initTransfer` and `initTransfer1155`.
2. **Cache the nonce in a local variable** before any external call and use that local variable in `initTransferExtension` and `emit`:

```solidity
function initTransfer(...) external payable nonReentrant whenNotPaused(PAUSED_INIT_TRANSFER) {
    currentOriginNonce += 1;
    uint64 originNonce = currentOriginNonce; // capture before external call
    ...
    IERC20(tokenAddress).safeTransferFrom(msg.sender, address(this), amount);
    ...
    initTransferExtension(msg.sender, tokenAddress, originNonce, ...);
    emit BridgeTypes.InitTransfer(msg.sender, tokenAddress, originNonce, ...);
}
```

---

### Proof of Concept

```
State before attack: currentOriginNonce = N

Step 1 — Attacker calls initTransfer(erc777Token, amount1, ...)
  → currentOriginNonce becomes N+1
  → safeTransferFrom fires tokensToSend hook on attacker's contract

Step 2 — Inside tokensToSend hook, attacker reenters initTransfer(legitimateToken, amount2, ...)
  → currentOriginNonce becomes N+2
  → safeTransferFrom of legitimateToken completes (no hook)
  → initTransferExtension called with currentOriginNonce = N+2  ✓
  → emit InitTransfer with currentOriginNonce = N+2             ✓
  → inner call returns

Step 3 — Outer call resumes after safeTransferFrom
  → initTransferExtension called with currentOriginNonce = N+2  ✗ (should be N+1)
  → emit InitTransfer with currentOriginNonce = N+2             ✗ (should be N+1)

Result on EVM:
  - Nonce N+1 is never published (skipped)
  - Nonce N+2 appears in TWO InitTransfer events for different tokens/amounts

Result on NEAR (fin_transfer calls):
  - fin_transfer for inner call (nonce N+2, legitimateToken) → succeeds
  - fin_transfer for outer call (nonce N+2, erc777Token)    → fails, nonce already used
  - Outer call's tokens locked in EVM bridge permanently, no recovery mechanism
```

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

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L439-448)
```text
    function initTransfer1155(
        address tokenAddress,
        uint256 tokenId,
        uint128 amount,
        uint128 fee,
        uint128 nativeFee,
        string calldata recipient,
        string calldata message
    ) external payable whenNotPaused(PAUSED_INIT_TRANSFER) {
        currentOriginNonce += 1;
```

**File:** evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol (L118-150)
```text
    function initTransferExtension(
        address sender,
        address tokenAddress,
        uint64 originNonce,
        uint128 amount,
        uint128 fee,
        uint128 nativeFee,
        string calldata recipient,
        string calldata message,
        uint256 value
    ) internal override {
        bytes memory payload = bytes.concat(
            bytes1(uint8(MessageType.InitTransfer)),
            bytes1(omniBridgeChainId),
            Borsh.encodeAddress(sender),
            bytes1(omniBridgeChainId),
            Borsh.encodeAddress(tokenAddress),
            Borsh.encodeUint64(originNonce),
            Borsh.encodeUint128(amount),
            Borsh.encodeUint128(fee),
            Borsh.encodeUint128(nativeFee),
            Borsh.encodeString(recipient),
            Borsh.encodeString(message)
        );
        // slither-disable-next-line reentrancy-eth
        _wormhole.publishMessage{value: value}(
            wormholeNonce,
            payload,
            _consistencyLevel
        );

        wormholeNonce++;
    }
```

**File:** near/omni-bridge/src/lib.rs (L700-746)
```rust
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

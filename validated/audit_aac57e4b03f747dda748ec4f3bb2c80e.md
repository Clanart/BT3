### Title
Missing Empty Recipient Validation in `initTransfer` Causes Permanent Irrecoverable Fund Loss — (`evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary

`OmniBridge.sol::initTransfer()` accepts a `recipient` string parameter without validating that it is non-empty. An unprivileged user who passes an empty string causes bridge tokens to be irreversibly burned (or native tokens locked) on the EVM side, while the corresponding Wormhole message carries an empty recipient that the NEAR bridge cannot process, permanently freezing the funds.

---

### Finding Description

`OmniBridge.sol::initTransfer()` takes a `string calldata recipient` parameter and immediately proceeds to burn or lock the caller's tokens before any validation of the recipient string occurs. [1](#0-0) 

The function performs no check such as `require(bytes(recipient).length > 0, ...)` before executing the token burn/lock: [2](#0-1) 

After the burn/lock, `OmniBridgeWormhole.sol::initTransferExtension()` encodes the empty string into the Wormhole payload and publishes it: [3](#0-2) 

On the NEAR side, `fin_transfer_callback` decodes the prover result and attempts to parse the recipient as an `OmniAddress`. An empty string cannot be parsed as any valid `OmniAddress` variant, causing the callback to panic with `BridgeError::InvalidProofMessage`: [4](#0-3) 

The transfer is permanently unprocessable. For bridge tokens (burned via `BridgeToken.burn`), the loss is irrecoverable. For locked ERC-20 tokens (held in the contract), no standard recovery path exists in the contract.

The same class of issue is explicitly acknowledged for the Solana side in `solana/SECURITY.md` as a known low-severity item, but it is **not** acknowledged for the EVM path, and the EVM path involves irreversible token burns rather than merely locked tokens. [5](#0-4) 

---

### Impact Explanation

**Critical — Permanent irrecoverable lock/burn of user funds.**

For bridge tokens (`isBridgeToken[tokenAddress] == true`), `BridgeToken.burn` is called and the tokens are destroyed on-chain. There is no admin recovery function in `OmniBridge.sol` that can recredit burned tokens. The Wormhole message is published and the nonce is consumed, so the transfer cannot be retried. Funds are permanently lost.

For locked ERC-20 tokens, they remain in the contract with no standard withdrawal path for the affected user.

---

### Likelihood Explanation

**Medium.** Any unprivileged user interacting with `initTransfer` can trigger this by passing an empty `recipient` string, either by mistake (UI bug, copy-paste error) or deliberately (griefing their own funds). No special role or key is required. The function is publicly callable and unpaused by default.

---

### Recommendation

Add a non-empty recipient check at the top of `initTransfer` (and `initTransfer1155`) before any token movement:

```solidity
require(bytes(recipient).length > 0, "OmniBridge: empty recipient");
```

Optionally, add a minimum-length check consistent with the shortest valid `OmniAddress` encoding (e.g., `"eth"` prefix + address).

---

### Proof of Concept

1. Deploy `OmniBridgeWormhole` with a bridge token registered (`isBridgeToken[token] == true`).
2. Approve the bridge to spend `amount` of the bridge token.
3. Call:
   ```solidity
   omniBridge.initTransfer(
       bridgeTokenAddress,
       amount,
       0,       // fee
       0,       // nativeFee
       "",      // empty recipient ← root cause
       ""
   );
   ```
4. `BridgeToken.burn(msg.sender, amount)` executes — tokens are destroyed.
5. Wormhole publishes a message with `Borsh.encodeString("")` as the recipient field.
6. On NEAR, `fin_transfer_callback` receives the proof; the prover attempts to deserialize the empty byte sequence as an `OmniAddress` and fails; the callback panics with `InvalidProofMessage`.
7. The transfer ID is never marked finalised; the burned tokens are unrecoverable. [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L373-384)
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
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L394-413)
```text
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
```

**File:** evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol (L129-147)
```text
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
```

**File:** near/omni-bridge/src/lib.rs (L705-712)
```rust
        let Ok(ProverResult::InitTransfer(init_transfer)) = Self::decode_prover_result(0) else {
            env::panic_str(BridgeError::InvalidProofMessage.to_string().as_str())
        };
        require!(
            self.factories
                .get(&init_transfer.emitter_address.get_chain())
                == Some(init_transfer.emitter_address),
            BridgeError::UnknownFactory.as_ref()
```

**File:** solana/SECURITY.md (L17-17)
```markdown
- **No validation of `recipient` string in `InitTransferPayload`** — An invalid recipient causes the transfer to fail on the NEAR side after tokens are locked/burned on Solana. Manual intervention would be needed.
```

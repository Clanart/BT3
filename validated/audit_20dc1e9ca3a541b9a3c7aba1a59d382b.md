### Title
Unbounded Return Data in `finTransfer` Low-Level ETH Send Enables Permanent Freeze of Bridged Native ETH — (`evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary

`OmniBridge.finTransfer` uses a bare low-level call to deliver native ETH to an attacker-controlled `payload.recipient`. The EVM copies all return data into memory regardless of whether the caller captures it, so a recipient contract that returns a large blob (e.g. 10 MB) inflates memory-expansion gas costs beyond the block gas limit. Because the transaction always reverts with OOG, `completedTransfers[nonce]` is never durably set, the transfer is never finalized, and the user's tokens that were already burned/locked on NEAR are permanently frozen with no on-chain recovery path.

---

### Finding Description

In `finTransfer`, the nonce guard and the nonce-marking write happen before the external call:

```
completedTransfers[payload.destinationNonce] = true;   // line 287
...
(bool success, ) = payload.recipient.call{value: payload.amount}(""); // line 319
if (!success) revert FailedToSendEther();
``` [1](#0-0) 

Although the return data is syntactically discarded with `(bool success, )`, the EVM unconditionally copies the entire return buffer into the active memory region before the CALL opcode returns. Memory expansion cost is quadratic in the number of 32-byte words:

```
cost ≈ words² / 512 + 3 × words
```

For a 10 MB return blob (~312 500 words) this is ≈ 191 M gas — far above Ethereum's ~30 M block gas limit. No amount of gas forwarding by the relayer can make the transaction succeed.

Because the OOG exception causes a full transaction revert, the write at line 287 is also rolled back. The nonce is therefore never consumed, and the relayer will keep retrying indefinitely, each time paying gas and failing.

`payload.recipient` is a plain `address` field inside `TransferMessagePayload`: [2](#0-1) 

It is set by the originating user on NEAR when they call `ft_on_transfer` → `init_transfer` with an `InitTransferMsg` whose `recipient` field is the target EVM address. The MPC signs the full payload including this address, so the signed payload faithfully encodes the attacker's chosen contract address. There is no validation that the recipient is an EOA or that its return data is bounded.

---

### Impact Explanation

**Severity: High — Permanent freezing of user funds.**

1. The user burns/locks their NEAR-side tokens when initiating the transfer (`ft_on_transfer` → `init_transfer`). Those tokens are gone from the NEAR side.
2. The EVM-side `finTransfer` can never succeed because every attempt OOGs.
3. `completedTransfers[nonce]` is never set, so the transfer is stuck in limbo.
4. There is no `cancel_transfer` or admin-rescue function visible in `OmniBridge.sol` that could unilaterally release the locked NEAR-side tokens or force-complete the EVM delivery.
5. The user's funds are irrecoverably locked in the bridge.

This matches the allowed impact: *"Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows."*

---

### Likelihood Explanation

- Any unprivileged bridge user can deploy a contract whose fallback returns a large byte array and then specify that contract as their EVM recipient when initiating a NEAR→EVM transfer of native ETH.
- No privileged access, no key compromise, and no third-party dependency is required.
- The attack is deterministic: once the malicious recipient is set and the NEAR-side tokens are burned, every subsequent `finTransfer` attempt will OOG.
- The only constraint is that the transfer must be for native ETH (`tokenAddress == address(0)`), which is a supported bridge path (the contract has a `receive()` function and the `initTransfer` path explicitly handles `tokenAddress == address(0)`). [3](#0-2) [4](#0-3) 

---

### Recommendation

Replace the bare low-level call with an *excessively safe call* pattern (as recommended by Trail of Bits) that caps the return data copied into memory:

```solidity
// Short-term: use assembly to limit returndata copy
assembly {
    success := call(gas(), recipient, amount, 0, 0, 0, 0)
    // returndata is intentionally not copied
}
require(success, "Failed to send Ether");
```

By passing `0` as the output buffer size and offset, the EVM does not copy return data into memory, eliminating the memory-expansion attack surface entirely. Alternatively, adopt a pull-payment pattern so recipients withdraw ETH themselves, removing the push-call entirely.

---

### Proof of Concept

```solidity
// Attacker deploys this on EVM
contract MaliciousRecipient {
    fallback() external payable {
        // Return 10 MB of data — costs ~191 M gas to copy into caller memory
        assembly {
            return(0, 10000000)
        }
    }
}
```

**Attack steps:**

1. Attacker deploys `MaliciousRecipient` on the target EVM chain.
2. Attacker holds wrapped-ETH (or native ETH bridged to NEAR) on NEAR.
3. Attacker calls `ft_on_transfer` on the NEAR bridge with `InitTransferMsg { recipient: OmniAddress::Eth(<MaliciousRecipient address>), ... }`. NEAR-side tokens are burned/locked immediately.
4. MPC signs the `TransferMessagePayload` containing `recipient = <MaliciousRecipient>`.
5. Relayer calls `OmniBridge.finTransfer` on EVM. The call at line 319 triggers `MaliciousRecipient.fallback()`, which returns 10 MB. Memory expansion costs ~191 M gas → OOG → full revert.
6. `completedTransfers[nonce]` is never set. Relayer retries indefinitely, always OOG.
7. Attacker's NEAR-side tokens are permanently burned with no EVM-side delivery ever completing. [5](#0-4) [6](#0-5)

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L283-322)
```text
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
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L373-413)
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
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L574-574)
```text
    receive() external payable {}
```

**File:** evm/src/omni-bridge/contracts/BridgeTypes.sol (L5-14)
```text
    struct TransferMessagePayload {
        uint64 destinationNonce;
        uint8 originChain;
        uint64 originNonce;
        address tokenAddress;
        uint128 amount;
        address recipient;
        string feeRecipient;
        bytes message;
    }
```

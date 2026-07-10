### Title
Native Fee ETH Permanently Locked in OmniBridge Contract with No Withdrawal Mechanism - (File: `evm/src/omni-bridge/contracts/OmniBridge.sol`)

### Summary

Every call to `OmniBridge#initTransfer` or `OmniBridge#initTransfer1155` that includes a non-zero `nativeFee` deposits ETH into the `OmniBridge` contract. That ETH is never forwarded to any relayer or treasury, and there is no withdrawal function in the contract. The `nativeFee` is permanently locked.

### Finding Description

In `OmniBridge#initTransfer`, the caller sends `msg.value` covering the bridged amount (for native ETH), the `nativeFee`, and any extension value (e.g., Wormhole message fee). The contract explicitly separates these:

```solidity
// For ERC20 tokens
extensionValue = msg.value - nativeFee;
// For native ETH
extensionValue = msg.value - amount - nativeFee;
```

Only `extensionValue` is forwarded to `initTransferExtension` (and from there to Wormhole in `OmniBridgeWormhole`). The `nativeFee` portion of `msg.value` is retained by the `OmniBridge` contract itself. [1](#0-0) 

In `OmniBridgeWormhole#initTransferExtension`, only `value` (= `extensionValue`) is forwarded to Wormhole: [2](#0-1) 

Searching the entire EVM contract surface for any withdrawal, rescue, or sweep function yields zero results. The only ETH-related function is a bare `receive() external payable {}` with no corresponding egress path: [3](#0-2) 

On the NEAR side, when the origin chain is not NEAR (e.g., Eth), the relayer's native fee is compensated by **minting a wrapped token** on NEAR — the actual ETH deposited on the EVM side is never claimed or forwarded: [4](#0-3) 

This means the ETH `nativeFee` paid by every EVM-originating bridge user accumulates in the `OmniBridge` contract with no mechanism to recover it.

### Impact Explanation

Every `initTransfer` or `initTransfer1155` call with `nativeFee > 0` permanently locks that ETH in the `OmniBridge` contract. Over time, this constitutes an irrecoverable loss of user-paid bridge fees. This matches the allowed impact: **Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows.**

### Likelihood Explanation

The `nativeFee` parameter is a first-class field in the bridge protocol, documented and used in tests. Any user or relayer who pays a `nativeFee` on the EVM side triggers the lock. The path is fully permissionless — any bridge user calling `initTransfer` with `nativeFee > 0` is affected.

### Recommendation

Add an admin-controlled withdrawal function to `OmniBridge.sol` that allows the protocol to recover accumulated `nativeFee` ETH:

```solidity
function withdrawNativeFees(address payable recipient, uint256 amount)
    external
    onlyRole(DEFAULT_ADMIN_ROLE)
{
    (bool success, ) = recipient.call{value: amount}("");
    require(success, "ETH transfer failed");
}
```

Alternatively, forward the `nativeFee` directly to a designated treasury address at the time of `initTransfer`.

### Proof of Concept

1. User calls `OmniBridge#initTransfer` with `tokenAddress = someERC20`, `amount = 1000`, `nativeFee = 0.01 ether`, sending `msg.value = 0.01 ether + wormholeFee`.
2. Contract computes `extensionValue = msg.value - nativeFee = wormholeFee`.
3. `initTransferExtension` is called with `value = wormholeFee`; Wormhole receives only the message fee.
4. The `0.01 ether` `nativeFee` remains in the `OmniBridge` contract balance.
5. On NEAR, the relayer receives wrapped ETH minted by `ext_token::mint` — the actual ETH is never released from the EVM contract.
6. No function exists in `OmniBridge.sol` or `OmniBridgeWormhole.sol` to withdraw this ETH. [5](#0-4)

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L386-413)
```text
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

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L492-506)
```text
    function initTransferExtension(
        address /*sender*/,
        address /*tokenAddress*/,
        uint64 /*originNonce*/,
        uint128 /*amount*/,
        uint128 /*fee*/,
        uint128 /*nativeFee*/,
        string calldata /*recipient*/,
        string calldata /*message*/,
        uint256 value
    ) internal virtual {
        if (value != 0) {
            revert InvalidValue();
        }
    }
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L574-574)
```text
    receive() external payable {}
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

**File:** near/omni-bridge/src/lib.rs (L2668-2673)
```rust
            } else {
                ext_token::ext(self.get_native_token_id(origin_chain))
                    .with_static_gas(MINT_TOKEN_GAS)
                    .mint(fee_recipient.clone(), transfer_message.fee.native_fee, None)
                    .detach();
            }
```

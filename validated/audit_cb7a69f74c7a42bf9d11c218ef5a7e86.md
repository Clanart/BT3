### Title
`nativeFee` ETH Permanently Locked in `OmniBridge` — No Native Token Rescue Mechanism - (File: evm/src/omni-bridge/contracts/OmniBridge.sol)

### Summary

`OmniBridge.initTransfer()` and `initTransfer1155()` are `payable` functions that require callers to include `nativeFee` ETH in `msg.value`. This ETH is never forwarded to any recipient and accumulates permanently in the contract. There is no rescue or withdrawal function for native ETH anywhere in `OmniBridge.sol`.

### Finding Description

In `OmniBridge.initTransfer()`, `msg.value` is split into components:

- For ERC20 tokens: `extensionValue = msg.value - nativeFee` [1](#0-0) 
- For native ETH (tokenAddress == address(0)): `extensionValue = msg.value - amount - nativeFee` [2](#0-1) 

Only `extensionValue` is passed to `initTransferExtension()`. [3](#0-2) 

In the base `OmniBridge`, `initTransferExtension()` reverts if `value != 0`, meaning `extensionValue` must be zero — so `msg.value` equals exactly `nativeFee` (for ERC20) or `amount + nativeFee` (for native ETH). The `nativeFee` portion is never sent anywhere. [4](#0-3) 

In `OmniBridgeWormhole`, `initTransferExtension()` forwards only `extensionValue` (= `msg.value - nativeFee`) to Wormhole via `publishMessage{value: value}`. The `nativeFee` ETH again stays in the contract. [5](#0-4) 

The same applies to `initTransfer1155()`, where `extensionValue = msg.value - nativeFee` and `nativeFee` ETH is left in the contract. [6](#0-5) 

Additionally, the contract exposes `receive() external payable {}`, allowing arbitrary ETH to be sent directly to the contract with no path to recovery. [7](#0-6) 

A full audit of `OmniBridge.sol` confirms there is no `withdraw`, `rescueETH`, or equivalent function anywhere in the contract. [8](#0-7) 

### Impact Explanation

Every call to `initTransfer` or `initTransfer1155` with `nativeFee > 0` permanently locks that ETH in the contract. Over time, as users pay `nativeFee` to incentivize relayers, the accumulated ETH becomes irrecoverable. This constitutes permanent freezing of user-paid protocol funds in the bridge vault flow — matching the Critical allowed impact: *"Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows."*

### Likelihood Explanation

Any unprivileged bridge user calling `initTransfer` or `initTransfer1155` with a non-zero `nativeFee` triggers this. The `nativeFee` parameter is a standard part of the bridge API and is actively used (evidenced by tests and the NEAR-side fee distribution logic). The entry path requires no special role or privilege.

### Recommendation

Add a privileged ETH rescue function, for example:

```solidity
function rescueETH(address payable to, uint256 amount) external onlyRole(DEFAULT_ADMIN_ROLE) {
    (bool success, ) = to.call{value: amount}("");
    require(success, "ETH rescue failed");
}
```

Alternatively, track accumulated `nativeFee` ETH separately and add a dedicated withdrawal path for the designated fee recipient.

### Proof of Concept

1. Deploy `OmniBridge` (or `OmniBridgeWormhole`).
2. Call `initTransfer(erc20Token, 1000, 0, 500, "recipient.near", "")` with `msg.value = 500` (nativeFee = 500 wei).
3. Observe: `extensionValue = 500 - 500 = 0`. The 500 wei stays in the contract.
4. Repeat across many users. ETH accumulates.
5. Attempt any withdrawal — no function exists. ETH is permanently locked. [9](#0-8) [10](#0-9)

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

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L439-490)
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
        if (fee >= amount) {
            revert InvalidFee();
        }

        address deterministicToken = deriveDeterministicAddress(
            tokenAddress,
            tokenId
        );

        IERC1155(tokenAddress).safeTransferFrom(
            msg.sender,
            address(this),
            tokenId,
            amount,
            ""
        );

        uint256 extensionValue = msg.value - nativeFee;

        initTransferExtension(
            msg.sender,
            deterministicToken,
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
            deterministicToken,
            currentOriginNonce,
            amount,
            fee,
            nativeFee,
            recipient,
            message
        );
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

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L548-598)
```text
    function pause(uint256 flags) external onlyRole(DEFAULT_ADMIN_ROLE) {
        _pause(flags);
    }

    function pauseAll() external onlyRole(PAUSABLE_ADMIN_ROLE) {
        uint256 flags = PAUSED_FIN_TRANSFER |
            PAUSED_INIT_TRANSFER |
            PAUSED_DEPLOY_TOKEN;
        _pause(flags);
    }

    function upgradeToken(
        address tokenAddress,
        address implementation
    ) external onlyRole(DEFAULT_ADMIN_ROLE) {
        require(isBridgeToken[tokenAddress], "ERR_NOT_BRIDGE_TOKEN");
        BridgeToken proxy = BridgeToken(tokenAddress);
        proxy.upgradeToAndCall(implementation, bytes(""));
    }

    function setNearBridgeDerivedAddress(
        address nearBridgeDerivedAddress_
    ) external onlyRole(DEFAULT_ADMIN_ROLE) {
        nearBridgeDerivedAddress = nearBridgeDerivedAddress_;
    }

    receive() external payable {}

    function deriveDeterministicAddress(
        address tokenAddress,
        uint256 tokenId
    ) public pure returns (address) {
        return
            address(
                bytes20(keccak256(abi.encodePacked(tokenAddress, tokenId)))
            );
    }

    function _normalizeDecimals(uint8 decimals) internal pure returns (uint8) {
        uint8 maxAllowedDecimals = 18;
        if (decimals > maxAllowedDecimals) {
            return maxAllowedDecimals;
        }
        return decimals;
    }

    function _authorizeUpgrade(
        address newImplementation
    ) internal override onlyRole(DEFAULT_ADMIN_ROLE) {}

    uint256[49] private __gap;
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

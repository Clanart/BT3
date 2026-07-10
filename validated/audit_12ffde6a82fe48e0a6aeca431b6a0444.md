### Title
Native Fee ETH Permanently Locked in EVM Bridge Contract — No Withdrawal Interface - (File: `evm/src/omni-bridge/contracts/OmniBridge.sol`)

### Summary
Every call to `initTransfer` (and `initTransfer1155`) on the EVM bridge accepts a `nativeFee` denominated in ETH. This ETH is retained by the contract but there is no function — admin or otherwise — to withdraw or distribute it. The accumulated native fees are permanently frozen in the contract.

### Finding Description

In `OmniBridge.initTransfer`, the caller sends `msg.value` covering both the bridged `amount` (for native ETH transfers) and the `nativeFee`:

```solidity
// tokenAddress == address(0) (native ETH bridge)
extensionValue = msg.value - amount - nativeFee;

// ERC-20 bridge
extensionValue = msg.value - nativeFee;
``` [1](#0-0) 

The `extensionValue` is forwarded to `initTransferExtension`. In the base `OmniBridge`, that function reverts if `value != 0`, so `extensionValue` must be zero — meaning the entire `msg.value` equals `amount + nativeFee` (ETH) or `nativeFee` (ERC-20). In `OmniBridgeWormhole`, `extensionValue` is consumed by the Wormhole `publishMessage` call, but `nativeFee` still remains in the contract. [2](#0-1) 

The contract has a bare `receive()` fallback and no `withdrawFees`, `rescueETH`, or any other function that moves accumulated ETH out to a relayer or admin: [3](#0-2) 

The full function list — `deployToken`, `finTransfer`, `setMetadata`, `pause`, `upgradeToken`, `setNearBridgeDerivedAddress` — contains no ETH-withdrawal path. [4](#0-3) 

On the NEAR side, `fee.native_fee` is recorded in `TransferMessage` and included in the signed `TransferMessagePayload`, but that only governs NEAR-side accounting; it does not trigger any EVM-side release of the locked ETH. [5](#0-4) 

### Impact Explanation

Every user who pays a non-zero `nativeFee` when calling `initTransfer` or `initTransfer1155` permanently donates that ETH to the contract with no recovery path. Over time this accumulates into a growing pool of irrecoverable ETH. Relayers are never compensated from the EVM side, and users cannot reclaim the fee even if the transfer is never finalised. This constitutes **permanent freezing of user funds** in the bridge vault flow.

### Likelihood Explanation

`nativeFee` is a first-class parameter of the public `initTransfer` interface. Any user who follows the documented fee model and sets `nativeFee > 0` to incentivise relayers triggers the lock. The condition is reachable by any unprivileged bridge user with no special preconditions.

### Recommendation

Add an admin-gated (or relayer-gated) ETH withdrawal function, for example:

```solidity
function withdrawNativeFees(address payable recipient, uint256 amount)
    external onlyRole(DEFAULT_ADMIN_ROLE)
{
    (bool ok,) = recipient.call{value: amount}("");
    if (!ok) revert FailedToSendEther();
}
```

Alternatively, forward `nativeFee` directly to a designated fee recipient inside `initTransfer` rather than retaining it in the contract.

### Proof of Concept

1. User calls `initTransfer(usdcAddress, 1000e6, 0, 1e16, "near:alice.near", "")` sending `msg.value = 1e16` (0.01 ETH as `nativeFee`).
2. `extensionValue = msg.value - nativeFee = 0`; base `initTransferExtension` does not revert.
3. The 0.01 ETH is now held by `OmniBridge`. No code path in the contract can move it out.
4. Repeat for every user paying a native fee — ETH accumulates indefinitely with no withdrawal interface.

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

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L548-597)
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

```

**File:** near/omni-bridge/src/lib.rs (L540-553)
```rust
        let transfer_message = TransferMessage {
            origin_nonce: self.current_origin_nonce,
            token: OmniAddress::Near(token_id),
            amount,
            recipient: init_transfer_msg.recipient,
            fee: Fee {
                fee: init_transfer_msg.fee,
                native_fee: init_transfer_msg.native_token_fee,
            },
            sender: OmniAddress::Near(sender_id),
            msg: init_transfer_msg.msg.map(String::from).unwrap_or_default(),
            destination_nonce,
            origin_transfer_id: None,
        };
```

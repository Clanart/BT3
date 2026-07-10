### Title
Untracked ETH Deposits via `receive()` Are Permanently Locked With No Recovery Path - (File: evm/src/omni-bridge/contracts/OmniBridge.sol)

### Summary
`OmniBridge.sol` exposes a bare `receive() external payable {}` function that silently accepts ETH without emitting any `InitTransfer` event, updating any accounting state, or providing any recovery mechanism. ETH deposited this way is permanently locked in the contract. The NEAR side never learns of the deposit, so no MPC signature for a corresponding `finTransfer` will ever be produced, and there is no admin withdrawal path.

### Finding Description
The bridge's intended ETH-bridging path is `initTransfer(address(0), amount, ...)`, which:
1. Validates `fee == 0` for native ETH
2. Computes `extensionValue = msg.value - amount - nativeFee`
3. Emits `BridgeTypes.InitTransfer` with all fields the NEAR side needs to mint wrapped ETH [1](#0-0) 

The NEAR side relies **solely** on the `InitTransfer` event to authorize minting: [2](#0-1) 

However, the contract also exposes:

```solidity
receive() external payable {}
``` [3](#0-2) 

This function:
- Emits **no** `InitTransfer` event
- Updates **no** accounting state
- Has **no** recovery or withdrawal mechanism

The only way ETH ever leaves the contract is through `finTransfer` with `tokenAddress == address(0)`, which requires a valid MPC signature over a Borsh-encoded `TransferMessagePayload`: [4](#0-3) 

Because no `InitTransfer` event was emitted for ETH deposited via `receive()`, the NEAR side will never produce an MPC signature authorizing its release. The ETH is irrecoverably locked.

There is no admin `withdraw`, `rescue`, or emergency-drain function anywhere in `OmniBridge.sol`. [5](#0-4) 

### Impact Explanation
Any ETH sent directly to the `OmniBridge` contract address (e.g., via a plain `transfer`, `send`, or `call` with no calldata) is **permanently and irrecoverably locked**. There is no admin path, no emergency withdrawal, and no MPC-signed `finTransfer` will ever be generated for it because the NEAR side has no record of the deposit. This matches the allowed impact: **"Critical. Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows."**

### Likelihood Explanation
The attack surface is reachable by any unprivileged user:
- A user who intends to bridge ETH but sends it directly to the contract address (e.g., from a hardware wallet, exchange withdrawal, or scripting error) loses their funds permanently.
- A phishing or UI-spoofing attack can direct victims to send ETH to the contract address rather than calling `initTransfer`.
- Smart contracts that attempt to "push" ETH to the bridge (e.g., a DeFi integration that calls `address(bridge).transfer(amount)` instead of `initTransfer`) will silently lock funds.

The `receive()` function is not listed as an intentional design decision in `evm/SECURITY.md`: [6](#0-5) 

### Recommendation
Remove the bare `receive() external payable {}` or replace it with a reverting stub:

```solidity
receive() external payable {
    revert("Use initTransfer to bridge ETH");
}
```

If the Wormhole variant requires the contract to receive ETH refunds from the Wormhole core contract, scope the `receive()` to only accept ETH from the Wormhole address:

```solidity
receive() external payable {
    require(msg.sender == address(_wormhole), "ETH only accepted from Wormhole");
}
```

and override it in `OmniBridgeWormhole` accordingly, while keeping the base `OmniBridge` non-payable via `receive`.

### Proof of Concept

1. Deploy `OmniBridge` (or `OmniBridgeWormhole`) on a testnet.
2. From any EOA, execute:
   ```js
   await signer.sendTransaction({ to: omniBridgeAddress, value: ethers.parseEther("1.0") });
   ```
3. Observe: the transaction succeeds, the contract's ETH balance increases by 1 ETH, **no** `InitTransfer` event is emitted.
4. Attempt to recover the ETH: there is no function to call. The only ETH-release path is `finTransfer` with `tokenAddress == address(0)`, which requires a valid MPC signature. Since no `InitTransfer` event was emitted, the NEAR side will never produce such a signature.
5. The 1 ETH is permanently locked. [3](#0-2) [7](#0-6) [8](#0-7)

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

**File:** evm/CLAUDE.md (L22-23)
```markdown

**EVM → NEAR (initTransfer)**: User calls `initTransfer` which burns/locks tokens on EVM and emits `InitTransfer` with all transfer details (sender, token, amount, fee, nativeFee, recipient, message). In the Wormhole variant, a Wormhole message is also sent. The NEAR side reads this event (via light client or Wormhole) to complete the transfer. Every field needed to reconstruct the transfer must be in the event — it is the only data the NEAR side sees.
```

**File:** evm/SECURITY.md (L1-21)
```markdown
# Security Notes

## Design Decisions (Non-Issues)

These patterns have been reviewed and confirmed as intentional. Do not flag or "fix" them.

- **Fee-on-transfer tokens not supported**: `initTransfer` emits the requested `amount`, not the actual received balance. Fee-on-transfer and rebasing tokens are intentionally unsupported
- **`logMetadata` and `deployToken` are permissionless**: Anyone can call `logMetadata` for any ERC20, and anyone can submit a valid MPC signature to `deployToken`. This is by design — the bridge is fully permissionless
- **`ENearProxy.burn` uses empty NEAR recipient**: `eNear.transferToNear(amount, "")` is intentional — `transferToNear` is a legacy method used purely as a burn mechanism. The actual NEAR recipient is tracked in the OmniBridge `InitTransfer` event
- **`deployToken` signature has no chain ID**: Metadata signatures are intentionally chain-agnostic — one NEAR-side signature deploys the same token on all EVM chains

## Known Issues

Low-severity items acknowledged but not yet addressed:

- **`addCustomToken` can overwrite existing mappings** (H-01): Admin-only function. No existence check — calling with an already-mapped `nearTokenId` silently overwrites `nearToEthToken`. Accepted as operational risk
- **`pause(flags)` replaces all flags** (H-02): `_pause(flags)` does full replacement, not bitwise OR. Calling `pause(PAUSED_INIT_TRANSFER)` when `PAUSED_FIN_TRANSFER` is set will unpause finTransfer. Use `pauseAll()` for emergencies
- **`BridgeToken.initialize` stores metadata redundantly** (L-01): `__ERC20_init(name_, symbol_)` writes to parent storage that is never read (getters are overridden). Minor gas waste on init
- **`require` strings instead of custom errors** (L-02): Several locations use `require` with string messages instead of custom errors (`OmniBridge.sol:150,204,556`, `SelectivePausableUpgradable.sol:100,107`, `ENearProxy.sol:56,76,86`)
- **`OmniBridgeWormhole` has no `__gap`** (L-04): Three storage variables with no gap array. Safe as a leaf contract but would need a gap if inherited from
- **`PayloadType.ClaimNativeFee` defined but unused** (L-05): Enum value 2 is never referenced. Native fees are recovered via `finTransfer` with `tokenAddress=address(0)`
```

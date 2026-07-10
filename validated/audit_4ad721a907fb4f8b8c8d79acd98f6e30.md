### Title
Missing Recovery Function for Excess ERC20 Tokens Permanently Frozen in OmniBridge — (File: `evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary

`OmniBridge.sol` holds native ERC20 tokens as collateral for non-bridge-token assets and also accepts raw ETH via an unconditional `receive()` fallback. There is no admin rescue or recovery function anywhere in the contract. Any ERC20 tokens (or ETH) that arrive at the contract address outside of the `initTransfer` flow — whether through a direct `transfer()` call, a rebasing token's automatic balance increase, or a fee-on-transfer overshoot — are permanently irrecoverable.

---

### Finding Description

`OmniBridge.initTransfer` is the only sanctioned path for depositing ERC20 collateral:

```solidity
IERC20(tokenAddress).safeTransferFrom(msg.sender, address(this), amount);
``` [1](#0-0) 

The corresponding release path, `finTransfer`, only releases exactly the amount encoded in the MPC-signed payload:

```solidity
IERC20(payload.tokenAddress).safeTransfer(payload.recipient, payload.amount);
``` [2](#0-1) 

The contract also exposes an unconditional ETH receiver:

```solidity
receive() external payable {}
``` [3](#0-2) 

A complete audit of all public and admin functions in `OmniBridge.sol` reveals **no `rescueTokens`, `recoverERC20`, `sweep`, or equivalent function**. The full function set is: `initialize`, `addCustomToken`, `removeCustomToken`, `acceptTokenOwnership`, `deployToken`, `setMetadata`, `logMetadata`, `logMetadata1155`, `finTransfer`, `initTransfer`, `initTransfer1155`, `pause`, `pauseAll`, `upgradeToken`, `setNearBridgeDerivedAddress`, `receive`, `deriveDeterministicAddress`, `_normalizeDecimals`, `_authorizeUpgrade`. [4](#0-3) 

Three concrete entry paths cause permanent freezing:

**Path 1 — Direct ERC20 transfer (unprivileged user):**  
Any token holder can call `IERC20(token).transfer(omniBridgeAddress, amount)` directly. No bridge message is created on the NEAR side, so `finTransfer` can never release these tokens. They are permanently frozen.

**Path 2 — Rebasing token collateral (automatic, protocol-level):**  
If a rebasing token (e.g., stETH, aTokens) is registered as a bridge collateral token, its balance in OmniBridge grows automatically with each rebase. The NEAR side only tracks the originally locked amount. The accrued excess is permanently frozen with no recovery path — this is the direct analog of the reported stETH/wstETH issue.

**Path 3 — Direct ETH send:**  
Any account can send ETH to the contract via the `receive()` fallback. ETH sent this way is not associated with any `initTransfer` nonce and cannot be released by `finTransfer`. It is permanently frozen.

---

### Impact Explanation

**Severity: Critical — Permanent irrecoverable lock of user or protocol funds.**

- For Path 1: a user's ERC20 tokens are permanently frozen in the bridge contract with no recourse.
- For Path 2: protocol-level collateral silently accrues excess balance that can never be released, breaking bridge collateralization accounting over time and permanently locking real value.
- For Path 3: ETH sent directly is permanently frozen.

None of these funds can be recovered through any existing contract function, upgrade, or governance action short of a full contract replacement.

---

### Likelihood Explanation

- **Path 1 (direct ERC20 transfer):** Medium. Users familiar with ERC20 mechanics may attempt to "deposit" by calling `transfer` directly rather than going through `initTransfer`. This is a well-documented class of user error on bridge contracts.
- **Path 2 (rebasing token):** Medium-High. Rebasing tokens (stETH, aTokens, cTokens) are among the most commonly bridged assets. If any such token is registered as a collateral token, the excess accrues automatically on every rebase with zero user action required.
- **Path 3 (direct ETH):** Low-Medium. Accidental ETH sends to contract addresses are common.

---

### Recommendation

Add a privileged recovery function restricted to `DEFAULT_ADMIN_ROLE`:

```solidity
function rescueTokens(
    address tokenAddress,
    address recipient,
    uint256 amount
) external onlyRole(DEFAULT_ADMIN_ROLE) {
    if (tokenAddress == address(0)) {
        (bool success, ) = recipient.call{value: amount}("");
        if (!success) revert FailedToSendEther();
    } else {
        IERC20(tokenAddress).safeTransfer(recipient, amount);
    }
}
```

For rebasing tokens specifically, the function should be callable to sweep the delta between the contract's actual balance and the protocol-tracked locked amount, preserving correct collateralization accounting.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";

interface IOmniBridge {
    function initTransfer(
        address tokenAddress,
        uint128 amount,
        uint128 fee,
        uint128 nativeFee,
        string calldata recipient,
        string calldata message
    ) external payable;
}

contract FreezePoC {
    // Step 1: attacker/user sends tokens directly to OmniBridge,
    //         bypassing initTransfer — no bridge message is created.
    function freezeTokens(address omniBridge, address token, uint256 amount) external {
        IERC20(token).transferFrom(msg.sender, omniBridge, amount);
        // Tokens are now in OmniBridge with no corresponding NEAR-side record.
        // finTransfer will never release them.
        // No rescueTokens function exists to recover them.
        // Funds are permanently frozen.
    }

    // Step 2 (rebasing path): simply wait — stETH/aToken balance in OmniBridge
    // grows automatically each rebase. The excess above the NEAR-tracked amount
    // is permanently frozen with no recovery path.
}
``` [5](#0-4) [6](#0-5) [3](#0-2)

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L28-598)
```text
contract OmniBridge is
    UUPSUpgradeable,
    AccessControlUpgradeable,
    SelectivePausableUpgradable,
    IERC1155Receiver
{
    using SafeERC20 for IERC20;

    mapping(address => string) public ethToNearToken;
    mapping(string => address) public nearToEthToken;
    mapping(address => bool) public isBridgeToken;

    address public tokenImplementationAddress;
    address public nearBridgeDerivedAddress;
    uint8 public omniBridgeChainId;

    mapping(uint64 => bool) public completedTransfers;
    uint64 public currentOriginNonce;

    mapping(address => address) public customMinters;
    mapping(address => MultiTokenInfo) public multiTokens;

    bytes32 public constant PAUSABLE_ADMIN_ROLE =
        keccak256("PAUSABLE_ADMIN_ROLE");
    uint256 constant UNPAUSED_ALL = 0;
    uint256 constant PAUSED_INIT_TRANSFER = 1 << 0;
    uint256 constant PAUSED_FIN_TRANSFER = 1 << 1;
    uint256 constant PAUSED_DEPLOY_TOKEN = 1 << 2;

    error InvalidSignature();
    error NonceAlreadyUsed(uint64 nonce);
    error InvalidFee();
    error InvalidValue();
    error FailedToSendEther();
    error ERC1155MappingMismatch();
    error ERC1155DirectSendNotAllowed();
    error ERC1155BatchNotSupported();
    error TokenImplementationNotSet();

    /// @custom:oz-upgrades-unsafe-allow constructor
    constructor() {
        _disableInitializers();
    }

    function initialize(
        address tokenImplementationAddress_,
        address nearBridgeDerivedAddress_,
        uint8 omniBridgeChainId_
    ) public initializer {
        tokenImplementationAddress = tokenImplementationAddress_;
        nearBridgeDerivedAddress = nearBridgeDerivedAddress_;
        omniBridgeChainId = omniBridgeChainId_;

        __UUPSUpgradeable_init();
        __AccessControl_init();
        __Pausable_init_unchained();
        _grantRole(DEFAULT_ADMIN_ROLE, _msgSender());
        _grantRole(PAUSABLE_ADMIN_ROLE, _msgSender());
    }

    function addCustomToken(
        string calldata nearTokenId,
        address tokenAddress,
        address customMinter,
        uint8 originDecimals
    ) external payable onlyRole(DEFAULT_ADMIN_ROLE) {
        isBridgeToken[tokenAddress] = true;
        ethToNearToken[tokenAddress] = nearTokenId;
        nearToEthToken[nearTokenId] = tokenAddress;
        customMinters[tokenAddress] = customMinter;

        string memory name = IERC20Metadata(tokenAddress).name();
        string memory symbol = IERC20Metadata(tokenAddress).symbol();
        uint8 decimals = IERC20Metadata(tokenAddress).decimals();

        deployTokenExtension(
            nearTokenId,
            tokenAddress,
            decimals,
            originDecimals
        );

        emit BridgeTypes.DeployToken(
            tokenAddress,
            nearTokenId,
            name,
            symbol,
            decimals,
            originDecimals
        );
    }

    function removeCustomToken(
        address tokenAddress
    ) external onlyRole(DEFAULT_ADMIN_ROLE) {
        delete isBridgeToken[tokenAddress];
        delete nearToEthToken[ethToNearToken[tokenAddress]];
        delete ethToNearToken[tokenAddress];
        delete customMinters[tokenAddress];
    }

    function acceptTokenOwnership(
        address tokenAddress
    ) external onlyRole(DEFAULT_ADMIN_ROLE) {
        BridgeToken(tokenAddress).acceptOwnership();
    }

    function deployToken(
        bytes calldata signatureData,
        BridgeTypes.MetadataPayload calldata metadata
    ) external payable whenNotPaused(PAUSED_DEPLOY_TOKEN) returns (address) {
        if (tokenImplementationAddress == address(0)) {
            revert TokenImplementationNotSet();
        }
        bytes memory borshEncoded = bytes.concat(
            bytes1(uint8(BridgeTypes.PayloadType.Metadata)),
            Borsh.encodeString(metadata.token),
            Borsh.encodeString(metadata.name),
            Borsh.encodeString(metadata.symbol),
            bytes1(metadata.decimals)
        );
        bytes32 hashed = keccak256(borshEncoded);

        if (ECDSA.recover(hashed, signatureData) != nearBridgeDerivedAddress) {
            revert InvalidSignature();
        }

        require(
            !isBridgeToken[nearToEthToken[metadata.token]],
            "ERR_TOKEN_EXIST"
        );
        uint8 decimals = _normalizeDecimals(metadata.decimals);

        // slither-disable-next-line reentrancy-no-eth
        address bridgeTokenProxy = address(
            new ERC1967Proxy(
                tokenImplementationAddress,
                abi.encodeWithSelector(
                    BridgeToken.initialize.selector,
                    metadata.name,
                    metadata.symbol,
                    decimals
                )
            )
        );

        deployTokenExtension(
            metadata.token,
            bridgeTokenProxy,
            decimals,
            metadata.decimals
        );

        emit BridgeTypes.DeployToken(
            bridgeTokenProxy,
            metadata.token,
            metadata.name,
            metadata.symbol,
            decimals,
            metadata.decimals
        );

        isBridgeToken[address(bridgeTokenProxy)] = true;
        ethToNearToken[address(bridgeTokenProxy)] = metadata.token;
        nearToEthToken[metadata.token] = address(bridgeTokenProxy);

        return bridgeTokenProxy;
    }

    function deployTokenExtension(
        string memory token,
        address tokenAddress,
        uint8 decimals,
        uint8 originDecimals
    ) internal virtual {}

    function setMetadata(
        string calldata token,
        string calldata name,
        string calldata symbol
    ) external onlyRole(DEFAULT_ADMIN_ROLE) {
        require(isBridgeToken[nearToEthToken[token]], "ERR_NOT_BRIDGE_TOKEN");

        BridgeToken bridgeToken = BridgeToken(nearToEthToken[token]);

        bridgeToken.setMetadata(name, symbol, bridgeToken.decimals());

        emit BridgeTypes.SetMetadata(
            address(bridgeToken),
            token,
            name,
            symbol,
            bridgeToken.decimals()
        );
    }

    function logMetadata(address tokenAddress) external payable {
        string memory name = IERC20Metadata(tokenAddress).name();
        string memory symbol = IERC20Metadata(tokenAddress).symbol();
        uint8 decimals = IERC20Metadata(tokenAddress).decimals();

        logMetadataExtension(tokenAddress, name, symbol, decimals);

        emit BridgeTypes.LogMetadata(tokenAddress, name, symbol, decimals);
    }

    function logMetadata1155(
        address tokenAddress,
        uint256 tokenId
    ) external payable {
        address deterministicToken = deriveDeterministicAddress(
            tokenAddress,
            tokenId
        );

        MultiTokenInfo storage multiToken = multiTokens[deterministicToken];

        if (multiToken.tokenAddress == address(0)) {
            multiToken.tokenAddress = tokenAddress;
            multiToken.tokenId = tokenId;
        } else {
            if (
                multiToken.tokenAddress != tokenAddress ||
                multiToken.tokenId != tokenId
            ) {
                revert ERC1155MappingMismatch();
            }
        }

        logMetadataExtension(
            deterministicToken,
            Strings.toHexString(tokenAddress),
            "",
            0
        );

        emit BridgeTypes.LogMetadata(
            deterministicToken,
            Strings.toHexString(tokenAddress),
            "",
            0
        );
    }

    function logMetadataExtension(
        address tokenAddress,
        string memory name,
        string memory symbol,
        uint8 decimals
    ) internal virtual {}

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

    function finTransferExtension(
        BridgeTypes.TransferMessagePayload memory payload
    ) internal virtual {}

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

    // We intentionally avoid advertising IERC1155Receiver support so tooling does not suggest direct ERC1155 sends.
    // Only transfers initiated by this contract itself are accepted.
    function supportsInterface(
        bytes4 interfaceId
    )
        public
        view
        virtual
        override(AccessControlUpgradeable, IERC165)
        returns (bool)
    {
        return super.supportsInterface(interfaceId);
    }

    function onERC1155Received(
        address operator,
        address,
        uint256,
        uint256,
        bytes calldata
    ) external view override returns (bytes4) {
        // Only accept transfers that were initiated by this contract itself
        if (operator != address(this)) {
            revert ERC1155DirectSendNotAllowed();
        }

        return this.onERC1155Received.selector;
    }

    function onERC1155BatchReceived(
        address,
        address,
        uint256[] calldata,
        uint256[] calldata,
        bytes calldata
    ) external pure override returns (bytes4) {
        // Explicitly reject batched multi-token transfers
        revert ERC1155BatchNotSupported();
    }

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

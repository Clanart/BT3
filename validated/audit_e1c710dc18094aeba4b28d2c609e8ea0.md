## Analog Found

### Title
`handleERC20Transfer` (and `handleERC721Transfer`) lack the `nonReentrant` modifier that its sibling `handleKLAYTransfer` has, within the same Bridge contract - (File: contracts/service_chain/bridge/BridgeTransferERC20.sol)

### Summary
The `Bridge` contract composes `BridgeTransferKLAY`, `BridgeTransferERC20`, and `BridgeTransferERC721` into a single deployed contract. `BridgeTransferKLAY` explicitly inherits `ReentrancyGuard` and protects `handleKLAYTransfer`/`_requestKLAYTransfer` with `nonReentrant` [1](#0-0) , while the sibling value-transfer handlers `handleERC20Transfer` in `BridgeTransferERC20.sol` and the analogous ERC721 handler perform state-changing external calls without any `nonReentrant` guard, since `BridgeTransferERC20`/`BridgeTransferERC721` do not inherit `ReentrancyGuard` at all.

### Finding Description
`handleERC20Transfer` is marked only `public onlyOperators`, with no `nonReentrant` protection [2](#0-1) . After updating handled-nonce bookkeeping and emitting the event, it performs an external call to the token contract, either `ERC20Mintable(_tokenAddress).mint(_to, _value)` or `IERC20(_tokenAddress).safeTransfer(_to, _value)` [3](#0-2) . Notably, unlike `_requestERC20Transfer`, which is protected by `onlyRegisteredToken`, `handleERC20Transfer` places no restriction on `_tokenAddress`, so any address supplied by an operator is called directly.

This mirrors exactly the audit-report pattern: within the same protocol/contract family, one value-transfer entry point (`createPool`/`handleKLAYTransfer`) carries the `nonReentrant` guard while a structurally identical sibling entry point that also triggers external calls (`createPoolWithCustomStrategy`/`handleERC20Transfer`) does not, despite both being reachable through the same operator-controlled multisig voting flow (`_voteValueTransfer`) defined in `BridgeTransfer.sol` [4](#0-3) .

Because `Bridge.sol` combines all these mixins into one deployed contract, the absence of a shared `nonReentrant` guard on `handleERC20Transfer`/`handleERC721Transfer` means a malicious or misbehaving token contract invoked as `_tokenAddress` (or an ERC721 contract with a `safeTransferFrom`-style callback) can re-enter other bridge functions — including `handleERC20Transfer` itself with a different nonce, or unrelated operator-voting functions like `setERC20Fee`/`setFeeReceiver` — while the outer call's execution context is still active.

### Impact Explanation
If reentrancy is achieved through a malicious/registered token during `handleERC20Transfer` (which any operator can trigger for any `_tokenAddress`, since there is no `onlyRegisteredToken` restriction on this handler), an attacker-influenced token contract could reenter other state-mutating bridge functions before the outer call completes, potentially double-processing transfers or corrupting the nonce/vote bookkeeping shared across token types, leading to duplicate token minting/transfer or fee-accounting inconsistency — a concrete value-movement or state-divergence risk consistent with the required "Medium" severity bar.

### Likelihood Explanation
Exploitation requires an operator to call `handleERC20Transfer` with an attacker-controlled or malicious `_tokenAddress` argument (not restricted to registered tokens), or a malicious contract to be the target of `_to` in `handleERC721Transfer`/`onERC721Received`-style flows. This is only reachable via a bridge operator transaction (multisig-gated by `onlyOperators`/`_voteValueTransfer`), which somewhat limits the likelihood versus a fully permissionless entry point, but the missing guard is a direct structural gap identical to the reported issue class.

### Recommendation
Have `BridgeTransferERC20` and `BridgeTransferERC721` inherit `ReentrancyGuard` (as `BridgeTransferKLAY` already does) and add the `nonReentrant` modifier to `handleERC20Transfer`, `_requestERC20Transfer`, and the equivalent ERC721 handler/request functions, consistent with the protection already applied to the KLAY transfer path.

### Proof of Concept
1. Register a malicious ERC20-like contract as `_tokenAddress` is not required for `handleERC20Transfer` (no `onlyRegisteredToken` modifier present) [5](#0-4) .
2. An operator calls `handleERC20Transfer(txHash1, from, to, maliciousToken, value, nonce1, blockNum, extraData)`.
3. Inside `maliciousToken.transfer`/`mint`, the malicious contract calls back into `Bridge.handleERC20Transfer` with a different `_requestTxHash`/`_requestedNonce`, or into `setFeeReceiver`/`setERC20Fee`, before the outer call's bookkeeping settles, since no `nonReentrant` guard exists on this call path (contrast with `handleKLAYTransfer`'s guard at [6](#0-5) ).

### Citations

**File:** contracts/service_chain/bridge/BridgeTransferKLAY.sol (L17-24)
```text
pragma solidity 0.5.6;

import "./BridgeTransfer.sol";
import "../../libs/openzeppelin-contracts-v2/contracts/utils/ReentrancyGuard.sol";


contract BridgeTransferKLAY is BridgeTransfer, ReentrancyGuard {
    bool public isLockedKLAY;
```

**File:** contracts/service_chain/bridge/BridgeTransferKLAY.sol (L62-100)
```text
    function handleKLAYTransfer(
        bytes32 _requestTxHash,
        address _from,
        address payable _to,
        uint256 _value,
        uint64 _requestedNonce,
        uint64 _requestedBlockNumber,
        bytes memory _extraData
    )
        public
        onlyOperators
        nonReentrant
    {
        _lowerHandleNonceCheck(_requestedNonce);

        if (!_voteValueTransfer(_requestedNonce)) {
            return;
        }

        _setHandledRequestTxHash(_requestTxHash);

        handleNoncesToBlockNums[_requestedNonce] = _requestedBlockNumber;
        _updateHandleNonce(_requestedNonce);

        emit HandleValueTransfer(
            _requestTxHash,
            TokenType.KLAY,
            _from,
            _to,
            address(0),
            _value,
            _requestedNonce,
            lowerHandleNonce,
            _extraData
        );

        (bool ok, ) = _to.call.value(_value)("");
        require(ok, "handleKLAYTransfer: transfer failed");
    }
```

**File:** contracts/service_chain/bridge/BridgeTransferERC20.sol (L32-73)
```text
    function handleERC20Transfer(
        bytes32 _requestTxHash,
        address _from,
        address _to,
        address _tokenAddress,
        uint256 _value,
        uint64 _requestedNonce,
        uint64 _requestedBlockNumber,
        bytes memory _extraData
    )
        public
        onlyOperators
    {
        _lowerHandleNonceCheck(_requestedNonce);

        if (!_voteValueTransfer(_requestedNonce)) {
            return;
        }

        _setHandledRequestTxHash(_requestTxHash);

        handleNoncesToBlockNums[_requestedNonce] = _requestedBlockNumber;
        _updateHandleNonce(_requestedNonce);

        emit HandleValueTransfer(
            _requestTxHash,
            TokenType.ERC20,
            _from,
            _to,
            _tokenAddress,
            _value,
            _requestedNonce,
            lowerHandleNonce,
            _extraData
        );

        if (modeMintBurn) {
            require(ERC20Mintable(_tokenAddress).mint(_to, _value), "handleERC20Transfer: mint failed");
        } else {
            IERC20(_tokenAddress).safeTransfer(_to, _value);
        }
    }
```

**File:** contracts/service_chain/bridge/BridgeTransfer.sol (L138-160)
```text
    // _updateHandleNonce increases lower and upper handle nonce after the _requestedNonce is handled.
    function _updateHandleNonce(uint64 _requestedNonce) internal {
        if (_requestedNonce > upperHandleNonce) {
            upperHandleNonce = _requestedNonce;
        }

        uint64 limit = lowerHandleNonce + 200;
        if (limit > upperHandleNonce) {
            limit = upperHandleNonce;
        }

        uint64 i;
        for (i = lowerHandleNonce; i <= limit && handleNoncesToBlockNums[i] > 0; i++) {
            recoveryBlockNumber = handleNoncesToBlockNums[i];
            delete handleNoncesToBlockNums[i];
            delete closedValueTransferVotes[i];
        }
        lowerHandleNonce = i;
    }

    function _lowerHandleNonceCheck(uint64 _requestedNonce) internal {
        require(lowerHandleNonce <= _requestedNonce, "removed vote");
    }
```

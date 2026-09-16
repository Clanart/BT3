### Title
Fee-on-transfer ERC20 tokens break the service-chain Bridge's request/handle accounting, allowing cross-chain value inflation - ([File: contracts/service_chain/bridge/BridgeTransferERC20.sol])

### Summary
`BridgeTransferERC20.requestERC20Transfer` pulls tokens from the caller with `safeTransferFrom(msg.sender, address(this), _value.add(_feeLimit))` and then unconditionally emits `RequestValueTransfer` with the caller-supplied `_value`, without verifying that the bridge actually received `_value` (net of any transfer fee) [1](#0-0) . If the registered token charges a fee-on-transfer, the bridge contract's real token balance increase is smaller than the declared `_value`, yet the emitted event (which off-chain operators/relayers use as the source of truth for cross-chain settlement) still states the full `_value`.

### Finding Description
`_requestERC20Transfer` computes the bridge fee from the caller-declared `_feeLimit` via `_payERC20FeeAndRefundChange`, burns `_value` in mint/burn mode, and emits `RequestValueTransfer(..., _value, ...)` — all based on the nominal amount, not the amount actually credited to the bridge's balance [2](#0-1) . In lock/release mode (`modeMintBurn == false`), the bridge is supposed to hold `_value` tokens as backing until the counterpart bridge calls `handleERC20Transfer`, which trustingly performs `IERC20(_tokenAddress).safeTransfer(_to, _value)` on the destination side using the exact `_value` from the request event [3](#0-2) . There is no check anywhere in `BridgeTransferERC20.sol` or `BridgeTransfer.sol`/`BridgeTokens.sol` that the token's actual balance delta equals `_value + _feeLimit`; `onlyRegisteredToken`/`onlyUnlockedToken` only gate on an allow-list, not on transfer semantics.

This is the direct analog of the reported Cooler bug: an amount is recorded and propagated (`_value` in the emitted `RequestValueTransfer` event, later consumed by `handleERC20Transfer`) without accounting for the fact that fee-on-transfer tokens deliver less than the nominal transferred amount to the receiving contract.

### Impact Explanation
Because the destination-side `handleERC20Transfer` mints/unlocks exactly `_value` (the declared amount from the event) while the source bridge only actually received `_value - tokenFee`, each request using a fee-on-transfer token silently under-collateralizes the bridge by the fee amount. Over repeated requests this creates a systemic supply/backing mismatch: in mint-burn mode, more tokens are minted on the counterpart chain than were ever burned/held on the source chain (effectively unbacked supply inflation); in lock-release mode, the bridge's real ERC20 balance falls below the cumulative amount it is obligated to release, causing insolvency and eventually preventing legitimate withdrawal requests from being honored — a direct value-extraction/DoS vector for any user who registers or uses a fee-on-transfer token with the bridge.

### Likelihood Explanation
Any unprivileged user who can get (or already has) a fee-on-transfer ERC20 registered with the bridge can trigger this simply by calling the public `requestERC20Transfer` function — no special privileges, validator collusion, or malicious node behavior is required; the bug is purely in how the contract computes/propagates the transferred amount versus the token's real balance change.

### Recommendation
In `requestERC20Transfer`/`onERC20Received`, measure the bridge's token balance before and after the `transferFrom` call and use the actual received delta (rather than the caller-declared `_value`/`_feeLimit`) for fee computation, burn amount, and the `RequestValueTransfer` event; alternatively, restrict token registration (`registerToken`) to tokens verified not to charge a transfer fee, or explicitly document/enforce that fee-on-transfer tokens are disallowed.

### Proof of Concept
1. Register a fee-on-transfer ERC20 token (e.g., 1% fee) with the bridge via the existing token registration flow.
2. User calls `requestERC20Transfer(token, to, value=100, feeLimit=0, extraData)`; `safeTransferFrom` moves 100 tokens from the user, but due to the 1% fee the bridge's balance only increases by 99 [4](#0-3) .
3. `_requestERC20Transfer` still emits `RequestValueTransfer(..., value=100, ...)` [5](#0-4) .
4. Bridge operators on the counterpart chain observe the event and call `handleERC20Transfer(..., _value=100, ...)`, which mints or releases 100 tokens to `_to` [6](#0-5) , even though only 99 tokens were actually backing this request on the source chain — a 1-token deficit created per such request.

### Citations

**File:** contracts/service_chain/bridge/BridgeTransferERC20.sol (L56-73)
```text
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

**File:** contracts/service_chain/bridge/BridgeTransferERC20.sol (L75-108)
```text
    // _requestERC20Transfer requests transfer ERC20 to _to on relative chain.
    function _requestERC20Transfer(
        address _tokenAddress,
        address _from,
        address _to,
        uint256 _value,
        uint256 _feeLimit,
        bytes memory _extraData
    )
        internal
        onlyRegisteredToken(_tokenAddress)
        onlyUnlockedToken(_tokenAddress)
    {
        require(isRunning, "stopped bridge");
        require(_value > 0, "zero ERC20 token amount");

        uint256 fee = _payERC20FeeAndRefundChange(_from, _tokenAddress, _feeLimit);

        if (modeMintBurn) {
            ERC20Burnable(_tokenAddress).burn(_value);
        }

        emit RequestValueTransfer(
            TokenType.ERC20,
            _from,
            _to,
            _tokenAddress,
            _value,
            requestNonce,
            fee,
            _extraData
        );
        requestNonce++;
    }
```

**File:** contracts/service_chain/bridge/BridgeTransferERC20.sol (L123-135)
```text
    // requestERC20Transfer requests transfer ERC20 to _to on relative chain.
    function requestERC20Transfer(
        address _tokenAddress,
        address _to,
        uint256 _value,
        uint256 _feeLimit,
        bytes memory _extraData
    )
        public
    {
        IERC20(_tokenAddress).safeTransferFrom(msg.sender, address(this), _value.add(_feeLimit));
        _requestERC20Transfer(_tokenAddress, msg.sender, _to, _value, _feeLimit, _extraData);
    }
```

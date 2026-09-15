### Title
Fee-on-transfer / deflationary ERC20 tokens cause value duplication in `requestERC20Transfer` - (File: `contracts/service_chain/bridge/BridgeTransferERC20.sol`)

### Summary
`BridgeTransferERC20.requestERC20Transfer()` pulls tokens with `safeTransferFrom` and then unconditionally treats the caller-supplied `_value` as the amount actually held by the bridge, without verifying the real balance delta. For a fee-on-transfer / deflationary ERC20 token, the bridge receives less than `_value + _feeLimit`, yet it still emits `RequestValueTransfer` with the full nominal `_value` and (in mint-burn mode) attempts to burn exactly `_value`.

### Finding Description
`requestERC20Transfer` performs: [1](#0-0) 

It calls `IERC20(_tokenAddress).safeTransferFrom(msg.sender, address(this), _value.add(_feeLimit))` and then immediately calls `_requestERC20Transfer` with the caller-supplied `_value`, without measuring the bridge's actual token balance before/after the transfer.

Inside `_requestERC20Transfer`, the fee is paid/refunded via `_payERC20FeeAndRefundChange`, and in `modeMintBurn` mode the contract burns exactly `_value`: [2](#0-1) 

`_payERC20FeeAndRefundChange` also just transfers out nominal `fee`/`feeRefund` amounts assuming they equal what was actually received: [3](#0-2) 

On the receiving side, `handleERC20Transfer` mints (or transfers from bridge liquidity) the full nominal `_value` to `_to` on the counterpart chain based solely on the `RequestValueTransfer` event's `_value` field, with no cross-check against tokens actually locked/burned on the source chain: [4](#0-3) 

If `_tokenAddress` is a fee-on-transfer token, `safeTransferFrom` delivers `(_value + _feeLimit) - transferFee` tokens to the bridge, but the event and burn/handle logic still reference the full `_value`. This is the direct analog of the reported bug class: the contract assumes `amount transferred == amount received`, causing an accounting mismatch between what the bridge actually locked/burned and what it credits on the other chain.

### Impact Explanation
In `modeMintBurn` mode, the bridge burns `_value` tokens even though it only actually collected `_value - fee_on_transfer` (it may not even hold enough balance to burn `_value`, causing revert/DoS), while the counterpart chain mints the full nominal `_value` — this creates a supply/value mismatch across chains (effectively inflating the wrapped-token supply relative to real locked/burned collateral). In non-mint-burn (locked liquidity) mode, the bridge's on-chain token reserve becomes permanently short by the accumulated transfer fees relative to the liabilities it has promised to pay out on request, since `HandleValueTransfer`/`RequestValueTransfer` accounting always uses the nominal, non-fee-adjusted `_value`. This is a legitimate cross-chain value-integrity issue reachable by any unprivileged bridge user submitting a single `requestERC20Transfer` transaction for a fee-on-transfer token.

### Likelihood Explanation
Likelihood depends on whether a fee-on-transfer token is registered in the bridge via `onlyRegisteredToken`/token-registration flow (operator-controlled). Once such a token is registered — which can happen for any ERC20 that charges transfer fees, including tokens that add fee-on-transfer functionality via upgradeable proxies after initial registration — every unprivileged holder of that token can trigger the mismatch with a normal `requestERC20Transfer` call; no special privilege is needed to exploit the bug itself.

### Recommendation
In `requestERC20Transfer` (and `onERC20Received`), measure the bridge's actual token balance immediately before and after `safeTransferFrom`, and use the measured delta (rather than the caller-supplied `_value`/`_feeLimit`) for fee computation, burn amount, and the `RequestValueTransfer` event. Reject or explicitly document non-support for fee-on-transfer/rebasing tokens during token registration.

### Proof of Concept
1. Register a fee-on-transfer ERC20 token (that deducts e.g. 1% on every `transfer`/`transferFrom`) in the bridge with `modeMintBurn = true`.
2. User calls `requestERC20Transfer(token, to, value=100, feeLimit=0, extraData)`.
3. `safeTransferFrom(msg.sender, bridge, 100)` actually delivers only 99 tokens to the bridge due to the token's fee.
4. `_requestERC20Transfer` attempts `ERC20Burnable(token).burn(100)` — either reverts (DoS, bridge balance is only 99) if no other liquidity exists, or, if excess balance exists from other users, burns 100 while only 99 was contributed by this request, and emits `RequestValueTransfer(..., value=100, ...)`.
5. On the counterpart chain, `handleERC20Transfer` mints the full 100 tokens to `to`, even though only 99 were actually removed from the source chain's real supply — creating a 1-token discrepancy (supply inflation) per transfer, which compounds with the token's fee rate and swap volume over time.

### Citations

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

**File:** contracts/service_chain/bridge/BridgeFee.sol (L68-88)
```text
    function _payERC20FeeAndRefundChange(address from, address _token, uint256 _feeLimit) internal returns(uint256) {
        uint256 fee = feeOfERC20[_token];

        if (feeReceiver != address(0) && fee > 0) {
            require(_feeLimit >= fee, "insufficient feeLimit");

            IERC20(_token).safeTransfer(feeReceiver, fee);

            uint256 feeRefund = _feeLimit.sub(fee);
            if (feeRefund > 0) {
                IERC20(_token).safeTransfer(from, feeRefund);
            }

            return fee;
        }

        if (_feeLimit > 0) {
            IERC20(_token).safeTransfer(from, _feeLimit);
        }
        return 0;
    }
```

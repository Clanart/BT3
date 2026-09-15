### Title
Fee-on-transfer ERC20 tokens cause bridge to credit nominal value while receiving less, enabling value drain in `BridgeTransferERC20.requestERC20Transfer` - (File: contracts/service_chain/bridge/BridgeTransferERC20.sol)

### Summary
`BridgeTransferERC20.requestERC20Transfer` and the internal `_requestERC20Transfer`/`_payERC20FeeAndRefundChange` flow assume that calling `safeTransferFrom(msg.sender, address(this), _value.add(_feeLimit))` always credits the bridge with exactly `_value + _feeLimit` tokens. For a fee-on-transfer ERC20 token, the bridge actually receives less than the nominal amount, yet all downstream accounting (fee payment, refund, burn, and the `RequestValueTransfer` event that the counterpart chain trusts to mint/unlock funds) still uses the nominal, unadjusted `_value`.

### Finding Description
In `requestERC20Transfer`:
```solidity
IERC20(_tokenAddress).safeTransferFrom(msg.sender, address(this), _value.add(_feeLimit));
_requestERC20Transfer(_tokenAddress, msg.sender, _to, _value, _feeLimit, _extraData);
``` [1](#0-0) 

`_requestERC20Transfer` then calls `_payERC20FeeAndRefundChange`, which unconditionally transfers a fixed `fee` to `feeReceiver` and refunds `_feeLimit - fee` to the sender, and — in `modeMintBurn` mode — burns exactly `_value` tokens from the bridge's own balance: [2](#0-1) 

`_payERC20FeeAndRefundChange` moves tokens based purely on the nominal `_feeLimit`/`fee` parameters, with no verification of the bridge's actual received balance: [3](#0-2) 

The `RequestValueTransfer` event carries the nominal `_value` unmodified: [4](#0-3) 

On the counterpart chain, `handleERC20Transfer` trusts this nominal `_value` and either mints new tokens or releases previously locked liquidity to `_to` for that exact amount: [5](#0-4) 

If the registered `_tokenAddress` is a fee-on-transfer token, the `safeTransferFrom` call silently delivers fewer tokens than `_value + _feeLimit` to the bridge. None of the subsequent logic reconciles this shortfall against actual balances — `_payERC20FeeAndRefundChange`, the optional `burn(_value)`, and the emitted event all still operate on the nominal amounts the caller supplied.

### Impact Explanation
- In `modeMintBurn = false` (lock/unlock) deployments, the bridge under-collects real tokens relative to the nominal `_value` it advertises via `RequestValueTransfer`. The counterpart bridge instance will still unlock/transfer the full nominal `_value` to `_to` on the other chain, meaning the destination-side liquidity pool pays out more real value than was actually locked on the source side. Repeated use drains the destination bridge's genuine reserves — an unauthorized value movement/theft.
- In `modeMintBurn = true` deployments, `ERC20Burnable(_tokenAddress).burn(_value)` operates on the bridge's own (now short) balance; this either reverts (causing a stuck/DoS state for the specific token) or, if the shortfall is absorbed by other balance in the bridge, still results in the counterpart chain minting a nominal amount not fully backed by real collateral burned.

This is a direct analog of the Teller `CollateralManager` fee-on-transfer issue: value accounting assumes full receipt of ERC20 transfers, but on a two-sided cross-chain bridge, the consequence escalates from "operation reverts" to genuine value/supply mismatch between chains when the transfer succeeds.

### Likelihood Explanation
Exploitability depends on a fee-on-transfer token being registered on the bridge via `registerToken` (an operator action), which is a normal, expected configuration path for a general-purpose ERC20 bridge — the bridge code makes no attempt to reject or account for such tokens. Any unprivileged user holding such a token can call the public `requestERC20Transfer` function to trigger the mismatch; no privileged access is required to exploit the flow once such a token is bridge-registered.

### Recommendation
Measure the bridge's actual token balance before and after the `safeTransferFrom` call and use the delta (rather than the caller-supplied `_value`/`_feeLimit`) for all downstream accounting: fee payment/refund calculation, the optional `burn`, and the `_value` emitted in `RequestValueTransfer`. Alternatively, explicitly document/enforce that only standard (non-fee-on-transfer, non-rebasing) ERC20 tokens may be registered, and add a balance-based sanity check in `registerToken`/`requestERC20Transfer` that reverts if the received amount does not match the expected amount.

### Proof of Concept
1. Operator registers a fee-on-transfer ERC20 token `T` (e.g., 5% transfer tax) via `registerToken` on a bridge instance configured with `modeMintBurn = false`.
2. User calls `requestERC20Transfer(T, to, value=100, feeLimit=0, extraData)`. `safeTransferFrom` pulls nominally 100 `T` from the user, but due to the 5% tax the bridge only receives 95 `T`.
3. `_payERC20FeeAndRefundChange` finds `fee = 0`, so no fee/refund transfer occurs; `_requestERC20Transfer` emits `RequestValueTransfer(..., value=100, ...)`, unaware that only 95 `T` are actually held.
4. The counterpart bridge operator (or automated relayer) calls `handleERC20Transfer` on the destination chain with `_value = 100`, releasing 100 units of the mirrored/locked asset to `to`, even though only 95 real `T` back this transfer on the source chain.
5. Repeating this drains the destination-side liquidity pool by the accumulated fee-on-transfer shortfall, with no revert or detection anywhere in the flow.

### Citations

**File:** contracts/service_chain/bridge/BridgeTransferERC20.sol (L31-73)
```text
    // handleERC20Transfer sends the token by the request.
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

**File:** contracts/service_chain/bridge/BridgeTransferERC20.sol (L87-107)
```text
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
```

**File:** contracts/service_chain/bridge/BridgeTransferERC20.sol (L124-135)
```text
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

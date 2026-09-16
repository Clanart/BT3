### Title
Fee-on-transfer / non-standard ERC20 tokens registered on the Kaia service-chain Bridge allow draining of pooled token liquidity - ([File: contracts/service_chain/bridge/BridgeTransferERC20.sol])

### Summary
`BridgeTransferERC20.requestERC20Transfer()` and `BridgeFee._payERC20FeeAndRefundChange()` assume that the amount of ERC20 tokens actually received by the Bridge contract equals the nominal `_value + _feeLimit` parameter supplied by the caller. If a fee-on-transfer (or otherwise deflationary/rebasing) ERC20 is registered as a bridged token, the Bridge will credit, refund and forward amounts based on the nominal value instead of the amount actually held, letting a depositor extract more value on the counter-chain than they contributed, at the expense of the Bridge's pooled token reserve.

### Finding Description
`requestERC20Transfer()` pulls tokens from the caller using the nominal amount and then immediately proceeds with fee/refund accounting and event emission using the same nominal amount, without ever checking the Bridge's actual token balance: [1](#0-0) 

`_requestERC20Transfer()` then pays the fee to `feeReceiver`, refunds the remainder to the sender, and (in lock/unlock, i.e. non-mint-burn, mode) leaves the nominal `_value` implicitly "backed" in the Bridge, before emitting `RequestValueTransfer` with the full nominal `_value`: [2](#0-1) 

`_payERC20FeeAndRefundChange()` performs `safeTransfer` calls for `fee` and `feeRefund` using the nominal `_feeLimit`, again without verifying the tokens actually received: [3](#0-2) 

On the counter-chain, `handleERC20Transfer()` (called by bridge operators once the two-vote handling process completes) releases the full nominal `_value` recorded in the `RequestValueTransfer` event, either by transferring from the counter-chain Bridge's real token reserve (lock/unlock mode) or by minting a wrapped representation (mint/burn mode): [4](#0-3) 

Because a fee-on-transfer token deducts a transfer tax during `safeTransferFrom(msg.sender, address(this), _value.add(_feeLimit))`, the Bridge actually receives less than `_value + _feeLimit`. The contract still pays out `fee` in full and refunds `feeRefund` in full, then treats the full nominal `_value` as backed liquidity and reports it in the `RequestValueTransfer` event that operators use to release funds on the other chain. The shortfall is silently absorbed from the Bridge's existing pooled balance (contributed by prior, unrelated depositors of the same token), rather than being detected or reverted.

### Impact Explanation
This is not merely a revert/DoS as in the fee-on-transfer collateral analog — because the Bridge never checks pre/post balances, the transaction actually succeeds while under-collateralizing the value it promises to release on the counter-chain. Any unprivileged sender holding a fee-on-transfer ERC20 that is registered on the Bridge can repeatedly call `requestERC20Transfer` to have the operators release the full nominal amount on the destination chain while contributing less than that amount to the Bridge's reserve, draining value that ultimately comes from other users' deposits of the same token. This is a concrete unauthorized value-movement / theft of pooled bridge liquidity, satisfying a Medium/High severity bar depending on deployment configuration (lock/unlock vs mint/burn mode) and the specific token registered.

### Likelihood Explanation
Exploitability depends on a fee-on-transfer (or similarly non-standard) ERC20 being registered via `registerToken`/token-registration flow, which is generally performed by bridge operators/admins rather than the depositor. However, once such a token is registered — which can happen inadvertently for community-listed tokens, or via a malicious token that initially behaves standard-compliant and later enables a transfer fee (a common rug-pull pattern) — any regular, unprivileged transaction sender can trigger the shortfall by simply calling `requestERC20Transfer`/`onERC20Received`, with no special privileges required.

### Recommendation
Before performing fee/refund/burn accounting in `_requestERC20Transfer`/`_payERC20FeeAndRefundChange`, measure the Bridge's token balance before and after `transferFrom`/`safeTransferFrom` and use the actual delta (rather than the nominal `_value + _feeLimit`) for all downstream accounting, refunds, burns, and the `RequestValueTransfer` event emitted for the counter-chain operators to act upon. Alternatively, explicitly disallow registration of tokens whose `balanceOf` delta does not match the transferred amount (i.e., reject fee-on-transfer/rebasing tokens at registration time).

### Proof of Concept
1. Bridge operators register a fee-on-transfer ERC20 token `T` (e.g., 5% transfer fee) via the standard token-registration flow, with the Bridge configured in lock/unlock (non mint-burn) mode and holding an existing reserve of `T` from prior legitimate deposits.
2. Attacker approves the Bridge for `T` and calls `requestERC20Transfer(T, attackerAddrOnOtherChain, value=1000, feeLimit=0, "")`.
3. `safeTransferFrom(attacker, bridge, 1000)` actually transfers only 950 `T` to the Bridge due to the 5% fee, but `_requestERC20Transfer` still emits `RequestValueTransfer(..., _value=1000, ...)`.
4. Bridge operators observe the event and call `handleERC20Transfer(..., _value=1000, ...)` on the counter-chain Bridge, releasing 1000 `T` (or minted equivalent) to the attacker's address there.
5. The attacker has extracted 1000 units of value while contributing only 950, with the 50-unit deficit drawn from the Bridge's existing pooled reserve funded by other depositors.

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

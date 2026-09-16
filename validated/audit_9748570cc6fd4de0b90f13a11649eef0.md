### Title
Fee-on-transfer / non-standard ERC20 tokens break BridgeTransferERC20 accounting, enabling supply inflation and bridge fund drainage - ([File: contracts/service_chain/bridge/BridgeTransferERC20.sol])

### Summary
`requestERC20Transfer` and `onERC20Received` in `BridgeTransferERC20.sol` trust the caller-supplied `_value` (and `_feeLimit`) as the exact amount of tokens the bridge actually received, without verifying the bridge's real token balance delta. For any ERC20 token that charges a transfer fee, rebases, or otherwise delivers less than the nominal amount on `transferFrom`, this assumption is false, and the bridge's internal request/handle accounting becomes inflated relative to what it actually holds.

### Finding Description
`requestERC20Transfer` pulls tokens with `safeTransferFrom(msg.sender, address(this), _value.add(_feeLimit))` and then immediately calls `_requestERC20Transfer` with the nominal `_value`, without comparing the bridge's token balance before/after the transfer: [1](#0-0) 

Inside `_requestERC20Transfer`, the fee is deducted/refunded via `_payERC20FeeAndRefundChange`, and then — in mint/burn mode — the full nominal `_value` is burned and emitted in `RequestValueTransfer`, which the counterparty chain's operators use to `mint(_to, _value)` on `handleERC20Transfer`: [2](#0-1) [3](#0-2) 

`_payERC20FeeAndRefundChange` also assumes `_feeLimit` (nominal) tokens are available to transfer out as fee/refund, again with no balance verification: [4](#0-3) 

If the registered token deducts a fee on transfer (or is otherwise non-standard, e.g. rebasing), the bridge contract's real balance increase from `safeTransferFrom` is strictly less than `_value + _feeLimit`, yet:
- In `modeMintBurn == false` (lock/release) mode, the bridge burns nothing and simply records `_value` as transferred, later releasing the full nominal `_value` to `_to` on the destination side; because the bridge holds less than that nominal amount, honest users' previously-locked balances are used to cover the shortfall, i.e. later withdrawals can drain funds belonging to other users.
- In `modeMintBurn == true` mode, `ERC20Burnable(_tokenAddress).burn(_value)` will burn the full nominal `_value` (assuming the bridge's balance is sufficient from prior deposits) while only receiving a lesser amount, and `handleERC20Transfer` on the counterparty side mints the full nominal `_value`, inflating total token supply beyond what was actually deposited.
- `_payERC20FeeAndRefundChange`'s outgoing `safeTransfer` calls for fee/refund can eventually revert or drain the bridge's other-token holdings if the bridge's actual balance is insufficient, or (worse) can silently succeed by consuming other depositors' balances, since the contract keeps no per-request balance accounting — only a global registered-token allowlist via `onlyRegisteredToken`.

This mirrors the reported Allo-v2 issue: a contract that receives ERC20 tokens and records/propagates a caller-declared `_amount` as ground truth without checking `balanceOf(address(this))` before and after the transfer.

### Impact Explanation
This is reachable by any unprivileged externally-owned account calling `requestERC20Transfer` (or by any ERC20 token's `transfer` hook calling `onERC20Received`) with a registered fee-on-transfer token. The impact is concrete value theft/inflation:
- Supply inflation across the bridged chains (mint side credits more tokens than were ever locked/burned on the source side).
- Drainage of other users' bridged token balances in lock/release mode, since accounting is based on nominal amounts rather than actual balance deltas.

This satisfies the "Medium/High" bar: unauthorized value movement and supply inflation via a public, unprivileged transaction path.

### Likelihood Explanation
Likelihood depends on whether an operator registers a fee-on-transfer or rebasing ERC20 token via `registerToken`/`setERC20Fee` (governance-level bridge admin action), which is plausible since the bridge is a generic framework intended to support arbitrary ERC20 tokens across service chains — the contract does not restrict registrable tokens to a known-safe list, and there is no on-chain validation rejecting non-standard transfer behavior.

### Recommendation
In `requestERC20Transfer`/`onERC20Received`/`_requestERC20Transfer`, measure the bridge's actual token balance before and after `safeTransferFrom` (and before/after any outgoing fee transfers) and use the observed balance delta as the effective `_value` for burning, fee accounting, and the `RequestValueTransfer` event, rather than trusting the caller-supplied nominal amount. Alternatively, explicitly document/enforce that only standard, non-fee-on-transfer, non-rebasing ERC20 tokens may be registered, and add a registration-time check (e.g., a test transfer) to reject tokens whose received amount deviates from the requested amount.

### Proof of Concept
1. Deploy `BridgeTransferERC20` in `modeMintBurn = false` (lock/release) with `feeOfERC20[token] = 0`.
2. Register a fee-on-transfer token (e.g., 10% transfer fee) via `registerToken`.
3. User A calls `requestERC20Transfer(token, userB, 100, 0, "0x")`. `safeTransferFrom` pulls 100 nominal tokens from A, but bridge only receives 90 due to the 10% fee.
4. `_requestERC20Transfer` still emits `RequestValueTransfer(..., _value = 100, ...)`.
5. Operators call `handleERC20Transfer` on the destination chain (or same chain if bridging back), which calls `IERC20(_tokenAddress).safeTransfer(_to, 100)`, releasing 100 tokens even though the bridge only ever received 90 — the extra 10 tokens are pulled from the bridge's other holdings (i.e., other users' deposited balances), demonstrating value siphoning enabled purely by an unprivileged `requestERC20Transfer` call using a fee-on-transfer token.

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

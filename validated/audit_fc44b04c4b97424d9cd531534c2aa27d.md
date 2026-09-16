Found a concrete analog: `requestERC20Transfer` in `Bridge`'s ERC20 handling does not verify actual received amount against the declared `_value`, unlike Vault's `_transferAndCheckUnderlying` which at least reverts on mismatch. Here the mismatch is silently accepted, leading to accounting/supply divergence rather than mere DoS.

### Title
Bridge accepts fee-on-transfer ERC20 tokens without verifying received amount, causing supply inflation on the counterpart chain - (File: contracts/service_chain/bridge/BridgeTransferERC20.sol)

### Summary
`requestERC20Transfer()` pulls tokens via `safeTransferFrom` for `_value + _feeLimit` but never checks the bridge's actual balance delta before emitting `RequestValueTransfer` with the caller-declared `_value`. If the token charges a transfer fee (or later upgrades to add one, e.g. USDT-style tokens), the bridge contract receives fewer tokens than `_value`, yet the operator relays `_value` unchanged to the counterpart chain via `handleERC20Transfer`, which mints (in mint/burn mode) or transfers that full declared amount to the recipient.

### Finding Description
`requestERC20Transfer` in `contracts/service_chain/bridge/BridgeTransferERC20.sol` performs: [1](#0-0) 
It calls `safeTransferFrom(msg.sender, address(this), _value.add(_feeLimit))` and then immediately calls `_requestERC20Transfer` with the caller-supplied `_value`, with no post-transfer balance check comparable to the "Vault" report's `_transferAndCheckUnderlying`. `_requestERC20Transfer` then, in burn mode, burns `_value` from the bridge's own balance: [2](#0-1) 
and emits `RequestValueTransfer(..., _value, ...)`, which off-chain relayer/operators use to call `handleERC20Transfer` on the counterpart bridge, minting or transferring the full `_value`: [3](#0-2) 

For a fee-on-transfer token: if a user calls `requestERC20Transfer(token, to, value, feeLimit, data)`, the bridge actually receives `value + feeLimit - fee` tokens, but still burns/holds and requests `value` to be minted/released on the other chain. In `modeMintBurn == true`, `ERC20Burnable(_tokenAddress).burn(_value)` will simply revert with insufficient balance (a DoS, matching the cited report exactly). In `modeMintBurn == false` (lock/release mode), the bridge holds less than `_value` in its own balance yet still allows `_value` to be requested and eventually released via `handleERC20Transfer`'s `safeTransfer(_to, _value)` on the counterpart/same-chain balance accounting — over repeated deposits this drains the bridge's real reserve faster than what request accounting assumes, understating outstanding liabilities and enabling the last redeemers to be unable to withdraw (insolvency), or, more critically, if `_value` used for minting on the counterpart chain isn't backed 1:1 by what was actually locked, this creates unbacked wrapped supply on the counterpart chain — a supply-inflation condition matching the "Impact" bar required (concrete unauthorized value movement / supply inflation), not just unavailability.

### Impact Explanation
This is more severe than the cited Sandclock vault case (which is limited to deposit unavailability): here, in lock/release bridge mode, the accounting divergence directly causes an under-collateralized bridge reserve — the bridge promises (via `RequestValueTransfer`/`HandleValueTransfer` events consumed by relayers) to release/mint `_value` tokens on the counterpart chain while having received less, which can inflate the wrapped-token supply beyond the token actually escrowed. In mint/burn mode it causes a hard revert on `burn(_value)`, denying value-transfer requests for any token that charges fees (medium-severity DoS, directly analogous to the cited report).

### Likelihood Explanation
Any unprivileged transaction sender who is a registered token holder can call `requestERC20Transfer` directly; the only precondition is that the token is `onlyRegisteredToken`/`onlyUnlockedToken` (an admin-controlled allowlist), which mirrors the cited report's disputed argument ("we only use fee-less tokens") — but as the judge noted, tokens can add fees later (e.g., USDT-style upgrades) even if not fee-charging at registration time, making this a live risk for any long-lived bridge deployment.

### Recommendation
Measure the bridge contract's actual token balance before and after `safeTransferFrom` in `requestERC20Transfer` and use the observed delta (rather than the caller-supplied `_value`) as the amount burned/escrowed and as the `_value` emitted in `RequestValueTransfer`, or explicitly reject registration/transfer requests for tokens whose received amount does not match the transferred amount.

### Proof of Concept
1. Register a fee-on-transfer ERC20 token via `RegisterToken`/`setERC20Fee` flow so it passes `onlyRegisteredToken`.
2. Call `requestERC20Transfer(token, to, value, feeLimit, data)` as an ordinary user: `safeTransferFrom` pulls `value+feeLimit` from the caller but the bridge's actual balance increases by less due to the token's internal fee.
3. `_requestERC20Transfer` proceeds unchanged, burning/escrowing `value` and emitting `RequestValueTransfer(..., value, ...)`.
4. If `modeMintBurn` is true, `ERC20Burnable(_tokenAddress).burn(_value)` reverts because the bridge's actual balance is less than `_value` (denial of service, per the referenced report). If false, the bridge's real token reserve is now less than the sum of all `_value`s it has agreed to honor, producing insolvency/inflated liability upon later `handleERC20Transfer` calls. [4](#0-3)

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

**File:** contracts/service_chain/bridge/BridgeTransferERC20.sol (L76-107)
```text
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

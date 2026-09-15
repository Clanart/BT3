### Title
Bridge fee-on-transfer token bug: `requestERC20Transfer` requests transfer of the declared `_value` instead of the actually-received amount, enabling cross-chain supply inflation - (File: contracts/service_chain/bridge/BridgeTransferERC20.sol)

### Summary
`BridgeTransferERC20.requestERC20Transfer()` pulls tokens via `safeTransferFrom(msg.sender, address(this), _value.add(_feeLimit))` and then unconditionally forwards the caller-declared `_value` (not the amount actually received by the bridge) into `_requestERC20Transfer`, which is what gets minted/unlocked on the counterpart chain. For any ERC20 token that charges a transfer fee (deflationary/fee-on-transfer tokens), the bridge receives less than `_value + _feeLimit`, yet still requests the full, un-discounted `_value` to be credited on the other chain.

### Finding Description [1](#0-0) 

```solidity
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

`_requestERC20Transfer` then pays the configured fee out of the bridge's balance (`_payERC20FeeAndRefundChange`, which itself does `safeTransfer(feeReceiver, fee)` / `safeTransfer(from, feeRefund)` — both further transfers subject to the same fee-on-transfer distortion), optionally burns `_value` in mint-burn mode, and finally emits `RequestValueTransfer` carrying the **caller-supplied `_value`**, unadjusted for any fee actually deducted by the token contract: [2](#0-1) 

This `RequestValueTransfer` event (with the declared `_value`) is what the bridge operators on the other chain observe and act on via `handleERC20Transfer`, which either mints `_value` tokens (mint-burn mode) or transfers `_value` out of its own escrow to `_to`: [3](#0-2) 

Nowhere does the contract measure `balanceOf(address(this))` before/after the `transferFrom` call to determine the actual amount received, unlike the recommended fee-on-transfer mitigation pattern (measure balance delta around the transfer). The contract assumes `transferFrom` moves exactly `_value + _feeLimit` — the same root-cause assumption flagged in the referenced Malt `Bonding._bond()` finding.

### Impact Explanation
- In **mint-burn mode**, if the registered token deducts a transfer fee, the source chain bridge burns/holds less than `_value`, but the destination chain still mints the full declared `_value` to `_to` — this inflates the token's cross-chain circulating supply (value created out of nothing) each time a fee-on-transfer token is bridged.
- In **lock/unlock (escrow) mode**, the source bridge holds less than `_value + _feeLimit` in escrow (fee deducted on ingress, and again on any `safeTransfer` fee-payment/refund inside `_payERC20FeeAndRefundChange`), while the destination bridge still releases the full declared `_value` from its own escrow to `_to`. This can eventually drain the destination-side escrow beyond what was actually deposited, harming other bridge users/liquidity.
- Any unprivileged sender who can call the public `requestERC20Transfer()` (or the public `onERC20Received()` callback) with a registered fee-on-transfer token can trigger this discrepancy — this is a normal, permissionless transaction path, matching the "unprivileged transaction sender" reachability requirement.

Severity is Medium: it requires the bridge operators to register a fee-on-transfer ERC20 for bridging (an operational/admission decision), but once such a token is registered, exploitation requires only an ordinary `requestERC20Transfer` call from any user.

### Likelihood Explanation
Likelihood depends on whether the deployment registers any deflationary/fee-on-transfer token via `RegisterToken`/`onlyRegisteredToken`. The contract contains no validation rejecting such tokens, and no balance-delta accounting to make the flow safe even if such tokens are allowed. Given that ERC20 registration is a governance/admin action but the transfer/request path itself is fully permissionless and reachable via a single transaction from any user holding the token, this is a realistic path once such a token is present in the registry.

### Recommendation
In `requestERC20Transfer` (and `onERC20Received`), measure the bridge's token balance immediately before and after `safeTransferFrom`, and use the delta as the actual amount that arrived, deriving `_value`/`_feeLimit` proportionally from that measured delta instead of trusting the caller-supplied `_value`. Apply the same before/after balance measurement around every subsequent `safeTransfer` in `_payERC20FeeAndRefundChange` so fee payment/refund amounts also reflect actual token movement. Alternatively, explicitly disallow non-standard/fee-on-transfer tokens at `RegisterToken` time (e.g., by verifying a test transfer moves the exact declared amount).

### Proof of Concept
1. Bridge operator registers a fee-on-transfer ERC20 `T` (e.g., charges 1% fee on every `transfer`/`transferFrom`) via `RegisterToken`, and mint-burn mode is enabled with `T.mint`/`T.burn` permission granted to the bridge.
2. User `Alice` holds `100 T` and approves the bridge for `100 T`.
3. Alice calls `requestERC20Transfer(T, Bob, 100, 0, "")`.
   - `safeTransferFrom(Alice, bridge, 100)` actually delivers only `99 T` to the bridge (1% fee burned/redirected by token logic).
   - `_requestERC20Transfer` burns as much of `_value` (100) as the bridge can (or reverts/burns from insufficient balance depending on token semantics), and emits `RequestValueTransfer(..., _value=100, ...)`.
4. On the destination chain, operators call `handleERC20Transfer(..., _value=100, ...)`, which mints exactly `100 T'` to Bob.
5. Net effect: only `99 T` worth of value was actually removed on the source chain, but `100 T'` was minted on the destination chain — a 1-unit supply inflation per bridging operation, repeatable indefinitely by any user holding the fee-on-transfer token.

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

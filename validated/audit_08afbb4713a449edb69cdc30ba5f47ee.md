### Title
Cross-Function Reentrancy via Shared `requestNonce` Counter Bypasses `nonReentrant` Guard - (File: `contracts/service_chain/bridge/BridgeTransferKLAY.sol`, `contracts/service_chain/bridge/BridgeFee.sol`, `contracts/service_chain/bridge/BridgeTransferERC20.sol`)

### Summary
`BridgeTransferKLAY._requestKLAYTransfer` is protected by `nonReentrant`, but it performs a raw, attacker-controllable external call (`msg.sender.call.value(...)`) via `BridgeFee._payKLAYFeeAndRefundChange` *before* the shared state variable `requestNonce` is incremented. Because the sibling contract `BridgeTransferERC20`, which shares the same `requestNonce` storage slot through the common parent `BridgeTransfer`, exposes `requestERC20Transfer`/`_requestERC20Transfer` **without** any `nonReentrant` protection, an attacker contract can reenter the bridge through the ERC20 path during the KLAY fee-refund callback and manipulate the shared nonce/state ordering — exactly the class of bug described in the external report ("`nonReentrant` only prevents reentrancy within the same function/guard, not through other externally reachable functions").

### Finding Description
Any unprivileged transaction sender can call the public entry points `requestKLAYTransfer` or the payable fallback `()` on `BridgeTransferKLAY`: [1](#0-0) 

Both route into the internal `_requestKLAYTransfer`, guarded by `nonReentrant`: [2](#0-1) 

Inside it, `_payKLAYFeeAndRefundChange` performs a low-level call directly to `msg.sender` (attacker-controlled) to refund excess fee, *before* `requestNonce` is read/incremented for the emitted `RequestValueTransfer` event: [3](#0-2) 

`ReentrancyGuard`'s `_guardCounter` mechanism only blocks reentry into functions carrying the `nonReentrant` modifier within the same guard instance: [4](#0-3) 

However, `BridgeTransferERC20` — which inherits the same `BridgeTransfer` base and therefore shares the same `requestNonce` storage — does **not** inherit `ReentrancyGuard` and its `requestERC20Transfer`/`_requestERC20Transfer` carry no reentrancy protection at all: [5](#0-4) [6](#0-5) 

`requestNonce` is declared once in the common parent and incremented by both token-type request paths: [7](#0-6) 

During the callback triggered by `msg.sender.call.value(feeRefund)("")` in `_payKLAYFeeAndRefundChange`, an attacker's fallback executes with full control while the outer `_requestKLAYTransfer` call is still "in-flight" (its own `nonReentrant` slot is locked, but this only blocks re-entry into functions carrying that modifier). The attacker's fallback can call the unguarded `requestERC20Transfer`, which reads the *same* `requestNonce` value the pending KLAY call has not yet incremented, and increments it independently. This produces two `RequestValueTransfer` events (one KLAY, one ERC20) emitted with an identical `requestNonce` value, or interleaved nonce advancement that breaks the strict FIFO ordering the counterpart-chain operator relies on (`_lowerHandleNonceCheck`, `_updateHandleNonce`, `lowerHandleNonce`/`upperHandleNonce` windowing) as seen in `BridgeTransfer._updateHandleNonce`: [8](#0-7) 

This is a direct analog of the reported bug class: the `nonReentrant` modifier gives a false sense of safety because a raw external call to `msg.sender` is made mid-function, and the invariant it protects (monotonic, unique `requestNonce` per bridge request) is shared mutable state reachable through a second, unguarded public function.

### Impact Explanation
Duplicate or out-of-order `requestNonce` values break the ordering guarantee the counterpart-chain operator/relayer depends on to correctly correlate and settle cross-chain value transfers. This can cause: value-transfer requests to be dropped or double-processed on the counterpart chain, state divergence between honest bridge operators voting on the same nonce, or an operator settling the wrong token type/value for a given nonce — resulting in unauthorized value movement or loss of bridged funds. This is High severity because it is triggerable by any unprivileged account that funds a bridge transfer, and it corrupts a shared accounting primitive used for cross-chain settlement.

### Likelihood Explanation
Likelihood is Medium-High: the attacker only needs to be a contract (not an EOA) calling `requestKLAYTransfer` (or the payable fallback) with `msg.sender` set to itself so the fee-refund call lands on its own fallback, from which it calls the unguarded `requestERC20Transfer`. No special privileges, validator/proposer control, or timing races beyond standard call-stack reentrancy are required — only that `feeOfKLAY` fee-limit logic yields a nonzero refund (`feeRefund > 0`), which the attacker fully controls by choosing `_feeLimit` from its own transaction.

### Recommendation
- Apply a single, contract-wide `nonReentrant` guard covering *all* state-mutating request paths (`_requestKLAYTransfer`, `_requestERC20Transfer`, `_requestERC721Transfer`, and their public wrappers/fallback), not just the KLAY path.
- Follow strict checks-effects-interactions ordering: increment `requestNonce` and emit `RequestValueTransfer` *before* making any external call (fee refund transfer), rather than after.
- Avoid raw `.call.value()` refunds to arbitrary `msg.sender`; prefer pull-based refunds or OpenZeppelin's `Address.sendValue` with reentrancy protection applied at the shared-state boundary rather than per-token-type contract.

### Proof of Concept
1. Attacker deploys a contract `Attacker` with a `fallback()`/`receive()` that, when invoked, calls `bridge.requestERC20Transfer(token, to, value, feeLimit, extraData)` (assuming `Attacker` pre-approved token to the bridge).
2. `Attacker` calls `bridge.requestKLAYTransfer(to, value, extraData)` with `msg.value > _value` such that `feeOfKLAY > 0` and `feeRefund = _feeLimit - fee > 0`.
3. Execution enters `_requestKLAYTransfer` (nonReentrant lock acquired) → `_payKLAYFeeAndRefundChange` → `msg.sender.call.value(feeRefund)("")` transfers control to `Attacker.fallback()`.
4. Inside the fallback, `Attacker` calls `requestERC20Transfer` (no reentrancy guard), which reads the current (not-yet-incremented) `requestNonce`, emits `RequestValueTransfer` for ERC20 with that nonce, and increments `requestNonce`.
5. Control returns to `_requestKLAYTransfer`, which itself emits `RequestValueTransfer` for KLAY using the nonce value it captured *before* the reentrant call (now stale/duplicated) and increments `requestNonce` again.
6. Result: two distinct value-transfer requests (KLAY and ERC20) are emitted referencing overlapping/duplicate nonce values, corrupting the ordered nonce sequence the counterpart bridge operator uses for `_voteValueTransfer`/`_updateHandleNonce`, which can be leveraged to desynchronize or manipulate settlement on the counterpart chain.

### Citations

**File:** contracts/service_chain/bridge/BridgeTransferKLAY.sol (L102-135)
```text
    // _requestKLAYTransfer requests transfer KLAY to _to on relative chain.
    function _requestKLAYTransfer(address _to, uint256 _feeLimit,  bytes memory _extraData)
        internal
        unlockedKLAY
        nonReentrant
    {
        require(isRunning, "stopped bridge");
        require(msg.value > _feeLimit, "insufficient amount");

        uint256 fee = _payKLAYFeeAndRefundChange(_feeLimit);

        emit RequestValueTransfer(
            TokenType.KLAY,
            msg.sender,
            _to,
            address(0),
            msg.value.sub(_feeLimit),
            requestNonce,
            fee,
            _extraData
        );
        requestNonce++;
    }

    // () requests transfer KLAY to msg.sender address on relative chain.
    function () external payable {
        _requestKLAYTransfer(msg.sender, feeOfKLAY, new bytes(0));
    }

    // requestKLAYTransfer requests transfer KLAY to _to on relative chain.
    function requestKLAYTransfer(address _to, uint256 _value, bytes calldata _extraData) external payable {
        uint256 feeLimit = msg.value.sub(_value);
        _requestKLAYTransfer(_to, feeLimit, _extraData);
    }
```

**File:** contracts/service_chain/bridge/BridgeFee.sol (L43-66)
```text
    function _payKLAYFeeAndRefundChange(uint256 _feeLimit) internal returns(uint256) {
        uint256 fee = feeOfKLAY;

        if (feeReceiver != address(0) && fee > 0) {
            require(_feeLimit >= fee, "insufficient feeLimit");

            (bool ok, ) = feeReceiver.call.value(fee)("");
            require(ok, "transfer fee failed");

            uint256 feeRefund = _feeLimit.sub(fee);
            if (feeRefund > 0) {
                (bool ok, ) = msg.sender.call.value(feeRefund)("");
                require(ok, "refund fee failed");
            }

            return fee;
        }

        if (_feeLimit > 0) {
            (bool ok, ) = msg.sender.call.value(_feeLimit)("");
            require(ok, "refund fee failed");
        }
        return 0;
    }
```

**File:** contracts/libs/openzeppelin-contracts-v2/contracts/utils/ReentrancyGuard.sol (L15-37)
```text
contract ReentrancyGuard {
    /// @dev counter to allow mutex lock with only one SSTORE operation
    uint256 private _guardCounter;

    constructor () internal {
        // The counter starts at one to prevent changing it from zero to a non-zero
        // value, which is a more expensive operation.
        _guardCounter = 1;
    }

    /**
     * @dev Prevents a contract from calling itself, directly or indirectly.
     * Calling a `nonReentrant` function from another `nonReentrant`
     * function is not supported. It is possible to prevent this from happening
     * by making the `nonReentrant` function external, and make it call a
     * `private` function that does the actual work.
     */
    modifier nonReentrant() {
        _guardCounter += 1;
        uint256 localCounter = _guardCounter;
        _;
        require(localCounter == _guardCounter, "ReentrancyGuard: reentrant call");
    }
```

**File:** contracts/service_chain/bridge/BridgeTransferERC20.sol (L76-108)
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

**File:** contracts/service_chain/bridge/BridgeTransfer.sol (L26-34)
```text
contract BridgeTransfer is BridgeHandledRequests, BridgeFee, BridgeOperator {
    bool public modeMintBurn = false;
    bool public isRunning = true;

    uint64 public requestNonce; // the number of value transfer request that this contract received.
    uint64 public lowerHandleNonce; // a minimum nonce of a value transfer request that will be handled.
    uint64 public upperHandleNonce; // a maximum nonce of the counterpart bridge's value transfer request that is handled.
    uint64 public recoveryBlockNumber = 1; // the block number that recovery start to filter log from.
    mapping(uint64 => uint64) public handleNoncesToBlockNums;  // <request nonce> => <request blockNum>
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

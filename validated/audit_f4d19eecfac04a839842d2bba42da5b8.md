## Analog Found: Frozen Host State Permanently Strands Escrowed Relayer Fees With No Delivery *or* Timeout-Refund Path

### Title
Setting `FrozenStatus.Incoming` (or `All`) permanently locks escrowed relayer fees for in-flight requests, because both delivery and timeout-refund paths are gated by the same freeze check with no rescue mechanism — ([File: evm/src/core/HandlerV2.sol](), [File: evm/src/core/EvmHost.sol]())

### Summary
The external report's core broken invariant is: a user's funds sit in an escrow state (borrower's stables in `PositionController`) whose only two exits — normal claim, or "top-up-and-withdraw" — both become unreachable once the system transitions into a protective/decommissioning state, and no rescue function exists to free the funds. The Hyperbridge analog is `EvmHost`'s `FrozenStatus` mechanism: a dispatched request's relayer fee is escrowed in `_requestCommitments[commitment]` [1](#0-0) , and it can only leave escrow via `handlePostRequests` (delivery, pays relayer) or `handlePostRequestTimeouts`/`handleGetRequestTimeouts` (refund to payer). Both of these exits are gated by the identical `notFrozen(host)` modifier that reverts when `frozen == FrozenStatus.Incoming || FrozenStatus.All` [2](#0-1) .

### Finding Description
`handlePostRequests` and `handlePostRequestTimeouts` share the exact same `notFrozen` gate: [3](#0-2) [4](#0-3) 

If the host is put into `FrozenStatus.Incoming` (or `All`) — via `setFrozenState`, callable by the admin or the handler itself [5](#0-4)  — then for *every request already dispatched with an escrowed fee* (`_requestCommitments[commitment]` populated by `dispatch()`/`fundRequest()` [1](#0-0) ):

- `handlePostRequests` reverts with `HostFrozen()` — delivery is impossible, so the relayer can never collect the fee and the request can never be marked delivered.
- `handlePostRequestTimeouts` / `handleGetRequestTimeouts` also revert with `HostFrozen()` — even after the `timeout_timestamp` has elapsed, the non-membership proof cannot be submitted, so `dispatchTimeOut` (which refunds `meta.fee` to `meta.sender`) can never run [6](#0-5) .

There is no third path. `fundRequest` (which the docs explicitly frame as *"provided for use only on pending requests, such that when they timeout, the user can recover the entire relayer fee"* [7](#0-6) ) cannot rescue the situation — it is gated by `EvmHost`'s own `notFrozen` modifier (`Outgoing || All`) [8](#0-7) , so it still works during `Incoming`-only freezes, but adding more fee is meaningless when both exits from escrow remain blocked — it merely puts more capital at risk, mirroring the original report's dead-end "add more collateral" workaround.

This is structurally identical to the reported bug: an escrow (stablecoins / relayer fee) has exactly two designed exits (claim / add-collateral-then-withdraw vs. deliver / timeout-refund), and a single system-wide state transition (recovery mode / `FrozenStatus`) simultaneously disables both exits with no admin or user rescue call to unwind the position.

### Impact Explanation
Any payer who dispatched a POST/GET request with a non-zero fee before a freeze event has that fee permanently stuck in the `EvmHost` contract for the entire duration of the freeze — which per the docs is not guaranteed to ever be lifted for consensus-fault-triggered freezes ("frozen consensus clients cannot be unfrozen" is the stated Substrate-side policy for the same conceptual state [9](#0-8) ). Even for an admin-lifted freeze, users have zero self-service recourse in the interim — this is a loss/lock-of-funds condition reachable through the protocol's own designed state machine, not a compromised-peer assumption.

### Likelihood Explanation
`setFrozenState` is a normal, expected operational lever (invoked by admin for incident response, or by the handler in response to detected byzantine behavior per the consensus-proofs documentation on fraud-proof freezing). Any request in flight at the moment of freezing is affected — this is not a rare edge case but the default outcome for every pending request whenever `Incoming`/`All` freeze is applied, which is precisely the scenario the freeze mechanism is designed to be used in (byzantine attacks, chain forks, emergency pauses).

### Recommendation
Add a rescue/refund path that is *not* gated by `notFrozen`, so payers can recover escrowed fees for requests that timed out (or were never delivered) while the host is frozen — analogous to the external report's recommendation to let escrowed users act during the decommissioning window. E.g., permit `dispatchTimeOut`-style refunds (or a dedicated `refundExpiredRequest`) to run under `FrozenStatus.Incoming`/`All` once `timeout_timestamp` has elapsed, decoupling "can we trust new inbound delivery/timeout proofs" from "can a payer reclaim their own already-expired escrowed fee."

### Proof of Concept
1. User calls `EvmHost.dispatch(DispatchPost{...})` with `fee > 0`; `_requestCommitments[commitment] = FeeMetadata{sender: user, fee: X}` is set [10](#0-9) .
2. Before delivery, admin (or handler, in response to a detected consensus fault) calls `setFrozenState(FrozenStatus.Incoming)` [5](#0-4) .
3. `timeout_timestamp` elapses on the request.
4. Relayer attempts `handlePostRequests` → reverts `HostFrozen()` [11](#0-10) [3](#0-2) .
5. User/relayer attempts `handlePostRequestTimeouts` to trigger the refund → also reverts `HostFrozen()` [4](#0-3) .
6. `fee` remains locked in `EvmHost` indefinitely for as long as the freeze persists, with no function available to the payer to reclaim it.

### Citations

**File:** evm/src/core/EvmHost.sol (L354-357)
```text
    modifier notFrozen() {
        if (_frozen == FrozenStatus.Outgoing || _frozen == FrozenStatus.All) revert FrozenHost();
        _;
    }
```

**File:** evm/src/core/EvmHost.sol (L746-753)
```text
    function setFrozenState(FrozenStatus newState) external {
        address caller = _msgSender();
        if (caller != _hostParams.admin && caller != _hostParams.handler) revert UnauthorizedAction();

        _frozen = newState;

        emit HostFrozen({status: newState});
    }
```

**File:** evm/src/core/EvmHost.sol (L885-900)
```text
    function dispatchTimeOut(
        PostRequestTimeout memory timeout,
        FeeMetadata memory meta,
        bytes32 commitment
    ) external restrict(_hostParams.handler) {
        // replay protection
        delete _requestCommitments[commitment];
        (bool success,) = _bytesToAddress(timeout.request.from)
            .call(abi.encodeWithSelector(IApp.onPostRequestTimeout.selector, timeout));

        if (!success) {
            // so that it can be retried
            _requestCommitments[commitment] = meta;
            return;
        }

```

**File:** evm/src/core/EvmHost.sol (L999-1001)
```text
        // make the commitment
        commitment = request.hash();
        _requestCommitments[commitment] = FeeMetadata({sender: _msgSender(), fee: get.fee});
```

**File:** evm/src/core/EvmHost.sol (L1015-1030)
```text
    /**
     * @dev Increase the relayer fee for a previously dispatched request.
     * This is provided for use only on pending requests, such that when they timeout,
     * the user can recover the entire relayer fee.
     *
     * @notice Payment can be made with either the native token or the feeToken.
     * If native tokens are supplied, it will perform a swap under the hood using the local uniswap router.
     * Will revert if enough native tokens are not provided.
     *
     * If no native tokens are provided then it will try to collect payment from the calling contract in
     * the feeToken.
     *
     * If called on an already delivered request, these funds will be seen as a donation to the hyperbridge protocol.
     * @param commitment - The request commitment
     * @param amount - The amount provided in `feeToken()`
     */
```

**File:** evm/src/core/EvmHost.sol (L1031-1051)
```text
    function fundRequest(bytes32 commitment, uint256 amount) external payable notFrozen {
        if (msg.value > 0) {
            address[] memory path = new address[](2);
            address uniswapV2 = _hostParams.uniswapV2;
            path[0] = IUniswapV2Router02(uniswapV2).WETH();
            path[1] = feeToken();
            IUniswapV2Router02(uniswapV2).swapETHForExactTokens{value: msg.value}(
                amount, path, address(this), block.timestamp
            );
        } else {
            IERC20(feeToken()).safeTransferFrom(_msgSender(), address(this), amount);
        }

        FeeMetadata memory metadata = _requestCommitments[commitment];
        if (metadata.sender == address(0)) revert UnknownRequest();

        metadata.fee += amount;
        _requestCommitments[commitment] = metadata;

        emit RequestFunded({commitment: commitment, newFee: metadata.fee});
    }
```

**File:** evm/src/core/HandlerV2.sol (L106-112)
```text
     * @dev Checks if the host permits incoming datagrams
     */
    modifier notFrozen(IHost host) {
        FrozenStatus state = host.frozen();
        if (state == FrozenStatus.Incoming || state == FrozenStatus.All) revert HostFrozen();
        _;
    }
```

**File:** evm/src/core/HandlerV2.sol (L181-181)
```text
    function handlePostRequests(IHost host, PostRequestMessage calldata request) external notFrozen(host) {
```

**File:** evm/src/core/HandlerV2.sol (L254-257)
```text
    function handlePostRequestTimeouts(IHost host, PostRequestTimeoutMessage calldata message)
        external
        notFrozen(host)
    {
```

**File:** docs/content/protocol/ismp/consensus.mdx (L197-197)
```text
The `freeze_client` method is used to prove the existence of a consensus fault to an onchain consensus client. This message will be sent by offchain parties, colloquially known as _fishermen_ when they detect the existence of two conflicting views of the network backed by consensus proofs. This may arise from double signing or eclipse attacks. The consensus client after successfully verifying the validity of the conflicting views of the network will go into a frozen state. In this state it can no longer process new consensus messages as well as new requests & responses. Frozen consensus clients cannot be unfrozen and a new consensus client must be initialized through the `create_client` method instead.
```

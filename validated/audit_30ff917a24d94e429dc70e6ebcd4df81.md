### Title
GET request can be both answered and timed-out, causing duplicate `onGetResponse`/`onGetTimeout` delivery to the receiving app — (File: `evm/src/core/EvmHost.sol`)

### Summary
The GTL bug reduces to: a code path that moves real state (posted collateral) fails to update the bookkeeping structure (`_subaccounts`) that a *different* code path (`totalAssets`) relies on to decide correctness, letting the system treat "moved" state as "not yet happened." The same class of bug exists in `EvmHost.sol`'s GET-request lifecycle: the success path of `dispatchIncoming(GetResponse)` never clears `_requestCommitments[commitment]`, so the `handleGetRequestTimeouts` path (which gates on that very mapping) can still treat an already-answered GET request as pending and dispatch a timeout to the app module after the response has already been delivered.

### Finding Description
`EvmHost.dispatchIncoming(GetResponse memory response, address relayer)` [1](#0-0)  sets `_responseReceipts[commitment]` for replay protection, invokes `onGetResponse` on the destination app, and on success pays the relayer fee straight out of `_requestCommitments[commitment].fee` — but it never deletes `_requestCommitments[commitment]`.

Compare this with the sibling function `dispatchTimeOut(GetRequestTimeout, ...)`, which explicitly does `delete _requestCommitments[commitment]` as its stated replay protection: [2](#0-1) .

`HandlerV2.handleGetRequestTimeouts` gates dispatch of a GET timeout purely on:
1. `meta.sender == address(0)` (read from `host.requestCommitments(commitment)`) — i.e. whether `_requestCommitments[commitment]` still exists, and
2. a non-membership proof that `ResponseReceipts[commitment]` does not exist on **Hyperbridge's** state trie at the specific proven `message.height`. [3](#0-2) 

Nothing in this timeout path checks the EVM host's own `_responseReceipts[commitment]` (the mapping that `dispatchIncoming(GetResponse)` sets when the response is actually delivered locally). Because `dispatchIncoming(GetResponse)` never clears `_requestCommitments[commitment]` on success, the request commitment remains "known" (`meta.sender != address(0)`) even after being fully answered. If a relayer (or anyone submitting a valid proof — this call is permissionless) can produce a valid non-membership proof at some already-finalized `message.height` on Hyperbridge that predates when the response was actually recorded there, `handleGetRequestTimeouts` will accept it and call `host.dispatchTimeOut(...)`, which in turn calls `onGetTimeout` on the very same app module that already received `onGetResponse` for that request.

The corrupted/missing value is `_requestCommitments[commitment]` in `EvmHost.sol` — it should be deleted (mirroring `dispatchTimeOut`'s own replay-protection comment) once a response has been successfully delivered, exactly as the GTL bug's fix would require adding the subaccount to `_subaccounts` on every path that moves collateral, not only on the fill path.

### Impact Explanation
An IApp module built on the ISMP GET/response/timeout contract (per the protocol's "one-time receipt handling" guarantee — a request must terminate in exactly one of `onGetResponse` or `onGetTimeout`) can receive both callbacks for the same GET request. Depending on the module's logic (e.g. releasing escrow on response and refunding on timeout, as intent/vault-style apps in this same repo do), this enables double-settlement / double-refund style fund loss or logic corruption — exactly the "replay/double-claim/double-settlement" impact called out as in-scope. The bug requires no malicious relayer, prover, or admin: any permissionless caller who can assemble the two message batches with genuine, validly-signed consensus/state proofs (using a stale-but-legitimate `message.height`) can trigger it.

### Likelihood Explanation
This requires a get request whose response has already landed, and then a subsequent timeout message built against an earlier already-finalized Hyperbridge height at which the response was not yet committed — a timing condition that can arise naturally with concurrent relayers submitting responses and timeouts, or can be deliberately engineered by any unprivileged actor holding both proofs. No governance, node compromise, or leaked key is needed, only reachable via the two already-public entrypoints `handleGetResponses`/`handleGetRequestTimeouts`.

### Recommendation
In `EvmHost.dispatchIncoming(GetResponse memory response, address relayer)`, delete `_requestCommitments[commitment]` on the success branch (mirroring `dispatchTimeOut`), and/or have `handleGetRequestTimeouts` additionally check that `host.responseReceipts(commitment).relayer == address(0)` before dispatching a timeout, so a GET request that has already been answered locally can never also be timed out.

### Proof of Concept
1. User dispatches a `GetRequest` from chain A; `_requestCommitments[commitment]` is set with `fee > 0`.
2. A relayer delivers the response via `handleGetResponses` → `dispatchIncoming(GetResponse, relayer)`: `_responseReceipts[commitment]` is set, `onGetResponse` succeeds, relayer fee is paid, but `_requestCommitments[commitment]` is left intact (`meta.sender != address(0)`).
3. Separately, a relayer (or the same one) obtains a valid consensus/state proof for an earlier Hyperbridge height `H` — one that was finalized before the response was recorded on Hyperbridge — together with a non-membership proof of `ResponseReceipts[commitment]` at height `H`.
4. That relayer calls `handleGetRequestTimeouts` with this proof. The checks pass: `meta.sender != address(0)` (still true, since never deleted), and the non-membership proof at height `H` is valid (true, because at that height the response indeed hadn't landed on Hyperbridge yet).
5. `host.dispatchTimeOut(...)` executes, calling `onGetTimeout` on the same destination module that already received `onGetResponse` for the identical request — a duplicate, protocol-violating delivery that a well-formed app module is not expected to defend against on its own.

### Citations

**File:** evm/src/core/EvmHost.sol (L824-847)
```text
    function dispatchIncoming(GetResponse memory response, address relayer) external restrict(_hostParams.handler) {
        // replay protection
        bytes32 commitment = response.request.hash();
        _responseReceipts[commitment] = ResponseReceipt({
            relayer: relayer,
            responseCommitment: response.hash()
        });

        (bool success,) = _bytesToAddress(response.request.from)
            .call(abi.encodeWithSelector(IApp.onGetResponse.selector, IncomingGetResponse(response, relayer)));

        if (!success) {
            // so that it can be retried
            delete _responseReceipts[commitment];
            return;
        }

        // reward the relayer fee
        uint256 fee = _requestCommitments[commitment].fee;
        if (fee != 0) {
            IERC20(feeToken()).safeTransfer(relayer, fee);
        }
        emit GetRequestHandled({commitment: commitment, relayer: relayer});
    }
```

**File:** evm/src/core/EvmHost.sol (L856-878)
```text
    function dispatchTimeOut(
        GetRequestTimeout memory timeout,
        FeeMetadata memory meta,
        bytes32 commitment
    ) external restrict(_hostParams.handler) {
        // replay protection
        delete _requestCommitments[commitment];
        (bool success,) = _bytesToAddress(timeout.request.from)
            .call(abi.encodeWithSelector(IApp.onGetTimeout.selector, timeout));

        if (!success) {
            // so that it can be retried
            _requestCommitments[commitment] = meta;
            return;
        }

        if (meta.fee != 0) {
            // refund relayer fee
            IERC20(feeToken()).safeTransfer(meta.sender, meta.fee);
        }
        emit GetRequestTimeoutHandled({commitment: commitment, dest: string(timeout.request.dest)});
    }

```

**File:** evm/src/core/HandlerV2.sol (L293-321)
```text
    function handleGetRequestTimeouts(IHost host, GetTimeoutMessage calldata message) external notFrozen(host) {
        uint256 delay = block.timestamp - host.stateMachineCommitmentUpdateTime(message.height);
        uint256 challengePeriod = host.challengePeriod();
        if (challengePeriod != 0 && challengePeriod > delay) revert ChallengePeriodNotElapsed();

        // fetch the state commitment
        StateCommitment memory state = host.stateMachineCommitment(message.height);
        if (state.stateRoot == bytes32(0)) revert StateCommitmentNotFound();
        uint256 timeoutsLength = message.timeouts.length;

        for (uint256 i = 0; i < timeoutsLength; ++i) {
            GetRequest memory request = message.timeouts[i];
            // timed-out?
            if (request.timeout() > state.timestamp) revert MessageNotTimedOut();

            bytes32 commitment = request.hash();
            FeeMetadata memory meta = host.requestCommitments(commitment);
            if (meta.sender == address(0)) revert UnknownMessage();

            bytes[] memory keys = new bytes[](1);
            keys[0] = bytes.concat(RESPONSE_RECEIPTS_STORAGE_PREFIX, commitment);

            // verify state trie non-membership proofs
            PolkadotTrie.StorageValue memory entry = PolkadotTrie.VerifyProof(state.stateRoot, message.proof, keys)[0];
            if (entry.value.length != 0) revert InvalidProof();

            host.dispatchTimeOut(GetRequestTimeout(request, _msgSender()), meta, commitment);
        }
    }
```

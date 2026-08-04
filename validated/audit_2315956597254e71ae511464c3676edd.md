### Title
Unbounded `Order.inputs` array lets a solver-luring order permanently strand cross-chain escrow via an undeliverable oversized settlement message - ([File: evm/src/apps/intentsv2/ExtrinsicIntents.sol])

### Summary
`IntentGatewayV2.placeOrder` only checks that `order.inputs.length == 0` is rejected — there is no upper bound on the number of distinct input tokens an order can specify. [1](#0-0) 
For a cross-chain order, `_fillCrossChain` embeds the *entire* `order.inputs` array (unbounded) into the `WithdrawalRequest.tokens` field of the `RedeemEscrow` body dispatched back to the source chain. [2](#0-1) 
This settlement message must later be delivered through `HandlerV2.handlePostRequests`, which decodes the whole calldata request (including this large `body`) and verifies it against an MMR proof in a single Ethereum transaction, bounded by the block gas limit. [3](#0-2) 
Once delivered, `withdraw()`/`_withdraw()` loops over every entry in `body.tokens`, performing an external transfer per token. [4](#0-3) 
This is the same structural flaw as the tBTC finding: an unbounded, attacker-controlled payload is required to be proven/executed as one atomic unit on a gas-limited EVM, so a legitimately-earned settlement can become permanently unprovable/unexecutable.

### Finding Description
An unprivileged user places a cross-chain order with a very large number of distinct `TokenInfo` entries in `order.inputs` (each pointing at a cheap or attacker-deployed ERC-20, minimal escrowed amount, no duplicate-token restriction is bypassed since addresses differ). `placeOrder` accepts this with no cap. [5](#0-4) 

An honest solver, unaware of the danger, fills the order on the destination chain by delivering the required output assets to the beneficiary via `fillOrder`/`_fillCrossChain`. [6](#0-5) 
Immediately after paying out, the contract dispatches a `RedeemEscrow` `DispatchPost` whose `body` carries `order.inputs` verbatim — with attacker-chosen length. [2](#0-1) 

For the solver to be reimbursed from source-chain escrow, this exact request must be delivered through `HandlerV2.handlePostRequests`, decoded as calldata, MMR-proof-verified, and then dispatched to `onAccept` → `withdraw()`, which iterates `body.tokens.length` times doing a `safeTransfer`/native `call` per token. [3](#0-2) [4](#0-3) 

If the attacker sizes `order.inputs` so that the resulting calldata + per-token external-call gas exceeds the destination (source-of-escrow) chain's block gas limit — or simply approaches/ exceeds the practical gas ceiling any relayer/handler transaction can spend — the message becomes structurally undeliverable: no transaction can ever include it. There is no cap enforced at `placeOrder`, at `DispatchPost`, or at the handler layer on the number of tokens/body size relative to gas cost, so nothing stops the order from being crafted this way, and nothing lets the protocol split or reduce the payload after the fact (the commitment is fixed by `keccak256(abi.encode(order))` at placement time). [7](#0-6) 

The solver already delivered real value on the destination chain before dispatch; the only path to reclaim the corresponding source-chain escrow is this one oversized, permanently-stuck `RedeemEscrow` message — exactly analogous to the tBTC report's core broken invariant: a legitimate value-transfer proof can be made unprovable by exceeding the chain's execution/gas ceiling, causing irreversible loss for the honest party (here, the solver) while the funds sit frozen in the source `_orders[commitment]` escrow forever.

### Impact Explanation
This directly matches the bounty's "stealing or loss of funds" and "transaction manipulation" categories: an unprivileged order-placer can craft an order whose settlement message is guaranteed to never fit within any deliverable transaction, causing:
- Permanent loss of the solver's already-delivered destination-chain assets (solver pays real tokens, gets nothing back).
- Permanent lock of the user's/attacker's own source-chain escrow (less impactful to the attacker, since it was their own capital, but the *solver's* loss is the exploitable value — a griefing/theft-by-design vector against solvers, or a way to sabotage a competitor solver into an unrecoverable loss).
No malicious relayer, prover, or governance actor is required — only an unprivileged order placer and a solver that fills it normally.

### Likelihood Explanation
Likelihood is bounded by economic incentive: a solver would normally simulate/estimate gas before filling (as the SDK's `estimateGas` helpers suggest is standard practice), so an obviously-oversized order might be avoided by solvers doing due diligence. [8](#0-7) 
However, the contract itself provides **no on-chain protection** — no maximum input count, no body-size cap in `DispatchPost`/`placeOrder`, and no gas-cost precheck — so the safety of the protocol currently depends entirely on off-chain solver diligence, not on any enforced invariant. A less careful/automated solver (or one lured by an attractive but crafted order) remains fully exposed.

### Recommendation
- Enforce a maximum `order.inputs.length` (and/or a maximum ABI-encoded `WithdrawalRequest` body size) at `placeOrder` time on both `IntentGatewayV2.sol` and its Tron/EVM variants, sized conservatively against realistic destination-chain block gas limits.
- Alternatively/additionally, cap the total per-token external-call gas budget in `withdraw()`/`_withdraw()` and structure settlement so escrow release can be split across multiple messages/transactions rather than requiring one atomic all-or-nothing payload.
- Add integration tests that place an order at the token-count boundary and assert that `handlePostRequests`/`onAccept` gas usage stays well under configured chain gas limits for the maximum allowed input count.

### Proof of Concept
1. Attacker calls `IntentGatewayV2.placeOrder` on the source chain with `order.inputs` containing N (e.g., 500+) distinct cheap ERC-20 tokens, each with a minimal `amount`, and a modest single-token `output` that is attractive to solvers. No length check rejects this.
2. A solver calls `fillOrder`/`_fillCrossChain` on the destination chain, delivering the required output tokens to the beneficiary. [6](#0-5) 
3. `_fillCrossChain` dispatches the `RedeemEscrow` `DispatchPost` with `body` containing all N `TokenInfo` entries. [2](#0-1) 
4. When a relayer attempts to submit this via `HandlerV2.handlePostRequests` on the source chain, the calldata size and/or the gas required by `withdraw()`'s per-token loop (N `safeTransfer`/native calls) exceeds the source chain's block gas limit — the transaction cannot be mined regardless of gas price. [3](#0-2) [4](#0-3) 
5. The solver's destination-chain payout is final and irreversible, while the source-chain escrow release (and the solver's reimbursement) can never be delivered — funds are permanently stuck, and the solver has suffered an unrecoverable loss.

### Citations

**File:** evm/src/apps/IntentGatewayV2.sol (L162-196)
```text
    function placeOrder(Order memory order, bytes32 graffiti) public payable nonReentrant {
        if (order.inputs.length == 0) revert InvalidInput();

        // Reject duplicate output tokens 
        uint256 outputsLen_ = order.output.assets.length;
        for (uint256 i; i < outputsLen_;) {
            bytes32 token = order.output.assets[i].token;
            assembly ("memory-safe") {
                if tload(token) {
                    mstore(0, 0xb4fa3fb3) // InvalidInput.selector
                    revert(0x1c, 0x04)
                }
                tstore(token, 1)
            }
            unchecked {
                ++i;
            }
        }
        // Clean up transient storage so repeated placeOrder calls in the same tx don't false-positive.
        for (uint256 i; i < outputsLen_;) {
            bytes32 token = order.output.assets[i].token;
            assembly ("memory-safe") {
                tstore(token, 0)
            }
            unchecked {
                ++i;
            }
        }

        address hostAddr = host();
        order.user = bytes32(uint256(uint160(msg.sender)));
        order.source = IDispatcher(hostAddr).host();
        order.nonce = _nonce++;

        uint256 inputsLen = order.inputs.length;
```

**File:** evm/src/apps/IntentGatewayV2.sol (L326-331)
```text
            order.inputs = reducedInputs;
            commitment = keccak256(abi.encode(order));
        } else {
            reducedInputs = order.inputs;
            commitment = keccak256(abi.encode(order));
        }
```

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L89-136)
```text
    function _fillCrossChain(Order calldata order, FillOptions calldata options, bytes32 commitment) internal {
        uint256 outputsLen = order.output.assets.length;

        _filled[commitment] = msg.sender;

        uint256 msgValue = msg.value;
        address beneficiary = address(uint160(uint256(order.output.beneficiary)));
        TokenInfo[] memory outputFills = new TokenInfo[](outputsLen);

        for (uint256 i; i < outputsLen; i++) {
            bytes32 outputToken = order.output.assets[i].token;
            if (options.outputs[i].token != outputToken) revert InvalidInput();

            address token = address(uint160(uint256(outputToken)));
            uint256 totalRequired = order.output.assets[i].amount;
            uint256 solverAmount = options.outputs[i].amount;

            if (solverAmount < totalRequired) revert InvalidInput();

            uint256 dust = solverAmount - totalRequired;
            uint256 beneficiaryShare = 0;
            uint256 protocolShare = 0;

            if (dust > 0) {
                if (order.output.call.length > 0) {
                    protocolShare = dust;
                } else {
                    protocolShare = (dust * _params.surplusShareBps) / 10_000;
                    beneficiaryShare = dust - protocolShare;
                }
            }

            if (token == address(0)) {
                if (msgValue < solverAmount) revert InsufficientNativeToken();
                uint256 beneficiaryTotal = totalRequired + beneficiaryShare;
                (bool sent,) = beneficiary.call{value: beneficiaryTotal}("");
                if (!sent) revert InsufficientNativeToken();
                msgValue -= (beneficiaryTotal + protocolShare);
            } else {
                IERC20(token).safeTransferFrom(msg.sender, beneficiary, totalRequired + beneficiaryShare);
                if (protocolShare > 0) {
                    IERC20(token).safeTransferFrom(msg.sender, address(this), protocolShare);
                }
            }
            if (protocolShare > 0) emit DustCollected(token, protocolShare);
            outputFills[i] = TokenInfo({token: outputToken, amount: totalRequired});
        }

```

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L139-151)
```text
        address hostAddr = host();
        bytes memory body = bytes.concat(
            bytes1(uint8(RequestKind.RedeemEscrow)),
            abi.encode(
                WithdrawalRequest({
                    commitment: commitment, tokens: order.inputs, beneficiary: bytes32(uint256(uint160(msg.sender)))
                })
            )
        );
        DispatchPost memory request = DispatchPost({
            dest: order.source,
            to: abi.encodePacked(_instance(order.source)),
            body: body,
```

**File:** evm/src/core/HandlerV2.sol (L181-209)
```text
    function handlePostRequests(IHost host, PostRequestMessage calldata request) external notFrozen(host) {
        uint256 timestamp = block.timestamp;
        uint256 delay = timestamp - host.stateMachineCommitmentUpdateTime(request.proof.height);
        uint256 challengePeriod = host.challengePeriod();
        if (challengePeriod != 0 && challengePeriod > delay) revert ChallengePeriodNotElapsed();

        uint256 requestsLen = request.requests.length;
        MerkleMountainRange.Leaf[] memory leaves = new MerkleMountainRange.Leaf[](requestsLen);

        for (uint256 i = 0; i < requestsLen; ++i) {
            PostRequestLeaf memory leaf = request.requests[i];
            // check destination
            if (!leaf.request.dest.equals(host.host())) revert InvalidMessageDestination();
            // check time-out
            if (timestamp >= leaf.request.timeout()) revert MessageTimedOut();
            leaves[i] = MerkleMountainRange.Leaf(leaf.index, leaf.request.hash());
        }

        bytes32 root = host.stateMachineCommitment(request.proof.height).overlayRoot;
        if (root == bytes32(0)) revert StateCommitmentNotFound();
        bool valid = MerkleMountainRange.VerifyProof(root, request.proof.multiproof, leaves, request.proof.leafCount);
        if (!valid) revert InvalidProof();

        for (uint256 i = 0; i < requestsLen; ++i) {
            PostRequestLeaf memory leaf = request.requests[i];
            // duplicate request?
            if (host.requestReceipts(leaf.request.hash()) != address(0)) revert DuplicateMessage();
            host.dispatchIncoming(leaf.request, _msgSender());
        }
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L391-410)
```text
        address beneficiary = address(uint160(uint256(body.beneficiary)));
        if (finalize) _filled[body.commitment] = beneficiary;

        uint256 len = body.tokens.length;
        for (uint256 i; i < len; i++) {
            address token = address(uint160(uint256(body.tokens[i].token)));
            uint256 amount = body.tokens[i].amount;
            if (amount == 0) continue;

            uint256 escrowed = _orders[body.commitment][token];
            if (escrowed == 0) revert UnknownOrder();

            _orders[body.commitment][token] = escrowed - amount;
            if (token == address(0)) {
                (bool sent,) = beneficiary.call{value: amount}("");
                if (!sent) revert InsufficientNativeToken();
            } else {
                IERC20(token).safeTransfer(beneficiary, amount);
            }
        }
```

**File:** sdk/packages/sdk/src/chains/evm.ts (L798-878)
```typescript
	/**
	 * Estimates the gas required for a post request execution on this chain.
	 * This function generates mock proofs for the post request, creates a state override
	 * with the necessary overlay root, and estimates the gas cost for executing the
	 * handlePostRequests transaction on the handler contract.
	 *
	 * @param request - The post request to estimate gas for
	 * @param paraId - The ID of the parachain (Hyperbridge) that will process the request
	 * @returns The estimated gas amount in gas units and the generated calldata
	 */
	async estimateGas(request: IPostRequest): Promise<{ gas: bigint; postRequestCalldata: HexString }> {
		const hostParams = await this.publicClient.readContract({
			address: this.params.host,
			abi: EvmHost.ABI,
			functionName: "hostParams",
		})

		const { root, proof, index, treeSize } = await generateRootWithProof(request, 2n ** 10n)
		const latestStateMachineHeight = 6291991n
		const paraId = 4009n
		const overlayRootSlot = getStateCommitmentFieldSlot(
			paraId, // Hyperbridge chain id
			latestStateMachineHeight, // Hyperbridge chain height
			1, // For overlayRoot
		)
		const postParams = {
			height: {
				stateMachineId: BigInt(paraId),
				height: latestStateMachineHeight,
			},
			multiproof: proof,
			leafCount: treeSize,
		}

		const formattedRequest = {
			...request,
			source: toHex(request.source),
			dest: toHex(request.dest),
		}

		const contractArgs = [
			this.params.host,
			{
				proof: postParams,
				requests: [
					{
						request: formattedRequest,
						index,
					},
				],
			},
		] as const

		const postRequestCalldata = encodeFunctionData({
			abi: HandlerV2.ABI,
			functionName: "handlePostRequests",
			args: contractArgs,
		})

		let gas = await this.publicClient.estimateContractGas({
			address: hostParams.handler,
			abi: HandlerV2.ABI,
			functionName: "handlePostRequests",
			args: contractArgs,
			stateOverride: [
				{
					address: this.params.host,
					stateDiff: [
						{
							slot: overlayRootSlot,
							value: root,
						},
					],
				},
			],
		})

		// Add the cost of consensus verification (~600k gas)
		gas += 600_000n

		return { gas, postRequestCalldata }
```

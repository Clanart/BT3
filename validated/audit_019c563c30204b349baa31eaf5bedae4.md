## Analysis

The Chainlink VRF bug boils down to one broken invariant: **a fixed, too-shallow confirmation depth was trusted as "final" on a chain (Polygon) whose common reorg depth exceeds it**, letting the "final" state be silently rewritten before it is truly settled. The direct local analog is Simplex's **cross-chain confirmation-policy defaults**, which gate when the filler commits destination-chain capital against a source-chain order.

### Title
Simplex's default Polygon/BNB confirmation depth is shallower than real-world reorg depth, letting an attacker get destination funds released against a source order that reorgs away - (File: `sdk/packages/simplex/src/config/interpolated-curve.ts`)

### Summary
`DEFAULT_CONFIRMATION_POLICIES` hard-codes a minimum of **2 confirmation blocks** for Polygon (`"137"`) and BNB Chain (`"56"`) orders under $1,000, scaling only up to 32/3 blocks respectively at $100k [1](#0-0) . These values are used directly by the filler's order-processing loop to decide how long to wait on the source chain before delivering output tokens on the destination chain for a cross-chain intent order [2](#0-1) . As documented in the same repo's own bounty-facing docs, Polygon's confirmation curve is calibrated only against nominal block time ("milestone finality"), not against observed reorg depth [3](#0-2) .

### Finding Description
The filler's `handleNewOrder` path computes `requiredConfirmations` purely from the configured/default curve for the order's USD value and source chain, then waits until the BFT-quorum-observed confirmation count reaches that number before evaluating and executing the fill [4](#0-3) . Once `waitForConfirmations()` resolves, the filler proceeds to `executeOrder`, which delivers output tokens to the beneficiary on the destination chain and dispatches the cross-chain settlement message back to the source chain [5](#0-4) .

For a sub-$1,000 order on Polygon, this "finality" gate is only **2 blocks** (~4 seconds) [6](#0-5) . Polygon has documented reorgs of depth far greater than 2-3 blocks occurring regularly (exactly the class of event the original VRF report cites, including a historical 157-block reorg). The quorum-confirmation mechanism only protects against providers disagreeing about the *current* chain head — it does not protect against a *real*, network-wide reorg that later invalidates the block the `OrderPlaced` transaction was included in, since a quorum of honest RPC endpoints will simply update together once the reorg propagates [7](#0-6) . Once the filler has moved past its own confirmation gate and executed the fill, there is no roll-back protection: `IntentGatewayV2.fillOrder` on the destination has already released tokens to the beneficiary [8](#0-7) , and settlement of the source-side escrow depends on the source order commitment still existing when Hyperbridge processes the corresponding message.

The existing `verifyOrderOnSource` check only confirms that escrow currently exists at read-time [9](#0-8) ; it does not — and cannot — guard against the order's placing transaction later being reorged out after the (too-shallow) confirmation threshold was satisfied but before the source chain has truly finalized. This is a direct configuration analog of the VRF bug: the constant chosen (`REQUEST_CONFIRMATIONS = 3`) was too low for the actual chain's reorg profile; here `DEFAULT_CONFIRMATION_POLICIES["137"]` (2 blocks) and `["56"]` (2 blocks) suffer the identical flaw.

### Impact Explanation
If Polygon (or BNB Chain) experiences a reorg deeper than the filler's configured/default confirmation depth for an order's USD tier, the source-chain `placeOrder`/escrow transaction can be reorged out or superseded after the filler has already delivered destination-chain output tokens to the beneficiary and dispatched a settlement message. This can result in permanent loss of the filler's own committed capital on the destination chain with no corresponding recovered escrow on the source chain — a direct loss of funds outcome, and if the attacker can additionally reuse the same source-chain tokens (double-spend the escrow input across two competing chain histories), it produces a double-claim style outcome as well.

### Likelihood Explanation
This requires no malicious relayer, prover, or governance actor — only a natural/engineered Polygon or BNB Chain reorg of depth greater than the filler's default 2-block confirmation floor for low/medium-value orders, which per the seed report's own cited data happens multiple times per day on Polygon at depths exceeding far more than 2-3 blocks. An unprivileged user placing an order at the threshold amount, timed around network instability (or a self-induced validator-level reorg on a chain they can influence), can realistically trigger this without any protocol-side collusion.

### Recommendation
Raise Polygon's and BNB Chain's minimum default confirmation values in `DEFAULT_CONFIRMATION_POLICIES` to depths that exceed realistically observed reorg depths (not just nominal block-time targets), and document/enforce a floor so that any custom `[confirmationPolicies]` override cannot be set below a chain-specific safe minimum. Consider also delaying settlement-message dispatch or adding a post-fill re-verification step that re-checks source escrow existence at a deeper confirmation count before finalizing withdrawal on the source chain.

### Proof of Concept
1. Attacker places a Polygon-source cross-chain order for an amount under the $1,000 confirmation tier (2 blocks required per default policy) [6](#0-5) .
2. Filler's `handleNewOrder` computes `requiredConfirmations = 2` from the curve and waits only until the quorum-observed confirmation count reaches 2 [10](#0-9) .
3. Filler executes the fill, delivering destination-chain output tokens to the beneficiary and dispatching the `RedeemEscrow`/settlement message [5](#0-4) .
4. A Polygon reorg deeper than 2 blocks occurs (naturally, or induced), reverting/replacing the `placeOrder`/escrow transaction so the canonical chain no longer contains the original escrow.
5. The filler has already paid out on the destination chain against a source-chain state that no longer exists in the canonical history, resulting in fund loss.

### Citations

**File:** sdk/packages/simplex/src/config/interpolated-curve.ts (L26-44)
```typescript
export const DEFAULT_CONFIRMATION_POLICIES: Record<string, CurveConfig> = {
	"1": {
		points: [
			{ amount: "1000", value: 2 },
			{ amount: "100000", value: 15 },
		],
	}, // Ethereum (~12s blocks, ~24s–3min)
	"56": {
		points: [
			{ amount: "1000", value: 2 },
			{ amount: "100000", value: 3 },
		],
	}, // BNB Chain (~3s blocks, fast finality)
	"137": {
		points: [
			{ amount: "1000", value: 2 },
			{ amount: "100000", value: 32 },
		],
	}, // Polygon (~2s blocks, milestone finality)
```

**File:** sdk/packages/simplex/src/core/filler.ts (L391-444)
```typescript
	private async verifyOrderOnSource(order: Order): Promise<boolean> {
		if (order.inputs.length === 0) {
			this.logger.warn({ orderId: order.id }, "Order has no inputs, rejecting")
			return false
		}

		const sourceClient = this.chainClientManager.getPublicClient(order.source)
		const intentGatewayAddress = this.configService.getIntentGatewayAddress(order.source)
		const commitment = order.id as HexString

		try {
			const escrows = await Promise.all(
				order.inputs.map((input: TokenInfo) =>
					retryPromise(
						() =>
							sourceClient.readContract({
								address: intentGatewayAddress,
								abi: INTENT_GATEWAY_V2_ABI,
								functionName: "_orders",
								args: [commitment, bytes32ToBytes20(input.token) as Address],
							}) as Promise<bigint>,
						{
							maxRetries: 3,
							backoffMs: 250,
							logMessage: "Failed to read _orders on source chain",
						},
					),
				),
			)

			for (let i = 0; i < escrows.length; i++) {
				if (escrows[i] === 0n) {
					this.logger.warn(
						{
							orderId: order.id,
							source: order.source,
							inputIndex: i,
							token: order.inputs[i].token,
						},
						"Phantom commitment: source escrow missing for input, skipping order",
					)
					return false
				}
			}

			return true
		} catch (err) {
			this.logger.error(
				{ orderId: order.id, source: order.source, err },
				"Failed to verify source escrow, skipping order",
			)
			return false
		}
	}
```

**File:** sdk/packages/simplex/src/core/filler.ts (L522-606)
```typescript
				const isCrossChain = order.source !== order.destination
				let requiredConfirmations = 0
				if (isCrossChain) {
					const fillableStrategies = [...canFillCache].filter(([, canFill]) => canFill)
					if (fillableStrategies.length === 0) {
						this.logger.debug(
							{ orderId: order.id, source: order.source, destination: order.destination },
							"Skipping cross-chain order: no strategy can fill it",
						)
						return
					}
					if (!fillableStrategies.some(([strategy]) => strategy.confirmationPolicy)) {
						this.logger.warn(
							{ orderId: order.id, source: order.source, destination: order.destination },
							"Skipping cross-chain order: no fillable strategy has a confirmation policy configured",
						)
						return
					}
					for (const [strategy, canFill] of canFillCache) {
						if (!canFill || !strategy.confirmationPolicy) continue
						requiredConfirmations = Math.max(
							requiredConfirmations,
							strategy.confirmationPolicy.getConfirmationBlocks(
								getChainId(order.source)!,
								inputUsdValue.toNumber(),
							),
						)
					}
				}

				// Run confirmation waiting and evaluation in parallel.
				// The AbortController lets evaluateOrder cancel the confirmation
				// loop early when the order turns out to be unprofitable.
				const abortController = new AbortController()
				const confirmStartMs = Date.now()

				// Single-provider setups keep the tight 300ms poll; quorum setups
				// fan every poll out to all providers, so poll less aggressively
				// to stay within their rate limits.
				const confirmationPollMs = sourceQuorumClient.size > 1 ? 1000 : 300
				const waitForConfirmations = async (): Promise<void> => {
					// Nothing to wait for: same-chain orders (and zero-valued curve
					// points) require no confirmations, and the quorum read they'd
					// otherwise run gains nothing — it would only gate the fill on
					// third-party RPC availability, where a transient QuorumError
					// rejects the surrounding Promise.all and drops the order.
					if (requiredConfirmations <= 0) return

					let currentConfirmations = await retryPromise(
						() =>
							sourceQuorumClient.getTransactionConfirmations({
								hash: transactionHash as HexString,
							}),
						{
							maxRetries: 3,
							backoffMs: 250,
							logMessage: "Failed to get initial transaction confirmations",
						},
					)

					this.logger.info(
						{ orderId: order.id, requiredConfirmations, currentConfirmations },
						"Order confirmation requirements",
					)

					while (currentConfirmations < requiredConfirmations) {
						if (abortController.signal.aborted) return
						await new Promise((resolve) => setTimeout(resolve, confirmationPollMs))
						if (abortController.signal.aborted) return
						currentConfirmations = await retryPromise(
							() =>
								sourceQuorumClient.getTransactionConfirmations({
									hash: transactionHash as HexString,
								}),
							{
								maxRetries: 3,
								backoffMs: 250,
								logMessage: "Failed to get transaction confirmations",
							},
						)
						this.logger.debug({ orderId: order.id, currentConfirmations }, "Order confirmation progress")
					}

					this.logger.info({ orderId: order.id, currentConfirmations }, "Order confirmed on source chain")
				}
```

**File:** docs/content/developers/evm/intent-gateway/simplex.mdx (L438-446)
```text
| Chain | Chain ID | Min ($1,000) | Max ($100,000) | Block Time |
|---|---|---|---|---|
| Ethereum | 1 | 2 blocks (~24s) | 15 blocks (~3m) | ~12s |
| BNB Chain | 56 | 2 blocks (~6s) | 3 blocks (~9s) | ~3s |
| Polygon | 137 | 2 blocks (~4s) | 32 blocks (~1m) | ~2s |
| Base | 8453 | 2 blocks (~4s) | 90 blocks (~3m) | ~2s |
| Arbitrum | 42161 | 8 blocks (~2s) | 720 blocks (~3m) | ~0.25s |
| Unichain | 130 | 2 blocks (~2s) | 180 blocks (~3m) | ~1s |

```

**File:** docs/content/developers/evm/intent-gateway/simplex.mdx (L492-494)
```text
#### Quorum-checked confirmations

Cross-chain orders wait for source-chain confirmations (per the confirmation policy) before a bid is submitted. That count is computed with the same quorum: every endpoint is asked for the transaction receipt and its chain head, and the transaction is confirmed only when a quorum agrees on the *inclusion block* — the receipt's `(blockHash, blockNumber)`. The depth is the quorum head of that agreeing group. Crucially, an endpoint answering **"receipt not found"** is a valid *no* vote — it's responsive but doesn't see the transaction — not a failure: so if the transaction is reorged out, the honest endpoints report not-found, no group reaches quorum, and the count does not advance. A minority still serving a stale or fabricated receipt can never reach the quorum.
```

**File:** docs/content/developers/evm/intent-gateway/overview.mdx (L41-57)
```text
### Fill Flow

The solver calls `fillOrder(order, options)` on the **destination chain**. The function verifies the order hasn't expired (`order.deadline >= block.number`), confirms execution is on the correct chain, and checks the order hasn't already been filled. The solver must provide output amounts greater than or equal to the order's required amounts — any amount below the required amount reverts with `InvalidInput()`.

If the solver provides more tokens than required, the excess (surplus) is split according to `surplusShareBps`. If the order includes calldata, 100% of surplus goes to the protocol to prevent manipulation.

After delivering output tokens to the beneficiary, the contract dispatches a cross-chain `RedeemEscrow` message back to the source chain.


### Settlement

When the settlement message arrives on the source chain, the ISMP host calls `onAccept()`. The handler authenticates the message (verifying it came from a known IntentGateway instance), decodes the `WithdrawalRequest`, and calls `withdraw()` which:

1. Marks the order as filled (`_filled[commitment] = solver`)
2. Transfers each escrowed input token to the solver
3. Releases stored transaction fees (in fee token) to the solver
4. Emits `EscrowReleased(commitment)`
```

**File:** evm/src/apps/IntentGatewayV2.sol (L413-452)
```text
    function fillOrder(Order calldata order, FillOptions calldata options) public payable nonReentrant {
        if (order.deadline < _blockNumber()) revert Expired();
        bytes32 commitment = keccak256(abi.encode(order));

        address hostAddr = host();
        bytes32 currentChain = keccak256(IDispatcher(hostAddr).host());
        bytes32 orderSource = keccak256(order.source);
        bytes32 orderDest = keccak256(order.destination);
        bool isSameChain = orderSource == orderDest;

        if (isSameChain && orderSource != currentChain) revert WrongChain();
        if (!isSameChain && orderDest != currentChain) revert WrongChain();

        if (_filled[commitment] != address(0)) revert Filled();

        if (_params.solverSelection) {
            bytes32 storedSelectionHash;
            assembly {
                storedSelectionHash := tload(commitment)
            }

            bytes32 expectedSelectionHash = keccak256(abi.encode(msg.sender, order.session));
            if (storedSelectionHash != expectedSelectionHash) revert Unauthorized();
        }

        uint256 outputsLen = order.output.assets.length;
        if (options.outputs.length != outputsLen) revert InvalidInput();
        if (order.inputs.length != outputsLen) revert InvalidInput();

        if (isSameChain) {
            _fillSameChain(order, options, commitment);
        } else {
            _fillCrossChain(order, options, commitment);
        }

        if (_params.priceOracle != address(0)) {
            IIntentPriceOracle(_params.priceOracle)
                .recordSpread(commitment, order.source, order.inputs, options.outputs);
        }
    }
```

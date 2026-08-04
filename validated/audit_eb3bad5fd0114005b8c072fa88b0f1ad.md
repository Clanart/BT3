### Title
Flash-loan-inflated liquidity weight lets a solver skew the Phantom order price feed used to size intent-gateway swaps - ([File: sdk/packages/sdk/src/protocols/intents/phantom-aggregation.ts])

### Summary
`getTotalSolverBalance` measures a solver's live on-chain ERC-20 + ERC-4626 balance at the moment the `PhantomBidWindowExhausted` aggregation runs, and `weightedMedian` uses that instantaneous balance as the sole weight to pick the price that all users pricing a Phantom order will trade at. [1](#0-0)  There is no time-averaging, no minimum holding period, and no snapshot-block delay between funding a wallet and having that balance count toward the weighted median. [2](#0-1)  This is the same broken invariant as the reported Rewarder/Farm bug: a value that should reflect durable, at-risk capital is instead read as an instantaneous balance that can be manufactured for one measurement and then removed, letting an attacker "calibrate" an economically consequential number (there: reward rate; here: the settlement price) with capital it never actually risks.

### Finding Description
The Phantom pricing flow works as follows:
1. Solvers submit bids (signed `UserOperation`s) during a bid window.
2. When the bid window closes, `aggregatePhantomBids` fetches all bids, verifies the solver signature/delegation, and for each surviving bid computes a `weight = getTotalSolverBalance(...)` — the solver's current raw ERC-20 balance plus `maxWithdraw` from any configured ERC-4626 vault, read via a live `eth_call` against the destination chain "latest" block. [3](#0-2) [4](#0-3) 
3. `weightedMedian` then picks the lowest price whose cumulative weight crosses half of total weight — i.e., a large enough weight can single-handedly determine the reported median. [5](#0-4) 
4. This `medianPrice` is persisted as the canonical `PhantomOrderPriceSnapshot` and is exactly what the SDK uses to size real intent-gateway orders: `floor(netInput × medianPrice / standardAmount)` for exact-input quotes, or the inverse for exact-output quotes. [6](#0-5) 

The weight has no persistence requirement: nothing checks that the balance existed before the bid window opened, was held through settlement, or wasn't sourced from a flash loan taken and repaid in the same transaction/block as the balance read. A solver can:
1. Submit a lowball (solver-favorable) bid during the window.
2. Flash-loan a large amount of the output token (or its ERC-4626 wrapper) into its delegated `SolverAccount`-controlled EOA right before/at the block the aggregation's `eth_call` reads balances.
3. Have `getTotalSolverBalance` return the inflated balance, giving that lowball bid overwhelming weight in `weightedMedian`, dragging `medianPrice` down to (or near) the attacker's quote.
4. Repay/return the flash loan immediately after — the capital is never actually available to fill any order at that price.
5. Every subsequent user who prices a Phantom order off this snapshot computes their required input against this manipulated, unbacked price, receiving a worse rate than the genuine liquidity-weighted market would produce.

Existing guards (`isVerifiedSolverBid`, EIP-7702 delegation check, nonce/session binding) authenticate *who* is bidding, not *what capital backs the bid weight* — none of them touch the balance read in `getTotalSolverBalance`, so they do nothing to stop this. [7](#0-6) 

### Impact Explanation
This is a transaction/price manipulation on the exact mechanism (`quote`/`queryAvailableLiquidity`) that determines how much a user's real IntentGateway order escrows and receives. A manipulated `medianPrice` directly changes the `inputs`/`output.assets` amounts computed for a live cross-chain order, meaning real user funds move at an attacker-skewed rate — matching the bounty's "transaction manipulation" / "logic attacks" category rather than a pure off-chain analytics defect, since the snapshot is the authoritative price source the SDK instructs users to build orders from with "no further fee or slippage adjustment... required." [8](#0-7) 

### Likelihood Explanation
Any address able to acquire and quickly return a large balance of the target output token or its configured ERC-4626 wrapper (a standard flash loan) and hold an EIP-7702 delegation to `SolverAccount` can execute this without needing a malicious relayer, prover, or admin — it's a fully unprivileged, self-serve attack exploiting the specific timing gap between "balance measured" and "capital actually committed to fill."

### Recommendation
Do not weight bids by an instantaneous spot balance read at aggregation time. Require the weight to reflect capital that is durably committed and verifiably deliverable, e.g.:
- Require the weighted balance to be held/attested over a window preceding the bid window close (checkpoint balances at bid-submission time and again at a delayed block, using the minimum).
- Cap a single solver's weight contribution (e.g., at some multiple of the order's requested size) so no single balance spike can dominate the median.
- Tie weight to escrowed/staked collateral in the `SolverAccount`/vault rather than a freely flash-loanable spot balance, mirroring the fix pattern from the reported bug (bind the measured value to a timestamp/holding-period check rather than an instantaneous read).

### Proof of Concept
1. Attacker deploys/controls an EOA delegated via EIP-7702 to the chain's `SolverAccount`.
2. Attacker submits a bid for a Phantom order quoting an output amount favorable to itself (low output).
3. In the same block the indexer's `handlePhantomOrderPrices` handler triggers `aggregatePhantomBids` (on `PhantomBidWindowExhausted`), the attacker takes a flash loan of the output token, moves it into the delegated EOA, and (via the same or an adjacent transaction) ensures it is present when `getTotalSolverBalance`'s `eth_call` at `"latest"` executes. [9](#0-8) 
4. `weightedMedian` returns (or is pulled sharply toward) the attacker's quoted price because its weight now dominates cumulative weight. [5](#0-4) 
5. Attacker repays the flash loan; the borrowed capital never backs an actual fill.
6. The resulting `PhantomOrderPriceSnapshot.medianPrice` is persisted and served to every user pricing a `USDC→cNGN`-style order via `quote()`, which computes their order's real escrow/output amounts directly from this manipulated value. [6](#0-5) 

Note: I could not verify from the indexed code whether any additional server-side rate limiting, minimum-balance-duration check, or off-chain sanity bound exists in the SubQuery handler beyond what's shown in `handlePhantomOrderPrices.handler.ts` — if such a check exists elsewhere in the indexer package that wasn't surfaced by search, it could mitigate this. I recommend confirming this in a full checkout of `sdk/packages/indexer` before treating this as fully unmitigated in production.

### Citations

**File:** sdk/packages/sdk/src/protocols/intents/phantom-aggregation.ts (L115-136)
```typescript
// Liquidity-weighted median of solver quotes. Each quote's influence is proportional to `weight` —
// the solver's total balance for the output token across native + vault venues — so a solver that
// can actually deliver size moves the price more than one quoting on thin liquidity. Returns the
// lower weighted median: the smallest price whose cumulative weight reaches half of the total.
// Zero-weight quotes contribute nothing; if every weight is zero it falls back to the unweighted
// median so a price is still reported.
export function weightedMedian(entries: { price: bigint; weight: bigint }[]): bigint {
	const sorted = [...entries].sort((a, b) => (a.price < b.price ? -1 : a.price > b.price ? 1 : 0))
	const totalWeight = sorted.reduce((acc, e) => (e.weight > 0n ? acc + e.weight : acc), 0n)

	if (totalWeight === 0n) {
		return sorted[Math.floor(sorted.length / 2)].price
	}

	let cumulative = 0n
	for (const entry of sorted) {
		if (entry.weight <= 0n) continue
		cumulative += entry.weight
		if (cumulative * 2n >= totalWeight) return entry.price
	}
	return sorted[sorted.length - 1].price
}
```

**File:** sdk/packages/sdk/src/protocols/intents/phantom-aggregation.ts (L273-327)
```typescript
async function isVerifiedSolverBid(params: {
	userOp: PackedUserOperation
	commitment: string
	sessionKey: HexString
	chainId: bigint
	solverAccount: string
	evmRpcUrl: string
	recoverSigner: RecoverBidSigner
	bidNonceKey: BidNonceKeyFn
	logger?: AggregationLogger
}): Promise<boolean> {
	const { userOp, commitment, sessionKey, chainId, solverAccount, evmRpcUrl, recoverSigner, bidNonceKey, logger } =
		params
	const solver = userOp.sender

	const parsed = splitBidSignature(userOp.signature)
	if (!parsed) {
		logger?.warn({ solver, commitment }, "Rejecting phantom bid: malformed userOp signature")
		return false
	}

	// Cheap early-out ONLY. The prefix sits inside userOp.signature, which userOpHash excludes, so it
	// is attacker-mutable and must never be what binds a bid to an order — the nonce key below is.
	if (parsed.commitment.toLowerCase() !== commitment.toLowerCase()) {
		logger?.warn(
			{ solver, commitment, signedFor: parsed.commitment },
			"Rejecting phantom bid: signed for another order",
		)
		return false
	}

	// The authoritative binding, mirroring SolverAccount.validateUserOp on-chain. The nonce IS
	// covered by userOpHash, so a solver signature stays valid only for the (order, sessionKey) pair
	// its nonce key was derived from. `sessionKey` is read from the bid's own calldata, which is also
	// covered by userOpHash — so every operand here is signed, leaving nothing for a replay to swap.
	if (BigInt(userOp.nonce) >> 64n !== bidNonceKey(commitment as HexString, sessionKey)) {
		logger?.warn({ solver, commitment }, "Rejecting phantom bid: nonce does not bind order and session key")
		return false
	}

	// SolverAccount._rawSignatureValidation recovers over the bare userOpHash and requires the signer
	// to be the account itself, which under EIP-7702 is the sender EOA.
	const signer = await recoverSigner(userOp, ENTRY_POINT_V08_ADDRESS, chainId, parsed.solverSignature)
	if (!signer || signer.toLowerCase() !== solver.toLowerCase()) {
		logger?.warn({ solver, commitment, signer }, "Rejecting phantom bid: signature does not recover to the sender")
		return false
	}

	if (!(await isDelegatedToSolverAccount(evmRpcUrl, solver, solverAccount))) {
		logger?.warn({ solver, commitment, solverAccount }, "Rejecting phantom bid: sender is not a delegated solver")
		return false
	}

	return true
}
```

**File:** sdk/packages/sdk/src/protocols/intents/phantom-aggregation.ts (L339-370)
```typescript
async function ethCallUint(evmRpcUrl: string, to: string, data: string): Promise<bigint> {
	try {
		const result = await rpcCall(evmRpcUrl, {
			id: 1,
			jsonrpc: "2.0",
			method: "eth_call",
			params: [{ to, data }, "latest"],
		})
		if (result.error || !result.result || result.result === "0x") return 0n
		return BigInt(result.result)
	} catch {
		return 0n
	}
}

// Sums the solver's redeemable balance of a single token on its destination chain: the raw ERC-20
// balance plus any ERC-4626 vault positions wrapping it.
async function getTotalSolverBalance(
	evmRpcUrl: string,
	chain: string,
	token: string,
	solver: string,
	yieldVaults: YieldVaultMap,
): Promise<bigint> {
	const padded = solver.replace("0x", "").padStart(64, "0")
	const raw = await ethCallUint(evmRpcUrl, token, `0x70a08231${padded}`) // balanceOf(address)
	const vaults = yieldVaults[chain]?.[token.toLowerCase()] ?? []
	const vaultBalances = await Promise.all(
		vaults.map((v) => ethCallUint(evmRpcUrl, v, `0xce96cb77${padded}`)), // maxWithdraw(address)
	)
	return vaultBalances.reduce((acc, b) => acc + b, raw)
}
```

**File:** sdk/packages/sdk/src/protocols/intents/phantom-aggregation.ts (L505-508)
```typescript
			// Price influence: the solver's liquidity in the output token on the destination chain.
			const outputTokenAddress = toAddress(fillData.outputToken)
			const weight = await getTotalSolverBalance(destUrl, chain, outputTokenAddress, solver, yieldVaults)
			quotes.push({ price: fillData.solverAmount, weight })
```

**File:** docs/content/developers/sdk/api/intent-gateway.mdx (L228-232)
```text
The result includes `amountIn`, `amountOut`, and strategy-specific quote metadata. Phantom metadata contains the snapshot commitment, block number/time, directional token addresses, `standardAmount`, median/low/high prices, contributing bid count, and source-chain protocol fee. Uniswap metadata contains the V4 PoolKey, quoter, quote chain, and protocol fee.

`amountIn` and `amountOut` already account for the IntentGateway protocol fee that the gateway deducts from order inputs. Exact-input quotes price the swap against the post-fee input, so `amountOut` is the snapshot-priced output; exact-output quotes return the gross `amountIn` required to produce the requested `amountOut`. Use the returned amounts directly as the order's `inputs` and `output.assets`—no further fee or slippage adjustment is required.

For an exact-input Phantom quote, the SDK deducts the gateway fee and computes `floor(netInput × medianPrice / standardAmount)`. For an exact-output quote, it computes `ceil(amountOut × standardAmount / medianPrice)` and grosses that input up for the gateway fee. A missing indexer, missing snapshot, or invalid snapshot throws an explicit error; the SDK does not silently switch price sources.
```

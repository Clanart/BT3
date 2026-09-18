Found a solid analog. The `solo` precompile's `ClaimSpecific` function exhibits the same bug class as the reported issue: a loop over multiple assets to transfer, where the failure of a single item aborts the entire batch, causing legitimate, unrelated claims to also fail.

### Title
`ClaimSpecific` reverts the entire multi-asset claim (native + CW20 + all CW721 tokens) if any single CW721 transfer fails - ([File: precompiles/solo/solo.go])

### Summary
`PrecompileExecutor.ClaimSpecific` in the `solo` precompile lets an EOA redeem a legacy signed Cosmos message (`MsgClaimSpecific`) that lists native coins, CW20 balances, and/or CW721 collections to transfer from a legacy `sender` account to the caller's associated Sei address. For CW721 assets that don't specify a single token (`Denom == ""`), the code paginates through *every* token the sender owns in that collection and then loops, calling `wasmKeeper.Execute` (`transfer_nft`) for each token one at a time [1](#0-0) . If any single token in that list fails to transfer — e.g. it is currently escrowed/approved to another contract, is non-transferable, or the pagination cursor picks up a stale/duplicate entry — the function returns immediately with an error [2](#0-1) , aborting the whole EVM call and rolling back every other transfer that had already succeeded or was still pending in the same call (native coin transfer, CW20 transfer, and all other CW721 tokens in the collection) [3](#0-2) .

### Finding Description
This is the same bug class as the external report: a loop that performs one transfer per array element with no per-item error isolation, so one problematic element (there: an already-refunded NFT deposit; here: an untransferable/stale CW721 token) causes the entire batch operation — and everything else bundled with it — to fail. Because the CW721 branch re-queries "all tokens owned by sender" at execution time rather than using a token list fixed in the original signed message, resubmitting the exact same `claimSpecific` call will deterministically hit the same failing token again and again as long as that token remains in the problematic state, permanently blocking the native-coin and CW20 portions of the claim bundled in the same asset list, plus every other valid CW721 token in that collection.

### Impact Explanation
Any funds (native coins, CW20 balances, or other CW721 tokens) that were meant to be claimed together with a single stuck/untransferable NFT in the same `MsgClaimSpecific` message become unclaimable through that call — the transaction always reverts as long as the problematic token exists in that state. The user's only recourse is to know which specific token is broken and re-sign a brand new message using per-token `Denom` entries to exclude it, which is a nontrivial and error-prone workaround, not a fix.

### Likelihood Explanation
Reachable by any legacy-account holder calling the public `solo` precompile at `0x000000000000000000000000000000000000100C` with a valid signed claim message. Non-transferable or escrowed CW721 tokens are a normal real-world occurrence (marketplace listings, escrow contracts, soulbound-style restrictions), so hitting this condition does not require an attacker — a single stuck NFT that the sender legitimately owns is enough to block the whole claim.

### Recommendation
Isolate per-asset (and per-token, within the CW721 branch) failures so that a failing transfer does not abort transfers of unrelated assets — e.g., collect and skip/report failed token transfers instead of returning immediately, or process each asset independently and aggregate errors/results rather than failing fast on the first error.

### Proof of Concept
1. Legacy account `sender` owns native coins, a CW20 balance, and multiple CW721 tokens in one collection, one of which (`tokenX`) is currently approved/escrowed to a third-party contract such that `transfer_nft` from `sender` will fail.
2. `sender` signs a `MsgClaimSpecific` listing all three asset types, with the CW721 asset referencing the whole collection (empty `Denom`) so all owned tokens are claimed.
3. Caller submits this signed tx via the `solo` precompile's `claimSpecific` method.
4. `ClaimSpecific` transfers native coins and CW20 balance successfully, then begins iterating CW721 tokens; when it reaches `tokenX` the `wasmKeeper.Execute` call fails [4](#0-3) , causing the whole precompile call — and therefore the whole EVM transaction — to revert, undoing the native and CW20 transfers as well.
5. Every subsequent identical call to `claimSpecific` with the same signed message reproduces the same failure, since the token list is re-derived live from `tokenX`'s ongoing escrow/approval state.

### Citations

**File:** precompiles/solo/solo.go (L167-223)
```go
	for _, asset := range claimSpecificMsg.GetIAssets() {
		if asset.IsNative() {
			denom := asset.GetDenom()
			balance := p.bankKeeper.GetBalance(ctx, sender, denom)
			if !balance.IsZero() {
				if err := p.bankKeeper.SendCoins(ctx, sender, callerSeiAddr, sdk.NewCoins(balance)); err != nil {
					return nil, 0, err
				}
			}
			continue
		}
		contractAddr, err := sdk.AccAddressFromBech32(asset.GetContractAddress())
		if err != nil {
			return nil, 0, fmt.Errorf("failed to parse contract address %s: %w", asset.GetContractAddress(), err)
		}
		switch {
		case asset.IsCW20():
			res, err := p.wasmViewKeeper.QuerySmartSafe(ctx, contractAddr, CW20BalanceQueryPayload(sender))
			if err != nil {
				return nil, 0, fmt.Errorf("failed to query CW20 contract %s for balance: %w", contractAddr.String(), err)
			}
			balance, err := ParseCW20BalanceQueryResponse(res)
			if err != nil {
				return nil, 0, fmt.Errorf("failed to parse CW20 contract %s balance response: %w", contractAddr.String(), err)
			}
			_, err = p.wasmKeeper.Execute(ctx, contractAddr, sender, CW20TransferPayload(callerSeiAddr, balance), sdk.NewCoins())
			if err != nil {
				return nil, 0, fmt.Errorf("failed to transfer on CW20 contract %s: %w", contractAddr.String(), err)
			}
		case asset.IsCW721():
			allTokens := []string{}
			if token := asset.GetDenom(); token != "" {
				allTokens = append(allTokens, token)
			} else {
				startAfter := ""
				for {
					res, err := p.wasmViewKeeper.QuerySmartSafe(ctx, contractAddr, CW721TokensQueryPayload(sender, startAfter))
					if err != nil {
						return nil, 0, fmt.Errorf("failed to query CW721 contract %s for all tokens: %w", contractAddr.String(), err)
					}
					tokens, err := ParseCW721TokensQueryResponse(res)
					if err != nil {
						return nil, 0, fmt.Errorf("failed to parse CW20 contract %s balance response: %w", contractAddr.String(), err)
					}
					if len(tokens) == 0 {
						break
					}
					allTokens = append(allTokens, tokens...)
					startAfter = tokens[len(tokens)-1]
				}
			}
			for _, token := range allTokens {
				_, err := p.wasmKeeper.Execute(ctx, contractAddr, sender, CW721TransferPayload(callerSeiAddr, token), sdk.NewCoins())
				if err != nil {
					return nil, 0, fmt.Errorf("failed to transfer token %s on CW721 contract %s: %w", token, contractAddr.String(), err)
				}
			}
```

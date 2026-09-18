### Title
Unbounded CW721 token loop in solo precompile `ClaimSpecific` can permanently block asset migration - (File: `precompiles/solo/solo.go`)

### Summary
The `solo` precompile's `ClaimSpecific` function performs an unbounded pagination loop to collect every CW721 token ID owned by a claimant, then iterates over the full, unbounded list to execute a `transfer_nft` CosmWasm call for each token in the same transaction. Because this happens entirely within a single EVM transaction with no per-iteration checkpointing, an address holding (or being made to hold) a very large number of NFTs from a given CW721 contract can never successfully execute `claimSpecific()`, since the transaction will always exceed the available gas before completing all transfers.

### Finding Description
`ClaimSpecific` builds `allTokens` by repeatedly querying the CW721 contract's `tokens` query with `start_after` pagination until an empty page is returned, with no cap on the total number of tokens collected: [1](#0-0) 

It then iterates over the entire, unbounded `allTokens` slice, issuing one `p.wasmKeeper.Execute` (a full CW20/CW721 contract execution) per token to transfer it to the caller's associated Sei address: [2](#0-1) 

This is structurally the same bug class as the Knox `_previewWithdraw()`/`_redeemMax()` issue: a permissionless, single-transaction entry point drives a loop whose iteration count is controlled by state that can grow without bound (here, the number of CW721 tokens owned by `sender` in a given contract), with an external call (`wasmKeeper.Execute`) performed on every iteration. There is no batching, pagination limit, or partial-progress mechanism — the whole operation must complete within one transaction or it reverts entirely via the panic-recovery wrapper in `Execute`: [3](#0-2) 

### Impact Explanation
If the number of CW721 tokens owned by the claiming account in a target contract grows large enough that transferring all of them (query + `Execute` per token) cannot fit within the maximum transaction/block gas, `claimSpecific()` becomes permanently uncallable for that account/contract pair — the transaction will always run out of gas and revert before finishing. Since `ClaimSpecific` provides no way to transfer a bounded subset of tokens (the `asset.GetDenom()` field is only used to select a single explicit token id; the "transfer all" path is all-or-nothing), this results in a permanent freezing of the CW721 assets from this migration/claim path for accounts with sufficiently large NFT holdings. An adversary could also grief a specific victim by transferring or airdropping large quantities of CW721 tokens to the victim's Sei address before they claim, deliberately pushing them over the per-tx gas ceiling.

### Likelihood Explanation
Reachable by any unprivileged EOA that can construct and sign the inner Cosmos `MsgClaimSpecific` (or have such tokens sent to their address by an attacker) and submit it via the `solo` precompile at `0x000000000000000000000000000000000000100C` from a top-level EVM transaction (`evm.GetDepth() > 1` is rejected, but a direct EOA call is exactly the expected/primary use case). No special privilege or validator control is required — only holding (or receiving) enough CW721 tokens under one contract.

### Recommendation
Bound the number of CW721 tokens processed per `ClaimSpecific` call (e.g., cap `allTokens` length and require multiple calls / explicit token-id batches from the caller), or require callers to always specify explicit token IDs via `asset.GetDenom()`/a list argument instead of an implicit "transfer everything" mode. Alternatively, expose a paginated/incremental claim entry point so partial progress can be persisted across transactions instead of an all-or-nothing loop.

### Proof of Concept
1. Deploy/target a CW721 contract and mint (or transfer) enough NFTs to the `sender` address referenced in the claim message so that the number of tokens `N` is large enough that `N` iterations of `QuerySmartSafe` + `wasmKeeper.Execute` (each performing CW721 state reads/writes and event emission) exceed the maximum gas allowed in a single transaction/block.
2. Construct and sign a Cosmos `MsgClaimSpecific` referencing that CW721 contract with an empty `denom` (so the "transfer all tokens" branch at `precompiles/solo/solo.go:196-217` is taken), embed it in an EVM transaction calling `claimSpecific()` on the `solo` precompile (`0x...100C`) as described in `precompiles/solo/solo.go:140-227`.
3. Submit the transaction as a top-level EOA call (`evm.GetDepth() == 1`) with the maximum block gas limit.
4. Observe the transaction always reverts with an out-of-gas condition inside the `for _, token := range allTokens` transfer loop, and no partial transfers are retained — the claim can never complete for this account/contract combination.

### Citations

**File:** precompiles/solo/solo.go (L90-99)
```go
func (p PrecompileExecutor) Execute(ctx sdk.Context, method *abi.Method, caller common.Address, callingContract common.Address, args []interface{}, value *big.Int, readOnly bool, evm *vm.EVM, suppliedGas uint64, _ *tracing.Hooks) (ret []byte, remainingGas uint64, err error) {
	// Needed to catch gas meter panics
	defer func() {
		if r := recover(); r != nil {
			err = fmt.Errorf("execution reverted: %v", r)
			ret = nil
			remainingGas = 0
			return
		}
	}()
```

**File:** precompiles/solo/solo.go (L196-217)
```go
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
```

**File:** precompiles/solo/solo.go (L218-224)
```go
			for _, token := range allTokens {
				_, err := p.wasmKeeper.Execute(ctx, contractAddr, sender, CW721TransferPayload(callerSeiAddr, token), sdk.NewCoins())
				if err != nil {
					return nil, 0, fmt.Errorf("failed to transfer token %s on CW721 contract %s: %w", token, contractAddr.String(), err)
				}
			}
		}
```

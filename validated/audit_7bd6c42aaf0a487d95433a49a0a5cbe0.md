## Title
Hardcoded, unverified `MultiCallCode` runtime bytecode drives GaslessSwapRouter trust decisions with no bytecode-regression check - (File: `blockchain/system/multicall.go`, `blockchain/system/constant.go`, `kaiax/gasless/impl/getter.go`)

### Summary
Kaia's gasless module resolves the trusted `GaslessSwapRouter` address and the allow-listed token set by transiently injecting a hardcoded compiled bytecode blob (`MultiCallCode`) into the state at address `0x402` and calling it, rather than reading the real registered contract's code. This is structurally the same class of bug as the TOB `app-vault` finding: a critical security decision (which address/bytecode is "the" trusted contract) is driven by a hardcoded bytecode constant that is not verified to match its Solidity source, and — unlike the other system contracts in this codebase — it is not covered by the project's own bytecode-regression safety net.

### Finding Description
`ContractCallerForMultiCall.CallContract` and `NewMultiCallContractCaller` do not read the code that is actually deployed at `MultiCallAddr`; instead they overwrite the state with a hardcoded constant, `MultiCallCode`, before executing the call: [1](#0-0) [2](#0-1) 

`MultiCallCode` itself is a hardcoded hex constant derived from an abigen-generated Go binding: [3](#0-2) 

The gasless module then trusts whatever this injected bytecode returns as the canonical `GaslessSwapRouter` address and allowed-token list, which directly determines which router/tokens are eligible for gasless (fee-delegated-by-relayer) swap transactions and lend/repay settlement: [4](#0-3) [5](#0-4) 

The repository has an explicit safeguard for exactly this class of bug — `TestRuntimeCodeRegression`, which pins the keccak256 hash of "every system-contract runtime bytecode this codebase deploys at a well-known address" specifically because "changing it flips the post-install state root and forks the chain. Consensus surface": [6](#0-5) 

However, that test list only covers `MainnetCreditCode`, `MainnetCreditV2Code`, `RegistryCode`, `Kip113Code`, `ERC1967ProxyCode`, and `ERC1967ProxyV5Code`. `MultiCallCode` (used live on every gasless-info lookup) and `AddressBookV2Code` are hardcoded bytecode constants that are **not** included in this regression check, so a stale or incorrectly regenerated `MultiCallContractBinRuntime` binding (e.g. from a solc/optimizer change, or a manual edit) could silently diverge from the audited `MultiCallContract.sol` source with no automated test catching it — the exact "bytecode used does not match the bytecode generated from the source code" failure mode described in the TOB report, just for a different hardcoded contract blob.

### Impact Explanation
Because `getGaslessInfo` is the sole mechanism by which the node's gasless tx-pool admission and lend/repay settlement logic learns the trusted `GaslessSwapRouter` and allowed tokens, an unnoticed mismatch between `MultiCallCode` and the real, audited `MultiCallContract.sol` (e.g. after a routine binding regeneration that isn't caught because there is no regression test) can:
- Change router/token resolution logic silently, potentially admitting fee-delegation-abuse-eligible swap transactions that should have been rejected (e.g., token/router combinations the reviewed source intended to filter out).
- Reintroduce a bug that had been fixed in the audited Solidity source but not reflected in the actually-executing runtime bytecode, since the two are never cross-checked.

This directly touches the gasless/fee-delegation settlement path (`kaiax/gasless/impl/tx_pool.go`, `getter.go`), which is reachable by any unprivileged gasless transaction sender.

### Likelihood Explanation
This requires a maintenance-time slip (an unverified regeneration or hand-edit of `MultiCallContractBinRuntime`) rather than a runtime attacker, mirroring the "Configuration/High difficulty" classification of the original TOB finding. The likelihood is elevated by the fact that the project's own review process assumes `TestRuntimeCodeRegression` covers "every system-contract runtime bytecode this codebase deploys," while `MultiCallCode` and `AddressBookV2Code` are demonstrably absent from that list — i.e., the existing safety mechanism has a documented but unenforced scope, so a future silent divergence would not be caught by CI.

### Recommendation
- Short term: Add `MultiCallCode` (and `AddressBookV2Code`) to `TestRuntimeCodeRegression` in `blockchain/system/constant_test.go`, pinning their keccak256 hashes exactly as done for the other system contracts.
- Long term: Add a CI check that regenerates all `contracts/bindings/*` from the checked-in Solidity sources and fails the build if the resulting `BinRuntime` differs from the committed constant, closing the class of bug entirely rather than relying on a manually maintained allow-list of hashes.

### Proof of Concept
Not applicable as a live exploit — this is a configuration/process gap, not an exploitable code path in itself. The concrete verification is: enumerate all hardcoded `...BinRuntime`/`...Code` constants used at well-known system addresses (`blockchain/system/constant.go`) and confirm each is present in `TestRuntimeCodeRegression` (`blockchain/system/constant_test.go`); `MultiCallCode` and `AddressBookV2Code` are absent, demonstrating the gap analogous to the TOB `app-vault` bytecode-mismatch finding.

### Citations

**File:** blockchain/system/multicall.go (L43-45)
```go
func (caller *ContractCallerForMultiCall) CodeAt(ctx context.Context, contract common.Address, blockNumber *big.Int) ([]byte, error) {
	return MultiCallCode, nil
}
```

**File:** blockchain/system/multicall.go (L59-64)
```go
	// Set the code of the multicall contract
	err := caller.state.SetCode(MultiCallAddr, MultiCallCode)
	if err != nil {
		return nil, err
	}

```

**File:** blockchain/system/constant.go (L96-98)
```go
	MultiCallCode             = hexutil.MustDecode("0x" + multicall.MultiCallContractBinRuntime)
	MultiCallMockCode         = hexutil.MustDecode("0x" + testcontract.MultiCallContractMockBinRuntime)
	MultiCallPermlessMockCode = hexutil.MustDecode("0x" + permlesstestcontract.MultiCallContractMockPermissionlessBinRuntime)
```

**File:** kaiax/gasless/impl/getter.go (L315-344)
```go
func (g *GaslessModule) updateAddresses(header *types.Header) error {
	g.gaslessInfoMu.Lock()
	defer g.gaslessInfoMu.Unlock()

	swapRouter, tokens, err := getGaslessInfo(g.Chain, header)
	// proceed even if there is something wrong with multicall contract
	if err != nil {
		g.swapRouter = common.Address{}
		g.allowedTokens = map[common.Address]bool{}
		logger.Warn("there is something wrong with multicall contract", "err", err.Error())
		return nil
	}

	g.swapRouter = swapRouter

	g.allowedTokens = map[common.Address]bool{}
	for _, addr := range tokens {
		// all tokens are allowed if nil
		if g.GaslessConfig.AllowedTokens == nil {
			g.allowedTokens[addr] = true
		}
		for _, allowed := range g.GaslessConfig.AllowedTokens {
			if addr == allowed {
				g.allowedTokens[addr] = true
			}
		}
	}

	return nil
}
```

**File:** kaiax/gasless/impl/getter.go (L369-389)
```go
func getGaslessInfo(bc backends.BlockChainForCaller, header *types.Header) (common.Address, []common.Address, error) {
	statedb, err := bc.StateAt(header.Root)
	if err != nil {
		return common.Address{}, nil, err
	}

	// If Registry is not installed, do not query GaslessSwapRouter contract.
	if statedb.GetCode(system.RegistryAddr) == nil || bc.Config().IsRandaoForkBlockParent(header.Number) {
		return common.Address{}, nil, nil
	}

	caller, err := system.NewMultiCallContractCaller(statedb, bc, header)
	if err != nil {
		return common.Address{}, nil, err
	}

	opts := &bind.CallOpts{BlockNumber: header.Number}
	info, err := caller.MultiCallGaslessInfo(opts)

	return info.Gsr, info.Tokens, err
}
```

**File:** blockchain/system/constant_test.go (L25-53)
```go
// TestRuntimeCodeRegression pins the keccak256 hash of every system-contract
// runtime bytecode this codebase deploys at a well-known address. The
// bytecode is part of the canonical chain state once installed; changing it
// flips the post-install state root and forks the chain. Consensus surface.
//
// DO NOT MODIFY THE EXPECTED HASHES BELOW.
//
// If a regenerated abigen output makes this test fail, investigate why
// (solc version, optimizer, source change) and either revert the
// regeneration or route the new bytecode through a new constant — the way
// ERC1967ProxyV5Code was added alongside the existing ERC1967ProxyCode.
func TestRuntimeCodeRegression(t *testing.T) {
	tcs := []struct {
		code []byte
		hash string
	}{
		{MainnetCreditCode, "0x24dccf9f86d49ffe0385d6fd43ceed51a71d53d9e72df9d7943a24128b4916ec"},
		{MainnetCreditV2Code, "0xb45837dfb0d4edd411a8962780361c0b984e2a25a5a03be465ae9731a5d5c0ab"},
		{RegistryCode, "0xfd3c2152828579b98068570231554ed4bacf528f50ff1bf9fce6300ec023f720"},
		{Kip113Code, "0x236841ea654b0f18e83e934ba0f69b4ab215f0b6ffbeee288797ce67c89aea25"},
		{ERC1967ProxyCode, "0x7bd49b148f3b1ffd97fb2ef2fdc773271822fa8306d3bcba626fbd412ed21c12"},
		{ERC1967ProxyV5Code, "0xea418405ca57c8c6b4cbef32defcf1a8e86a5dc8e2a1ec0c4a0941285483cfcb"},
	}

	for _, tc := range tcs {
		codeHash := crypto.Keccak256Hash(tc.code)
		assert.Equal(t, tc.hash, codeHash.Hex())
	}
}
```

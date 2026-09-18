### Title
Weak, fully-predictable PREVRANDAO substitute lets any EVM caller precompute on-chain "randomness" before broadcasting, defeating fee/lottery/selection logic that assumes unpredictable randomness - (File: x/evm/keeper/keeper.go)

### Summary
Sei's EVM `BlockContext.Random` field (the value returned to contracts via the `PREVRANDAO`/`DIFFICULTY` opcode, EIP-4399) is not derived from any unpredictable, block-proposer-committed entropy source. It is computed as `keccak256(blockHeader.Time)` — a pure hash of the block timestamp — inside `Keeper.GetVMBlockContext`. [1](#0-0) 

### Finding Description
On real Ethereum post-merge, `block.prevrandao`/`DIFFICULTY` returns the RANDAO mix — an accumulated, validator-committed value that is unknown to anyone (including the proposer) until very close to the block it seeds, which is why many contracts use it as a (weak but reasonably unpredictable) randomness source for lotteries, weighted selection, or "who gets the discount / who gets the rare item" logic.

Sei substitutes this with `crypto.Keccak256Hash(ctx.BlockHeader().Time.MarshalBinary())`:
```go
// Use hash of block timestamp as info for PREVRANDAO
r, err := ctx.BlockHeader().Time.MarshalBinary()
...
rh := crypto.Keccak256Hash(r)
...
Random: &rh,
``` [2](#0-1) 

The block timestamp is a low-entropy, externally observable/derivable value (it tracks real time in fixed-size increments and is visible in every block header). Because the "random" value is a deterministic function of a single public, low-entropy input, any unprivileged party can:
1. Query the current/pending block timestamp via the public JSON-RPC (`eth_getBlockByNumber`) or simply predict it (blocks arrive at a known, roughly fixed cadence).
2. Locally compute `keccak256(timestamp)` off-chain, with zero cost and zero risk, to know in advance exactly what value any contract's `block.prevrandao` read will return in the next block.
3. Alternatively, use the public `eth_call`/`eth_estimateGas` simulation RPC surface (`evmrpc/simulate.go`, `SimulationAPI.Call`) to execute the exact target contract call against the latest/pending state for free and observe the outcome before deciding to submit a real transaction.

This is a stronger version of the report's bug class: the original NFTX finding required actually submitting and (Flashbots-)reverting a transaction to discover an unfavorable outcome at zero cost. Here, the "randomness" itself is precomputable/observable off-chain with no transaction at all, so any contract deployed on Sei's EVM that relies on `block.prevrandao`/`DIFFICULTY` for outcome selection (fee waivers, reward/NFT selection, raffle winners, weighted routing, discount eligibility, etc., i.e. exactly the same use pattern demonstrated in the repository's own test contract) can be reliably gamed: an attacker submits only when the precomputed hash resolves in their favor, and abstains (at zero cost) otherwise. The repository's own `EVMCompatibilityTester.sol` demonstrates the intended usage pattern of `block.prevrandao` as an entropy source for on-chain logic, confirming this is a supported/expected pattern for dApps built on Sei's EVM. [3](#0-2) [4](#0-3) 

### Impact Explanation
Any protocol built on Sei's EVM that uses `block.prevrandao`/`DIFFICULTY` as a randomness source for economically meaningful outcomes (fee determination, reward/NFT distribution, weighted matching, raffle-style mechanisms) is exposed to guaranteed, cost-free gaming, because the "random" seed is a public, deterministic function of the block timestamp rather than unpredictable RANDAO-style entropy. This enables fee/reward abuse and unfair value extraction at the expense of honest users/protocols, matching the accepted medium-risk impact category ("fee or refund abuse") from the referenced report's bug class. Unlike Ethereum mainnet (where PREVRANDAO has genuine, if imperfect, unpredictability), Sei's implementation offers effectively none.

### Likelihood Explanation
Likelihood is high for any contract that adopts the "standard" EVM randomness pattern on Sei: no special privileges are needed, the public JSON-RPC (`eth_getBlockByNumber`, `eth_call`) is sufficient to predict or verify the outcome before spending gas, and the block timestamp has very low entropy and predictable cadence. The vulnerability is triggered purely by calling public RPC/EVM interfaces available to any unprivileged client.

### Recommendation
Do not derive `BlockContext.Random` from the block timestamp alone. If a randomness beacon is unavailable in Sei's consensus, either: (a) clearly document that `PREVRANDAO`/`DIFFICULTY` MUST NOT be used as a security-relevant randomness source on Sei and is provided for EVM-compatibility only, or (b) seed it from a higher-entropy, harder-to-predict input that is not fully known before the block is sealed (e.g., a hash combining the previous block's app hash/committed state root together with additional block-level entropy inaccessible to callers in advance), and evaluate adopting a genuine commit-reveal/VRF pattern for any first-party (precompile-level) primitives that expose randomness to CosmWasm/EVM contracts.

### Proof of Concept
1. Deploy (or identify) any EVM contract on Sei that uses `block.prevrandao` for outcome selection, e.g. the pattern demonstrated in `getBlockProperties()`/`useGas()` in `contracts/src/EVMCompatibilityTester.sol`.
2. As an unprivileged client, call the public `eth_getBlockByNumber("pending", false)` (or `"latest"`) RPC to read the current/anticipated block timestamp.
3. Compute `keccak256(abi.encodePacked(timestamp))` locally (mirroring `crypto.Keccak256Hash(ctx.BlockHeader().Time.MarshalBinary())` in `x/evm/keeper/keeper.go`) to derive the exact value that will be returned by `block.prevrandao`/`DIFFICULTY` for the transaction's execution block.
4. Alternatively, use the public `eth_call` simulation endpoint (`evmrpc/simulate.go: SimulationAPI.Call`) to execute the target function against the current state for free and observe the resulting outcome.
5. Only broadcast the real, fee-paying transaction if the precomputed/simulated outcome is favorable; abstain otherwise at zero cost — defeating any fairness or fee-avoidance assumption the contract relies on `block.prevrandao` to provide.

**Uncertainty:** I was unable to fully verify within the index whether any additional, harder-to-predict entropy (e.g., from Autobahn/consensus layer or `giga` block-hash pipeline) is mixed into the EVM block context at a different call site, since `giga/deps/xevm/keeper/keeper.go` also references `Difficulty`/PREVRANDAO but its exact contents were not retrieved in this session. If a Devin session is available, it would be worth confirming the giga (parallel-execution) path uses the identical timestamp-only randomness source before finalizing remediation scope.

### Citations

**File:** x/evm/keeper/keeper.go (L274-313)
```go

	// Use hash of block timestamp as info for PREVRANDAO
	r, err := ctx.BlockHeader().Time.MarshalBinary()
	if err != nil {
		return nil, err
	}
	rh := crypto.Keccak256Hash(r)

	txfer := func(db vm.StateDB, sender, recipient common.Address, amount *uint256.Int) {
		if IsPayablePrecompile(&recipient) {
			state.TransferWithoutEvents(db, sender, recipient, amount)
		} else {
			core.Transfer(db, sender, recipient, amount)
		}
	}
	var baseFee *big.Int
	if ctx.ChainID() == Pacific1ChainID && ctx.BlockHeight() < 114945913 {
		baseFee = k.GetBaseFeePerGas(ctx).TruncateInt().BigInt()
	} else {
		baseFee = k.GetNextBaseFeePerGas(ctx).TruncateInt().BigInt()
	}

	return &vm.BlockContext{
		CanTransfer: core.CanTransfer,
		Transfer:    txfer,
		GetHash:     k.GetHashFn(ctx),
		Coinbase:    coinbase,
		GasLimit: func() uint64 {
			if ctx.ConsensusParams() != nil && ctx.ConsensusParams().Block != nil {
				return uint64(ctx.ConsensusParams().Block.MaxGas) //nolint:gosec
			}
			return DefaultBlockGasLimit
		}(),
		BlockNumber: big.NewInt(ctx.BlockHeight()),
		Time:        uint64(ctx.BlockHeader().Time.Unix()), //nolint:gosec
		Difficulty:  utils.Big0,                            // only needed for PoW
		BaseFee:     baseFee,
		BlobBaseFee: utils.Big1, // Cancun not enabled
		Random:      &rh,
	}, nil
```

**File:** contracts/src/EVMCompatibilityTester.sol (L119-128)
```text
    function getBlockProperties() public view returns (bytes32 blockHash, address payable coinbase, uint prevrandao, uint gaslimit, uint number, uint timestamp) {
        blockHash = blockhash(block.number - 1);
        coinbase = block.coinbase;
        prevrandao = block.prevrandao;
        gaslimit = block.gaslimit;
        number = block.number;
        timestamp = block.timestamp;

        return (blockHash, coinbase, prevrandao, gaslimit, number, timestamp);
    }
```

**File:** contracts/src/EVMCompatibilityTester.sol (L198-211)
```text
    // useGas will at least use gasToUse amount of gas
    function useGas(uint256 gasToUse) public {
        // while gasleft() > gasUse, use use storage to use gas
        uint256 counter = 0;
        uint256 startGas = gasleft();
        uint256 gasUsed = 0;
        while (gasUsed < gasToUse) {
            uint256 randomNumber = uint256(keccak256(abi.encodePacked(block.number, block.prevrandao, counter)));
            counter++;
            gasGuzzler[randomNumber] = randomNumber;
            uint256 endGas = gasleft();
            gasUsed = startGas - endGas;
        }
    }
```

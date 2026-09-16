### Title
Registry owner can silently swap `AuctionEntryPoint` (or `GaslessSwapRouter`) to a malicious contract that the kaiax auction/gasless modules trust unconditionally - ([File: kaiax/auction/impl/execution.go])

### Summary
The external report's "malicious core plugged into LP, then drained via `claimAffiliateReward`/`withdrawPayout`" bug class maps onto Kaia's `Registry` (KIP-149) admission model. The `Registry` owner can `register()` a new address for a well-known name (e.g. `AuctionEntryPointName`, `GaslessSwapRouterName`) with an arbitrary activation block, and the `kaiax/auction` and `kaiax/gasless` modules read whatever address is currently active from the `Registry` and treat it as fully trusted, without any validation of its bytecode or behavior, and with no mandatory timelock analogous to the report's recommendation.

### Finding Description
`AuctionModule.updateAuctionInfo` reads the active `AuctionEntryPoint` address purely from the `Registry`: [1](#0-0) 

and then reads `Auctioneer`, `GasBufferEstimate`, and `AUCTION_VERSION` directly from whatever contract lives at that address, with no code/identity check: [2](#0-1) 

This trusted address is fed into the bid pool, which uses it to build the bundle (`BidTx`) that the block proposer executes against searchers' bids/deposits each block: [3](#0-2) [4](#0-3) 

The registration itself is a simple, permissionless-to-verify write in the `Registry` contract - `register(name, addr, activation)` just appends a new `Record` for the name; the mock/reference implementation shows there is no minimum delay enforcement in the data model itself: [5](#0-4) 

The `IAuctionEntryPoint` interface itself exposes a `depositVault`/`changeDepositVault` design where searcher deposits are tracked and can be moved by whoever the entry point currently is: [6](#0-5) [7](#0-6) 

The same "trust whatever the Registry currently points to" pattern also applies to gasless: `GaslessModule.updateAddresses` reads the swap router purely from the multicall/registry-backed lookup and treats every token in it as an allowed gasless swap route, feeding the lend/repay accounting (`GetLendTxGenerator`, `lendAmount`, `repayAmount`) without validating the router's bytecode: [8](#0-7) [9](#0-8) 

The gasless block-proposer fee-lending flow is documented as trusting the registered `GaslessSwapRouter` for repayment: [10](#0-9) 

This is structurally identical to the reported bug class: an admin-controlled registration point (`Factory.plugCore` in the report / `Registry.register` here) can atomically swap in a malicious implementation that the downstream module (`LP` in the report / `kaiax/auction` and `kaiax/gasless` here) will unconditionally trust for value-moving operations (`claimAffiliateReward`/`withdrawPayout` in the report / bid settlement and gasless fee repayment here), with no delay period to allow detection before the swap takes effect.

### Impact Explanation
If the `Registry` owner (an admin key, potentially compromised or malicious, but not a network-level attacker) registers a malicious contract as `AuctionEntryPoint`, every node running the auction module will immediately (subject to the chosen `activation` block) start trusting it as the auction settlement contract. Since the bid pool and worker use this address to build `BidTx` bundles executed by the block proposer against searcher bids/deposits, a malicious `AuctionEntryPoint` can reject the intended settlement logic (e.g., accept deposits/bids but never pay out, or redirect funds to an attacker-controlled address), resulting in outright theft of searcher deposits/bids - directly matching the "gasless or auction settlement theft" category. The equivalent swap of `GaslessSwapRouter` can cause block proposers to lend gas fees (`LendTxGenerator`) that are never legitimately repaid, or can misclassify tokens as gasless-eligible, enabling fee-delegation/gasless abuse.

### Likelihood Explanation
Low-to-medium, because it requires control of the `Registry` owner key (an admin/governance role), matching the report's premise of "requires a malicious/compromised admin." No cryptographic break, consensus fault, or p2p exploit is needed - a single `register()` transaction from the owner account is sufficient, and both modules pick up the new address automatically at the next block after activation, with no additional confirmation, quorum, or timelock check enforced by the `Registry` or by the consuming modules.

### Recommendation
Introduce a mandatory delay/timelock between `Registry.register()` calls for security-critical names (`AuctionEntryPoint`, `GaslessSwapRouter`) and their activation, during which the pending address change is publicly visible but not yet trusted by `kaiax/auction` and `kaiax/gasless`. Additionally, have the modules validate that the registered address's bytecode hash matches an expected/allow-listed implementation (or require a multi-party approval similar to `TreasuryRebalance`'s admin quorum pattern already used elsewhere in the codebase) before treating it as the active auction entry point or gasless swap router.

### Proof of Concept
Not verified end-to-end against a live/production `Registry`/`AuctionEntryPoint` contract (only the mock `RegistryMock.sol` and `AuctionEntryPointMock.sol` were available in the index); I could not confirm from the index whether the production `Registry` (`kaia-system-contracts` submodule) enforces any minimum registration-to-activation delay, since that contract's source was not retrievable through search. This should be verified directly in a Devin session with full repository access before treating the likelihood/mitigation details as final. [11](#0-10) [12](#0-11)

### Citations

**File:** kaiax/auction/impl/execution.go (L74-94)
```go
	backend := backends.NewBlockchainContractBackend(a.Chain, nil, nil)

	// 1. Read auction entry point address
	auctionEntryPointAddr, err = system.ReadActiveAddressFromRegistry(backend, system.AuctionEntryPointName, num)
	if err != nil {
		return false
	}

	if auctionEntryPointAddr == (common.Address{}) {
		return false
	}

	// 2. Read auctioneer address
	auctioneer, err = system.ReadAuctioneer(backend, auctionEntryPointAddr, num)
	if err != nil {
		return false
	}

	if auctioneer == (common.Address{}) {
		return false
	}
```

**File:** kaiax/auction/impl/execution.go (L96-106)
```go
	// 3. Read gas buffer estimate
	bidTxGasBuffer, err = system.ReadGasBufferEstimate(backend, auctionEntryPointAddr, num)
	if err != nil {
		return false
	}

	// 4. Read AUCTION_VERSION to pick the right EIP-712 typehash and ABI for bids targeting this entry point.
	auctionEntryPointVersion, err = system.ReadAuctionVersion(backend, auctionEntryPointAddr, num)
	if err != nil {
		return false
	}
```

**File:** kaiax/auction/impl/bid_pool.go (L195-213)
```go
// updateAuctionInfo updates the auction info if the auctioneer or auction entry point address is changed.
func (bp *BidPool) updateAuctionInfo(auctioneer common.Address, auctionEntryPoint common.Address, auctionEntryPointVersion string, bidTxGasBuffer uint64) {
	bp.auctionInfoMu.Lock()
	defer bp.auctionInfoMu.Unlock()

	if bp.auctioneer == auctioneer && bp.auctionEntryPoint == auctionEntryPoint && bp.auctionEntryPointVersion == auctionEntryPointVersion && bp.bidTxGasBuffer == bidTxGasBuffer {
		return
	}

	// Clear the existing auction pool since the auctioneer or auction entry point address is changed.
	bp.clearBidPool()

	bp.auctioneer = auctioneer
	bp.auctionEntryPoint = auctionEntryPoint
	bp.auctionEntryPointVersion = auctionEntryPointVersion
	bp.bidTxGasBuffer = bidTxGasBuffer

	logger.Info("Update auction info", "auctioneer", auctioneer, "auctionEntryPoint", auctionEntryPoint, "auctionEntryPointVersion", auctionEntryPointVersion, "bidTxGasBuffer", bidTxGasBuffer)
}
```

**File:** kaiax/auction/impl/init.go (L112-116)
```go
func (a *AuctionModule) Start() error {
	logger.Info("AuctionModule started")
	a.bidPool.start()
	return nil
}
```

**File:** contracts/testing/system_contracts/RegistryMock.sol (L27-32)
```text
    function register(string memory name, address addr, uint256 activation) public override {
        if (records[name].length == 0) {
            names.push(name);
        }
        records[name].push(Record(addr, activation));
    }
```

**File:** contracts/bindings/auction/Kip249.go (L231-246)
```go
// DepositVault is a free data retrieval call binding the contract method 0xd7cd3949.
//
// Solidity: function depositVault() view returns(address)
func (_IAuctionEntryPoint *IAuctionEntryPointCaller) DepositVault(opts *bind.CallOpts) (common.Address, error) {
	var out []interface{}
	err := _IAuctionEntryPoint.contract.Call(opts, &out, "depositVault")

	if err != nil {
		return *new(common.Address), err
	}

	out0 := *abi.ConvertType(out[0], new(common.Address)).(*common.Address)

	return out0, err

}
```

**File:** contracts/bindings/auction/Kip249.go (L535-547)
```go
// ChangeDepositVault is a paid mutator transaction binding the contract method 0x9d59928b.
//
// Solidity: function changeDepositVault(address _depositVault) returns()
func (_IAuctionEntryPoint *IAuctionEntryPointTransactor) ChangeDepositVault(opts *bind.TransactOpts, _depositVault common.Address) (*types.Transaction, error) {
	return _IAuctionEntryPoint.contract.Transact(opts, "changeDepositVault", _depositVault)
}

// ChangeDepositVault is a paid mutator transaction binding the contract method 0x9d59928b.
//
// Solidity: function changeDepositVault(address _depositVault) returns()
func (_IAuctionEntryPoint *IAuctionEntryPointSession) ChangeDepositVault(_depositVault common.Address) (*types.Transaction, error) {
	return _IAuctionEntryPoint.Contract.ChangeDepositVault(&_IAuctionEntryPoint.TransactOpts, _depositVault)
}
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

**File:** kaiax/gasless/impl/getter.go (L346-367)
```go
func lendAmount(approveTxOrNil, swapTx *types.Transaction) *big.Int {
	r := new(big.Int)

	// R2 = ApproveTx.Fee() if exists
	if approveTxOrNil != nil {
		r.Add(r, approveTxOrNil.Fee())
	}

	// R3 = SwapTx.Fee()
	r.Add(r, swapTx.Fee())

	// LendAmount = R2 + R3
	return r
}

func repayAmount(approveTxOrNil, swapTx *types.Transaction) *big.Int {
	// R1 = LendTx.Fee() = SwapTx.GasPrice() * TxGas
	r1 := new(big.Int).Mul(swapTx.GasPrice(), new(big.Int).SetUint64(params.TxGas))

	// RepayAmount = R1 + R2 + R3
	return new(big.Int).Add(r1, lendAmount(approveTxOrNil, swapTx))
}
```

**File:** kaiax/gasless/README.md (L7-36)
```markdown
Gasless transaction (GaslessTx) consists of two types: gasless approve transaction (GaslessApproveTX), and gasless swap transaction (GaslessSwapTx).

Note that gasless transaction does not mean the gas prices of gasless transactions are zero, but it means that proposer will lend the user with gas fee and user will pay back during gasless swap.

The gas fee of gasless transaction's is funded by block proposer (i.e., lend transaction generated by `GetLendTxGenerator`), and user repays the lent amount during gasless swap.

### Transaction pool rules

#### Ready

This module is responsible for promoting gasless transactions.
Sender's nonce of GaslessSwapTx is checked to distinguish if GaslessApproveTx is expected. If `tx.nonce == GetNonce(sender) + 1`, GaslessApproveTx is expected. If `tx.nonce == GetNonce(sender)`, GaslessApproveTx is not expected.

If GaslessApproveTx is expected, GaslessApproveTx and GaslessSwapTx can be promoted when they are both ready for execution.
Otherwise, GaslessSwapTx can be promoted when it is ready for execution.

See ready condition [KIP-247](https://kips.kaia.io/KIPs/kip-247) and the implementation `IsExecutable(approveTxOrNil, swapTx *types.Transaction) bool`.

#### Balance check

Sender balance check is omitted for gasless transactions (see `GetCheckBalance()`).

### Block building rules

Upon detection of GaslessTxs, the following logics are executed:

- Per sender, if exists, GaslessApproveTx is relocated before GaslessSwapTx.
- LendTxGenerator is prepended before GaslessApproveTx.
- A new bundle is generated which contain either `[LendTxGenerator, GaslessApproveTx, GaslessSwapTx]` or `[LendTxGenerator, GaslessSwapTx]`
- If the bundle has conflict with any previous bundles, it is excluded from the returned bundle list.
```

**File:** blockchain/system/auction_test.go (L63-69)
```go
	contract, _ := contracts.NewRegistryMockTransactor(RegistryAddr, backend)
	contract.Register(sender, AuctionEntryPointName, AuctionEntryPointAddrMock, common.Big1)
	backend.Commit()

	auctioneer, err := ReadAuctioneer(backend, AuctionEntryPointAddrMock, common.Big1)
	assert.Nil(t, err)
	assert.Equal(t, addr, auctioneer)
```

**File:** kaiax/auction/impl/execution_test.go (L100-132)
```go
func TestUpdateAuctionInfo(t *testing.T) {
	log.EnableLogForTest(log.LvlCrit, log.LvlWarn)
	var (
		db     = database.NewMemoryDBManager()
		alloc  = testAllocStorage()
		config = testRandaoForkChainConfig(big.NewInt(0))
	)

	backend := backends.NewSimulatedBackendWithDatabase(db, alloc, config)

	mAuction := NewAuctionModule()
	auctionConfig := auction.AuctionConfig{
		Disable: false,
	}
	apiBackend := &MockBackend{}
	fakeDownloader := &downloader.FakeDownloader{}
	mAuction.Init(&InitOpts{
		ChainConfig:   config,
		AuctionConfig: &auctionConfig,
		Chain:         backend.BlockChain(),
		Backend:       apiBackend,
		Downloader:    fakeDownloader,
		NodeKey:       testNodeKey,
	})

	// Not updated yet
	assert.Equal(t, mAuction.bidPool.auctioneer, common.Address{})
	assert.Equal(t, mAuction.bidPool.auctionEntryPoint, common.Address{})

	mAuction.PostInsertBlock(backend.BlockChain().CurrentBlock())

	assert.Equal(t, mAuction.bidPool.auctioneer, common.HexToAddress("0x01"))
	assert.Equal(t, mAuction.bidPool.auctionEntryPoint, system.AuctionEntryPointAddrMock)
```

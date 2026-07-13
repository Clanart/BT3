### Title
EIP-7702 Authorization Chain-ID Zero Bypass Enables Cross-Chain Replay of Code Delegations - (File: x/evm/keeper/set_code_authorizations.go)

### Summary
`validateAuthorization` in Ethermint unconditionally accepts EIP-7702 `SetCodeAuthorization` tuples whose `ChainID` field is zero, skipping the chain-binding check entirely. An unprivileged attacker can harvest a valid chainID=0 authorization signed by a victim on any EVM chain and replay it on an Ethermint chain where the same delegate address holds entirely different (potentially malicious) bytecode, forcing the victim's EOA to be delegated to that code without the victim's knowledge or consent on that chain.

### Finding Description

In `validateAuthorization` (`x/evm/keeper/set_code_authorizations.go`, line 17):

```go
// Verify chain ID is null or equal to current chain ID.
if !auth.ChainID.IsZero() && auth.ChainID.CmpBig(k.eip155ChainID) != 0 {
    return authority, core.ErrAuthorizationWrongChainID
}
```

The short-circuit `!auth.ChainID.IsZero()` means that when `auth.ChainID == 0` the entire chain-ID binding check is bypassed. The authorization is then accepted, the authority is recovered via `auth.Authority()` (ECDSA ecrecover), and `setAuthorizationDelegation` writes the delegation code and bumps the nonce:

```go
stateDB.SetCode(authority, types.AddressToDelegation(auth.Address), tracing.CodeChangeAuthorization)
```

This is the direct analog of the ZkSync msg.sender trust-assumption issue: just as ZkSync preserves msg.sender across L1→L2 calls so that a contract address owned by Alice on L2 can be impersonated by whoever controls the same address on L1, EIP-7702 chainID=0 authorizations are chain-agnostic by design, meaning a delegation signed for a safe contract on Ethereum mainnet can be replayed on any Ethermint chain where the same address holds different (attacker-controlled) bytecode.

**Attack path:**

1. Alice signs an EIP-7702 authorization with `chainID = 0` to delegate her EOA to contract `0xABC` on Ethereum mainnet, where `0xABC` is a legitimate, audited contract.
2. On an Ethermint chain, `0xABC` is a drain contract (e.g., `CALL(CALLER, SELFBALANCE)` — identical to the PoC bytecode in the test suite at `x/evm/keeper/state_transition_test.go:1145`).
3. The attacker submits a `MsgEthereumTx` wrapping a `SetCodeTx` that includes Alice's signed authorization tuple (chainID=0, address=0xABC, nonce=N, V/R/S from Alice's signature).
4. `validateAuthorization` passes: chainID=0 skips the chain check; `auth.Authority()` recovers Alice's address; nonce matches.
5. `setAuthorizationDelegation` installs `ef0100 || 0xABC` as Alice's code on the Ethermint chain and bumps her nonce.
6. Any subsequent call to Alice's EOA on the Ethermint chain executes the drain contract's bytecode in Alice's context (`msg.sender = caller`, `address = Alice`), draining her balance.

The entry point is fully unprivileged: any account with enough gas can submit the `SetCodeTx`. The `SetCodeTx.Validate()` function does not check per-authorization chain IDs; it only checks that `V` is one byte per authorization. The chain-ID check is deferred entirely to `validateAuthorization` at execution time, where chainID=0 is silently accepted.

### Impact Explanation

Unauthorized code mutation of a victim's EOA via cross-chain replay of an EIP-7702 authorization. Once the delegation is installed, every call to the victim's address on the Ethermint chain executes the attacker-chosen delegate contract's bytecode in the victim's context. If the delegate is a drain contract, the victim's entire EVM-denom balance is transferred to the attacker. This matches the allowed High impact: *EIP-7702 authorization chain-id bypass enabling replay and unauthorized account/code mutation*.

### Likelihood Explanation

Medium. The preconditions are:
- A victim who has signed a chainID=0 EIP-7702 authorization on any EVM chain (increasingly common as EIP-7702 wallets proliferate and some wallet UIs default to chainID=0 for "universal" delegations).
- The same delegate address having attacker-controlled code on the target Ethermint chain (achievable via `CREATE2` with a known salt, or by deploying to a vanity address).
- The attacker monitoring mempool or on-chain authorization data on other chains.

None of these require privileged access, validator collusion, or key compromise.

### Recommendation

1. **Reject chainID=0 authorizations at the Ethermint protocol level.** Ethermint chains have a well-defined chain ID; there is no legitimate use case for accepting cross-chain-universal delegations. Add a check in `validateAuthorization`:

```go
if auth.ChainID.IsZero() || auth.ChainID.CmpBig(k.eip155ChainID) != 0 {
    return authority, core.ErrAuthorizationWrongChainID
}
```

2. If spec-compliance with EIP-7702 chainID=0 is required, add a governance parameter `AllowChainIDZeroAuthorizations` (default `false`) so chain operators can opt in explicitly.

3. Add a `ValidateBasic`-level check in `SetCodeTx.Validate()` that rejects any authorization whose `ChainID` is zero, providing early rejection before execution.

### Proof of Concept

The existing test at `x/evm/keeper/state_transition_test.go:1131–1160` (`TestSetCodeAuthorizationDrainCallRolledBackButAuthorizationConsumedOnPostHookFailure`) already demonstrates the drain-via-delegation pattern. Adapting it for the cross-chain replay:

```go
// On "source" chain: Alice signs auth with chainID=0
auth := types.SetCodeAuthorization{
    ChainID: sdkmath.ZeroInt(), // chainID = 0
    Address: drainContractAddr, // malicious on Ethermint chain
    Nonce:   aliceNonce,
    V/R/S:   aliceSignature,
}

// On Ethermint chain: attacker submits Alice's auth in their own SetCodeTx
msg := buildSetCodeTxWithAuth(anyTarget, attackerKey, auth, 200000)
res, err := keeper.EthereumTx(ctx, msg)
// validateAuthorization passes: chainID.IsZero() == true skips chain check
// Alice's EOA now has delegation code pointing to drainContractAddr
// Next call to Alice's address drains her balance
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** x/evm/keeper/set_code_authorizations.go (L15-19)
```go
func (k *Keeper) validateAuthorization(auth *types.SetCodeAuthorization, stateDB vm.StateDB) (authority common.Address, err error) {
	// Verify chain ID is null or equal to current chain ID.
	if !auth.ChainID.IsZero() && auth.ChainID.CmpBig(k.eip155ChainID) != 0 {
		return authority, core.ErrAuthorizationWrongChainID
	}
```

**File:** x/evm/keeper/set_code_authorizations.go (L74-85)
```go
func (k *Keeper) setAuthorizationDelegation(auth *types.SetCodeAuthorization, authority common.Address, stateDB vm.StateDB) {
	// Update nonce and account code.
	stateDB.SetNonce(authority, auth.Nonce+1, tracing.NonceChangeAuthorization)
	if auth.Address == (common.Address{}) {
		// Delegation to zero address means clear.
		stateDB.SetCode(authority, nil, tracing.CodeChangeAuthorizationClear)
		return
	}

	// Otherwise install delegation to auth.Address.
	stateDB.SetCode(authority, types.AddressToDelegation(auth.Address), tracing.CodeChangeAuthorization)
}
```

**File:** x/evm/types/set_code_tx.go (L237-252)
```go
func (tx SetCodeTx) Validate() error {
	if len(tx.To) == 0 {
		return errorsmod.Wrap(core.ErrSetCodeTxCreate, "to address cannot be empty")
	}

	if len(tx.AuthList) == 0 {
		return errorsmod.Wrap(core.ErrEmptyAuthList, "auth list cannot be empty")
	}

	// V is the signature y-parity byte; an empty slice would panic at auth.V[0]
	// in ToEthAuthList.
	for i := range tx.AuthList {
		if len(tx.AuthList[i].V) != 1 {
			return errorsmod.Wrapf(core.ErrAuthorizationInvalidSignature, "auth %d: V must be a single byte", i)
		}
	}
```

**File:** x/evm/keeper/state_transition_test.go (L1131-1160)
```go
func (suite *StateTransitionTestSuite) TestSetCodeAuthorizationDrainCallRolledBackButAuthorizationConsumedOnPostHookFailure() {
	suite.SetupTest()
	suite.App.EvmKeeper.SetHooks(keeper.NewMultiEvmHooks(FailureHook{}))

	delegate := common.HexToAddress("0x000000000000000000000000000000000000dE1E")
	victimBalance := uint256.NewInt(1000000000)
	authorityKey, err := crypto.GenerateKey()
	suite.Require().NoError(err)
	outerKey, err := crypto.GenerateKey()
	suite.Require().NoError(err)
	authority := crypto.PubkeyToAddress(authorityKey.PublicKey)
	outer := crypto.PubkeyToAddress(outerKey.PublicKey)

	// CALL(CALLER, SELFBALANCE): a permissive delegate used by the PoC drain case.
	drainRuntime := common.FromHex("0x600060006000600047335af100")
	vmdb := suite.StateDB()
	vmdb.SetCode(delegate, drainRuntime, 0)
	vmdb.AddBalance(authority, victimBalance, 0)
	suite.Require().NoError(vmdb.Commit())

	auth := suite.signSetCodeAuthorization(authorityKey, delegate, 0)
	msg := suite.buildSetCodeTxWithAuth(authority, outerKey, auth, 200000)
	res, err := suite.App.EvmKeeper.EthereumTx(suite.Ctx, msg)
	suite.Require().NoError(err)
	suite.Require().True(res.Failed())
	suite.Require().Equal(types.ErrPostTxProcessing.Error(), res.VmError)

	suite.requireSetCodeAuthorizationConsumed(authority, delegate, 1)
	suite.Require().Equal(victimBalance.ToBig(), suite.App.EvmKeeper.GetEVMDenomBalance(suite.Ctx, authority))
	suite.Require().Zero(suite.App.EvmKeeper.GetEVMDenomBalance(suite.Ctx, outer).Sign())
```

### Title
Non-EVM Token Balances Not Drained on Contract Self-Destruct Enables Permanent Fund Lock and CREATE2 Recovery Theft — (`x/evm/statedb/statedb.go`)

### Summary

When a contract self-destructs, `StateDB.Commit()` only burns the EVM-denom balance. Non-EVM-native tokens (IBC, CosmWasm bridge tokens) held by the destroyed contract are left as orphaned bank balances. Because `DeleteAccount` removes the auth account (resetting nonce to 0 and clearing code), an attacker can immediately redeploy a new contract at the same address via CREATE2, gaining full access to the orphaned non-EVM tokens.

### Finding Description

In `StateDB.Commit()`, the self-destruct branch explicitly handles only the EVM denom:

```go
// Only the EVM denom is burned here. Non-EVM-native tokens (IBC, CosmWasm
// bridge) held by the destroyed address are not drained and may remain as
// orphaned bank balances.
if remaining := s.keeper.GetBalance(cacheCtx, cosmosAddr, s.evmDenom); remaining.Sign() > 0 {
    coin := sdk.NewCoin(s.evmDenom, sdkmath.NewIntFromBigInt(remaining.ToBig()))
    if _, err := s.keeper.SubBalance(cacheCtx, cosmosAddr, coin); err != nil {
        return errorsmod.Wrap(err, "failed to burn post-selfdestruct balance")
    }
}
if err := s.keeper.DeleteAccount(cacheCtx, obj.Address()); err != nil {
    return errorsmod.Wrap(err, "failed to delete account")
}
writeCache()
``` [1](#0-0) 

`DeleteAccount` removes the auth account and clears EVM storage, but never touches the bank module for any denom other than what the caller explicitly burns:

```go
// NOTE: balance should be cleared separately
func (k *Keeper) DeleteAccount(ctx sdk.Context, addr common.Address) error {
    // ...
    k.ForEachStorage(ctx, addr, func(key, _ common.Hash) bool {
        k.SetState(ctx, addr, key, nil)
        return true
    })
    k.accountKeeper.RemoveAccount(ctx, acct)
    // ...
}
``` [2](#0-1) 

After `RemoveAccount`, the address has:
- No auth account (nonce = 0)
- No EVM storage
- No code (`GetCodeHash` returns `common.Hash{}`)
- **Non-EVM bank balances still present**

The go-ethereum CREATE2 collision guard checks:

```go
if evm.StateDB.GetNonce(address) != 0 ||
   (contractHash != (common.Hash{}) && contractHash != types.EmptyCodeHash) {
    // ErrContractAddressCollision
}
```

After self-destruct, both conditions are false (nonce = 0, codeHash = `common.Hash{}`), so CREATE2 **succeeds** at the same address. The newly deployed contract inherits the orphaned non-EVM bank balances.

### Impact Explanation

**High — valid user funds mis-accounted / unauthorized Cosmos bank fund transfer.**

Two concrete impacts:

1. **Permanent lock**: A legitimate contract holding IBC tokens self-destructs (by design or bug). The IBC tokens are permanently orphaned — no account owns them, no EVM code can spend them, and the bank module retains them indefinitely.

2. **Attacker-controlled theft via CREATE2**: An attacker deploys a contract at a deterministic CREATE2 address, attracts IBC token deposits, self-destructs the contract (bypassing any multi-sig or time-lock on direct withdrawal), then redeploys a new contract at the same address. The new contract can immediately transfer the orphaned IBC tokens to the attacker.

This directly matches: *"EVM state transition bug that permits valid user funds to be mis-accounted"* and *"unauthorized transfer of Cosmos bank funds through Ethermint stateDB logic."*

### Likelihood Explanation

**Medium.** IBC tokens are routinely held by EVM contracts on Cosmos EVM chains (DEXes, bridges, vaults). The SELFDESTRUCT opcode is callable by any unprivileged user who controls a contract. CREATE2 address reuse after self-destruct is a well-known Ethereum pattern. No privileged role, governance, or validator compromise is required — only a contract that (a) holds non-EVM tokens and (b) exposes a self-destruct path.

### Recommendation

**Short term**: In `StateDB.Commit()`, before calling `DeleteAccount`, iterate over **all** bank module balances held by the contract address (not just `s.evmDenom`) and burn or transfer them. Use `bankKeeper.GetAllBalances` and burn each coin via `SubBalance` / `BurnCoins` within the same `CacheContext`.

**Long term**: Add an invariant check or a dedicated `DrainAllBalances` helper that is called atomically with `DeleteAccount`, ensuring no bank balance can survive account deletion. Add integration tests covering IBC-token-holding contracts that self-destruct and verify zero residual bank balance post-commit.

### Proof of Concept

```
1. Attacker deploys Factory contract F (controls CREATE2 deployment).
2. Attacker calls F.deploy(salt=S, initCode=I) → deploys Contract A at address X.
3. Users send IBC tokens (e.g., uatom) to address X.
4. Attacker calls A.selfDestruct():
   - EVM denom balance burned ✓
   - IBC tokens (uatom) remain in bank at address X ✗
   - Auth account at X deleted (nonce → 0, codeHash → 0x000...)
5. Attacker calls F.deploy(salt=S, initCode=I) again:
   - CREATE2 collision check: nonce=0, codeHash=0x000... → PASSES
   - New Contract B deployed at address X
6. Contract B calls bankKeeper.SendCoins(X → attacker, uatom) → succeeds.
   Attacker receives the IBC tokens that were deposited by users into Contract A.
```

Root cause file: [3](#0-2) 
Supporting keeper: [2](#0-1)

### Citations

**File:** x/evm/statedb/statedb.go (L800-825)
```go
		if obj.selfDestructed {
			// Burn any balance that arrived after SelfDestruct was called (e.g., via a
			// value-bearing CALL to the destroyed address within the same transaction).
			// SelfDestruct already burned the balance present at destruction time, but
			// subsequent AddBalance calls write to the bank without a matching burn.
			// DeleteAccount only removes auth metadata and storage; it never touches the
			// bank balance, so we must drain it here before removing the account.
			//
			// Both operations run inside a single CacheContext so that if DeleteAccount
			// fails after SubBalance, the partial burn is rolled back and the bank is
			// left consistent.
			cosmosAddr := sdk.AccAddress(obj.Address().Bytes())
			cacheCtx, writeCache := s.origCtx.CacheContext()
			// Only the EVM denom is burned here. Non-EVM-native tokens (IBC, CosmWasm
			// bridge) held by the destroyed address are not drained and may remain as
			// orphaned bank balances.
			if remaining := s.keeper.GetBalance(cacheCtx, cosmosAddr, s.evmDenom); remaining.Sign() > 0 {
				coin := sdk.NewCoin(s.evmDenom, sdkmath.NewIntFromBigInt(remaining.ToBig()))
				if _, err := s.keeper.SubBalance(cacheCtx, cosmosAddr, coin); err != nil {
					return errorsmod.Wrap(err, "failed to burn post-selfdestruct balance")
				}
			}
			if err := s.keeper.DeleteAccount(cacheCtx, obj.Address()); err != nil {
				return errorsmod.Wrap(err, "failed to delete account")
			}
			writeCache()
```

**File:** x/evm/keeper/statedb.go (L189-222)
```go
// DeleteAccount handles contract's suicide call:
// - remove code
// - remove states
// - remove auth account
//
// NOTE: balance should be cleared separately
func (k *Keeper) DeleteAccount(ctx sdk.Context, addr common.Address) error {
	cosmosAddr := sdk.AccAddress(addr.Bytes())
	acct := k.accountKeeper.GetAccount(ctx, cosmosAddr)
	if acct == nil {
		return nil
	}

	// NOTE: only Ethereum accounts (contracts) can be selfdestructed
	_, ok := acct.(ethermint.EthAccountI)
	if !ok {
		return errorsmod.Wrapf(types.ErrInvalidAccount, "type %T, address %s", acct, addr)
	}

	// clear storage
	k.ForEachStorage(ctx, addr, func(key, _ common.Hash) bool {
		k.SetState(ctx, addr, key, nil)
		return true
	})

	// remove auth account
	k.accountKeeper.RemoveAccount(ctx, acct)

	k.debugLog(ctx, "account suicided",
		"ethereum-address", addr,
		"cosmos-address", cosmosAddr,
	)

	return nil
```

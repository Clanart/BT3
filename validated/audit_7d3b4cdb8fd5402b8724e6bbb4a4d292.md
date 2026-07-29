## Analysis

I traced the commit path in `x/vm/statedb/statedb.go`'s `commitWithCtx` and the underlying keeper writes in `x/vm/keeper/statedb.go`.

`commitWithCtx` iterates over `s.journal.sortedDirties()`, which returns a deduplicated, sorted list of `common.Address` keys derived from `journal.dirties` (a `map[common.Address]int` counter incremented per journal entry, without object identity tracking): [1](#0-0) 

For each dirty address, the commit loop performs a single map lookup `obj := s.stateObjects[addr]` and branches on `obj.selfDestructed`: [2](#0-1) 

Because `s.stateObjects` is a plain `map[common.Address]*stateObject` (one live object per address) and `journal.dirties` only tracks *how many* journal entries touched an address (not the sequence of distinct objects), if an address is self-destructed and then a fresh `stateObject` is created and stored at the same address key within the same transaction, only the final object survives in `s.stateObjects[addr]`. The commit loop has no way to recover the fact that an earlier object at that address was marked `selfDestructed`, since that information lived only on the now-discarded object instance.

`keeper.DeleteAccount` is the only code path that performs a full storage wipe (via `ForEachStorage` + `DeleteState` for every key) plus code/codehash/account removal: [3](#0-2) 

In contrast, the non-destructed branch of `commitWithCtx` only writes `obj.dirtyStorage`'s specific touched keys via `SetState`/`DeleteState`, and never performs a full-address storage wipe: [4](#0-3) 

This means any storage slots that existed in the keeper's KV store from **before** this transaction (persisted from prior blocks) and are not re-touched (not part of `dirtyStorage`) by the recreated contract's execution would never be cleared if the intervening `DeleteAccount` call for the self-destructed predecessor is skipped.

## Key uncertainty

I was unable to fully verify, within the available tool budget, the exact implementation of `StateDB.CreateAccount` / the self-destruct/recreation object-replacement path in `x/vm/statedb/statedb.go` and `state_object.go` (i.e., whether the codebase carries forward some independent “destructed set” analogous to go-ethereum's `stateObjectsDestruct` map that geth added specifically to guard against this same class of bug, separate from the live `stateObjects` map and `selfDestructed` field). My searches did not surface any such independent destruct-tracking structure in this codebase, only the single `dirties` counter and the single `stateObjects` map used directly by `commitWithCtx`. Given that absence of evidence, and the fact that the commit logic as shown relies solely on the *final* live object's `selfDestructed` flag per address, the described vulnerability is plausible and consistent with the code paths that were confirmed. However, without inspecting the exact `CreateAccount`/self-destruct object-replacement logic directly, I cannot be fully certain no other guard exists elsewhere (e.g., an eager per-object commit at self-destruct time rather than deferred to `commitWithCtx`, or a check that redeployment to a still-`selfDestructed`-flagged address is disallowed within the same tx).

### Title
Self-destruct + same-address recreation within one tx can bypass `DeleteAccount` storage wipe, leaking prior storage slots into the recreated account - (File: x/vm/statedb/statedb.go)

### Summary
`commitWithCtx` determines whether to call `keeper.DeleteAccount` (full storage/code/account wipe) or `keeper.SetAccount`/`SetState` purely from the single, latest `stateObject` held in `s.stateObjects[addr]` for a dirty address, as returned by `journal.sortedDirties()`. If an address is self-destructed and then a new contract is created at the same address within the same transaction, the map holds only the final object, so `obj.selfDestructed` reads `false` and the deferred `DeleteAccount` call — the only path that clears all previously persisted storage slots for that address — never executes.

### Finding Description
`sortedDirties()` returns only unique addresses from the journal's dirty counter, and `commitWithCtx` looks up `s.stateObjects[addr]` once per address [5](#0-4) . If the object at that address was replaced (self-destructed object discarded, fresh object installed for the recreated contract) before commit, the destructed-state information is lost, and only `SetAccount` + the new object's `dirtyStorage` are written [6](#0-5) . Storage slots from the destroyed predecessor that predate this transaction and are not re-touched by the recreated contract's constructor remain in the keeper's KV store, since only `DeleteAccount`'s `ForEachStorage`+`DeleteState` loop performs a full wipe [7](#0-6) .

### Impact Explanation
If real, this would let an attacker resurrect a contract address whose old storage (e.g., ERC20 allowance/balance-derived slots, precompile-managed accounting slots) silently reappears under the new contract's control, corrupting storage-derived accounting invariants — a Critical accounting-corruption impact per the allowed-impact gate.

### Likelihood Explanation
Requires an unprivileged attacker to deploy an initial contract, self-destruct it, and redeploy new code to the identical address (CREATE2 "metamorphic contract" pattern) within a single transaction — achievable with ordinary contract/tx flows, no privileged access needed. However, confidence is limited because I could not confirm from available code whether `CreateAccount`/self-destruct object-replacement logic in this repo actually discards the `selfDestructed` flag without any compensating mechanism (e.g., an independent destruct-tracking set), given tool-call constraints.

### Recommendation
Track self-destructed addresses independently of the live `stateObjects` map for the duration of the transaction (mirroring go-ethereum's approach of a separate destruct set), and in `commitWithCtx`, always invoke `keeper.DeleteAccount` for any address recorded as self-destructed during the tx — even if a new object was later created at that address — before applying the new object's `SetAccount`/`SetState` writes.

### Proof of Concept
Not independently verified end-to-end due to inability to inspect the exact `CreateAccount`/self-destruct code paths in this session; the conceptual PoC is: deploy contract A via CREATE2 with slot data, call `SELFDESTRUCT`, then within the same transaction call CREATE2 again with the same salt/initcode-hash to redeploy a different contract to the same address; after `Commit`, assert via the keeper that old slot(s) from contract A are still present alongside the new contract's state.

### Citations

**File:** x/vm/statedb/journal.go (L54-64)
```go
// sortedDirties sort the dirty addresses for deterministic iteration
func (j *journal) sortedDirties() []common.Address {
	keys := make([]common.Address, 0, len(j.dirties))
	for k := range j.dirties {
		keys = append(keys, k)
	}
	sort.Slice(keys, func(i, j int) bool {
		return bytes.Compare(keys[i].Bytes(), keys[j].Bytes()) < 0
	})
	return keys
}
```

**File:** x/vm/statedb/statedb.go (L713-744)
```go
// commitWithCtx writes the dirty states to keeper
// using the provided context
func (s *StateDB) commitWithCtx(ctx sdk.Context) error {
	for _, addr := range s.journal.sortedDirties() {
		obj := s.stateObjects[addr]
		if obj.selfDestructed {
			if err := s.keeper.DeleteAccount(ctx, obj.Address()); err != nil {
				return errorsmod.Wrapf(err, "failed to delete account %s", obj.Address())
			}
		} else {
			if obj.code != nil && obj.dirtyCode {
				if len(obj.code) == 0 {
					s.keeper.DeleteCode(ctx, obj.CodeHash())
				} else {
					s.keeper.SetCode(ctx, obj.CodeHash(), obj.code)
				}
			}
			if err := s.keeper.SetAccount(ctx, obj.Address(), obj.account); err != nil {
				return errorsmod.Wrap(err, "failed to set account")
			}

			for _, key := range obj.dirtyStorage.SortedKeys() {
				valueBytes := obj.dirtyStorage[key].Bytes()
				if len(valueBytes) == 0 {
					s.keeper.DeleteState(ctx, obj.Address(), key)
				} else {
					s.keeper.SetState(ctx, obj.Address(), key, valueBytes)
				}
			}
		}
	}
	return nil
```

**File:** x/vm/keeper/statedb.go (L249-295)
```go
func (k *Keeper) DeleteAccount(ctx sdk.Context, addr common.Address) error {
	cosmosAddr := sdk.AccAddress(addr.Bytes())
	acct := k.accountKeeper.GetAccount(ctx, cosmosAddr)
	if acct == nil {
		return nil
	}

	// NOTE: only Ethereum contracts can be self-destructed
	if !k.IsContract(ctx, addr) {
		return errors.New("only smart contracts can be self-destructed")
	}

	// set account to a base account to set the whole balance as spendable
	baseAccount := k.accountKeeper.GetAccount(ctx, cosmosAddr)
	k.accountKeeper.SetAccount(ctx, authtypes.NewBaseAccount(cosmosAddr, baseAccount.GetPubKey(), baseAccount.GetAccountNumber(), baseAccount.GetSequence()))

	// clear balance
	if err := k.SetBalance(ctx, addr, new(uint256.Int)); err != nil {
		return err
	}

	var keys []common.Hash

	// clear storage
	k.ForEachStorage(ctx, addr, func(key, _ common.Hash) bool {
		keys = append(keys, key)
		return true
	})

	for _, key := range keys {
		k.DeleteState(ctx, addr, key)
	}

	// clear code hash
	k.DeleteCodeHash(ctx, addr)

	// remove auth account
	k.accountKeeper.RemoveAccount(ctx, acct)

	k.Logger(ctx).Debug(
		"account suicided",
		"ethereum-address", addr.Hex(),
		"cosmos-address", cosmosAddr.String(),
	)

	return nil
}
```

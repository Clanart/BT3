### Title
No zero-address (unspendable puzzle hash) check when a Plot NFT joins a pool, permanently redirecting future pool rewards - ([File: chia/pools/pool_wallet.py])

### Summary
`PoolWallet._verify_pool_state()` / `_verify_pooling_state()` / `_verify_self_pooled()` — the validation routines that gate every pool-state transition (create, `join_pool`/`pw_join_pool`) — never verify that the pool-supplied `target_puzzle_hash` is a real, spendable puzzle hash. The only guard is `if state.target_puzzle_hash is None`, which is dead code because `PoolState.target_puzzle_hash` is typed as a non-optional `bytes32`. There is no check rejecting `bytes32.zeros` or other unspendable/burn-style hashes, mirroring the audited `LMPVault.setRewarder()` bug class (a one-time/rarely-changed destination setter with no zero-address check that can permanently misdirect funds).

### Finding Description
`chia/pools/pool_wallet.py`:
- `_verify_self_pooled()` only checks `pool_url` and `relative_lock_height` [1](#0-0) 
- `_verify_pooling_state()` only checks `relative_lock_height` bounds and non-empty `pool_url` [2](#0-1) 
- `_verify_pool_state()` is the shared entry point and only rejects `target_puzzle_hash is None`, which can never happen for a `bytes32`-typed field [3](#0-2) 
- `_verify_initial_target_state()` is the only caller-facing guard used both at pool-wallet creation (`create_new_pool_wallet_transaction`) [4](#0-3)  and it delegates to the same incomplete `_verify_pool_state`.

The `target_puzzle_hash` used when joining a pool is taken directly from the pool server's untrusted `/pool_info` (or `/v2/pool_info`) HTTP response and passed straight into the RPC request without any sanity check: `target_puzzlehash=bytes32.from_hexstr(json_dict["target_puzzle_hash"])` [5](#0-4) . The wallet RPC (`pw_join_pool`) then builds a new `PoolState` from this value with `create_pool_state(FARMING_TO_POOL, request.target_puzzlehash, ...)` and commits it to the on-chain singleton via `wallet.join_pool(...)` [6](#0-5) . Test code even demonstrates that `bytes32.zeros` is accepted as a valid `target_puzzlehash` for `pw_join_pool` without any rejection [7](#0-6) .

Because `target_puzzle_hash` becomes curried directly into the pooling/waiting-room inner puzzles (`create_pooling_inner_puzzle`/`create_waiting_room_inner_puzzle`) [8](#0-7) , every future farmed/absorbed block reward for that singleton is paid to whatever puzzle hash was accepted, with no possibility of retroactive correction outside of another travel/leave transaction.

### Impact Explanation
If the target puzzle hash accepted at join-time is an all-zero (or otherwise unspendable/burn) hash — whether from a misconfigured or malicious pool server, a client-side bug decoding the pool's response, or manual RPC misuse — every pool reward absorbed by that Plot NFT going forward is paid to an address nobody controls a private key for. This is a direct, unrecoverable loss of farming rewards for the plot-NFT owner, analogous in severity/likelihood profile to the original `setRewarder` finding: rare code path, one bad value, permanent fund loss.

### Likelihood Explanation
Likelihood is low-to-medium: it requires either a buggy/malicious pool server response, an operator manually crafting an RPC call, or a client parsing bug — but nothing in the validated code path prevents it. The `_verify_pool_state` check is written as though it protects against this (`target_puzzle_hash is None`) but is actually inert given the type system, so the intended safety net does not function, which is exactly the class of oversight the reference finding flags.

### Recommendation
In `PoolWallet._verify_pool_state()` (and/or `_verify_pooling_state`/`_verify_self_pooled`), explicitly reject `target_puzzle_hash == bytes32.zeros` (and any other known-unspendable/burn constants used in the codebase, e.g. the `...dead` pattern seen in tests) before allowing pool-wallet creation or `join_pool` transitions to proceed. Apply the same check in `pw_join_pool`'s RPC handler and in `chia/cmds/plotnft_funcs.py join_pool` before trusting a pool's `target_puzzle_hash` response.

### Proof of Concept
1. Create a Plot NFT wallet in `SELF_POOLING` state.
2. Call `pw_join_pool` (RPC `PWJoinPool`) with `target_puzzlehash=bytes32.zeros`, as already exercised (without any error) in `chia/_tests/pools/test_pool_rpc.py` `test_self_pooling_to_pooling`/`test_leave_pool` [7](#0-6) .
3. `_verify_pool_state` passes because `target_puzzle_hash` is never `None`.
4. The singleton transitions to `FARMING_TO_POOL` with an inner puzzle that pays out to the zero puzzle hash.
5. Any block reward subsequently farmed by plots tied to this singleton and absorbed by the pool/farmer is sent to `bytes32.zeros`, permanently unrecoverable.

### Citations

**File:** chia/pools/pool_wallet.py (L135-144)
```python
    @classmethod
    def _verify_self_pooled(cls, state: PoolState) -> str | None:
        err = ""
        if state.pool_url not in {None, ""}:
            err += " Unneeded pool_url for self-pooling"

        if state.relative_lock_height != 0:
            err += " Incorrect relative_lock_height for self-pooling"

        return None if err == "" else err
```

**File:** chia/pools/pool_wallet.py (L146-162)
```python
    @classmethod
    def _verify_pooling_state(cls, state: PoolState) -> str | None:
        err = ""
        if state.relative_lock_height < cls.MINIMUM_RELATIVE_LOCK_HEIGHT:
            err += (
                f" Pool relative_lock_height ({state.relative_lock_height})"
                f"is less than recommended minimum ({cls.MINIMUM_RELATIVE_LOCK_HEIGHT})"
            )
        elif state.relative_lock_height > cls.MAXIMUM_RELATIVE_LOCK_HEIGHT:
            err += (
                f" Pool relative_lock_height ({state.relative_lock_height})"
                f"is greater than recommended maximum ({cls.MAXIMUM_RELATIVE_LOCK_HEIGHT})"
            )

        if state.pool_url in {None, ""}:
            err += " Empty pool url in pooling state"
        return err
```

**File:** chia/pools/pool_wallet.py (L164-181)
```python
    @classmethod
    def _verify_pool_state(cls, state: PoolState) -> str | None:
        if state.target_puzzle_hash is None:
            return "Invalid puzzle_hash"

        if state.version > POOL_PROTOCOL_VERSION:
            return (
                f"Detected pool protocol version {state.version}, which is "
                f"newer than this wallet's version ({POOL_PROTOCOL_VERSION}). Please upgrade "
                f"to use this pooling wallet"
            )

        if state.state == PoolSingletonState.SELF_POOLING.value:
            return cls._verify_self_pooled(state)
        elif state.state in {PoolSingletonState.FARMING_TO_POOL.value, PoolSingletonState.LEAVING_POOL.value}:
            return cls._verify_pooling_state(state)
        else:
            return "Internal Error"
```

**File:** chia/pools/pool_wallet.py (L425-426)
```python
        # Verify Parameters - raise if invalid
        PoolWallet._verify_initial_target_state(initial_target_state)
```

**File:** chia/cmds/plotnft_funcs.py (L391-404)
```python
    pprint(json_dict)
    msg = f"\nWill join pool: {pool_url} with Plot NFT {wallet_info.fingerprint}."
    func = functools.partial(
        wallet_info.client.pw_join_pool,
        PWJoinPool(
            wallet_id=uint32(selected_wallet_id),
            target_puzzlehash=bytes32.from_hexstr(json_dict["target_puzzle_hash"]),
            pool_url=pool_url,
            relative_lock_height=json_dict["relative_lock_height"],
            pool_memoization=Program.fromhex(json_dict.get("pool_memoization", "80")),
            fee=fee,
            push=True,
        ),
        DEFAULT_TX_CONFIG,
```

**File:** chia/wallet/wallet_rpc_api.py (L2941-2965)
```python
    async def pw_join_pool(
        self,
        request: PWJoinPool,
        action_scope: WalletActionScope,
        extra_conditions: tuple[Condition, ...] = tuple(),
    ) -> PWJoinPoolResponse:
        wallet = self.service.wallet_state_manager.wallets[request.wallet_id]

        if isinstance(wallet, PoolWallet):
            pool_wallet_info: PoolWalletInfo = await wallet.get_current_state()
            if (
                pool_wallet_info.current.state == FARMING_TO_POOL.value
                and pool_wallet_info.current.pool_url == request.pool_url
            ):
                raise ValueError(f"Already farming to pool {pool_wallet_info.current.pool_url}")

            new_target_state: PoolState = create_pool_state(
                FARMING_TO_POOL,
                request.target_puzzlehash,
                pool_wallet_info.current.owner_pubkey,
                request.pool_url,
                request.relative_lock_height,
            )

            total_fee = await wallet.join_pool(new_target_state, request.fee, action_scope)
```

**File:** chia/_tests/pools/test_pool_rpc.py (L1004-1014)
```python
        join_pool = await client.pw_join_pool(
            PWJoinPool(
                wallet_id=uint32(wallet_id),
                target_puzzlehash=pool_ph,
                pool_url="https://pool.example.com",
                relative_lock_height=uint32(10),
                fee=uint64(fee),
                push=True,
            ),
            DEFAULT_TX_CONFIG,
        )
```

**File:** chia/pools/pool_puzzles.py (L51-84)
```python
def create_waiting_room_inner_puzzle(
    target_puzzle_hash: bytes32,
    relative_lock_height: uint32,
    owner_pubkey: G1Element,
    launcher_id: bytes32,
    genesis_challenge: bytes32,
    delay_time: uint64,
    delay_ph: bytes32,
) -> Program:
    pool_reward_prefix = bytes32(genesis_challenge[:16] + b"\x00" * 16)
    p2_singleton_puzzle_hash: bytes32 = launcher_id_to_p2_puzzle_hash(launcher_id, delay_time, delay_ph)
    return POOL_WAITING_ROOM_MOD.curry(
        target_puzzle_hash, p2_singleton_puzzle_hash, bytes(owner_pubkey), pool_reward_prefix, relative_lock_height
    )


def create_pooling_inner_puzzle(
    target_puzzle_hash: bytes,
    pool_waiting_room_inner_hash: bytes32,
    owner_pubkey: G1Element,
    launcher_id: bytes32,
    genesis_challenge: bytes32,
    delay_time: uint64,
    delay_ph: bytes32,
) -> Program:
    pool_reward_prefix = bytes32(genesis_challenge[:16] + b"\x00" * 16)
    p2_singleton_puzzle_hash: bytes32 = launcher_id_to_p2_puzzle_hash(launcher_id, delay_time, delay_ph)
    return POOL_MEMBER_MOD.curry(
        target_puzzle_hash,
        p2_singleton_puzzle_hash,
        bytes(owner_pubkey),
        pool_reward_prefix,
        pool_waiting_room_inner_hash,
    )
```

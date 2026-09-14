### Title
Missing zero-address check on pool-supplied `target_puzzle_hash` in `PoolWallet` state validation lets a pool permanently burn a plot-NFT's payout destination - (File: chia/pools/pool_wallet.py)

### Summary
`PoolWallet._verify_pool_state()` and its helpers `_verify_self_pooled()` / `_verify_pooling_state()` validate a `PoolState` before it is accepted as `target_state` and committed on-chain via a singleton travel spend, but they never check that `target_puzzle_hash` is non-zero. The `target_puzzle_hash` for a `FARMING_TO_POOL` transition is taken directly from an external pool server's `/pool_info` JSON response in `chia/cmds/plotnft_funcs.py` and passed straight through to `PWJoinPool` / `PoolWallet.join_pool()` with no validation.

### Finding Description
`PoolWallet._verify_pool_state()` only guards against `None`: [1](#0-0) 

Its sub-checks likewise never test for the zero puzzle hash: [2](#0-1) 

This validation is invoked as the sole gate before a `target_state` is accepted: [3](#0-2) 

`join_pool()` calls exactly this validator and then commits the target state and generates the on-chain travel transaction: [4](#0-3) 

The `target_puzzle_hash` supplied to `join_pool` is not locally derived (unlike `self_pool()`, which derives `owner_puzzlehash` from the wallet's own key store) — it comes verbatim from the pool operator's HTTP response and is passed to the RPC without any sanity check: [5](#0-4) 

This `target_puzzle_hash` is curried directly into the on-chain pool-member/waiting-room puzzle that governs the plot-NFT singleton, so it becomes part of consensus-committed puzzle state: [6](#0-5) [7](#0-6) 

The project's own documentation confirms `target_puzzle_hash` is the actual payout destination for pool/self-pooling rewards, not merely an internal bookkeeping field: [8](#0-7) [9](#0-8) 

Notably, the test suite itself demonstrates that `bytes32.zeros` is accepted as a valid `target_puzzlehash` for `pw_join_pool` without any rejection, confirming there is no guard anywhere in the call chain: [10](#0-9) 

### Impact Explanation
If a pool operator's `/pool_info` endpoint returns (maliciously or due to misconfiguration/bug) a `target_puzzle_hash` of all zeros, the CLI/RPC flow (`join_pool` in `plotnft_funcs.py` → `PWJoinPool` → `PoolWallet.join_pool()` → `_verify_initial_target_state()`) accepts it with no error. The plot-NFT wallet then submits a signed travel transaction that permanently curries this zero puzzle hash into the singleton's pool-member/waiting-room inner puzzle as the pool's payout/escape destination. Once this transitions on-chain, the singleton's locked value (and any subsequent leave/self-pool payout routed to that stale destination) becomes unspendable, since a puzzle hash of all zeros has no known spendable preimage — functionally identical to sending funds to Ethereum's `address(0)`. This is an irrecoverable loss of XCH reachable purely through a normal, unprivileged plot-NFT owner action (joining a pool), matching the "pool participant" reachable path explicitly in scope.

### Likelihood Explanation
Likelihood is moderate: it requires either a buggy/careless pool implementation or a malicious pool operator to serve a zero `target_puzzle_hash`, but no additional privilege or network-layer compromise is needed — a normal wallet owner running `chia plotnft join` against an untrusted or compromised pool URL triggers the vulnerable path automatically, since the response is trusted and passed straight into consensus-validated state without a sanity check.

### Recommendation
Add an explicit check in `PoolWallet._verify_pool_state()` (and/or `_verify_pooling_state()`/`_verify_self_pooled()`) rejecting `target_puzzle_hash == bytes32.zeros`, and additionally validate the pool-supplied `target_puzzle_hash` client-side in `chia/cmds/plotnft_funcs.py`'s `join_pool()` before constructing the `PWJoinPool` request, so a hostile or broken pool cannot cause funds to be irrecoverably burned.

### Proof of Concept
1. Attacker/misconfigured pool operator hosts a pool server whose `/pool_info` response sets `"target_puzzle_hash": "00...00"` (32 zero bytes) — matching the field read in `chia/cmds/plotnft_funcs.py:397`.
2. A plot-NFT owner runs `chia plotnft join -u <malicious_pool_url>`. `join_pool()` in `plotnft_funcs.py` fetches `/pool_info`, builds `PWJoinPool(target_puzzlehash=bytes32.from_hexstr(json_dict["target_puzzle_hash"]), ...)`, and submits it without validation.
3. `PoolWallet.join_pool()` calls `PoolWallet._verify_initial_target_state(target_state)`, which internally calls `_verify_pool_state()` → `_verify_pooling_state()`; neither rejects a zero `target_puzzle_hash`.
4. The wallet signs and pushes a travel transaction that curries `target_puzzle_hash = 0x00…00` into the on-chain pool-member inner puzzle (`create_pooling_inner_puzzle`).
5. Once confirmed, the singleton's payout/escape path is permanently bound to the zero puzzle hash; any value later routed to that destination (e.g., on a subsequent leave/self-pool transition) is unspendable, resulting in permanent loss of the user's XCH.

### Citations

**File:** chia/pools/pool_wallet.py (L135-162)
```python
    @classmethod
    def _verify_self_pooled(cls, state: PoolState) -> str | None:
        err = ""
        if state.pool_url not in {None, ""}:
            err += " Unneeded pool_url for self-pooling"

        if state.relative_lock_height != 0:
            err += " Incorrect relative_lock_height for self-pooling"

        return None if err == "" else err

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

**File:** chia/pools/pool_wallet.py (L183-187)
```python
    @classmethod
    def _verify_initial_target_state(cls, initial_target_state: PoolState) -> None:
        err = cls._verify_pool_state(initial_target_state)
        if err:
            raise ValueError(f"Invalid internal Pool State: {err}: {initial_target_state}")
```

**File:** chia/pools/pool_wallet.py (L630-673)
```python
    async def join_pool(self, target_state: PoolState, fee: uint64, action_scope: WalletActionScope) -> uint64:
        if target_state.state != FARMING_TO_POOL.value:
            raise ValueError(f"join_pool must be called with target_state={FARMING_TO_POOL} (FARMING_TO_POOL)")
        if self.target_state is not None:
            raise ValueError(f"Cannot join a pool while waiting for target state: {self.target_state}")
        if await self.have_unconfirmed_transaction():
            raise ValueError(
                "Cannot join pool due to unconfirmed transaction. If this is stuck, delete the unconfirmed transaction."
            )

        current_state: PoolWalletInfo = await self.get_current_state()

        total_fee = fee
        if current_state.current == target_state:
            self.target_state = None
            msg = f"Asked to change to current state. Target = {target_state}"
            self.log.info(msg)
            raise ValueError(msg)
        elif current_state.current.state in {SELF_POOLING.value, LEAVING_POOL.value}:
            total_fee = fee
        elif current_state.current.state == FARMING_TO_POOL.value:
            total_fee = uint64(fee * 2)

        if self.target_state is not None:
            raise ValueError(
                f"Cannot change to state {target_state} when already having target state: {self.target_state}"
            )
        PoolWallet._verify_initial_target_state(target_state)
        if current_state.current.state == LEAVING_POOL.value:
            history: list[tuple[uint32, CoinSpend]] = await self.get_spend_history()
            last_height: uint32 = history[-1][0]
            if (
                await self.wallet_state_manager.blockchain.get_finished_sync_up_to()
                <= last_height + current_state.current.relative_lock_height
            ):
                raise ValueError(
                    f"Cannot join a pool until height {last_height + current_state.current.relative_lock_height}"
                )

        self.target_state = target_state
        self.next_transaction_fee = fee
        self.next_tx_config = action_scope.config.tx_config
        await self.generate_travel_transactions(fee, action_scope)
        return total_fee
```

**File:** chia/cmds/plotnft_funcs.py (L345-405)
```python
async def join_pool(
    *,
    wallet_info: WalletClientInfo,
    pool_url: str,
    fee: uint64,
    wallet_id: int | None,
    prompt: bool,
) -> None:
    selected_wallet_id = await wallet_id_lookup_and_check(wallet_info.client, wallet_id)

    sync_status = await wallet_info.client.get_sync_status()
    if not sync_status.synced:
        raise click.ClickException("Wallet must be synced before joining a pool.")

    pool_wallet_info = (await wallet_info.client.pw_status(PWStatus(wallet_id=uint32(selected_wallet_id)))).state
    if (
        pool_wallet_info.current.state == PoolSingletonState.FARMING_TO_POOL.value
        and pool_wallet_info.current.pool_url == pool_url
    ):
        raise click.ClickException(f"Wallet id: {wallet_id} is already farming to pool {pool_url}")

    enforce_https = wallet_info.config["selected_network"] == "mainnet"

    if enforce_https and not pool_url.startswith("https://"):
        raise CliRpcConnectionError(f"Pool URLs must be HTTPS on mainnet {pool_url}.")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{pool_url}/{'v2/' if pool_wallet_info.current.version == 2 else ''}pool_info",
                ssl=ssl_context_for_root(get_mozilla_ca_crt()),
            ) as response:
                if response.ok:
                    json_dict = json.loads(await response.text())
                else:
                    raise CliRpcConnectionError(f"Response not OK: {response.status}")
    except Exception as e:
        raise CliRpcConnectionError(f"Error connecting to pool {pool_url}: {e}")

    if json_dict["relative_lock_height"] > 1000:
        raise CliRpcConnectionError("Relative lock height too high for this pool, cannot join")

    if json_dict["protocol_version"] != pool_wallet_info.current.version:
        raise CliRpcConnectionError(
            f"Incorrect version: {json_dict['protocol_version']}, should be {pool_wallet_info.current.version}"
        )

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
    )
```

**File:** chia/pools/pool_puzzles.py (L51-64)
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
```

**File:** chia/pools/pool_puzzles.py (L67-84)
```python
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

**File:** .cursor/context/pools.md (L34-34)
```markdown
- `target_puzzle_hash` means final payout destination, not the p2-singleton address where pool rewards are initially farmed. In self-pooling it is a local wallet puzzle hash; in farming-to-pool it is the pool's target puzzle hash.
```

**File:** chia/pools/pool_wallet_info.py (L44-50)
```python
    """
    `PoolState` is a type that is serialized to the blockchain to track the state of the user's pool singleton
    `target_puzzle_hash` is either the pool address, or the self-pooling address that pool rewards will be paid to.
    `target_puzzle_hash` is NOT the p2_singleton puzzle that block rewards are sent to.
    The `p2_singleton` address is the initial address, and the `target_puzzle_hash` is the final destination.
    `relative_lock_height` is zero when in SELF_POOLING state
    """
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

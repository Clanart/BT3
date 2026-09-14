Confirmed: the wait-period value (`relative_lock_height`) used to prevent a pool member from prematurely leaving and reclaiming rewards pledged to the pool is only checked client-side by `PoolWallet._verify_pooling_state` [1](#0-0) , never enforced by the CLVM puzzle or by consensus/mempool validation. The on-chain waiting-room puzzle simply curries in whatever `relative_lock_height` the owner supplies and asserts it relatively at exit time [2](#0-1) .

### Title
Plot-NFT pool "leave" lockup (`relative_lock_height`) is only client-recommended, not consensus-enforced, letting a pool member quick-exit and redirect pool-pledged rewards to self - (File: chia/pools/pool_wallet.py)

### Summary
Chia's pool-singleton design intentionally forces a delay (`relative_lock_height`) between announcing intent to leave a pool (`LEAVING_POOL`) and actually completing the transition to `SELF_POOLING`/another pool, specifically so a member cannot "cheat by quickly leaving a pool, and claiming a block that was pledged to the pool" [3](#0-2) . `PoolWallet` enforces a `MINIMUM_RELATIVE_LOCK_HEIGHT = 5` / `MAXIMUM_RELATIVE_LOCK_HEIGHT = 1000`, but only inside its own `_verify_pooling_state()` helper, which is invoked from the wallet's own `join_pool()`/`_verify_initial_target_state()` code paths [4](#0-3) [1](#0-0) [5](#0-4) .

This mirrors the reported bug class: an off-chain/administrative recommended waiting period (`decreaseStakeLockupDuration` in Audius) that is not actually enforced by the on-chain mechanism responsible for the protection, allowing the protected party (the malicious service provider / here, the pool member) to bypass it.

### Finding Description
The actual on-chain enforcement of the leave delay is the `AssertHeightRelative` condition baked into the waiting-room puzzle, curried with whatever `relative_lock_height` value the pool-singleton owner supplied when creating the `FARMING_TO_POOL` state: `create_waiting_room_inner_puzzle(... relative_lock_height ...)` [2](#0-1) , and `pool_state_to_inner_puzzle()` builds this same puzzle straight from `pool_state.relative_lock_height` with no bound checking [6](#0-5) .

Nothing in mempool admission, block validation, or the CLVM puzzle itself rejects a `relative_lock_height` of `0` (or any arbitrarily small value) for a `FARMING_TO_POOL` singleton. The `MINIMUM_RELATIVE_LOCK_HEIGHT`/`MAXIMUM_RELATIVE_LOCK_HEIGHT` bounds are pure wallet-RPC/CLI conveniences, only exercised when the official `join_pool` wallet flow or CLI `create_pool_args`/`join_pool` helpers are used (client-side HTTP-fetched pool parameters are also only sanity-checked at the CLI layer) [7](#0-6) [8](#0-7) . A user who crafts and submits their own `CoinSpend`/`WalletSpendBundle` directly (bypassing `PoolWallet.join_pool`/`pw_join_pool`) can set `relative_lock_height=0` when transitioning to `FARMING_TO_POOL`, so that the very next block after declaring `LEAVING_POOL` satisfies the height-relative assertion and the second travel transaction (`self_pool()`/`join_pool()` completion, gated only by comparing `last_height + relative_lock_height` against synced height) succeeds immediately [9](#0-8) [10](#0-9) .

### Impact Explanation
This defeats the sole purpose of the pool-protocol lockup: preventing a farmer from pledging plots to a pool, farming a reward to the pool's `p2_singleton_puzzle_hash`, and then immediately exiting to `SELF_POOLING` before the pool's off-chain infrastructure has a chance to sweep/absorb that reward — after which the (now self-pooling) owner can call `claim_pool_rewards()` and redirect that same reward coin to themselves via `create_absorb_spend` instead of the pool [11](#0-10) . This is a reward-redirection/theft-of-pledged-funds scenario reachable by a single unprivileged plot-NFT owner submitting their own spend bundle, with no cooperation from any other party required.

### Likelihood Explanation
Any plot-NFT owner can independently choose to bypass the official wallet RPC/CLI (which enforces `MINIMUM_RELATIVE_LOCK_HEIGHT`) and construct the `FARMING_TO_POOL`/travel spends directly using `chia/pools/pool_puzzles.py` primitives with an arbitrarily small `relative_lock_height`. No special privileges, races with other users, or network position are needed — only knowledge of the pool puzzle construction, which is public source code.

### Recommendation
Do not rely purely on wallet-side recommendation for `relative_lock_height` bounds. Consider adding minimum-height validation to the pooling puzzles themselves (e.g. having the on-chain member puzzle refuse to curry/accept an escape puzzle hash whose lock height falls below a protocol-wide floor), or otherwise clearly document/flag in the pool protocol (`pool_protocol.py`) and pool server integration that `relative_lock_height` is entirely attacker-controlled and cannot be trusted as an anti-cheat guarantee unless independently verified by the pool operator from the actual singleton puzzle reveal before accepting a farmer, not from the wallet's self-reported/RPC value.

### Proof of Concept
1. Mint a plot-NFT singleton and directly call `PoolWallet.generate_launcher_spend` / low-level `create_travel_spend` (bypassing `join_pool()`/`pw_join_pool`) to transition to `FARMING_TO_POOL` with `pool_state.relative_lock_height = 0`, pointing at a real pool's `target_puzzle_hash`.
2. Farm a block; a pool reward coin lands on `p2_singleton_puzzle_hash`.
3. Immediately submit a `LEAVING_POOL` travel spend, then, one block later, submit the second travel spend to `SELF_POOLING` — the `AssertHeightRelative(height=0)` check in the waiting-room puzzle is trivially satisfied [12](#0-11) .
4. Call `claim_pool_rewards()` while `SELF_POOLING` to sweep the pool-pledged reward coin to the attacker's own `target_puzzle_hash` instead of the pool's [13](#0-12) , all before the pool operator's infrastructure has any chance to react (versus the intended ≥5-block cushion).

### Citations

**File:** chia/pools/pool_wallet.py (L61-72)
```python
@final
@dataclasses.dataclass
class PoolWallet:
    if TYPE_CHECKING:
        from chia.wallet.wallet_protocol import WalletProtocol

        _protocol_check: ClassVar[WalletProtocol] = cast("PoolWallet", None)

    MINIMUM_INITIAL_BALANCE: ClassVar[int] = 1
    MINIMUM_RELATIVE_LOCK_HEIGHT: ClassVar[int] = 5
    MAXIMUM_RELATIVE_LOCK_HEIGHT: ClassVar[int] = 1000
    DEFAULT_MAX_CLAIM_SPENDS: ClassVar[int] = 100
```

**File:** chia/pools/pool_wallet.py (L95-104)
```python
    The ability to change the farm-to target prevents abuse from pools
    by giving the user the ability to quickly change pools, or self-farm.

    The pool is also protected, by not allowing members to cheat by quickly leaving a pool,
    and claiming a block that was pledged to the pool.

    The pooling protocol and smart coin prevents a user from quickly leaving a pool
    by enforcing a wait time when leaving the pool. A minimum number of blocks must pass
    after the user declares that they are leaving the pool, and before they can start to
    self-claim rewards again.
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

**File:** chia/pools/pool_wallet.py (L630-657)
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
```

**File:** chia/pools/pool_wallet.py (L694-704)
```python
        if current_state.current.state == LEAVING_POOL.value:
            total_fee = fee
            history: list[tuple[uint32, CoinSpend]] = await self.get_spend_history()
            last_height: uint32 = history[-1][0]
            if (
                await self.wallet_state_manager.blockchain.get_finished_sync_up_to()
                <= last_height + current_state.current.relative_lock_height
            ):
                raise ValueError(
                    f"Cannot self pool until height {last_height + current_state.current.relative_lock_height}"
                )
```

**File:** chia/pools/pool_wallet.py (L820-827)
```python
        if (
            self.target_state.state in {FARMING_TO_POOL.value, SELF_POOLING.value}
            and pool_wallet_info.current.state == LEAVING_POOL.value
        ):
            leave_height = tip_height + pool_wallet_info.current.relative_lock_height

            # Add some buffer (+2) to reduce chances of a reorg
            if peak_height > leave_height + 2:
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

**File:** chia/pools/pool_puzzles.py (L183-221)
```python
# This spend will use the escape-type spend path for whichever state you are currently in
# If you are currently a waiting inner puzzle, then it will look at your target_state to determine the next
# inner puzzle hash to go to. The member inner puzzle is already committed to its next puzzle hash.
def create_travel_spend(
    last_coin_spend: CoinSpend,
    launcher_coin: Coin,
    current: PoolState,
    target: PoolState,
    genesis_challenge: bytes32,
    delay_time: uint64,
    delay_ph: bytes32,
) -> tuple[CoinSpend, Program]:
    inner_puzzle: Program = pool_state_to_inner_puzzle(
        current,
        launcher_coin.name(),
        genesis_challenge,
        delay_time,
        delay_ph,
    )
    if is_pool_member_inner_puzzle(inner_puzzle):
        # inner sol is key_value_list ()
        # key_value_list is:
        # "p" -> poolstate as bytes
        inner_sol: Program = Program.to([[("p", bytes(target))], 0])
    elif is_pool_waitingroom_inner_puzzle(inner_puzzle):
        # inner sol is (spend_type, key_value_list, pool_reward_height)
        destination_inner: Program = pool_state_to_inner_puzzle(
            target, launcher_coin.name(), genesis_challenge, delay_time, delay_ph
        )
        log.debug(
            f"create_travel_spend: waitingroom: target PoolState bytes:\n{bytes(target).hex()}\n"
            f"{target}"
            f"hash:{shatree_atom(bytes(target))}"
        )
        # key_value_list is:
        # "p" -> poolstate as bytes
        inner_sol = Program.to([1, [("p", bytes(target))], destination_inner.get_tree_hash()])  # current or target
    else:
        raise ValueError
```

**File:** chia/pools/pool_puzzles.py (L436-459)
```python
def pool_state_to_inner_puzzle(
    pool_state: PoolState, launcher_id: bytes32, genesis_challenge: bytes32, delay_time: uint64, delay_ph: bytes32
) -> Program:
    escaping_inner_puzzle: Program = create_waiting_room_inner_puzzle(
        pool_state.target_puzzle_hash,
        pool_state.relative_lock_height,
        pool_state.owner_pubkey,
        launcher_id,
        genesis_challenge,
        delay_time,
        delay_ph,
    )
    if pool_state.state in {LEAVING_POOL.value, SELF_POOLING.value}:
        return escaping_inner_puzzle
    else:
        return create_pooling_inner_puzzle(
            pool_state.target_puzzle_hash,
            escaping_inner_puzzle.get_tree_hash(),
            pool_state.owner_pubkey,
            launcher_id,
            genesis_challenge,
            delay_time,
            delay_ph,
        )
```

**File:** chia/cmds/plotnft_funcs.py (L59-79)
```python
async def create_pool_args(pool_url: str) -> dict[str, Any]:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{pool_url}/pool_info", ssl=ssl_context_for_root(get_mozilla_ca_crt())) as response:
                if response.ok:
                    json_dict: dict[str, Any] = json.loads(await response.text())
                else:
                    raise ValueError(f"Response from {pool_url} not OK: {response.status}")
    except Exception as e:
        raise ValueError(f"Error connecting to pool {pool_url}: {e}")

    if json_dict["relative_lock_height"] > 1000:
        raise ValueError("Relative lock height too high for this pool, cannot join")
    if json_dict["protocol_version"] not in {1, 2}:
        raise ValueError(f"Incorrect version: {json_dict['protocol_version']}, should be 1 or 2")

    header_msg = f"\n---- Pool parameters fetched from {pool_url} ----"
    print(header_msg)
    pprint(json_dict)
    print("-" * len(header_msg))
    return json_dict
```

**File:** chia/cmds/plotnft_funcs.py (L383-389)
```python
    if json_dict["relative_lock_height"] > 1000:
        raise CliRpcConnectionError("Relative lock height too high for this pool, cannot join")

    if json_dict["protocol_version"] != pool_wallet_info.current.version:
        raise CliRpcConnectionError(
            f"Incorrect version: {json_dict['protocol_version']}, should be {pool_wallet_info.current.version}"
        )
```

**File:** chia/pools/plotnft_drivers.py (L590-642)
```python
    def claim_pool_rewards(
        self,
        rewards_to_claim: list[PoolReward],
        reward_delegated_puzzles_and_solutions: list[DelegatedPuzzleAndSolution],
    ) -> list[CoinSpend]:
        if self.pooling:
            raise ValueError("Cannot claim rewards while pooling. If you're a pool, try `forward_pool_rewards`")
        if len(rewards_to_claim) != len(reward_delegated_puzzles_and_solutions):
            raise ValueError("Number of rewards and delegated puzzles and solutions must match")
        dpuz_and_solution = DelegatedPuzzleAndSolution(
            puzzle=Program.to(
                (
                    1,
                    [
                        CreateCoin(
                            puzzle_hash=self.inner_puzzle_hash(),
                            amount=self.coin.amount,
                            memos=[self.singleton_struct.struct_hash()],
                        ).to_program(),
                        *(
                            SendMessage(
                                msg=dpuz_and_sol.puzzle.get_tree_hash(),
                                sender=MessageParticipant(puzzle_hash_committed=self.puzzle_hash(nonce=0)),
                                receiver=MessageParticipant(coin_id_committed=reward.coin.name()),
                            ).to_program()
                            for reward, dpuz_and_sol in zip(rewards_to_claim, reward_delegated_puzzles_and_solutions)
                        ),
                    ],
                )
            ),
            solution=Program.to([]),
        )
        return [
            self.singleton_action_spend(
                inner_solution=self.puzzle_with_restrictions().solve(
                    member_validator_solutions=[],
                    dpuz_validator_solutions=[],
                    member_solution=self.bls_member.solve(),
                    delegated_puzzle_and_solution=dpuz_and_solution,
                )
            ),
            *(
                make_spend(
                    coin=reward.coin,
                    puzzle_reveal=reward.puzzle(),
                    solution=reward.solve(
                        self.inner_puzzle_hash(),
                        delegated_puzzle_and_solution=dpuz_and_sol,
                    ),
                )
                for reward, dpuz_and_sol in zip(rewards_to_claim, reward_delegated_puzzles_and_solutions)
            ),
        ]
```

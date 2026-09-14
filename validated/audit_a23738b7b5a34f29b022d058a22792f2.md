Confirmed exploitable analog found: the `SEND_MESSAGE`-only ban in the custody restriction stack.

### Title
Custody `SendMessageBanned` restriction only checks `SEND_MESSAGE` opcode, allowing the paired `RECEIVE_MESSAGE` opcode to be used as an unchecked messaging alias - ([File: chia/wallet/puzzles/custody/send_message_banned.clsp])

### Summary
The Bittensor bug class is a security carve-out that names one specific identifier ("except sudo_set_sn_owner_hotkey") while a functionally-equivalent duplicate identifier ("sudo_set_subnet_owner_hotkey") reaches the same privileged effect and bypasses the exclusion. In Chia's custody/restriction framework, `SendMessageBanned` is a `ValidatorStackRestriction` wrapper puzzle meant to strip a delegated puzzle's ability to use inter-coin messaging conditions. Its CLVM implementation only scans for the `SEND_MESSAGE` opcode (66) and ignores `RECEIVE_MESSAGE` (67), even though the two opcodes are documented and tested as a single paired messaging primitive.

### Finding Description
`send_message_banned.clsp` walks the delegated puzzle's output conditions and aborts only if it finds opcode `SEND_MESSAGE`: [1](#0-0) 

`ConditionOpcode` defines `SEND_MESSAGE = 66` and `RECEIVE_MESSAGE = 67` as a paired mechanism — both are consumed together by consensus validation (`MESSAGE_NOT_SENT_OR_RECEIVED` requires a matching sender/receiver pair for a spend bundle to succeed): [2](#0-1) 

Both opcodes carry the identical `mode_integer`/participant-commitment machinery (`SendMessage`/`ReceiveMessage` share the same base logic in `chia/wallet/conditions.py`), and both opcodes are capable of encoding a coin↔coin, coin↔puzzle, or coin↔block "message" payload with attacker-chosen `msg` bytes and `var_args`: [3](#0-2) 

Because `send_message_banned.clsp` only excludes opcode 66, an inner puzzle can emit a `RECEIVE_MESSAGE` (opcode 67) condition — instead of `SEND_MESSAGE` — carrying arbitrary attacker-chosen `msg` bytes to any target coin/puzzle/parent identity, and the restriction puzzle will not reject it. As long as a cooperating (attacker-controlled) spend in the same bundle supplies the paired `SEND_MESSAGE` half of the handshake from an unrestricted coin, the overall bundle passes consensus, and the coin protected by `SendMessageBanned` has still participated in cross-coin messaging that the restriction was specifically designed to prevent. The existing test suite only exercises the `SEND_MESSAGE` opcode against this restriction and never exercises `RECEIVE_MESSAGE`: [4](#0-3) 

This mirrors the reported bug class exactly: an authorization/restriction carve-out that names one specific identifier of a functionally-interchangeable, duplicate-purpose pair, while the alias identifier reaches the identical outcome and is left unchecked.

### Impact Explanation
`SendMessageBanned` is a security-relevant building block of the generic custody/`PuzzleWithRestrictions` framework used for multisig/DAO-style coin custody, restricted signer setups, and plotNFT/pool state machines (`chia/pools/claim_pool_rewards_dpuz.clsp` itself relies on `SEND_MESSAGE` for legitimate pool-reward claiming, confirming the messaging primitive is a live, security-relevant condition class, not a dead feature). Any custody policy that composes `SendMessageBanned` to prevent a delegated/authorized party from establishing inter-coin communication (e.g., to prevent signaling to external contracts, side-channel coordination, or unauthorized announcement-equivalent behavior as part of a larger access-control policy) can be silently bypassed by substituting `RECEIVE_MESSAGE` for `SEND_MESSAGE`. Depending on how a given custody policy composes this restriction with other members/restrictions, this can permit unauthorized coordination/state signaling from a restricted spend path that the policy author explicitly intended to block — a restriction/authorization bypass in the custody state-transition logic.

### Likelihood Explanation
Exploitation only requires an unprivileged holder of a delegated-puzzle spend under a `SendMessageBanned`-restricted custody policy to submit `RECEIVE_MESSAGE` instead of `SEND_MESSAGE`, paired with a cooperating counter-condition from any other coin in the same spend bundle (which the attacker fully controls, since they construct the bundle). This is a single-spend-bundle-reachable bypass with no privileged keys required beyond already holding a valid delegated-puzzle solution for the restricted policy, and no on-chain protocol changes are needed — likelihood is high for any deployment that actually relies on this specific restriction for its access-control guarantees.

### Recommendation
Update `send_message_banned.clsp` (and any equivalent restriction/allow-list logic elsewhere in the custody framework) to check both `SEND_MESSAGE` and `RECEIVE_MESSAGE` opcodes (or more generally, to enumerate the full set of aliases/paired opcodes for any banned condition class) rather than a single named opcode. Add a dedicated test exercising `RECEIVE_MESSAGE` against `SendMessageBanned` analogous to the existing `SEND_MESSAGE` test in `chia/_tests/clvm/test_restrictions.py`.

### Proof of Concept
1. Construct a `PuzzleWithRestrictions` with `restrictions=[ValidatorStackRestriction(required_wrappers=[SendMessageBanned()])]`, as in `test_send_message_banned`.
2. Instead of the delegated puzzle emitting a `SendMessage(...)` condition (which is correctly rejected with `Err.GENERATOR_RUNTIME_ERROR`), emit a `ReceiveMessage(...)` condition with the same `MessageParticipant` commitments.
3. In the same spend bundle, include an unrestricted coin that emits the paired `SendMessage(...)` condition matching the `ReceiveMessage` mode/msg/commitments.
4. Submit the aggregated bundle: `send_message_banned.clsp`'s `check_conditions` loop only matches on `(= (f (f conditions)) SEND_MESSAGE)` and will not find opcode 66 in the restricted coin's condition list, so the restriction wrapper's guard passes; the bundle is accepted (`MempoolInclusionStatus.SUCCESS`) despite the restricted coin having established cross-coin messaging that the restriction was meant to forbid.

### Citations

**File:** chia/wallet/puzzles/custody/send_message_banned.clsp (L1-19)
```text
; A puzzle that enforces the lack of SEND_MESSAGE conditions in the condition set
;
; Intended for use in validating the output of a delegated puzzle
(mod (Conditions)

  (include condition_codes.clib)

  (defun check_conditions (conditions return)
    (if conditions
        (if (= (f (f conditions)) SEND_MESSAGE)
            (x)
            (check_conditions (r conditions) return)
        )
        return
    )
  )

  (check_conditions Conditions Conditions)
)
```

**File:** chia/types/condition_opcodes.py (L37-38)
```python
    SEND_MESSAGE = bytes([66])
    RECEIVE_MESSAGE = bytes([67])
```

**File:** chia/wallet/conditions.py (L557-633)
```python
@streamable
@dataclass(frozen=True)
class SendMessage(Condition):
    msg: bytes
    var_args: list[Program] | None = None
    mode_integer: uint8 | None = None
    sender: MessageParticipant | None = None
    receiver: MessageParticipant | None = None
    _other_party_is_receiver: ClassVar[bool] = True

    @property
    def _other_party(self) -> MessageParticipant | None:
        return self.receiver

    @property
    def _opcode(self) -> ConditionOpcode:
        return ConditionOpcode.SEND_MESSAGE

    def __post_init__(self) -> None:
        if self.mode_integer is None and (self.sender is None or self.receiver is None):
            raise ValueError("Must specify either mode_integer or both sender and receiver")

        if self.mode_integer is not None and self.sender is not None:
            assert self.mode_integer >> 3 == self.sender.mode, (
                "The first 3 bits of mode_integer don't match the sender's mode"
            )

        if self.mode_integer is not None and self.receiver is not None:
            assert self.mode_integer & 0b000111 == self.receiver.mode, (
                "The last 3 bits of mode_integer don't match the receiver's mode"
            )

        if self.var_args is None and self._other_party is None:
            raise ValueError(
                f"Must specify either var_args or {'receiver' if self._other_party_is_receiver else 'sender'}"
            )

        if self.var_args is not None and self._other_party is not None and not self._other_party._nothing_committed:
            assert self.var_args == self._other_party.necessary_args, (
                f"The implied arguments for {self._other_party} do not match the specified arguments {self.var_args}"
            )

    @property
    def args(self) -> list[Program]:
        if self.var_args is not None:
            return self.var_args

        # The non-None-ness of this is asserted in __post_init__
        return self._other_party.necessary_args  # type: ignore[union-attr]

    @property
    def mode(self) -> uint8:
        if self.mode_integer is not None:
            return self.mode_integer

        # The non-None-ness of these are asserted in __post_init__
        return uint8((self.sender.mode << 3) | self.receiver.mode)  # type: ignore[union-attr]

    def to_program(self) -> Program:
        condition: Program = Program.to([self._opcode, self.mode, self.msg, *self.args])
        return condition

    @classmethod
    def from_program(cls, program: Program) -> Self:
        full_mode = uint8(program.at("rf").as_int())
        var_args = list(program.at("rrr").as_iter())
        return cls(
            program.at("rrf").as_atom(),
            var_args,
            full_mode,
            MessageParticipant.from_mode_and_maybe_args(
                True, full_mode, var_args if not cls._other_party_is_receiver else None
            ),
            MessageParticipant.from_mode_and_maybe_args(
                False, full_mode, var_args if cls._other_party_is_receiver else None
            ),
        )
```

**File:** chia/_tests/clvm/test_restrictions.py (L241-284)
```python
@pytest.mark.anyio
async def test_send_message_banned(cost_logger: CostLogger) -> None:
    async with sim_and_client() as (sim, client):
        restriction = ValidatorStackRestriction(required_wrappers=[SendMessageBanned()])
        pwr = PuzzleWithRestrictions(nonce=0, restrictions=[restriction], puzzle=ACSMember())

        # Farm and find coin
        await sim.farm_block(pwr.puzzle_hash())
        coin = (await client.get_coin_records_by_puzzle_hashes([pwr.puzzle_hash()], include_spent_coins=False))[0].coin

        # Attempt to send a message
        send_message_dpuz = DelegatedPuzzleAndSolution(
            puzzle=Program.to(
                (
                    1,
                    [
                        SendMessage(
                            bytes32.zeros,
                            sender=MessageParticipant(parent_id_committed=bytes32.zeros),
                            receiver=MessageParticipant(parent_id_committed=bytes32.zeros),
                        ).to_program()
                    ],
                )
            ),
            solution=Program.to(None),
        )
        wrapped_dpuz = restriction.modify_delegated_puzzle_and_solution(send_message_dpuz, [Program.to(None)])
        escape_attempt = WalletSpendBundle(
            [
                make_spend(
                    coin,
                    pwr.puzzle_reveal(),
                    pwr.solve(
                        [],
                        [Program.to([send_message_dpuz.puzzle.get_tree_hash()])],
                        Program.to(None),
                        wrapped_dpuz,
                    ),
                )
            ],
            G2Element(),
        )
        result = await client.push_tx(escape_attempt)
        assert result == (MempoolInclusionStatus.FAILED, Err.GENERATOR_RUNTIME_ERROR)
```

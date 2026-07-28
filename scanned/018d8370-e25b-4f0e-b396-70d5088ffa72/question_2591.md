# Q2591: IsTransaction binding confusion freeze

## Question
Can an unprivileged attacker enter through call the ICS20 precompile or submit `MsgTransfer` through the IBC transfer path and use source port/channel, receiver, denom trace, amount, memo, packet timing, ack/timeout behavior, and nested call flow so that `precompiles/ics20/ics20.go:IsTransaction` mishandles ICS20 transfer path because `IsTransaction` may normalize or validate source channel, port, or client identifiers inconsistently, so assets enter a path that cannot be completed or safely refunded, causing `the intended source routing binding` and `the routing identity actually used during settlement` to diverge or settle in the wrong order, breaking the invariant that channel/client binding must be canonical and unambiguous before any asset is escrowed or representation is created and leading to `Permanent locking / freezing of funds or clients`?

## Target
- File/function: `precompiles/ics20/ics20.go:IsTransaction`
- Entrypoint: call the ICS20 precompile or submit `MsgTransfer` through the IBC transfer path
- Attacker controls: source port/channel, receiver, denom trace, amount, memo, packet timing, ack/timeout behavior, and nested call flow
- Exploit idea: Drive the ICS20 transfer path through a crafted path that reaches `IsTransaction` with attacker-controlled source port/channel, receiver, denom trace, amount, memo, packet timing, ack/timeout behavior, and nested call flow. Then force the failure, replay, nested-call, or ordering condition described above and compare `the intended source routing binding` against `the routing identity actually used during settlement`.
- Invariant to test: channel/client binding must be canonical and unambiguous before any asset is escrowed or representation is created
- Expected Immunefi impact: `Permanent locking / freezing of funds or clients`
- Fast validation: fuzz v1/v2 channel identifiers and route normalization and assert invalid or ambiguous bindings fail before any asset moves

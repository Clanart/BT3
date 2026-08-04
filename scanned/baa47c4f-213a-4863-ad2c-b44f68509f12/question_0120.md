# Q120: getLeaderSchedule single-request panic surface

## Question
Can an unprivileged attacker enter through `getLeaderSchedule` and supply rpc params, commitment, encoding flags, and boundary account/slot/message inputs so that `get_leader_schedule` hits a path where a validly encoded but adversarial request can reach an assertion, unwrap, or impossible-state assumption, breaking the invariant that public input must not be able to crash the rpc path and leading to `RPC DoS/Crash`?

## Target
- File/function: rpc/src/rpc.rs::get_leader_schedule
- Entrypoint: JSON-RPC `getLeaderSchedule` from a single unauthenticated client at no more than once per `CLUSTER_SLOT_TIME_TARGET / 2`
- Attacker controls: RPC params, commitment, encoding flags, and boundary account/slot/message inputs
- Exploit idea: treat the API as a crash surface as well as a slow path surface
- Invariant to test: public input must not be able to crash the RPC path
- Expected Immunefi impact: RPC DoS/Crash
- Fast validation: run focused fuzzing on validly encoded parameters and stop on crashes

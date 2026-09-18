### Title
Unsynchronized `Status`/`Incarnation` field reads race with mutex-protected writes in the OCC scheduler - (File: `sei-cosmos/tasks/scheduler.go`)

### Summary
`sei-cosmos/tasks/scheduler.go` implements the OCC (optimistic concurrency control) transaction scheduler that drives parallel `DeliverTx` execution. `deliverTxTask.Status` is guarded by `dt.mx` via the `IsStatus`/`SetStatus` accessors [1](#0-0) , but several call sites read `task.Status` (and related fields such as `Incarnation`) directly, bypassing the mutex, while other goroutines concurrently mutate the same fields through the locked setter.

### Finding Description
`shouldRerun` switches directly on `task.Status` without taking `dt.mx` [2](#0-1) , and `findFirstNonValidated` likewise reads `t.Status` directly [3](#0-2) . These plain reads race with `SetStatus`, which is called under `dt.mx.Lock()` from `executeAll`'s worker goroutines (via `prepareAndRunTask`) and from `validateAll`'s per-task validation goroutines [4](#0-3) . `ProcessAll`'s main loop calls `s.executeAll(ctx, toExecute)` then `s.validateAll(ctx, tasks)` in a loop driven by `allValidated(tasks)` [5](#0-4) ; `validateTask`/`shouldRerun` run inside `validateAll`'s worker-pool goroutines concurrently with each other across different tasks, and `Reset()`/`Increment()` mutate `Incarnation`, `Response`, `Abort`, `AbortCh`, and `VersionStores` without holding `dt.mx` at all [6](#0-5) , even though `AppendDependencies` and other accessors on the same struct take the lock. This is analogous to the go-ethereum `chainSyncer` bug: a struct field is nominally protected by a mutex in some accessors but read/written directly elsewhere, producing a real data race under Go's memory model (detectable with `-race`), even though the racing goroutines belong to the same node process rather than a remote peer.

Because this is entirely internal scheduling state, not a p2p/consensus message path, it is reachable purely by submitting transactions into a block that gets processed with parallel OCC execution (`workers > 1`).

### Impact Explanation
A data race on `Status`/`Incarnation` can, under Go's memory model, produce torn or stale reads that cause the scheduler to take an incorrect branch in `shouldRerun`/`findFirstNonValidated` — e.g., treating an aborted/pending task as validated or vice versa. This could let a task's stale execution result (using a version-store snapshot that a coincident write invalidated) be accepted into the block's final response set via `collectResponses`, which is a correctness hazard for deterministic state transitions across validators: if the race resolves differently on different nodes (or across replays on the same node, e.g. consensus vs. re-execution), it can cause a consensus/app-hash mismatch (chain split) or an incorrect transaction result being committed (fund-safety-adjacent for any tx whose effects depend on OCC validation, e.g., token transfers, tokenfactory mints, EVM txs routed through this OCC path). This crosses the required severity bar because a non-deterministic/incorrect commit of block results is a form of "permanent chain split" risk, not merely a resource/perf issue.

### Likelihood Explanation
Likelihood is moderate to low in practice because Go's compiler/runtime rarely mis-observes single-word string/int writes on typical architectures, so most executions will not visibly misbehave; but this is exactly the class of bug the reference report demonstrates (`go test -race` catches an unsynchronized read/write of the same field from concurrent goroutines) and it is reachable on every block that has more than one transaction and runs with OCC workers enabled — no special validator collusion or crafted message is required, only ordinary concurrent transaction submission that produces contention (aborts/reruns) exercised by `shouldRerun`/`findFirstNonValidated`/`Reset`.

### Recommendation
Route every read and write of `deliverTxTask.Status`, `Incarnation`, `Response`, `Abort`, `AbortCh`, and `VersionStores` through the existing `dt.mx` lock: change `shouldRerun`'s `switch task.Status` to use `task.IsStatus(...)`-based checks (or add a `task.GetStatus()` accessor that takes `RLock`), make `findFirstNonValidated` call `t.IsStatus(statusValidated)` instead of comparing `t.Status` directly, and have `Reset()`/`Increment()` take `dt.mx.Lock()` for the whole set of field mutations (or otherwise ensure these are only ever called while already holding the lock, with that invariant documented/enforced). Running the scheduler test suite under `go test -race ./sei-cosmos/tasks/...` should be added to CI to catch regressions of this kind.

### Proof of Concept
Not directly reproducible without a live race detector run, but the unsynchronized access pattern is structural and visible in code:
1. `ProcessAll` starts worker pools and runs `executeAll` then `validateAll` in a loop over many tasks concurrently [7](#0-6) .
2. Inside `validateAll`, each task's `validateTask` → `shouldRerun` reads `task.Status` with no lock [2](#0-1)  while another goroutine may concurrently call `task.SetStatus(...)` (line 387/392) or `t.Reset()`/`t.Increment()` (lines 443-444) on a *different* task object that is nonetheless read via the shared `tasks`/`s.allTasks` slice by `findFirstNonValidated` on the driving goroutine at the same time as workers mutate other tasks' equivalent fields — reproducing the same "protected-in-some-places, not in-others" struct field access pattern that go-ethereum's `-race` run caught in `chainSyncer`. Running `go test -race ./sei-cosmos/tasks/...` with the existing scheduler stress tests (e.g., `scheduler_test.go`) under high task counts and worker parallelism should surface the same class of failure.

### Citations

**File:** sei-cosmos/tasks/scheduler.go (L76-86)
```go
func (dt *deliverTxTask) IsStatus(s status) bool {
	dt.mx.RLock()
	defer dt.mx.RUnlock()
	return dt.Status == s
}

func (dt *deliverTxTask) SetStatus(s status) {
	dt.mx.Lock()
	defer dt.mx.Unlock()
	dt.Status = s
}
```

**File:** sei-cosmos/tasks/scheduler.go (L88-102)
```go
func (dt *deliverTxTask) Reset() {
	dt.SetStatus(statusPending)
	dt.Response = nil
	dt.Abort = nil
	dt.AbortCh = nil
	dt.VersionStores = nil

	if dt.TxTracer != nil {
		dt.TxTracer.Reset()
	}
}

func (dt *deliverTxTask) Increment() {
	dt.Incarnation++
}
```

**File:** sei-cosmos/tasks/scheduler.go (L298-335)
```go
	workerCtx, cancel := context.WithCancel(ctx.Context())
	defer cancel()

	// execution tasks are limited by workers
	start(workerCtx, s.executeCh, workers)

	// validation tasks uses length of tasks to avoid blocking on validation
	start(workerCtx, s.validateCh, len(tasks))

	toExecute := tasks
	for !allValidated(tasks) {
		// if the max incarnation >= x, we should revert to synchronous
		if iterations >= maximumIterations {
			// process synchronously
			s.synchronous = true
			startIdx, anyLeft := s.findFirstNonValidated()
			if !anyLeft {
				break
			}
			toExecute = tasks[startIdx:]
		}

		// execute sets statuses of tasks to either executed or aborted
		if err := s.executeAll(ctx, toExecute); err != nil {
			return nil, err
		}

		// validate returns any that should be re-executed
		// note this processes ALL tasks, not just those recently executed
		var err error
		toExecute, err = s.validateAll(ctx, tasks)
		if err != nil {
			return nil, err
		}
		// these are retries which apply to metrics
		s.metrics.retries += len(toExecute)
		iterations++
	}
```

**File:** sei-cosmos/tasks/scheduler.go (L362-363)
```go
func (s *scheduler) shouldRerun(task *deliverTxTask) bool {
	switch task.Status {
```

**File:** sei-cosmos/tasks/scheduler.go (L412-419)
```go
func (s *scheduler) findFirstNonValidated() (int, bool) {
	for i, t := range s.allTasks {
		if t.Status != statusValidated {
			return i, true
		}
	}
	return 0, false
}
```

**File:** sei-cosmos/tasks/scheduler.go (L434-452)
```go
	wg := &sync.WaitGroup{}
	for i := startIdx; i < len(tasks); i++ {
		wg.Add(1)
		t := tasks[i]
		s.DoValidate(func() {
			defer wg.Done()
			if !s.validateTask(ctx, t) {
				mx.Lock()
				defer mx.Unlock()
				t.Reset()
				t.Increment()
				// update max incarnation for scheduler
				if t.Incarnation > s.maxIncarnation {
					s.maxIncarnation = t.Incarnation
				}
				res = append(res, t)
			}
		})
	}
```

[1](#0-0) [2](#0-1)

### Citations

**File:** app/src/lib/copilot/conflict-resolution-model.ts (L26-30)
```typescript
export function getConflictResolutionModelDisplay(
  selection: string | null,
  copilotModels: ReadonlyArray<Model> | null,
  byokProviders: ReadonlyArray<IBYOKProvider>
): IConflictResolutionModelDisplay {
```

**File:** app/src/lib/copilot/conflict-resolution-model.ts (L60-67)
```typescript
  // Metadata unavailable (list not loaded, or selection no longer offered):
  // mirror the engine — fall back to the requested id or default model, and
  // omit the effort since we can't confirm the model supports it.
  return {
    modelName: requestedModelId ?? DefaultCopilotModelName,
    reasoningEffort: undefined,
  }
}
```
